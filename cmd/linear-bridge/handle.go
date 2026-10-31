package main

import (
	"context"
	"encoding/json"
	"fmt"
	"log"
)

type bridge struct {
	linear    linearAPI
	notify    notifier
	kanban    kanbanAPI
	ownerID   string
	appUserID string
}

func (b *bridge) handle(ctx context.Context, ev event) error {
	switch {
	case ev.Type == "Issue" && ev.Action == "create":
		return b.auditIssue(ctx, ev)
	case ev.Type == "AgentSessionEvent" && ev.Action == "created":
		return b.ackSession(ctx, ev)
	case ev.Type == "Issue" && ev.Action == "update":
		return b.issueUpdated(ctx, ev)
	case ev.Type == "AgentSessionEvent" && ev.Action == "prompted":
		b.logPrompted(ev)
	}
	return nil
}

func (b *bridge) auditIssue(ctx context.Context, ev event) error {
	var d struct {
		ID         string `json:"id"`
		Identifier string `json:"identifier"`
		CreatorID  string `json:"creatorId"`
		ParentID   string `json:"parentId"`
	}
	if err := json.Unmarshal(ev.Data, &d); err != nil {
		return fmt.Errorf("issue data: %w", err)
	}
	if b.appUserID == "" || d.CreatorID != b.appUserID || d.ParentID != "" {
		return nil
	}
	msg := "linear-bridge: archived top-level issue " + d.Identifier + " created by the Hermes app"
	archiveErr := b.linear.ArchiveIssue(ctx, d.ID)
	if archiveErr != nil {
		msg = fmt.Sprintf("linear-bridge: failed to archive top-level issue %s created by the Hermes app: %v", d.Identifier, archiveErr)
	}
	if err := b.notify.Notify(ctx, msg); err != nil {
		log.Printf("linear-bridge: notify failed: %v", err)
		if archiveErr == nil {
			return err
		}
	}
	return archiveErr
}

func (b *bridge) ackSession(ctx context.Context, ev event) error {
	var s struct {
		ID        string `json:"id"`
		CreatorID string `json:"creatorId"`
		IssueID   string `json:"issueId"`
	}
	if err := json.Unmarshal(ev.AgentSession, &s); err != nil {
		return fmt.Errorf("agent session: %w", err)
	}
	if s.ID == "" {
		return fmt.Errorf("agent session has no id")
	}
	if b.ownerID == "" || s.CreatorID != b.ownerID {
		return b.linear.Thought(ctx, s.ID, "Only the workspace owner can start work for this agent.")
	}
	return b.startWork(ctx, s.ID, s.IssueID)
}

func (b *bridge) startWork(ctx context.Context, sessionID, issueID string) error {
	const ack = "Received. Checking readiness."
	issueCtx, cancel := context.WithTimeout(ctx, cardTimeout)
	issue, err := b.linear.Issue(issueCtx, issueID)
	cancel()
	if err != nil {
		return b.queueFailed(ctx, sessionID, issueID, fmt.Errorf("issue lookup: %w", err))
	}
	if b.appUserID == "" || issue.DelegateID != b.appUserID {
		return b.linear.Thought(ctx, sessionID, ack)
	}
	if issue.ParentID != "" {
		return b.linear.Thought(ctx, sessionID, "Sub-issues start from their root issue.")
	}
	cardCtx, cancel := context.WithTimeout(ctx, cardTimeout)
	_, err = b.kanban.CreateRootCard(cardCtx, RootCardRequest{
		IssueID:     issue.ID,
		Identifier:  issue.Identifier,
		Title:       issue.Title,
		Description: issue.Description,
		SessionID:   sessionID,
	})
	cancel()
	if err != nil {
		return b.queueFailed(ctx, sessionID, issue.Identifier, err)
	}
	return b.linear.Thought(ctx, sessionID, ack)
}

func (b *bridge) queueFailed(ctx context.Context, sessionID, issueRef string, cause error) error {
	msg := fmt.Sprintf("linear-bridge: could not queue %s on Kanban: %v", issueRef, cause)
	b.alert(msg)
	thoughtErr := b.linear.Thought(ctx, sessionID, "Could not queue this issue; the owner was notified.")
	if thoughtErr != nil {
		return thoughtErr
	}
	return cause
}

func (b *bridge) alert(msg string) {
	ctx, cancel := context.WithTimeout(context.Background(), notifyTimeout)
	defer cancel()
	if err := b.notify.Notify(ctx, msg); err != nil {
		log.Printf("linear-bridge: notify failed: %v", err)
	}
}

func (b *bridge) stopCard(identifier, cardID, reason string) error {
	ctx, cancel := context.WithTimeout(context.Background(), stopTimeout)
	defer cancel()
	err := b.kanban.StopCard(ctx, cardID, reason)
	if err != nil {
		b.alert(fmt.Sprintf("🔴 %s: stop failed: %s", identifier, shortErr(err)))
	}
	return err
}

func shortErr(err error) string {
	msg := err.Error()
	if len(msg) > 200 {
		return msg[:200]
	}
	return msg
}

func (b *bridge) issueUpdated(ctx context.Context, ev event) error {
	if b.ownerID == "" || ev.Actor == nil || ev.Actor.ID != b.ownerID {
		return nil
	}
	var d struct {
		ID         string `json:"id"`
		Identifier string `json:"identifier"`
		DelegateID string `json:"delegateId"`
		StateID    string `json:"stateId"`
	}
	if err := json.Unmarshal(ev.Data, &d); err != nil {
		return fmt.Errorf("issue data: %w", err)
	}
	var from map[string]json.RawMessage
	if len(ev.UpdatedFrom) > 0 {
		if err := json.Unmarshal(ev.UpdatedFrom, &from); err != nil {
			return fmt.Errorf("issue updatedFrom: %w", err)
		}
	}
	if d.ID == "" {
		return nil
	}
	_, delegateChanged := from["delegateId"]
	_, stateChanged := from["stateId"]
	switch {
	case delegateChanged && d.DelegateID == "":
		card, found, err := b.cardFor(ctx, d.ID)
		if err != nil || !found {
			return err
		}
		return b.stopCard(d.Identifier, card.ID, "owner: un-delegated")
	case stateChanged:
		card, found, err := b.cardFor(ctx, d.ID)
		if err != nil || !found {
			return err
		}
		switch card.Status {
		case "ready", "running", "blocked":
		default:
			return nil
		}
		issueCtx, cancel := context.WithTimeout(ctx, cardTimeout)
		issue, err := b.linear.Issue(issueCtx, d.ID)
		cancel()
		if err != nil {
			return fmt.Errorf("issue lookup: %w", err)
		}
		known := false
		for _, st := range issue.States {
			if st.ID != d.StateID {
				continue
			}
			known = true
			switch st.Type {
			case "backlog", "unstarted", "canceled":
				return b.stopCard(d.Identifier, card.ID, "owner: moved to "+st.Name)
			}
		}
		if !known {
			log.Printf("linear-bridge: issue %s moved to unknown state %s", d.ID, d.StateID)
		}
	}
	return nil
}

func (b *bridge) cardFor(ctx context.Context, issueID string) (Card, bool, error) {
	ctx, cancel := context.WithTimeout(ctx, cardTimeout)
	defer cancel()
	return b.kanban.CardByIssue(ctx, issueID)
}

func (b *bridge) logPrompted(ev event) {
	var s struct {
		ID string `json:"id"`
	}
	var a struct {
		UserID string `json:"userId"`
	}
	_ = json.Unmarshal(ev.AgentSession, &s)
	_ = json.Unmarshal(ev.AgentActivity, &a)
	log.Printf("linear-bridge: session %s prompted by %s", s.ID, a.UserID)
}
