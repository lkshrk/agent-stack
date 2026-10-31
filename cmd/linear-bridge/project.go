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
			lines = append(lines, "- "+gapLine(it))
		}
	}
	if len(lines) == 0 {
		return "", false
	}
	return strings.Join(lines, "\n"), true
}

func gapLine(it any) string {
	switch v := it.(type) {
	case string:
		return v
	case map[string]any:
		if id, q := metaString(v, "id"), metaString(v, "question"); id != "" && q != "" {
			line := id + ": " + q
			if where := metaString(v, "location"); where != "" {
				line += " (stub: " + where + ")"
			} else if stub := metaString(v, "stub"); stub != "" {
				line += " (stub: " + stub + ")"
			}
			return line
		}
		if f, r := metaString(v, "file"), metaString(v, "reason"); f != "" && r != "" {
			return f + ": " + r
		}
	}
	raw, err := json.Marshal(it)
	if err != nil {
		return fmt.Sprint(it)
	}
	return string(raw)
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
	skipped     map[string]bool
	warned      map[string]bool

	stallTicks   int
	stallCards   map[string]bool
	stallAlerted bool
}

const (
	maxCardFailures = 3
	stallThreshold  = 10
)

func (p *projector) resetStall() {
	p.stallTicks, p.stallCards, p.stallAlerted = 0, nil, false
}

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
	if p.skipped == nil {
		p.skipped = map[string]bool{}
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
		p.resetStall()
		return nil
	}
	seen := map[string]bool{}
	errs := map[string]error{}
	var order []string
	succeeded := false
	for _, ev := range events {
		if seen[ev.CardID] {
			continue
		}
		seen[ev.CardID] = true
		if err := p.project(ctx, ev.CardID); err != nil {
			errs[ev.CardID] = err
			order = append(order, ev.CardID)
			continue
		}
		succeeded = true
		delete(p.fails, ev.CardID)
	}
	if succeeded || len(errs) == 0 {
		p.resetStall()
	} else {
		p.stallTicks++
		if p.stallCards == nil {
			p.stallCards = map[string]bool{}
		}
		for id := range errs {
			p.stallCards[id] = true
		}
		if p.stallTicks >= stallThreshold && !p.stallAlerted {
			p.stallAlerted = true
			p.alert("🔴 linear-bridge projection stalled: " + shortErr(errs[order[0]]))
		}
		if p.stallTicks < stallThreshold || len(p.stallCards) > 1 {
			return fmt.Errorf("card %s: %w", order[0], errs[order[0]])
		}
	}
	var firstErr error
	for _, id := range order {
		if p.skipped[id] {
			continue
		}
		p.fails[id]++
		if p.fails[id] < maxCardFailures {
			if firstErr == nil {
				firstErr = fmt.Errorf("card %s: %w", id, errs[id])
			}
			continue
		}
		delete(p.fails, id)
		p.skipped[id] = true
		ident := p.identifiers[id]
		if ident == "" {
			ident = "unlinked"
		}
		msg := fmt.Sprintf("⚠️ projection skipped for card %s (%s): %s", id, ident, shortErr(errs[id]))
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
	p.skipped = map[string]bool{}
	p.resetStall()
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
	for _, st := range issue.States {
		if st.ID == issue.StateID && (st.Type == "completed" || st.Type == "canceled") {
			return nil
		}
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
