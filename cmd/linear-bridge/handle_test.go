package main

import (
	"context"
	"encoding/json"
	"errors"
	"os"
	"strings"
	"testing"
	"time"
)

const (
	testOwner = "86f579dd-3529-498b-b531-56ece905d2cc"
	testApp   = "d01486e2-a31b-43d2-b7b0-4b4dfb44b0eb"
)

const (
	testIssue    = "04959fb8-108f-421e-9ad8-7eea5e4104d6"
	testUpdated  = "c3f4380b-637c-4fd3-9643-1de802f5c79a"
	stateTodo    = "11111111-0000-0000-0000-000000000001"
	stateDone    = "11111111-0000-0000-0000-000000000002"
	stateReview  = "4820222c-3d20-4499-a456-da5d369196d7"
	stateBacklog = "11111111-0000-0000-0000-000000000003"
)

type fakeLinear struct {
	archived           []string
	thoughts           [][2]string
	archiveErr         error
	issue              IssueInfo
	issueErr           error
	issueCalls         []string
	notifier           *fakeNotifier
	thoughtAfterNotify bool
}

func (f *fakeLinear) Issue(_ context.Context, id string) (IssueInfo, error) {
	f.issueCalls = append(f.issueCalls, id)
	return f.issue, f.issueErr
}

type fakeKanban struct {
	created   []RootCardRequest
	createErr error
	card      Card
	found     bool
	stops     [][2]string
	stopErr   error
	stopCtxs  []context.Context
}

func (f *fakeKanban) CreateRootCard(_ context.Context, r RootCardRequest) (Card, error) {
	f.created = append(f.created, r)
	return Card{ID: "t1"}, f.createErr
}

func (f *fakeKanban) CardByIssue(context.Context, string) (Card, bool, error) {
	return f.card, f.found, nil
}

func (f *fakeKanban) StopCard(ctx context.Context, id, reason string) error {
	f.stopCtxs = append(f.stopCtxs, ctx)
	f.stops = append(f.stops, [2]string{id, reason})
	return f.stopErr
}

func (f *fakeKanban) Card(context.Context, string) (Card, error)              { return f.card, nil }
func (f *fakeKanban) Events(context.Context, int64, int) ([]CardEvent, error) { return nil, nil }
func (f *fakeKanban) Cursor(context.Context, string) (int64, error)           { return 0, nil }
func (f *fakeKanban) SetCursor(context.Context, string, int64) error          { return nil }

func (f *fakeLinear) ArchiveIssue(_ context.Context, id string) error {
	f.archived = append(f.archived, id)
	return f.archiveErr
}

func (f *fakeLinear) Thought(_ context.Context, s, b string) error {
	if f.notifier != nil && len(f.notifier.msgs) > 0 {
		f.thoughtAfterNotify = true
	}
	f.thoughts = append(f.thoughts, [2]string{s, b})
	return nil
}

type fakeNotifier struct {
	msgs    []string
	ctxErrs []error
}

func (f *fakeNotifier) Notify(ctx context.Context, t string) error {
	f.msgs = append(f.msgs, t)
	f.ctxErrs = append(f.ctxErrs, ctx.Err())
	return nil
}

func loadEvent(t *testing.T, name string, mutate func(map[string]any)) event {
	t.Helper()
	raw, err := os.ReadFile("testdata/" + name)
	if err != nil {
		t.Fatal(err)
	}
	if mutate != nil {
		var m map[string]any
		if err := json.Unmarshal(raw, &m); err != nil {
			t.Fatal(err)
		}
		mutate(m)
		if raw, err = json.Marshal(m); err != nil {
			t.Fatal(err)
		}
	}
	var ev event
	if err := json.Unmarshal(raw, &ev); err != nil {
		t.Fatal(err)
	}
	return ev
}

func delegatedIssue() IssueInfo {
	return IssueInfo{
		ID: testIssue, Identifier: "ROU-697", Title: "placeholder title", Description: "the body",
		DelegateID: testApp, TeamID: "b6a02ec0-3a75-44a7-a36e-01e5231d10f5",
		States: []WorkflowState{
			{ID: stateBacklog, Name: "Backlog", Type: "backlog"},
			{ID: stateTodo, Name: "Todo", Type: "unstarted"},
			{ID: "7511db60-aeb4-48ee-8a5e-f6a1d8660de4", Name: "In Progress", Type: "started"},
			{ID: stateReview, Name: "In Review", Type: "started"},
			{ID: stateDone, Name: "Done", Type: "completed"},
		},
	}
}

func newTestBridge() (*bridge, *fakeLinear, *fakeNotifier) {
	b, l, n, _ := newKanbanBridge()
	return b, l, n
}

func newKanbanBridge() (*bridge, *fakeLinear, *fakeNotifier, *fakeKanban) {
	n, k := &fakeNotifier{}, &fakeKanban{}
	l := &fakeLinear{issue: delegatedIssue(), notifier: n}
	return &bridge{linear: l, notify: n, kanban: k, ownerID: testOwner, appUserID: testApp}, l, n, k
}

func topLevel(creator string, parent any) func(map[string]any) {
	return func(m map[string]any) {
		d := m["data"].(map[string]any)
		d["creatorId"] = creator
		d["parentId"] = parent
	}
}

func TestAuditArchivesTopLevelAppIssue(t *testing.T) {
	b, l, n := newTestBridge()
	ev := loadEvent(t, "issue_create.json", topLevel(testApp, nil))
	if err := b.handle(context.Background(), ev); err != nil {
		t.Fatal(err)
	}
	if len(l.archived) != 1 || l.archived[0] != "c3f4380b-637c-4fd3-9643-1de802f5c79a" {
		t.Fatalf("archived = %v", l.archived)
	}
	if len(n.msgs) != 1 || !strings.Contains(n.msgs[0], "ROU-698") {
		t.Fatalf("msgs = %v", n.msgs)
	}
}

func TestAuditTreatsEmptyParentAsTopLevel(t *testing.T) {
	b, l, _ := newTestBridge()
	ev := loadEvent(t, "issue_create.json", topLevel(testApp, ""))
	if err := b.handle(context.Background(), ev); err != nil {
		t.Fatal(err)
	}
	if len(l.archived) != 1 {
		t.Fatalf("archived = %v", l.archived)
	}
}

func TestAuditIgnoresOwnerIssue(t *testing.T) {
	b, l, n := newTestBridge()
	ev := loadEvent(t, "issue_create.json", topLevel(testOwner, nil))
	if err := b.handle(context.Background(), ev); err != nil {
		t.Fatal(err)
	}
	if len(l.archived) != 0 || len(n.msgs) != 0 {
		t.Fatalf("archived=%v msgs=%v", l.archived, n.msgs)
	}
}

func TestAuditIgnoresAppSubIssue(t *testing.T) {
	b, l, n := newTestBridge()
	ev := loadEvent(t, "issue_create.json", nil)
	if err := b.handle(context.Background(), ev); err != nil {
		t.Fatal(err)
	}
	if len(l.archived) != 0 || len(n.msgs) != 0 {
		t.Fatalf("archived=%v msgs=%v", l.archived, n.msgs)
	}
}

func TestAuditNotifiesWhenArchiveFails(t *testing.T) {
	b, l, n := newTestBridge()
	l.archiveErr = errors.New("boom")
	ev := loadEvent(t, "issue_create.json", topLevel(testApp, nil))
	if err := b.handle(context.Background(), ev); err == nil {
		t.Fatal("want error")
	}
	if len(n.msgs) != 1 || !strings.Contains(n.msgs[0], "boom") || !strings.Contains(n.msgs[0], "ROU-698") {
		t.Fatalf("msgs = %v", n.msgs)
	}
}

func TestSessionCreatedByOwnerAcks(t *testing.T) {
	b, l, _ := newTestBridge()
	ev := loadEvent(t, "agent_session_created.json", nil)
	if err := b.handle(context.Background(), ev); err != nil {
		t.Fatal(err)
	}
	want := [2]string{"15678ced-2fe6-415a-90d9-6cbc9be21f9b", "Received. Checking readiness."}
	if len(l.thoughts) != 1 || l.thoughts[0] != want {
		t.Fatalf("thoughts = %v", l.thoughts)
	}
}

func TestSessionCreatedByOtherUserRefuses(t *testing.T) {
	b, l, _ := newTestBridge()
	ev := loadEvent(t, "agent_session_created.json", func(m map[string]any) {
		m["agentSession"].(map[string]any)["creatorId"] = "someone-else"
	})
	if err := b.handle(context.Background(), ev); err != nil {
		t.Fatal(err)
	}
	if len(l.thoughts) != 1 || l.thoughts[0][1] != "Only the workspace owner can start work for this agent." {
		t.Fatalf("thoughts = %v", l.thoughts)
	}
}

func TestPromptedAndUnknownEventsDoNothing(t *testing.T) {
	b, l, n := newTestBridge()
	for _, ev := range []event{
		loadEvent(t, "agent_session_prompted.json", nil),
		{Type: "Comment", Action: "create"},
	} {
		if err := b.handle(context.Background(), ev); err != nil {
			t.Fatal(err)
		}
	}
	if len(l.archived)+len(l.thoughts)+len(n.msgs) != 0 {
		t.Fatal("unexpected side effects")
	}
}

func TestWorkDrainsQueueThenReturns(t *testing.T) {
	b, l, _ := newTestBridge()
	q := make(chan event, 2)
	q <- loadEvent(t, "agent_session_created.json", nil)
	q <- loadEvent(t, "agent_session_created.json", nil)
	close(q)
	work(q, make(chan struct{}), b)
	if len(l.thoughts) != 2 {
		t.Fatalf("thoughts = %d", len(l.thoughts))
	}
}

func TestWorkStopsOnAbortWithoutClosingQueue(t *testing.T) {
	b, l, _ := newTestBridge()
	q := make(chan event, 2)
	q <- loadEvent(t, "agent_session_created.json", nil)
	abort := make(chan struct{})
	close(abort)
	work(q, abort, b)
	if len(l.thoughts) != 0 {
		t.Fatalf("thoughts = %d", len(l.thoughts))
	}
	q <- loadEvent(t, "agent_session_created.json", nil)
}

func TestDelegationCreatesRootCard(t *testing.T) {
	b, l, _, k := newKanbanBridge()
	ev := loadEvent(t, "agent_session_created.json", nil)
	if err := b.handle(context.Background(), ev); err != nil {
		t.Fatal(err)
	}
	want := RootCardRequest{
		IssueID: testIssue, Identifier: "ROU-697", Title: "placeholder title",
		Description: "the body", SessionID: "15678ced-2fe6-415a-90d9-6cbc9be21f9b",
	}
	if len(k.created) != 1 || k.created[0] != want {
		t.Fatalf("created = %+v", k.created)
	}
	if len(l.issueCalls) != 1 || l.issueCalls[0] != testIssue {
		t.Fatalf("issue calls = %v", l.issueCalls)
	}
	if len(l.thoughts) != 1 || l.thoughts[0][1] != "Received. Checking readiness." {
		t.Fatalf("thoughts = %v", l.thoughts)
	}
}

func TestMentionSessionDoesNotCreateCard(t *testing.T) {
	b, l, _, k := newKanbanBridge()
	l.issue.DelegateID = ""
	ev := loadEvent(t, "agent_session_created.json", nil)
	if err := b.handle(context.Background(), ev); err != nil {
		t.Fatal(err)
	}
	if len(k.created) != 0 {
		t.Fatalf("created = %+v", k.created)
	}
	if len(l.thoughts) != 1 || l.thoughts[0][1] != "Received. Checking readiness." {
		t.Fatalf("thoughts = %v", l.thoughts)
	}
}

func TestDelegationOfSubIssueRefused(t *testing.T) {
	b, l, _, k := newKanbanBridge()
	l.issue.ParentID = "parent-1"
	ev := loadEvent(t, "agent_session_created.json", nil)
	if err := b.handle(context.Background(), ev); err != nil {
		t.Fatal(err)
	}
	if len(k.created) != 0 {
		t.Fatalf("created = %+v", k.created)
	}
	if len(l.thoughts) != 1 || l.thoughts[0][1] != "Sub-issues start from their root issue." {
		t.Fatalf("thoughts = %v", l.thoughts)
	}
}

func TestCardFailureStillThoughtsAndNotifies(t *testing.T) {
	b, l, n, k := newKanbanBridge()
	k.createErr = errors.New("gate down")
	ev := loadEvent(t, "agent_session_created.json", nil)
	_ = b.handle(context.Background(), ev)
	if len(l.thoughts) != 1 || l.thoughts[0][1] != "Could not queue this issue; the owner was notified." {
		t.Fatalf("thoughts = %v", l.thoughts)
	}
	if len(n.msgs) != 1 || !strings.Contains(n.msgs[0], "ROU-697") {
		t.Fatalf("msgs = %v", n.msgs)
	}
}

func TestSessionByOtherUserDoesNotCreateCard(t *testing.T) {
	b, l, _, k := newKanbanBridge()
	ev := loadEvent(t, "agent_session_created.json", func(m map[string]any) {
		m["agentSession"].(map[string]any)["creatorId"] = "someone-else"
	})
	if err := b.handle(context.Background(), ev); err != nil {
		t.Fatal(err)
	}
	if len(k.created) != 0 || len(l.issueCalls) != 0 {
		t.Fatalf("created=%v issueCalls=%v", k.created, l.issueCalls)
	}
}

func TestUndelegateStopsCard(t *testing.T) {
	b, _, _, k := newKanbanBridge()
	k.card, k.found = Card{ID: "t1", Status: "done"}, true
	ev := loadEvent(t, "issue_update_undelegate.json", nil)
	if err := b.handle(context.Background(), ev); err != nil {
		t.Fatal(err)
	}
	if len(k.stops) != 1 || k.stops[0] != [2]string{"t1", "owner: un-delegated"} {
		t.Fatalf("stops = %v", k.stops)
	}
}

func TestUndelegateWithoutCardIsIgnored(t *testing.T) {
	b, _, _, k := newKanbanBridge()
	ev := loadEvent(t, "issue_update_undelegate.json", nil)
	if err := b.handle(context.Background(), ev); err != nil {
		t.Fatal(err)
	}
	if len(k.stops) != 0 {
		t.Fatalf("stops = %v", k.stops)
	}
}

func TestUndelegateWithAbsentDelegateKeyStops(t *testing.T) {
	b, _, _, k := newKanbanBridge()
	k.card, k.found = Card{ID: "t1", Status: "running"}, true
	ev := loadEvent(t, "issue_update_undelegate.json", func(m map[string]any) {
		delete(m["data"].(map[string]any), "delegateId")
	})
	if err := b.handle(context.Background(), ev); err != nil {
		t.Fatal(err)
	}
	if len(k.stops) != 1 {
		t.Fatalf("stops = %v", k.stops)
	}
}

func moveTo(stateID string) func(map[string]any) {
	return func(m map[string]any) {
		d := m["data"].(map[string]any)
		d["stateId"] = stateID
		delete(d, "state")
	}
}

func TestOwnerMovesRunningToTodoStops(t *testing.T) {
	b, l, _, k := newKanbanBridge()
	k.card, k.found = Card{ID: "t1", Status: "running"}, true
	ev := loadEvent(t, "issue_update_state.json", moveTo(stateTodo))
	if err := b.handle(context.Background(), ev); err != nil {
		t.Fatal(err)
	}
	if len(k.stops) != 1 || k.stops[0] != [2]string{"t1", "owner: moved to Todo"} {
		t.Fatalf("stops = %v", k.stops)
	}
	if len(l.issueCalls) != 1 || l.issueCalls[0] != testUpdated {
		t.Fatalf("issue calls = %v", l.issueCalls)
	}
}

func TestOwnerMovesReadyAndBlockedCardsStop(t *testing.T) {
	for _, status := range []string{"ready", "blocked"} {
		b, _, _, k := newKanbanBridge()
		k.card, k.found = Card{ID: "t1", Status: status}, true
		ev := loadEvent(t, "issue_update_state.json", moveTo(stateBacklog))
		if err := b.handle(context.Background(), ev); err != nil {
			t.Fatal(err)
		}
		if len(k.stops) != 1 {
			t.Fatalf("%s: stops = %v", status, k.stops)
		}
	}
}

func TestOwnerMovesDoneCardIgnored(t *testing.T) {
	b, _, _, k := newKanbanBridge()
	k.card, k.found = Card{ID: "t1", Status: "done"}, true
	ev := loadEvent(t, "issue_update_state.json", moveTo(stateTodo))
	if err := b.handle(context.Background(), ev); err != nil {
		t.Fatal(err)
	}
	if len(k.stops) != 0 {
		t.Fatalf("stops = %v", k.stops)
	}
}

func TestOwnerMovesToStartedTypeIgnored(t *testing.T) {
	b, _, _, k := newKanbanBridge()
	k.card, k.found = Card{ID: "t1", Status: "running"}, true
	ev := loadEvent(t, "issue_update_state.json", nil)
	if err := b.handle(context.Background(), ev); err != nil {
		t.Fatal(err)
	}
	if len(k.stops) != 0 {
		t.Fatalf("stops = %v", k.stops)
	}
}

func TestUpdateWithoutStateChangeIgnored(t *testing.T) {
	b, l, _, k := newKanbanBridge()
	k.card, k.found = Card{ID: "t1", Status: "running"}, true
	ev := loadEvent(t, "issue_update_state.json", func(m map[string]any) {
		m["data"].(map[string]any)["stateId"] = stateTodo
		m["updatedFrom"] = map[string]any{"updatedAt": "2026-10-30T09:15:02.114Z"}
	})
	if err := b.handle(context.Background(), ev); err != nil {
		t.Fatal(err)
	}
	if len(k.stops) != 0 || len(l.issueCalls) != 0 {
		t.Fatalf("stops=%v issueCalls=%v", k.stops, l.issueCalls)
	}
}

func TestUndelegateByOtherUserIgnored(t *testing.T) {
	b, _, _, k := newKanbanBridge()
	k.card, k.found = Card{ID: "t1", Status: "running"}, true
	ev := loadEvent(t, "issue_update_undelegate.json", func(m map[string]any) {
		m["actor"].(map[string]any)["id"] = "someone-else"
	})
	if err := b.handle(context.Background(), ev); err != nil {
		t.Fatal(err)
	}
	if len(k.stops) != 0 {
		t.Fatalf("stops = %v", k.stops)
	}
}

func TestIssueUpdateByAppIgnored(t *testing.T) {
	b, _, _, k := newKanbanBridge()
	k.card, k.found = Card{ID: "t1", Status: "running"}, true
	for _, name := range []string{"issue_update_undelegate.json", "issue_update_state.json"} {
		ev := loadEvent(t, name, func(m map[string]any) {
			m["actor"].(map[string]any)["id"] = testApp
			if name == "issue_update_state.json" {
				moveTo(stateTodo)(m)
			}
		})
		if err := b.handle(context.Background(), ev); err != nil {
			t.Fatal(err)
		}
	}
	if len(k.stops) != 0 {
		t.Fatalf("stops = %v", k.stops)
	}
}

func TestQueueFailureNotifiesBeforeThoughtOnLiveContext(t *testing.T) {
	b, l, n, k := newKanbanBridge()
	k.createErr = errors.New("gate down")
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	_ = b.handle(ctx, loadEvent(t, "agent_session_created.json", nil))
	if len(n.msgs) != 1 || n.ctxErrs[0] != nil {
		t.Fatalf("msgs=%v ctxErrs=%v", n.msgs, n.ctxErrs)
	}
	if !l.thoughtAfterNotify {
		t.Fatal("thought was sent before the notification")
	}
}

func TestStopUsesOwnLongBudget(t *testing.T) {
	b, _, _, k := newKanbanBridge()
	k.card, k.found = Card{ID: "t1", Status: "running"}, true
	ctx, cancel := context.WithTimeout(context.Background(), time.Second)
	defer cancel()
	if err := b.handle(ctx, loadEvent(t, "issue_update_undelegate.json", nil)); err != nil {
		t.Fatal(err)
	}
	dl, ok := k.stopCtxs[0].Deadline()
	if !ok || time.Until(dl) < 20*time.Second {
		t.Fatalf("stop deadline = %v ok=%v", time.Until(dl), ok)
	}
}

func TestStopFailureNotifies(t *testing.T) {
	for _, name := range []string{"issue_update_undelegate.json", "issue_update_state.json"} {
		b, _, n, k := newKanbanBridge()
		k.card, k.found = Card{ID: "t1", Status: "running"}, true
		k.stopErr = errors.New("2 interrupt/archive errors")
		var mut func(map[string]any)
		if name == "issue_update_state.json" {
			mut = moveTo(stateTodo)
		}
		_ = b.handle(context.Background(), loadEvent(t, name, mut))
		if len(n.msgs) != 1 || !strings.HasPrefix(n.msgs[0], "🔴 ROU-698: stop failed: ") ||
			!strings.Contains(n.msgs[0], "interrupt/archive") {
			t.Fatalf("%s msgs = %v", name, n.msgs)
		}
	}
}
