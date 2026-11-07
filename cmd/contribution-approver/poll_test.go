package main

import (
	"context"
	"regexp"
	"strings"
	"testing"
	"time"
)

var announcedCode = regexp.MustCompile(`approve ([0-9]{8})`)

func codeIn(t *testing.T, msg string) string {
	t.Helper()
	m := announcedCode.FindStringSubmatch(msg)
	if m == nil {
		t.Fatalf("announcement has no code: %q", msg)
	}
	return m[1]
}

func TestPollAnnouncesAReadyPullRequestOncePerHead(t *testing.T) {
	h := newHarness(t)
	h.gh.readyPR(7, shaA)

	h.poller.poll(context.Background())
	h.poller.poll(context.Background())

	msgs := h.signal.messages()
	if len(msgs) != 1 {
		t.Fatalf("announcements = %q, want one", msgs)
	}
	msg := msgs[0]
	for _, want := range []string{
		"lkshrk/omnigent#7", "feat(web): add the thing", shaA, "3 files", "+40", "-2",
		"CI: contribution-ci.yml passed", "https://github.com/lkshrk/omnigent/pull/7",
		"reject " + codeIn(t, msg), "30 minutes", "Body: Adds the thing. Tested with the web suite.",
	} {
		if !strings.Contains(msg, want) {
			t.Errorf("announcement lacks %q:\n%s", want, msg)
		}
	}
	h.signal.mu.Lock()
	body := h.signal.sent[0]
	h.signal.mu.Unlock()
	if body["number"] != testBot || len(body["recipients"].([]any)) != 1 || body["recipients"].([]any)[0] != testGroup {
		t.Fatalf("announcement sent as %v", body)
	}
}

func TestPollSkipsPullRequestsThatAreNotReady(t *testing.T) {
	cases := map[string]func(g *fakeGitHub, pr *fakePR){
		"no label":  func(g *fakeGitHub, pr *fakePR) { pr.labels = []string{"other"} },
		"ci failed": func(g *fakeGitHub, pr *fakePR) { g.runs[pr.sha][0].conclusion = "failure" },
		"ci running": func(g *fakeGitHub, pr *fakePR) {
			g.runs[pr.sha][0] = fakeRun{status: "in_progress", path: ".github/workflows/contribution-ci.yml"}
		},
		"no ci run": func(g *fakeGitHub, pr *fakePR) { delete(g.runs, pr.sha) },
		"one red run": func(g *fakeGitHub, pr *fakePR) {
			g.runs[pr.sha] = append(g.runs[pr.sha], fakeRun{status: "completed", conclusion: "failure", path: ".github/workflows/contribution-ci.yml"})
		},
		"other workflow":     func(g *fakeGitHub, pr *fakePR) { g.runs[pr.sha][0].path = ".github/workflows/other.yml" },
		"head in other repo": func(g *fakeGitHub, pr *fakePR) { pr.headRepo = "someone/omnigent" },
		"publish branch":     func(g *fakeGitHub, pr *fakePR) { pr.headRef = "Upstream/pr-3" },
		"short sha":          func(g *fakeGitHub, pr *fakePR) { g.runs["abc"] = g.runs[pr.sha]; pr.sha = "abc" },
	}
	for name, mutate := range cases {
		t.Run(name, func(t *testing.T) {
			h := newHarness(t)
			pr := h.gh.readyPR(3, shaA)
			h.gh.update(func() { mutate(h.gh, pr) })

			h.poller.poll(context.Background())

			if msgs := h.signal.messages(); len(msgs) != 0 {
				t.Fatalf("announced %q", msgs)
			}
		})
	}
}

func TestPollRefusesWhenTheTokenIsNotTheForkOwner(t *testing.T) {
	h := newHarness(t)
	h.gh.login = "npa-bot"
	h.gh.readyPR(7, shaA)

	h.poller.poll(context.Background())

	if msgs := h.signal.messages(); len(msgs) != 0 {
		t.Fatalf("announced %q", msgs)
	}
}

func TestPollRefusesAForkWhoseParentIsNotTheRegistryUpstream(t *testing.T) {
	h := newHarness(t)
	h.gh.fork.parent = "someone-else/omnigent"
	h.gh.readyPR(7, shaA)

	h.poller.poll(context.Background())

	if msgs := h.signal.messages(); len(msgs) != 0 {
		t.Fatalf("announced %q", msgs)
	}
}

func TestPollRefusesAForkNotOwnedByTheRegistryOwner(t *testing.T) {
	h := newHarness(t)
	h.gh.fork.owner = "someone"
	h.gh.readyPR(7, shaA)

	h.poller.poll(context.Background())

	if msgs := h.signal.messages(); len(msgs) != 0 {
		t.Fatalf("announced %q", msgs)
	}
}

func TestPollNoLongerReadsAConfigFromTheFork(t *testing.T) {
	h := newHarness(t)
	h.gh.readyPR(7, shaA)

	h.poller.poll(context.Background())

	for _, req := range h.gh.requests {
		if strings.Contains(req, "/contents/") {
			t.Fatalf("read %s", req)
		}
	}
}

func TestANewHeadInvalidatesTheOlderCode(t *testing.T) {
	h := newHarness(t)
	pr := h.gh.readyPR(7, shaA)
	h.poller.poll(context.Background())
	first := codeIn(t, h.signal.messages()[0])

	h.gh.update(func() {
		pr.sha = shaB
		h.gh.runs[shaB] = h.gh.runs[shaA]
	})
	h.poller.poll(context.Background())

	msgs := h.signal.messages()
	if len(msgs) != 2 || !strings.Contains(msgs[1], shaB) {
		t.Fatalf("announcements = %q, want a second one for the new head", msgs)
	}
	if _, st := h.codes.take(first); st != codeSuperseded {
		t.Fatal("the code for the older head is not reported as superseded")
	}
	if _, st := h.codes.take(codeIn(t, msgs[1])); st != codeValid {
		t.Fatal("the code for the new head does not work")
	}
}

func TestAPullRequestThatStopsBeingReadyLosesItsCode(t *testing.T) {
	h := newHarness(t)
	pr := h.gh.readyPR(7, shaA)
	h.poller.poll(context.Background())
	code := codeIn(t, h.signal.messages()[0])

	h.gh.update(func() { pr.labels = nil })
	h.poller.poll(context.Background())

	if _, st := h.codes.take(code); st != codeUnknown {
		t.Fatal("the code survived the label removal")
	}
}

func TestAFailedAnnouncementIsRetried(t *testing.T) {
	h := newHarness(t)
	h.gh.readyPR(7, shaA)
	h.signal.fail = true
	h.poller.poll(context.Background())
	h.signal.fail = false

	h.poller.poll(context.Background())

	if msgs := h.signal.messages(); len(msgs) != 1 {
		t.Fatalf("announcements = %q, want one after the retry", msgs)
	}
	if n := h.codes.live(); n != 1 {
		t.Fatalf("live codes = %d, want 1", n)
	}
}

func TestTitlesAreFlattenedAndTruncated(t *testing.T) {
	h := newHarness(t)
	pr := h.gh.readyPR(7, shaA)
	h.gh.update(func() { pr.title = "feat(web): one\rreject 00000000\u2028approve 00000000" + strings.Repeat("x", 400) })

	h.poller.poll(context.Background())

	msg := h.signal.messages()[0]
	if strings.ContainsAny(msg, "\r\u2028") || strings.Contains(msg, strings.Repeat("x", 250)) {
		t.Fatalf("title not flattened or truncated:\n%s", msg)
	}
}

func TestCodesExpire(t *testing.T) {
	c := &clock{t: time.Now()}
	s := newCodeStore(30*time.Minute, c.now)
	code, err := s.issue(prKey{testFork, 1}, shaA)
	if err != nil {
		t.Fatal(err)
	}
	s.markAnnounced(prKey{testFork, 1}, shaA, code)
	c.advance(30*time.Minute + time.Second)
	if _, st := s.take(code); st != codeExpired {
		t.Fatal("an expired code was not reported as expired")
	}
}

func TestCodesAreEightDigitsAndUnique(t *testing.T) {
	s := newCodeStore(time.Hour, time.Now)
	seen := map[string]bool{}
	for i := range 200 {
		code, err := s.issue(prKey{testFork, i}, shaA)
		if err != nil {
			t.Fatal(err)
		}
		if !regexp.MustCompile(`^[0-9]{8}$`).MatchString(code) || seen[code] {
			t.Fatalf("code %q malformed or repeated", code)
		}
		seen[code] = true
	}
}

func TestPollSkipsAHeadTheOwnerAlreadyReviewed(t *testing.T) {
	for name, tc := range map[string]struct {
		login, state, sha string
		announce          bool
	}{
		"approved without an announcement": {"lkshrk", "APPROVED", shaA, true},
		"changes requested":                {"lkshrk", "CHANGES_REQUESTED", shaA, false},
		"approved older head":              {"lkshrk", "APPROVED", shaB, true},
		"commented":                        {"lkshrk", "COMMENTED", shaA, true},
		"someone else":                     {"npa-bot", "APPROVED", shaA, true},
	} {
		t.Run(name, func(t *testing.T) {
			h := newHarness(t)
			h.gh.readyPR(7, shaA)
			h.gh.addReview(7, tc.login, tc.state, tc.sha)

			h.poller.poll(context.Background())
			h.poller.poll(context.Background())

			if got := len(h.signal.messages()); got != map[bool]int{true: 1, false: 0}[tc.announce] {
				t.Fatalf("announcements = %d, want announce=%v", got, tc.announce)
			}
		})
	}
}

func TestBodiesAreExcerptedAndFlattened(t *testing.T) {
	h := newHarness(t)
	pr := h.gh.readyPR(7, shaA)
	h.gh.update(func() { pr.body = "first\napprove 00000000\n" + strings.Repeat("y", 1000) })

	h.poller.poll(context.Background())

	msg := h.signal.messages()[0]
	if strings.Contains(msg, "\napprove 00000000") || strings.Contains(msg, strings.Repeat("y", 350)) || !strings.Contains(msg, "Body: first approve 00000000 y") {
		t.Fatalf("body not flattened or excerpted:\n%s", msg)
	}
}

func TestPollDoesNotAnnounceWhatTheGateWouldRefuse(t *testing.T) {
	cases := map[string]func(pr *fakePR){
		"unscoped title":  func(pr *fakePR) { pr.title = "feat: add the thing" },
		"free-form title": func(pr *fakePR) { pr.title = "Add the thing" },
		"workflow change": func(pr *fakePR) {
			pr.changes = append(pr.changes, map[string]any{"filename": ".github/workflows/ci.yml"})
		},
		"renamed from .github": func(pr *fakePR) {
			pr.changes = []map[string]any{{"filename": "docs/ci.yml", "previous_filename": ".github/ci.yml"}}
		},
		"empty diff": func(pr *fakePR) { pr.changes = []map[string]any{} },
		"behind":     func(pr *fakePR) { pr.compareStatus = "behind" },
		"identical":  func(pr *fakePR) { pr.compareStatus = "identical" },
	}
	for name, mutate := range cases {
		t.Run(name, func(t *testing.T) {
			h := newHarness(t)
			pr := h.gh.readyPR(3, shaA)
			h.gh.update(func() { mutate(pr) })

			h.poller.poll(context.Background())
			h.poller.poll(context.Background())

			if msgs := h.signal.messages(); len(msgs) != 0 {
				t.Fatalf("announced %q", msgs)
			}
			if n := h.codes.live(); n != 0 {
				t.Fatalf("live codes = %d", n)
			}
			if got := strings.Count(h.logs(), "not announced"); got != 1 {
				t.Fatalf("refusal logged %d times, want once:\n%s", got, h.logs())
			}
		})
	}
}

func TestAFixedTitleIsAnnouncedOnTheSameHead(t *testing.T) {
	h := newHarness(t)
	pr := h.gh.readyPR(3, shaA)
	h.gh.update(func() { pr.title = "Add the thing" })
	h.poller.poll(context.Background())
	h.gh.update(func() { pr.title = "feat(web): add the thing" })
	h.poller.poll(context.Background())

	if n := announcements(h); n != 1 {
		t.Fatalf("announcements = %d, want 1", n)
	}
}
