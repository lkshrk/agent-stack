package main

import (
	"context"
	"encoding/json"
	"fmt"
	"log"
	"strconv"
	"strings"
	"time"
)

const (
	cursorName      = "linear-bridge"
	projectInterval = 30 * time.Second
	defaultBatch    = 100
	postRunPrefix   = "BLOCKED{permission}: post-run check: "
)

type Activity struct{ Type, Body string }

func liveBlocked(c Card) bool {
	return (c.Status == "blocked" || c.Status == "triage") && c.RunID > 0
}

func linearStatus(c Card) (string, bool) {
	switch c.Status {
	case "triage", "blocked":
		if liveBlocked(c) {
			return "Blocked", true
		}
		return "Todo", true
	case "todo", "ready", "scheduled":
		return "Todo", true
	case "running":
		return "In Progress", true
	case "review", "done":
		return "In Review", true
	}
	return "", false
}

func sessionActivity(c Card) (Activity, bool) {
	switch {
	case c.Status == "running":
		return Activity{"thought", fmt.Sprintf("Working: %s run %d.", c.Assignee, c.RunID)}, true
	case liveBlocked(c) && c.Outcome == "gave_up":
		return Activity{"error", "Gave up: " + c.BlockReason}, true
	case liveBlocked(c) && c.BlockReason != "":
		return Activity{"elicitation", c.BlockReason}, true
	case c.Status == "done":
		if pr := metaString(c.Metadata, "pr_url"); pr != "" {
			return Activity{"response", "Done. PR: " + pr}, true
		}
		if c.Summary != "" {
			return Activity{"response", "Done: " + c.Summary}, true
		}
	}
	return Activity{}, false
}

func metaString(m map[string]any, key string) string {
	switch v := m[key].(type) {
	case nil:
		return ""
	case string:
		return v
	default:
		raw, err := json.Marshal(v)
		if err != nil {
			return fmt.Sprint(v)
		}
		return string(raw)
	}
}

func evidenceComment(c Card) (string, bool) {
	if c.Status != "done" && !liveBlocked(c) {
		return "", false
	}
	var lines []string
	for _, f := range []struct{ label, key string }{
		{"Bundle", "bundle"},
		{"Omnigent session", "omnigent_session_id"},
		{"PR", "pr_url"},
		{"Post-run", "post_run"},
	} {
		if v := metaString(c.Metadata, f.key); v != "" {
			lines = append(lines, f.label+": "+v)
		}
	}
	for _, g := range []struct{ label, key string }{{"Gaps", "gaps"}, {"Doc gaps", "doc_gaps"}} {
		items, _ := c.Metadata[g.key].([]any)
		if len(items) == 0 {
			continue
		}
		lines = append(lines, g.label+":")
		for _, it := range items {
			lines = append(lines, "- "+fmt.Sprint(it))
		}
	}
	if len(lines) == 0 {
		return "", false
	}
	return strings.Join(lines, "\n"), true
}

type projector struct {
	kanban kanbanAPI
	linear linearAPI
	notify notifier
	done   map[string]string
	batch  int

	linked map[string]bool
	warned map[string]bool
}

func newProjector(k kanbanAPI, l linearAPI, n notifier) *projector {
	return &projector{kanban: k, linear: l, notify: n, done: map[string]string{}, batch: defaultBatch}
}

func (p *projector) run(ctx context.Context) {
	t := time.NewTicker(projectInterval)
	defer t.Stop()
	for {
		if err := p.tick(ctx); err != nil && ctx.Err() == nil {
			log.Printf("linear-bridge: project: %v", err)
		}
		select {
		case <-ctx.Done():
			return
		case <-t.C:
		}
	}
}

func (p *projector) tick(ctx context.Context) error {
	if p.done == nil {
		p.done = map[string]string{}
	}
	if p.linked == nil {
		p.linked = map[string]bool{}
	}
	if p.warned == nil {
		p.warned = map[string]bool{}
	}
	batch := p.batch
	if batch <= 0 {
		batch = defaultBatch
	}
	cctx, cancel := context.WithTimeout(ctx, cardTimeout)
	after, err := p.kanban.Cursor(cctx, cursorName)
	cancel()
	if err != nil {
		return fmt.Errorf("cursor: %w", err)
	}
	cctx, cancel = context.WithTimeout(ctx, cardTimeout)
	events, err := p.kanban.Events(cctx, after, batch)
	cancel()
	if err != nil {
		return fmt.Errorf("events: %w", err)
	}
	if len(events) == 0 {
		return nil
	}
	seen := map[string]bool{}
	for _, ev := range events {
		if seen[ev.CardID] {
			continue
		}
		seen[ev.CardID] = true
		if err := p.project(ctx, ev.CardID); err != nil {
			return fmt.Errorf("card %s: %w", ev.CardID, err)
		}
	}
	cctx, cancel = context.WithTimeout(ctx, cardTimeout)
	defer cancel()
	if err := p.kanban.SetCursor(cctx, cursorName, events[len(events)-1].ID); err != nil {
		return fmt.Errorf("set cursor: %w", err)
	}
	return nil
}

func (p *projector) project(ctx context.Context, cardID string) error {
	cctx, cancel := context.WithTimeout(ctx, cardTimeout)
	card, err := p.kanban.Card(cctx, cardID)
	cancel()
	if err != nil {
		return err
	}
	if card.Link == nil {
		return nil
	}
	key := card.Status + "|" + strconv.FormatInt(card.RunID, 10) + "|" + card.Outcome
	if p.done[cardID] == key {
		return nil
	}
	cctx, cancel = context.WithTimeout(ctx, cardTimeout)
	issue, err := p.linear.Issue(cctx, card.Link.IssueID)
	cancel()
	if err != nil {
		return fmt.Errorf("issue lookup: %w", err)
	}
	if err := p.setState(ctx, card, issue); err != nil {
		return err
	}
	if !p.linked[cardID] {
		cctx, cancel = context.WithTimeout(ctx, cardTimeout)
		err = p.linear.LinkCard(cctx, card.Link.IssueID, cardID)
		cancel()
		if err != nil {
			return fmt.Errorf("link card: %w", err)
		}
		p.linked[cardID] = true
	}
	if a, ok := sessionActivity(card); ok && card.Link.SessionID != "" {
		cctx, cancel = context.WithTimeout(ctx, cardTimeout)
		err = p.linear.Activity(cctx, card.Link.SessionID, a)
		cancel()
		if err != nil {
			return fmt.Errorf("activity: %w", err)
		}
	}
	if body, ok := evidenceComment(card); ok {
		cctx, cancel = context.WithTimeout(ctx, cardTimeout)
		err = p.linear.Comment(cctx, card.Link.IssueID, body)
		cancel()
		if err != nil {
			return fmt.Errorf("comment: %w", err)
		}
	}
	p.done[cardID] = key
	if strings.HasPrefix(card.BlockReason, postRunPrefix) {
		p.alert("🔴 " + card.Link.Identifier + ": " + card.BlockReason)
	}
	return nil
}

func (p *projector) setState(ctx context.Context, card Card, issue IssueInfo) error {
	name, ok := linearStatus(card)
	if !ok {
		return nil
	}
	var target *WorkflowState
	for i := range issue.States {
		if issue.States[i].Name == name {
			target = &issue.States[i]
			break
		}
	}
	if target == nil {
		w := issue.TeamID + "|" + name
		if !p.warned[w] {
			p.warned[w] = true
			log.Printf("linear-bridge: team %s has no workflow state %q", issue.TeamID, name)
		}
		return nil
	}
	if target.ID == issue.StateID {
		return nil
	}
	cctx, cancel := context.WithTimeout(ctx, cardTimeout)
	defer cancel()
	if err := p.linear.SetState(cctx, issue.ID, target.ID); err != nil {
		return fmt.Errorf("set state: %w", err)
	}
	return nil
}

func (p *projector) alert(msg string) {
	ctx, cancel := context.WithTimeout(context.Background(), notifyTimeout)
	defer cancel()
	if err := p.notify.Notify(ctx, msg); err != nil {
		log.Printf("linear-bridge: notify failed: %v", err)
	}
}
