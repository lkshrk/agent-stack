package main

import (
	"context"
	"encoding/json"
	"errors"
	"os"
	"strings"
	"testing"
)

const (
	testOwner = "86f579dd-3529-498b-b531-56ece905d2cc"
	testApp   = "d01486e2-a31b-43d2-b7b0-4b4dfb44b0eb"
)

type fakeLinear struct {
	archived                            []string
	archiveErr                          error
	issue                               IssueInfo
	issueErr                            error
	issueCalls                          []string
	states, activities, comments, links [][2]string
	writeErr                            error
	commentErr                          error
	issueErrFor                         map[string]error
}

func (f *fakeLinear) Issue(_ context.Context, id string) (IssueInfo, error) {
	f.issueCalls = append(f.issueCalls, id)
	if err := f.issueErrFor[id]; err != nil {
		return IssueInfo{}, err
	}
	return f.issue, f.issueErr
}

func (f *fakeLinear) ArchiveIssue(_ context.Context, id string) error {
	f.archived = append(f.archived, id)
	return f.archiveErr
}

func (f *fakeLinear) writes() int {
	return len(f.archived) + len(f.issueCalls) + len(f.states) + len(f.activities) + len(f.comments) + len(f.links)
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

func newTestBridge() (*bridge, *fakeLinear, *fakeNotifier) {
	n, l := &fakeNotifier{}, &fakeLinear{}
	return &bridge{linear: l, notify: n, appUserID: testApp}, l, n
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

func TestSessionAndUpdateEventsAreIgnored(t *testing.T) {
	b, l, n := newTestBridge()
	for _, name := range []string{
		"agent_session_created.json",
		"agent_session_prompted.json",
		"issue_update_undelegate.json",
		"issue_update_state.json",
	} {
		if err := b.handle(context.Background(), loadEvent(t, name, nil)); err != nil {
			t.Fatalf("%s: %v", name, err)
		}
	}
	if err := b.handle(context.Background(), event{Type: "Comment", Action: "create"}); err != nil {
		t.Fatal(err)
	}
	if l.writes()+len(n.msgs) != 0 {
		t.Fatalf("side effects: linear=%d msgs=%v", l.writes(), n.msgs)
	}
}

func TestWorkDrainsQueueThenReturns(t *testing.T) {
	b, l, _ := newTestBridge()
	q := make(chan event, 2)
	q <- loadEvent(t, "issue_create.json", topLevel(testApp, nil))
	q <- loadEvent(t, "issue_create.json", topLevel(testApp, nil))
	close(q)
	work(q, make(chan struct{}), b)
	if len(l.archived) != 2 {
		t.Fatalf("archived = %d", len(l.archived))
	}
}

func TestWorkStopsOnAbortWithoutClosingQueue(t *testing.T) {
	b, l, _ := newTestBridge()
	q := make(chan event, 2)
	q <- loadEvent(t, "issue_create.json", topLevel(testApp, nil))
	abort := make(chan struct{})
	close(abort)
	work(q, abort, b)
	if len(l.archived) != 0 {
		t.Fatalf("archived = %d", len(l.archived))
	}
	q <- loadEvent(t, "issue_create.json", topLevel(testApp, nil))
}

func (f *fakeLinear) SetState(_ context.Context, issueID, stateID string) error {
	f.states = append(f.states, [2]string{issueID, stateID})
	return f.writeErr
}

func (f *fakeLinear) Activity(_ context.Context, s string, a Activity) error {
	f.activities = append(f.activities, [2]string{s, a.Type + ": " + a.Body})
	return f.writeErr
}

func (f *fakeLinear) Comment(_ context.Context, issueID, body string) error {
	f.comments = append(f.comments, [2]string{issueID, body})
	if f.commentErr != nil {
		return f.commentErr
	}
	return f.writeErr
}

func (f *fakeLinear) LinkCard(_ context.Context, issueID, cardID string) error {
	f.links = append(f.links, [2]string{issueID, cardID})
	return f.writeErr
}
