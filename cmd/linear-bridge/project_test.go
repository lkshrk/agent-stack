package main

import (
	"context"
	"errors"
	"strings"
	"testing"
)

func TestLinearStatus(t *testing.T) {
	cases := []struct {
		c    Card
		want string
		ok   bool
	}{
		{Card{Status: "triage"}, "Todo", true},
		{Card{Status: "triage", RunID: 3}, "Blocked", true},
		{Card{Status: "todo"}, "Todo", true},
		{Card{Status: "ready"}, "Todo", true},
		{Card{Status: "scheduled"}, "Todo", true},
		{Card{Status: "blocked"}, "Todo", true},
		{Card{Status: "blocked", RunID: 2}, "Blocked", true},
		{Card{Status: "running", RunID: 1}, "In Progress", true},
		{Card{Status: "review"}, "In Review", true},
		{Card{Status: "done"}, "In Review", true},
		{Card{Status: "archived"}, "", false},
	}
	for _, tc := range cases {
		got, ok := linearStatus(tc.c)
		if got != tc.want || ok != tc.ok {
			t.Errorf("%+v = %q,%v want %q,%v", tc.c, got, ok, tc.want, tc.ok)
		}
		if got == "Done" {
			t.Errorf("never Done: %+v", tc.c)
		}
	}
}

func TestSessionActivity(t *testing.T) {
	cases := []struct {
		c    Card
		want Activity
		ok   bool
	}{
		{Card{Status: "running", Assignee: "coder", RunID: 4}, Activity{"thought", "Working: coder run 4."}, true},
		{Card{Status: "blocked", RunID: 4, Outcome: "gave_up", BlockReason: "stuck"}, Activity{"error", "Gave up: stuck"}, true},
		{Card{Status: "blocked", RunID: 4, BlockReason: "need input"}, Activity{"elicitation", "need input"}, true},
		{Card{Status: "triage", RunID: 4, BlockReason: "again"}, Activity{"elicitation", "again"}, true},
		{Card{Status: "blocked", BlockReason: "parked"}, Activity{}, false},
		{Card{Status: "triage"}, Activity{}, false},
		{Card{Status: "done", Metadata: map[string]any{"pr_url": "http://pr/1"}}, Activity{"response", "Done. PR: http://pr/1"}, true},
		{Card{Status: "done", Summary: "all good"}, Activity{"response", "Done: all good"}, true},
		{Card{Status: "todo"}, Activity{}, false},
	}
	for _, tc := range cases {
		got, ok := sessionActivity(tc.c)
		if got != tc.want || ok != tc.ok {
			t.Errorf("%+v = %+v,%v want %+v,%v", tc.c, got, ok, tc.want, tc.ok)
		}
	}
}

func TestEvidenceListsGaps(t *testing.T) {
	c := Card{Status: "done", Metadata: map[string]any{
		"bundle": "coder", "omnigent_session_id": "om-1", "pr_url": "http://pr/1", "post_run": "ok",
		"gaps": []any{"no tests"}, "doc_gaps": []any{"no readme", "no changelog"},
	}}
	body, ok := evidenceComment(c)
	if !ok {
		t.Fatal("no comment")
	}
	for _, want := range []string{"coder", "om-1", "http://pr/1", "ok", "- no tests", "- no readme", "- no changelog"} {
		if !strings.Contains(body, want) {
			t.Errorf("missing %q in %q", want, body)
		}
	}
	if _, ok := evidenceComment(Card{Status: "running", Metadata: c.Metadata}); ok {
		t.Error("running card gets evidence")
	}
	if _, ok := evidenceComment(Card{Status: "blocked", Metadata: c.Metadata}); ok {
		t.Error("parked card gets evidence")
	}
	if _, ok := evidenceComment(Card{Status: "blocked", RunID: 1, Metadata: c.Metadata}); !ok {
		t.Error("blocked-with-run lacks evidence")
	}
}

type projKanban struct {
	fakeKanban
	events    []CardEvent
	cards     map[string]Card
	cursor    int64
	setCursor []int64
}

func (k *projKanban) Events(context.Context, int64, int) ([]CardEvent, error) { return k.events, nil }
func (k *projKanban) Cursor(context.Context, string) (int64, error)           { return k.cursor, nil }
func (k *projKanban) Card(_ context.Context, id string) (Card, error)         { return k.cards[id], nil }
func (k *projKanban) SetCursor(_ context.Context, _ string, v int64) error {
	k.setCursor = append(k.setCursor, v)
	return nil
}

func linkedCard(id, status string) Card {
	return Card{ID: id, Status: status, Link: &CardLink{IssueID: "iss-" + id, Identifier: "HCL-" + id, SessionID: "sess-" + id}}
}

func projIssue(stateID string) IssueInfo {
	return IssueInfo{ID: "iss", TeamID: "team", StateID: stateID, States: []WorkflowState{
		{ID: "s-todo", Name: "Todo"}, {ID: "s-prog", Name: "In Progress"},
		{ID: "s-rev", Name: "In Review"}, {ID: "s-blk", Name: "Blocked"},
	}}
}

func newProj(k *projKanban, l *fakeLinear) (*projector, *fakeNotifier) {
	n := &fakeNotifier{}
	return newProjector(k, l, n), n
}

func TestTickAdvancesCursorAfterSuccess(t *testing.T) {
	c := linkedCard("a", "running")
	c.RunID, c.Assignee = 1, "coder"
	k := &projKanban{events: []CardEvent{{ID: 5, CardID: "a"}, {ID: 6, CardID: "a"}}, cards: map[string]Card{"a": c}}
	l := &fakeLinear{issue: projIssue("s-todo")}
	p, _ := newProj(k, l)
	if err := p.tick(context.Background()); err != nil {
		t.Fatal(err)
	}
	if len(k.setCursor) != 1 || k.setCursor[0] != 6 {
		t.Fatalf("cursor = %v", k.setCursor)
	}
	if len(l.states) != 1 || l.states[0][1] != "s-prog" || len(l.links) != 1 || len(l.activities) != 1 {
		t.Fatalf("writes = %+v", l)
	}
	if err := p.tick(context.Background()); err != nil {
		t.Fatal(err)
	}
	if len(l.states) != 1 || len(l.links) != 1 || len(l.activities) != 1 {
		t.Fatalf("replay repeated writes: %+v", l)
	}
}

func TestTickKeepsCursorOnFailure(t *testing.T) {
	c := linkedCard("a", "running")
	c.RunID = 1
	k := &projKanban{events: []CardEvent{{ID: 5, CardID: "a"}}, cards: map[string]Card{"a": c}}
	l := &fakeLinear{issue: projIssue("s-todo"), writeErr: errors.New("boom")}
	p, _ := newProj(k, l)
	if err := p.tick(context.Background()); err == nil {
		t.Fatal("want error")
	}
	if len(k.setCursor) != 0 {
		t.Fatalf("cursor advanced: %v", k.setCursor)
	}
	l.writeErr = nil
	if err := p.tick(context.Background()); err != nil {
		t.Fatal(err)
	}
	if len(k.setCursor) != 1 || len(l.activities) != 1 {
		t.Fatalf("retry did not complete: %v %+v", k.setCursor, l.activities)
	}
}

func TestTickSkipsUnlinkedCards(t *testing.T) {
	k := &projKanban{events: []CardEvent{{ID: 2, CardID: "x"}}, cards: map[string]Card{"x": {ID: "x", Status: "running", RunID: 1}}}
	l := &fakeLinear{issue: projIssue("s-todo")}
	p, _ := newProj(k, l)
	if err := p.tick(context.Background()); err != nil {
		t.Fatal(err)
	}
	if len(l.issueCalls)+len(l.states)+len(l.links)+len(l.activities) != 0 {
		t.Fatalf("touched linear: %+v", l)
	}
	if len(k.setCursor) != 1 {
		t.Fatalf("cursor = %v", k.setCursor)
	}
}

func TestTickNoStateWriteWhenEqual(t *testing.T) {
	k := &projKanban{events: []CardEvent{{ID: 2, CardID: "a"}}, cards: map[string]Card{"a": linkedCard("a", "todo")}}
	l := &fakeLinear{issue: projIssue("s-todo")}
	p, _ := newProj(k, l)
	if err := p.tick(context.Background()); err != nil {
		t.Fatal(err)
	}
	if len(l.states) != 0 {
		t.Fatalf("states = %v", l.states)
	}
}

func TestPostRunViolationNotifies(t *testing.T) {
	c := linkedCard("a", "blocked")
	c.RunID = 2
	c.BlockReason = "BLOCKED{permission}: post-run check: wrote outside worktree"
	k := &projKanban{events: []CardEvent{{ID: 2, CardID: "a"}}, cards: map[string]Card{"a": c}}
	l := &fakeLinear{issue: projIssue("s-todo")}
	p, n := newProj(k, l)
	for i := 0; i < 2; i++ {
		if err := p.tick(context.Background()); err != nil {
			t.Fatal(err)
		}
	}
	if len(n.msgs) != 1 || n.msgs[0] != "🔴 HCL-a: "+c.BlockReason {
		t.Fatalf("msgs = %q", n.msgs)
	}
	c.BlockReason = "needs input"
	k.cards["a"] = c
	c.Status = "blocked"
	p2, n2 := newProj(k, l)
	if err := p2.tick(context.Background()); err != nil {
		t.Fatal(err)
	}
	if len(n2.msgs) != 0 {
		t.Fatalf("msgs = %q", n2.msgs)
	}
}

func TestMissingStateNameSkipped(t *testing.T) {
	c := linkedCard("a", "running")
	c.RunID = 1
	k := &projKanban{events: []CardEvent{{ID: 2, CardID: "a"}}, cards: map[string]Card{"a": c}}
	is := projIssue("s-todo")
	is.States = is.States[:1]
	l := &fakeLinear{issue: is}
	p, _ := newProj(k, l)
	if err := p.tick(context.Background()); err != nil {
		t.Fatal(err)
	}
	if len(l.states) != 0 || len(l.activities) != 1 {
		t.Fatalf("states=%v activities=%v", l.states, l.activities)
	}
}
