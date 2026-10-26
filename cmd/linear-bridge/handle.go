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
	ownerID   string
	appUserID string
}

func (b *bridge) handle(ctx context.Context, ev event) error {
	switch {
	case ev.Type == "Issue" && ev.Action == "create":
		return b.auditIssue(ctx, ev)
	case ev.Type == "AgentSessionEvent" && ev.Action == "created":
		return b.ackSession(ctx, ev)
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
	}
	if err := json.Unmarshal(ev.AgentSession, &s); err != nil {
		return fmt.Errorf("agent session: %w", err)
	}
	if s.ID == "" {
		return fmt.Errorf("agent session has no id")
	}
	body := "Only the workspace owner can start work for this agent."
	if b.ownerID != "" && s.CreatorID == b.ownerID {
		body = "Received. Checking readiness."
	}
	return b.linear.Thought(ctx, s.ID, body)
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
