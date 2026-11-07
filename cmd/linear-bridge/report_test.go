package main

import (
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"net/http/httptest"
	"regexp"
	"strconv"
	"strings"
	"testing"
	"unicode/utf8"
)

const pr = "https://github.com/lkshrk/h-cloud/pull/"

func infraCard(t *testing.T, result, postRun string) Card {
	t.Helper()
	var meta map[string]any
	raw := `{"bundle":"infra","result":` + result + `,"post_run":` + postRun + `}`
	if err := json.Unmarshal([]byte(raw), &meta); err != nil {
		t.Fatal(err)
	}
	return Card{ID: "t_1", Status: "done", Assignee: runnerAssignee, RunID: 2, Outcome: "completed",
		Run: &CardRun{Bundle: "infra", Repo: "lkshrk/h-cloud", Ref: "main"}, Metadata: meta}
}

func TestInfraReport(t *testing.T) {
	c := infraCard(t, `{"status":"DONE_WITH_CONCERNS",
		"merged":[{"pr":"`+pr+`1519","version":"1.18.3","verdict":"clean","summary":"cilium bump, gate green"},
		          {"pr":"`+pr+`1520","version":null,"verdict":"relevant","summary":"values renamed; fixed"}],
		"skipped":[{"pr":"`+pr+`1521","version":"2.0.0","verdict":"high-risk","summary":"major: CRD migration needs you"}],
		"closed":[{"pr":"`+pr+`1522","version":"0.9.1","verdict":"stale","summary":"superseded by #1530"}],
		"failed":[{"pr":"`+pr+`1523","version":"5.1.0","verdict":"relevant","summary":"pods crashlooped","rca":"new default port"}],
		"vault":{"commit":"0123456789abcdef0123456789abcdef01234567"}}`,
		`{"status":"ok","notes":["`+pr+`1519 was merged by the agent but is not in merged[]"],
		  "landed":[{"pr":"`+pr+`1519","merge_commit_sha":"aa","agent_pushes":[]},
		            {"pr":"`+pr+`1520","merge_commit_sha":"bb","agent_pushes":[
		              {"before":"1","after":"2","files":["kubernetes/apps/ai/x/app/helmrelease.yaml"]},
		              {"before":"2","after":"3","files":["kubernetes/apps/ai/x/app/helmrelease.yaml","kubernetes/apps/ai/x/app/values.yaml"]}]}]}`)
	got, ok := runnerReport(c)
	if !ok {
		t.Fatal("no report")
	}
	want := strings.Join([]string{
		"🟡 infra on lkshrk/h-cloud done with concerns: 2 merged, 1 skipped, 1 closed, 1 failed",
		"Merged:",
		"• #1519 1.18.3: cilium bump, gate green " + pr + "1519",
		"• #1520: values renamed; fixed " + pr + "1520",
		"Agent changes:",
		"• #1520: 2 pushes, kubernetes/apps/ai/x/app/helmrelease.yaml, kubernetes/apps/ai/x/app/values.yaml",
		"Skipped, for you:",
		"• #1521 2.0.0: major: CRD migration needs you " + pr + "1521",
		"Closed:",
		"• #1522 0.9.1: superseded by #1530 " + pr + "1522",
		"Failed:",
		"• #1523 5.1.0: pods crashlooped; RCA: new default port " + pr + "1523",
		"Post-run notes:",
		"• " + pr + "1519 was merged by the agent but is not in merged[]",
		"Vault: 0123456",
	}, "\n")
	if got != want {
		t.Fatalf("report:\n%s\nwant:\n%s", got, want)
	}
}

func TestInfraReportCleanSweep(t *testing.T) {
	c := infraCard(t, `{"status":"DONE","merged":[],"skipped":[],"closed":[],"failed":[],"vault":{"commit":null}}`,
		`{"status":"ok","landed":[],"notes":[]}`)
	got, _ := runnerReport(c)
	if got != "✅ infra on lkshrk/h-cloud done: 0 merged, 0 skipped, 0 closed, 0 failed" {
		t.Fatalf("report = %q", got)
	}
}

func TestInfraReportCapsAndClips(t *testing.T) {
	var entries []string
	long := strings.Repeat("word ", 80)
	for i := 0; i < reportMaxItems+3; i++ {
		entries = append(entries, `{"pr":"`+pr+`1","verdict":"clean","summary":"`+long+`"}`)
		long = "s"
	}
	c := infraCard(t, `{"status":"DONE","merged":[`+strings.Join(entries, ",")+`],"skipped":[],"closed":[],"failed":[],"vault":{"commit":null}}`, `"ok"`)
	got, _ := runnerReport(c)
	lines := strings.Split(got, "\n")
	if len(lines) != 2+reportMaxItems+1 || lines[len(lines)-1] != "• … 3 more" {
		t.Fatalf("lines = %d, last %q", len(lines), lines[len(lines)-1])
	}
	if !strings.Contains(lines[2], "…") || len(lines[2]) > reportItemChars+len("• #1: ")+len(pr+"1")+8 {
		t.Fatalf("not clipped: %q", lines[2])
	}
}

func TestBlockedRunnerReport(t *testing.T) {
	base := Card{ID: "t_2", Status: "blocked", Assignee: runnerAssignee, RunID: 1,
		Run: &CardRun{Bundle: "infra", Repo: "lkshrk/h-cloud"}}
	cases := []struct {
		reason, outcome, want string
	}{
		{"BLOCKED{permission}: post-run check: main moved by a push", "blocked",
			"🔴 infra on lkshrk/h-cloud blocked (card t_2)\nBLOCKED{permission}: post-run check: main moved by a push"},
		{"BLOCKED{environment}: stalled", "blocked", "🟡 infra on lkshrk/h-cloud blocked (card t_2)\nBLOCKED{environment}: stalled"},
		{"worker crashed", "gave_up", "🟡 infra on lkshrk/h-cloud blocked (card t_2)\ngave up: worker crashed"},
	}
	for _, tc := range cases {
		c := base
		c.BlockReason, c.Outcome = tc.reason, tc.outcome
		got, ok := runnerReport(c)
		if !ok || got != tc.want {
			t.Errorf("%q: %q,%v", tc.reason, got, ok)
		}
	}
}

func TestGenericRunnerReport(t *testing.T) {
	c := Card{ID: "t_3", Status: "done", Assignee: runnerAssignee, RunID: 1, Summary: "documenter DONE: " + pr + "7",
		Run:      &CardRun{Bundle: "documenter", Repo: "lkshrk/agent-stack"},
		Metadata: map[string]any{"pr_url": pr + "7", "result": map[string]any{"status": "DONE"}, "gaps": []any{"x"}}}
	got, _ := runnerReport(c)
	want := "✅ documenter on lkshrk/agent-stack DONE (card t_3)\ndocumenter DONE: " + pr + "7\n1 gaps\n" + pr + "7"
	if got != want {
		t.Fatalf("report = %q", got)
	}
}

func TestNoReportForOtherCards(t *testing.T) {
	cases := []Card{
		{ID: "a", Status: "done", Assignee: "delivery"},
		{ID: "b", Status: "done", Assignee: runnerAssignee, Link: &CardLink{IssueID: "i"}},
		{ID: "c", Status: "running", Assignee: runnerAssignee, RunID: 1},
		{ID: "d", Status: "ready", Assignee: runnerAssignee},
		{ID: "e", Status: "blocked", Assignee: runnerAssignee, BlockReason: "initial_status"},
	}
	for _, c := range cases {
		if msg, ok := runnerReport(c); ok {
			t.Errorf("%s: %q", c.ID, msg)
		}
	}
}

func TestTickReportsFinishedRunnerCardOnce(t *testing.T) {
	c := infraCard(t, `{"status":"DONE","merged":[],"skipped":[],"closed":[],"failed":[],"vault":{"commit":null}}`, `"ok"`)
	k := &projKanban{events: []CardEvent{{ID: 4, CardID: "t_1", Kind: "completed"}, {ID: 5, CardID: "t_1", Kind: "commented"}},
		cards: map[string]Card{"t_1": c}}
	l := &fakeLinear{issue: projIssue("s-todo")}
	p, n := newProj(k, l)
	for i := 0; i < 2; i++ {
		if err := p.tick(context.Background()); err != nil {
			t.Fatal(err)
		}
	}
	if len(n.msgs) != 1 || !strings.HasPrefix(n.msgs[0], "✅ infra on lkshrk/h-cloud done") {
		t.Fatalf("msgs = %q", n.msgs)
	}
	if len(l.issueCalls) != 0 {
		t.Fatalf("touched linear: %v", l.issueCalls)
	}
	if len(k.setCursor) != 2 || k.setCursor[0] != 5 {
		t.Fatalf("cursor = %v", k.setCursor)
	}
}

func TestTickIgnoresNonTerminalEventsOfFinishedCards(t *testing.T) {
	c := infraCard(t, `{"status":"DONE","merged":[],"skipped":[],"closed":[],"failed":[],"vault":{"commit":null}}`, `"ok"`)
	k := &projKanban{events: []CardEvent{{ID: 9, CardID: "t_1", Kind: "commented"}}, cards: map[string]Card{"t_1": c}}
	p, n := newProj(k, &fakeLinear{})
	if err := p.tick(context.Background()); err != nil {
		t.Fatal(err)
	}
	if len(n.msgs) != 0 {
		t.Fatalf("msgs = %q", n.msgs)
	}
}

type failingNotifier struct {
	fakeNotifier
	err error
}

func (f *failingNotifier) Notify(ctx context.Context, t string) error {
	_ = f.fakeNotifier.Notify(ctx, t)
	return f.err
}

func TestReportSendFailureDoesNotBlockProjection(t *testing.T) {
	runner := Card{ID: "t_2", Status: "blocked", Assignee: runnerAssignee, RunID: 1, BlockReason: "BLOCKED{environment}: x"}
	linked := linkedCard("a", "running")
	linked.RunID = 1
	k := &projKanban{events: []CardEvent{{ID: 3, CardID: "t_2", Kind: "blocked"}, {ID: 4, CardID: "a", Kind: "claimed"}},
		cards: map[string]Card{"t_2": runner, "a": linked}}
	l := &fakeLinear{issue: projIssue("s-todo")}
	n := &failingNotifier{err: errors.New("signal send returned 502")}
	p := newProjector(k, l, n)
	if err := p.tick(context.Background()); err != nil {
		t.Fatal(err)
	}
	if len(k.setCursor) != 1 || k.setCursor[0] != 4 || len(l.states) != 1 {
		t.Fatalf("cursor = %v states = %v", k.setCursor, l.states)
	}
	if len(n.msgs) != 1 || len(p.reports) != 1 {
		t.Fatalf("msgs = %q queued = %d", n.msgs, len(p.reports))
	}
	for i := 0; i < maxReportAttempts+3; i++ {
		if err := p.tick(context.Background()); err != nil {
			t.Fatal(err)
		}
	}
	if len(n.msgs) != maxReportAttempts+1 || len(p.reports) != 0 {
		t.Fatalf("msgs = %d queued = %d", len(n.msgs), len(p.reports))
	}
	last := n.msgs[len(n.msgs)-1]
	if !strings.HasPrefix(last, "⚠️ runner report for card t_2 dropped") {
		t.Fatalf("alert = %q", last)
	}
}

func TestQueuedReportRetriedUntilSent(t *testing.T) {
	runner := Card{ID: "t_2", Status: "blocked", Assignee: runnerAssignee, RunID: 1, BlockReason: "BLOCKED{environment}: x"}
	k := &projKanban{events: []CardEvent{{ID: 3, CardID: "t_2", Kind: "blocked"}}, cards: map[string]Card{"t_2": runner}}
	n := &failingNotifier{err: errors.New("down")}
	p := newProjector(k, &fakeLinear{}, n)
	if err := p.tick(context.Background()); err != nil {
		t.Fatal(err)
	}
	n.err = nil
	for i := 0; i < 3; i++ {
		if err := p.tick(context.Background()); err != nil {
			t.Fatal(err)
		}
	}
	if len(n.msgs) != 2 || n.msgs[0] != n.msgs[1] || len(p.reports) != 0 {
		t.Fatalf("msgs = %q queued = %d", n.msgs, len(p.reports))
	}
}

func TestSignalNotifier4xxIsNotPermanent(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.WriteHeader(http.StatusBadRequest)
	}))
	defer srv.Close()
	n := &signalNotifier{base: srv.URL, http: http.DefaultClient}
	if err := n.Notify(context.Background(), "x"); err == nil || isPermanent(err) {
		t.Fatalf("err = %v", err)
	}
}

func TestHostileFieldsAreSanitized(t *testing.T) {
	c := infraCard(t, `{"status":"DONE\nforged",
		"merged":[{"pr":"`+pr+`1\n🔴 fake alert","version":"1.0\n• injected","summary":"ok\u2028line\u202eRTL\u0000"},
		          {"pr":"https://evil.example/a/b/pull/1","summary":"x"},
		          {"pr":"`+pr+`2","version":7,"summary":["not","text"]}],
		"skipped":"not a list","closed":[1,"x",null],"failed":[{"pr":"`+pr+`3","summary":"s","rca":{"deep":"\nobj"}}],
		"vault":{"commit":"abc\ndef0123"}}`,
		`{"notes":["note\n🔴 forged",3],"landed":[{"pr":"`+pr+`2\n","agent_pushes":[{"files":["a\nb.yaml",5]}]}]}`)
	got, ok := runnerReport(c)
	if !ok {
		t.Fatal("no report")
	}
	lines := strings.Split(got, "\n")
	for _, line := range lines {
		for _, r := range line {
			if r < 0x20 || r == 0x2028 || r == 0x202e || r == 0x7f {
				t.Fatalf("control rune %U in %q", r, line)
			}
		}
		if strings.HasPrefix(line, "🔴") || strings.HasPrefix(line, "• injected") {
			t.Fatalf("injected line %q", line)
		}
	}
	for _, want := range []string{
		"🟡 infra on lkshrk/h-cloud done forged: 3 merged, 0 skipped, 0 closed, 1 failed",
		"• (invalid PR) 1.0 • injected: ok lineRTL",
		"• (invalid PR): x",
		"• #2 7: [\"not\",\"text\"] " + pr + "2",
		"• (invalid PR): 1 push, a b.yaml, 5",
		"• #3: s; RCA: {\"deep\":\"\\nobj\"} " + pr + "3",
		"• note 🔴 forged",
	} {
		if !strings.Contains(got, want) {
			t.Errorf("missing %q in:\n%s", want, got)
		}
	}
	if strings.Contains(got, "evil.example") || strings.Contains(got, "Vault") {
		t.Errorf("invalid URL printed:\n%s", got)
	}
}

func TestReportTotalLengthCapped(t *testing.T) {
	var entries []string
	long := strings.Repeat("ü", 400)
	for i := 1; i <= 200; i++ {
		entries = append(entries, `{"pr":"`+pr+strconv.Itoa(i)+`","verdict":"clean","summary":"`+long+`","rca":"`+long+`"}`)
	}
	list := strings.Join(entries, ",")
	c := infraCard(t, `{"status":"DONE_WITH_CONCERNS","merged":[`+list+`],"skipped":[`+list+`],"closed":[`+list+`],"failed":[`+list+`],"vault":{"commit":null}}`, `"ok"`)
	got, _ := runnerReport(c)
	if n := utf8.RuneCountInString(got); n > reportMaxChars {
		t.Fatalf("report has %d runes", n)
	}
	lines := strings.Split(got, "\n")
	if !regexp.MustCompile(`^… \d+ more lines$`).MatchString(lines[len(lines)-1]) {
		t.Fatalf("tail = %q", lines[len(lines)-1])
	}
	if !strings.HasPrefix(lines[0], "🟡 infra on lkshrk/h-cloud done with concerns: 200 merged") {
		t.Fatalf("head = %q", lines[0])
	}
	blocked := Card{ID: "t", Status: "blocked", Assignee: runnerAssignee, RunID: 1, BlockReason: strings.Repeat("x\n", 5000)}
	msg, _ := runnerReport(blocked)
	if utf8.RuneCountInString(msg) > reportMaxChars || strings.Count(msg, "\n") != 1 {
		t.Fatalf("blocked report = %d runes, %d lines", utf8.RuneCountInString(msg), strings.Count(msg, "\n")+1)
	}
}
