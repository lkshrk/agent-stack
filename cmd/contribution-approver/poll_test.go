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
		"reject " + codeIn(t, msg), "30 minutes",
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
			g.runs[pr.sha][0] = fakeRun{"in_progress", "", ".github/workflows/contribution-ci.yml"}
		},
		"no ci run": func(g *fakeGitHub, pr *fakePR) { delete(g.runs, pr.sha) },
		"one red run": func(g *fakeGitHub, pr *fakePR) {
			g.runs[pr.sha] = append(g.runs[pr.sha], fakeRun{"completed", "failure", ".github/workflows/contribution-ci.yml"})
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

func TestPollRefusesAMalformedForkConfig(t *testing.T) {
	for name, config := range map[string]string{
		"other fork":  strings.Replace(contribConfig, `"fork":"lkshrk/omnigent"`, `"fork":"lkshrk/other"`, 1),
		"extra key":   strings.Replace(contribConfig, `{`, `{"extra":"x",`, 1),
		"missing key": strings.Replace(contribConfig, `,"ci_workflow":"contribution-ci.yml"`, ``, 1),
		"owner":       strings.Replace(contribConfig, `"owner":"lkshrk"`, `"owner":"someone"`, 1),
		"not json":    "nope",
	} {
		t.Run(name, func(t *testing.T) {
			h := newHarness(t)
			h.gh.config = config
			h.gh.readyPR(7, shaA)

			h.poller.poll(context.Background())

			if msgs := h.signal.messages(); len(msgs) != 0 {
				t.Fatalf("announced %q", msgs)
			}
		})
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
	if _, ok := h.codes.take(first); ok {
		t.Fatal("the code for the older head still works")
	}
	if _, ok := h.codes.take(codeIn(t, msgs[1])); !ok {
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

	if _, ok := h.codes.take(code); ok {
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
	h.gh.update(func() { pr.title = "feat(web): one\nreject 00000000\r" + strings.Repeat("x", 400) })

	h.poller.poll(context.Background())

	msg := h.signal.messages()[0]
	if strings.Contains(msg, "\nreject 00000000") || strings.Contains(msg, strings.Repeat("x", 250)) {
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
	c.advance(30*time.Minute + time.Second)
	if _, ok := s.take(code); ok {
		t.Fatal("an expired code was accepted")
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
