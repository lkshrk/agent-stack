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

	steps       map[string]bool
	identifiers map[string]string
	fails       map[string]int
	warned      map[string]bool
}

const maxCardFailures = 3

func newProjector(k kanbanAPI, l linearAPI, n notifier) *projector {
	return &projector{kanban: k, linear: l, notify: n, done: map[string]string{}, batch: defaultBatch, identifiers: map[string]string{}}
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
	if p.identifiers == nil {
		p.identifiers = map[string]string{}
	}
	if p.steps == nil {
		p.steps = map[string]bool{}
	}
	if p.fails == nil {
		p.fails = map[string]int{}
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
	var firstErr error
	for _, ev := range events {
		if seen[ev.CardID] {
			continue
		}
		seen[ev.CardID] = true
		err := p.project(ctx, ev.CardID)
		if err == nil {
			delete(p.fails, ev.CardID)
			continue
		}
		p.fails[ev.CardID]++
		if p.fails[ev.CardID] < maxCardFailures {
			if firstErr == nil {
				firstErr = fmt.Errorf("card %s: %w", ev.CardID, err)
			}
			continue
		}
		delete(p.fails, ev.CardID)
		ident := p.identifiers[ev.CardID]
		if ident == "" {
			ident = "unlinked"
		}
		msg := fmt.Sprintf("⚠️ projection skipped for card %s (%s): %s", ev.CardID, ident, shortErr(err))
		log.Print("linear-bridge: " + msg)
		p.alert(msg)
	}
	if firstErr != nil {
		return firstErr
	}
	cctx, cancel = context.WithTimeout(ctx, cardTimeout)
	defer cancel()
	if err := p.kanban.SetCursor(cctx, cursorName, events[len(events)-1].ID); err != nil {
		return fmt.Errorf("set cursor: %w", err)
	}
	return nil
}

func (p *projector) step(key, name string, fn func() error) error {
	k := key + "|" + name
	if p.steps[k] {
		return nil
	}
	if err := fn(); err != nil {
		return fmt.Errorf("%s: %w", name, err)
	}
	p.steps[k] = true
	return nil
}

func callWith(ctx context.Context, fn func(context.Context) error) error {
	cctx, cancel := context.WithTimeout(ctx, cardTimeout)
	defer cancel()
	return fn(cctx)
}

func (p *projector) project(ctx context.Context, cardID string) error {
	var card Card
	err := callWith(ctx, func(c context.Context) (err error) {
		card, err = p.kanban.Card(c, cardID)
		return
	})
	if err != nil {
		return err
	}
	if card.Link == nil {
		return nil
	}
	p.identifiers[cardID] = card.Link.Identifier
	key := strings.Join([]string{cardID, card.Status, strconv.FormatInt(card.RunID, 10), card.Outcome, card.BlockReason}, "|")
	if p.done[cardID] == key {
		return nil
	}
	var issue IssueInfo
	err = callWith(ctx, func(c context.Context) (err error) {
		issue, err = p.linear.Issue(c, card.Link.IssueID)
		return
	})
	if err != nil {
		return fmt.Errorf("issue lookup: %w", err)
	}
	if err := p.step(key, "set state", func() error { return p.setState(ctx, card, issue) }); err != nil {
		return err
	}
	err = p.step(cardID, "link card", func() error {
		return callWith(ctx, func(c context.Context) error { return p.linear.LinkCard(c, card.Link.IssueID, cardID) })
	})
	if err != nil {
		return err
	}
	if a, ok := sessionActivity(card); ok && card.Link.SessionID != "" {
		err = p.step(key, "activity", func() error {
			return callWith(ctx, func(c context.Context) error { return p.linear.Activity(c, card.Link.SessionID, a) })
		})
		if err != nil {
			return err
		}
	}
	if body, ok := evidenceComment(card); ok {
		err = p.step(key, "comment", func() error {
			return callWith(ctx, func(c context.Context) error { return p.linear.Comment(c, card.Link.IssueID, body) })
		})
		if err != nil {
			return err
		}
	}
	if strings.HasPrefix(card.BlockReason, postRunPrefix) {
		_ = p.step(key, "alert", func() error {
			p.alert("🔴 " + card.Link.Identifier + ": " + card.BlockReason)
			return nil
		})
	}
	p.done[cardID] = key
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
