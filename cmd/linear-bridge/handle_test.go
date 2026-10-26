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
	archived   []string
	thoughts   [][2]string
	archiveErr error
}

func (f *fakeLinear) ArchiveIssue(_ context.Context, id string) error {
	f.archived = append(f.archived, id)
	return f.archiveErr
}

func (f *fakeLinear) Thought(_ context.Context, s, b string) error {
	f.thoughts = append(f.thoughts, [2]string{s, b})
	return nil
}

type fakeNotifier struct{ msgs []string }

func (f *fakeNotifier) Notify(_ context.Context, t string) error {
	f.msgs = append(f.msgs, t)
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
	l, n := &fakeLinear{}, &fakeNotifier{}
	return &bridge{linear: l, notify: n, ownerID: testOwner, appUserID: testApp}, l, n
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
	work(q, b)
	if len(l.thoughts) != 2 {
		t.Fatalf("thoughts = %d", len(l.thoughts))
	}
}
