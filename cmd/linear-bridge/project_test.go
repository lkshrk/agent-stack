package main

import (
	"context"
	"errors"
	"fmt"
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
	cursorErr error
	eventsErr error
}

func (k *projKanban) Events(context.Context, int64, int) ([]CardEvent, error) {
	return k.events, k.eventsErr
}
func (k *projKanban) Cursor(context.Context, string) (int64, error)   { return k.cursor, k.cursorErr }
func (k *projKanban) Card(_ context.Context, id string) (Card, error) { return k.cards[id], nil }
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

func TestPoisonCardSkippedAfterThreeFailures(t *testing.T) {
	good := linkedCard("b", "todo")
	k := &projKanban{
		events: []CardEvent{{ID: 1, CardID: "a"}, {ID: 2, CardID: "b"}},
		cards:  map[string]Card{"a": linkedCard("a", "todo"), "b": good},
	}
	l := &fakeLinear{issue: projIssue("s-prog"), issueErrFor: map[string]error{"iss-a": permanent(errors.New("issue not found"))}}
	p, n := newProj(k, l)
	for i := 0; i < 2; i++ {
		if err := p.tick(context.Background()); err == nil {
			t.Fatalf("tick %d: want error", i)
		}
		if len(k.setCursor) != 0 || len(n.msgs) != 0 {
			t.Fatalf("tick %d: cursor=%v msgs=%q", i, k.setCursor, n.msgs)
		}
	}
	if err := p.tick(context.Background()); err != nil {
		t.Fatal(err)
	}
	if len(k.setCursor) != 1 || k.setCursor[0] != 2 {
		t.Fatalf("cursor = %v", k.setCursor)
	}
	if len(n.msgs) != 1 || !strings.HasPrefix(n.msgs[0], "⚠️ projection skipped for card a (HCL-a): ") {
		t.Fatalf("msgs = %q", n.msgs)
	}
	if len(l.states) != 1 || l.states[0][0] != "iss" {
		t.Fatalf("good card not projected: %v", l.states)
	}
}

func TestFailureCounterResetsOnSuccess(t *testing.T) {
	k := &projKanban{events: []CardEvent{{ID: 1, CardID: "a"}}, cards: map[string]Card{"a": linkedCard("a", "todo")}}
	l := &fakeLinear{issue: projIssue("s-prog"), issueErrFor: map[string]error{"iss-a": errors.New("x")}}
	p, n := newProj(k, l)
	for i := 0; i < 2; i++ {
		_ = p.tick(context.Background())
	}
	l.issueErrFor = nil
	if err := p.tick(context.Background()); err != nil {
		t.Fatal(err)
	}
	l.issueErrFor = map[string]error{"iss-a": errors.New("x")}
	k.cards["a"] = linkedCard("a", "ready")
	for i := 0; i < 2; i++ {
		if err := p.tick(context.Background()); err == nil {
			t.Fatal("want error")
		}
	}
	if len(n.msgs) != 0 {
		t.Fatalf("msgs = %q", n.msgs)
	}
}

func TestRetrySkipsCompletedSteps(t *testing.T) {
	c := linkedCard("a", "done")
	c.Summary = "ok"
	c.RunID = 1
	c.Metadata = map[string]any{"bundle": "coder"}
	k := &projKanban{events: []CardEvent{{ID: 1, CardID: "a"}}, cards: map[string]Card{"a": c}}
	l := &fakeLinear{issue: projIssue("s-todo"), commentErr: errors.New("boom")}
	p, _ := newProj(k, l)
	if err := p.tick(context.Background()); err == nil {
		t.Fatal("want error")
	}
	l.commentErr = nil
	if err := p.tick(context.Background()); err != nil {
		t.Fatal(err)
	}
	if len(l.activities) != 1 || len(l.states) != 1 || len(l.links) != 1 || len(l.comments) != 2 {
		t.Fatalf("states=%d links=%d activities=%d comments=%d", len(l.states), len(l.links), len(l.activities), len(l.comments))
	}
}

func TestNewBlockReasonReprojectsAndAlerts(t *testing.T) {
	c := linkedCard("a", "blocked")
	c.RunID = 2
	c.BlockReason = "needs input"
	k := &projKanban{events: []CardEvent{{ID: 1, CardID: "a"}}, cards: map[string]Card{"a": c}}
	l := &fakeLinear{issue: projIssue("s-blk")}
	p, n := newProj(k, l)
	if err := p.tick(context.Background()); err != nil {
		t.Fatal(err)
	}
	c.BlockReason = postRunPrefix + "bad"
	k.cards["a"] = c
	if err := p.tick(context.Background()); err != nil {
		t.Fatal(err)
	}
	if len(l.activities) != 2 || len(n.msgs) != 1 {
		t.Fatalf("activities=%d msgs=%q", len(l.activities), n.msgs)
	}
}

func TestOutageDoesNotSkipCards(t *testing.T) {
	k := &projKanban{
		events: []CardEvent{{ID: 1, CardID: "a"}, {ID: 2, CardID: "b"}},
		cards:  map[string]Card{"a": linkedCard("a", "todo"), "b": linkedCard("b", "todo")},
	}
	l := &fakeLinear{issue: projIssue("s-prog"), issueErr: errors.New("linear down")}
	p, n := newProj(k, l)
	for i := 0; i < 5; i++ {
		if err := p.tick(context.Background()); err == nil {
			t.Fatalf("tick %d: want error", i)
		}
	}
	if len(k.setCursor) != 0 || len(n.msgs) != 0 || len(p.fails) != 0 {
		t.Fatalf("cursor=%v msgs=%q fails=%v", k.setCursor, n.msgs, p.fails)
	}
	l.issueErr = nil
	if err := p.tick(context.Background()); err != nil {
		t.Fatal(err)
	}
	if len(k.setCursor) != 1 || (len(l.states) != 0 && len(l.links) != 2) {
		t.Fatalf("cursor=%v links=%v", k.setCursor, l.links)
	}
}

func TestSkippedCardNotRealertedWhileStalled(t *testing.T) {
	k := &projKanban{
		events: []CardEvent{{ID: 1, CardID: "a"}, {ID: 2, CardID: "b"}, {ID: 3, CardID: "c"}},
		cards:  map[string]Card{"a": linkedCard("a", "todo"), "b": linkedCard("b", "todo"), "c": linkedCard("c", "todo")},
	}
	l := &fakeLinear{issue: projIssue("s-prog"), issueErrFor: map[string]error{"iss-a": permanent(errors.New("gone"))}}
	p, n := newProj(k, l)
	for i := 0; i < 2; i++ {
		_ = p.tick(context.Background())
	}
	l.issueErrFor["iss-b"] = permanent(errors.New("flaky"))
	k.cards["b"] = linkedCard("b", "ready")
	if err := p.tick(context.Background()); err == nil {
		t.Fatal("want error: b is failing")
	}
	if len(n.msgs) != 1 {
		t.Fatalf("msgs = %q", n.msgs)
	}
	for i := 0; i < 2; i++ {
		_ = p.tick(context.Background())
	}
	if len(k.setCursor) != 1 || len(n.msgs) != 2 || !strings.Contains(n.msgs[0], "card a") || !strings.Contains(n.msgs[1], "card b") {
		t.Fatalf("cursor=%v msgs=%q", k.setCursor, n.msgs)
	}
}

func stallKanban(ids ...string) *projKanban {
	k := &projKanban{cards: map[string]Card{}}
	for i, id := range ids {
		k.events = append(k.events, CardEvent{ID: int64(i + 1), CardID: id})
		k.cards[id] = linkedCard(id, "todo")
	}
	return k
}

func failingLinear(ids ...string) *fakeLinear {
	l := &fakeLinear{issue: projIssue("s-prog"), issueErrFor: map[string]error{}}
	for _, id := range ids {
		l.issueErrFor["iss-"+id] = permanent(errors.New("linear issue not found"))
	}
	return l
}

func TestLonePoisonCardAlertsThenIsSkipped(t *testing.T) {
	k := stallKanban("a")
	p, n := newProj(k, failingLinear("a"))
	for i := 1; i <= 9; i++ {
		if err := p.tick(context.Background()); err == nil {
			t.Fatalf("tick %d: want error", i)
		}
	}
	if len(n.msgs) != 0 || len(k.setCursor) != 0 {
		t.Fatalf("before threshold: msgs=%q cursor=%v", n.msgs, k.setCursor)
	}
	_ = p.tick(context.Background())
	if len(n.msgs) != 1 || !strings.HasPrefix(n.msgs[0], "🔴 linear-bridge projection stalled: ") ||
		!strings.Contains(n.msgs[0], "issue not found") {
		t.Fatalf("msgs = %q", n.msgs)
	}
	for i := 0; i < 2; i++ {
		_ = p.tick(context.Background())
	}
	if len(k.setCursor) != 1 || k.setCursor[0] != 1 {
		t.Fatalf("lone card not skipped: cursor=%v", k.setCursor)
	}
	if len(n.msgs) != 2 || !strings.HasPrefix(n.msgs[1], "⚠️ projection skipped for card a") {
		t.Fatalf("msgs = %q", n.msgs)
	}
}

func TestLoneTransientCardNeverSkipped(t *testing.T) {
	k := stallKanban("a")
	l := failingLinear()
	l.issueErrFor["iss-a"] = errors.New("linear down")
	p, n := newProj(k, l)
	for i := 0; i < 20; i++ {
		if err := p.tick(context.Background()); err == nil {
			t.Fatalf("tick %d: want error", i)
		}
	}
	if len(k.setCursor) != 0 || len(p.fails) != 0 || len(p.skipped) != 0 {
		t.Fatalf("cursor=%v fails=%v skipped=%v", k.setCursor, p.fails, p.skipped)
	}
	if len(n.msgs) != 1 || !strings.HasPrefix(n.msgs[0], "🔴 linear-bridge projection stalled: ") {
		t.Fatalf("msgs = %q", n.msgs)
	}
}

func TestTwoCardsTransientFailureNeverSkipped(t *testing.T) {
	k := stallKanban("a", "b")
	l := failingLinear()
	l.issueErrFor["iss-b"] = errors.New("timeout")
	p, n := newProj(k, l)
	for i := 0; i < 8; i++ {
		_ = p.tick(context.Background())
	}
	if len(k.setCursor) != 0 || len(p.fails) != 0 || len(p.skipped) != 0 || len(n.msgs) != 0 {
		t.Fatalf("cursor=%v fails=%v skipped=%v msgs=%q", k.setCursor, p.fails, p.skipped, n.msgs)
	}
}

func TestClassifyErrors(t *testing.T) {
	for _, tc := range []struct {
		err  gqlError
		perm bool
	}{
		{gqlError{Message: "x"}, false},
		{gqlError{Message: "Entity not found", Extensions: struct {
			Type string `json:"type"`
			Code string `json:"code"`
		}{Type: "invalid input"}}, true},
		{gqlError{Message: "slow", Extensions: struct {
			Type string `json:"type"`
			Code string `json:"code"`
		}{Type: "ratelimited"}}, false},
		{gqlError{Message: "no", Extensions: struct {
			Type string `json:"type"`
			Code string `json:"code"`
		}{Type: "authentication error"}}, false},
	} {
		if got := isPermanent(tc.err.err()); got != tc.perm {
			t.Errorf("%+v: permanent=%v", tc.err, got)
		}
	}
	for status, want := range map[int]bool{400: true, 404: true, 401: false, 403: false, 408: false, 429: false, 500: false, 503: false} {
		if permanentStatus(status) != want {
			t.Errorf("status %d", status)
		}
	}
	if !isPermanent(fmt.Errorf("w: %w", errUnknownCard)) || isPermanent(errors.New("x")) {
		t.Error("sentinel")
	}
}

func TestStallWithTwoFailingCardsAlertsOnceWithoutSkip(t *testing.T) {
	k := stallKanban("a", "b")
	p, n := newProj(k, failingLinear("a", "b"))
	for i := 0; i < 15; i++ {
		if err := p.tick(context.Background()); err == nil {
			t.Fatal("want error")
		}
	}
	if len(n.msgs) != 1 || !strings.HasPrefix(n.msgs[0], "🔴 linear-bridge projection stalled: ") {
		t.Fatalf("msgs = %q", n.msgs)
	}
	if len(k.setCursor) != 0 || len(p.fails) != 0 || len(p.skipped) != 0 {
		t.Fatalf("cursor=%v fails=%v skipped=%v", k.setCursor, p.fails, p.skipped)
	}
}

func TestStallCounterResetsWhenTickSucceeds(t *testing.T) {
	k := stallKanban("a", "b")
	l := failingLinear("a", "b")
	p, n := newProj(k, l)
	for i := 0; i < 9; i++ {
		_ = p.tick(context.Background())
	}
	l.issueErrFor = nil
	if err := p.tick(context.Background()); err != nil {
		t.Fatal(err)
	}
	l.issueErrFor = map[string]error{"iss-a": errors.New("x"), "iss-b": errors.New("x")}
	k.cards["a"], k.cards["b"] = linkedCard("a", "ready"), linkedCard("b", "ready")
	for i := 0; i < 9; i++ {
		_ = p.tick(context.Background())
	}
	if len(n.msgs) != 0 {
		t.Fatalf("msgs = %q", n.msgs)
	}
}

func TestStallAlertsAgainAfterRecovery(t *testing.T) {
	k := stallKanban("a", "b")
	l := failingLinear("a", "b")
	p, n := newProj(k, l)
	for i := 0; i < 10; i++ {
		_ = p.tick(context.Background())
	}
	l.issueErrFor = nil
	if err := p.tick(context.Background()); err != nil {
		t.Fatal(err)
	}
	l.issueErrFor = map[string]error{"iss-a": errors.New("x"), "iss-b": errors.New("x")}
	k.cards["a"], k.cards["b"] = linkedCard("a", "ready"), linkedCard("b", "ready")
	for i := 0; i < 10; i++ {
		_ = p.tick(context.Background())
	}
	if len(n.msgs) != 2 {
		t.Fatalf("msgs = %q", n.msgs)
	}
}

func TestEvidenceRendersObjectGaps(t *testing.T) {
	c := Card{Status: "done", Metadata: map[string]any{
		"gaps": []any{
			map[string]any{"id": "gap-1", "question": "which auth?", "class": "implementation", "stub": "TODO(gap-1): auth", "location": "a/b.go:12"},
			map[string]any{"id": "gap-2", "question": "what quota?", "class": "decision", "stub": nil, "location": nil},
		},
		"doc_gaps": []any{map[string]any{"file": "docs/x.md", "reason": "no owner"}, 42.0},
	}}
	body, _ := evidenceComment(c)
	for _, want := range []string{"- gap-1: which auth? (stub: a/b.go:12)", "- gap-2: what quota?\n", "- docs/x.md: no owner", "- 42"} {
		if !strings.Contains(body+"\n", want) {
			t.Errorf("missing %q in %q", want, body)
		}
	}
	if strings.Contains(body, "map[") {
		t.Errorf("Go map leaked: %q", body)
	}
}

func TestProjectorLeavesOwnerSetFinalStatesAlone(t *testing.T) {
	for _, typ := range []string{"completed", "canceled"} {
		k := &projKanban{events: []CardEvent{{ID: 1, CardID: "a"}}, cards: map[string]Card{"a": linkedCard("a", "running")}}
		iss := projIssue("s-final")
		iss.States = append(iss.States, WorkflowState{ID: "s-final", Name: "Final", Type: typ})
		l := &fakeLinear{issue: iss}
		p, _ := newProj(k, l)
		if err := p.tick(context.Background()); err != nil {
			t.Fatal(err)
		}
		if len(l.states) != 0 {
			t.Errorf("%s: projector moved the issue: %v", typ, l.states)
		}
		if len(l.links) != 1 {
			t.Errorf("%s: other projection skipped: %v", typ, l.links)
		}
	}
}

func TestGateOutageAlertsOnceAndResets(t *testing.T) {
	for _, which := range []string{"cursor", "events"} {
		k := stallKanban("a")
		p, n := newProj(k, failingLinear())
		if which == "cursor" {
			k.cursorErr = errors.New("gate down")
		} else {
			k.eventsErr = errors.New("gate down")
		}
		for i := 0; i < 9; i++ {
			_ = p.tick(context.Background())
		}
		if len(n.msgs) != 0 {
			t.Fatalf("%s: early alert %q", which, n.msgs)
		}
		for i := 0; i < 5; i++ {
			_ = p.tick(context.Background())
		}
		if len(n.msgs) != 1 || !strings.HasPrefix(n.msgs[0], "🔴 linear-bridge projection stalled: ") ||
			!strings.Contains(n.msgs[0], "gate down") {
			t.Fatalf("%s: msgs = %q", which, n.msgs)
		}
		k.cursorErr, k.eventsErr = nil, nil
		if err := p.tick(context.Background()); err != nil {
			t.Fatal(err)
		}
		if p.stallTicks != 0 || p.stallAlerted {
			t.Fatalf("%s: stall not reset", which)
		}
	}
}
