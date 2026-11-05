package main

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"strings"
	"testing"
	"time"
)

const (
	ciPath    = ".github/workflows/contribution-ci.yml"
	prNumber  = 7
	prTitle   = "feat(web): add the thing"
	prBody    = "Adds the thing.\n\nTested with the web suite."
	shaC      = "cccccccccccccccccccccccccccccccccccccccc"
	upCompare = "omnigent-ai:main..."
	fkCompare = "lkshrk:main..."
)

func ciCheck(name, status, conclusion string) fakeCheckRun {
	return fakeCheckRun{name: name, status: status, conclusion: conclusion, path: ciPath}
}

// publishable opens fork PR #7 at sha with green CI; mutate adjusts the PR before it is created.
func publishable(t *testing.T, sha string, mutate func(*fakePR)) *harness {
	t.Helper()
	h := newHarness(t)
	pr := fakePR{
		fakeIssue: &fakeIssue{number: prNumber, title: prTitle, body: prBody, user: testAgent, labels: []string{readyLabel}},
		headRef:   "feature-7", headRepo: testFork, base: "main", sha: sha, additions: 40, dels: 2, files: 3,
		changes: []map[string]any{{"filename": "web/src/thing.ts"}}, compareStatus: "ahead",
	}
	if mutate != nil {
		mutate(&pr)
	}
	h.gh.addPull(testFork, pr)
	greenCI(h, sha)
	h.clock.advance(time.Minute)
	return h
}

func greenCI(h *harness, sha string) {
	h.gh.update(func() { h.gh.fork.runs[sha] = []fakeRun{{status: "completed", conclusion: "success", path: ciPath}} })
	h.gh.addCheckRun(testFork, sha, ciCheck("test", "completed", "success"))
}

func setCI(h *harness, sha string, runs []fakeRun, checks ...fakeCheckRun) {
	h.gh.update(func() {
		h.gh.fork.runs[sha] = runs
		delete(h.gh.fork.checks, sha)
	})
	for _, c := range checks {
		h.gh.addCheckRun(testFork, sha, c)
	}
}

func setBothCompares(h *harness, sha, status string, ahead int, files ...map[string]any) {
	h.gh.setCompare(testFork, upCompare+sha, status, ahead, 0, files...)
	h.gh.setCompare(testFork, fkCompare+sha, status, ahead, 0, files...)
}

func announceText(h *harness, sha string) {
	pr := h.gh.pullOf(testFork, prNumber)
	h.codes.recordAnnouncement(prKey{fork: testFork, number: prNumber}, sha, contentDigest(pr.title, pr.body), lastReview(h))
}

// approve is an owner approval given after seeing the current text announced at sha.
func lastReview(h *harness) int64 {
	var last int64
	for _, rv := range h.gh.reviewsOf(prNumber) {
		last = max(last, rv.id)
	}
	return last
}

func approve(h *harness, sha string) fakeReview {
	announceText(h, sha)
	return h.gh.addReview(prNumber, testOwner, "APPROVED", sha)
}

const testOwner = "lkshrk"

func decidePR(t *testing.T, h *harness) publishDecision {
	t.Helper()
	before := len(h.gh.writes())
	d := h.poller.decidePublish(context.Background(), h.poller.forks[0], prNumber)
	if w := h.gh.writes(); len(w) != before {
		t.Errorf("the publish decision wrote to GitHub: %+v", w[before:])
	}
	for _, secret := range []string{testPAT, "Adds the thing", "Tested with the web suite", "@", "://", "evil"} {
		if strings.Contains(d.reason, secret) {
			t.Errorf("reason %q leaks %q", d.reason, secret)
		}
	}
	return d
}

func wantVerdict(t *testing.T, d publishDecision, want verdict, reason string) {
	t.Helper()
	if d.verdict != want {
		t.Fatalf("verdict = %v (%q), want %v", d.verdict, d.reason, want)
	}
	if !strings.Contains(d.reason, reason) {
		t.Fatalf("reason = %q, want it to mention %q", d.reason, reason)
	}
}

func TestPublishDecisionPublishesTheApprovedCommit(t *testing.T) {
	h := publishable(t, shaA, nil)
	rv := approve(h, shaA)
	d := decidePR(t, h)
	wantVerdict(t, d, verdictPublish, "")
	if d.sha != shaA || d.title != prTitle || d.body != prBody || d.reviewID != rv.id || !d.reviewedAt.Equal(rv.submittedAt) || d.number != prNumber {
		t.Fatalf("decision = %+v, want the approved commit, title and body of review %d", d, rv.id)
	}
	for _, path := range []string{"/repos/lkshrk/omnigent/compare/" + upCompare + shaA, "/repos/lkshrk/omnigent/compare/" + fkCompare + shaA} {
		if h.gh.callCount(http.MethodGet, path) != 1 {
			t.Errorf("expected one GET %s", path)
		}
	}
	for _, c := range h.gh.allCalls() {
		if c.Method != http.MethodGet && c.Path != "/graphql" {
			t.Errorf("unexpected call %s %s", c.Method, c.Path)
		}
	}
}

func TestPublishDecisionVerifiesTheForkFirst(t *testing.T) {
	for name, change := range map[string]func(*fakeGitHub){
		"parent": func(g *fakeGitHub) { g.fork.parent = "someone/omnigent" },
		"owner":  func(g *fakeGitHub) { g.fork.owner = "someone" },
	} {
		t.Run(name, func(t *testing.T) {
			h := publishable(t, shaA, nil)
			approve(h, shaA)
			h.gh.update(func() { change(h.gh) })
			wantVerdict(t, decidePR(t, h), verdictTransient, "")
			if calls := h.gh.allCalls(); len(calls) != 1 || calls[0].Path != "/repos/lkshrk/omnigent" {
				t.Fatalf("calls = %+v, want only the fork lookup", calls)
			}
		})
	}
}

func TestPublishDecisionPublishesTheReviewedCommitWhenTheHeadMovedOn(t *testing.T) {
	h := publishable(t, shaB, nil)
	greenCI(h, shaA)
	setBothCompares(h, shaA, "ahead", 1, map[string]any{"filename": "web/src/thing.ts"})
	approve(h, shaA)
	d := decidePR(t, h)
	wantVerdict(t, d, verdictPublish, "")
	if d.sha != shaA {
		t.Fatalf("sha = %s, want the reviewed %s", d.sha, shaA)
	}
}

func TestPublishDecisionUsesTheNewestOwnerApproval(t *testing.T) {
	h := publishable(t, shaA, nil)
	approve(h, shaA)
	h.clock.advance(time.Minute)
	greenCI(h, shaB)
	setBothCompares(h, shaB, "ahead", 1, map[string]any{"filename": "web/src/thing.ts"})
	rv := approve(h, shaB)
	d := decidePR(t, h)
	wantVerdict(t, d, verdictPublish, "")
	if d.sha != shaB || d.reviewID != rv.id {
		t.Fatalf("decision = %+v, want review %d at %s", d, rv.id, shaB)
	}
}

func TestPublishDecisionNeedsAnOwnerApproval(t *testing.T) {
	cases := map[string]func(h *harness){
		"no review":    func(h *harness) {},
		"another user": func(h *harness) { h.gh.addReview(prNumber, "someone", "APPROVED", shaA) },
		"a member": func(h *harness) {
			h.gh.addMember(testFork, "helper")
			h.gh.addReview(prNumber, "helper", "APPROVED", shaA)
		},
		"owner association only": func(h *harness) { h.gh.addReview(prNumber, "LKSHRK", "APPROVED", shaA) },
		"commented only":         func(h *harness) { h.gh.addReview(prNumber, testOwner, "COMMENTED", shaA) },
		"pending only":           func(h *harness) { h.gh.addReview(prNumber, testOwner, "PENDING", shaA) },
		"changes requested only": func(h *harness) { h.gh.addReview(prNumber, testOwner, "CHANGES_REQUESTED", shaA) },
		"newer changes requested": func(h *harness) {
			approve(h, shaA)
			h.clock.advance(time.Second)
			h.gh.addReview(prNumber, testOwner, "CHANGES_REQUESTED", shaA)
		},
		"same-second changes after": func(h *harness) { approve(h, shaA); h.gh.addReview(prNumber, testOwner, "CHANGES_REQUESTED", shaA) },
		"dismissed approval": func(h *harness) {
			rv := approve(h, shaA)
			h.clock.advance(time.Second)
			h.gh.dismissReview(prNumber, rv.id, testOwner, "not yet")
		},
		"newer dismissed review": func(h *harness) {
			approve(h, shaA)
			h.clock.advance(time.Second)
			rv := h.gh.addReview(prNumber, testOwner, "CHANGES_REQUESTED", shaA)
			h.gh.dismissReview(prNumber, rv.id, testOwner, "withdrawn")
		},
	}
	for name, setup := range cases {
		t.Run(name, func(t *testing.T) {
			h := publishable(t, shaA, nil)
			setup(h)
			wantVerdict(t, decidePR(t, h), verdictNone, "")
		})
	}
}

func TestPublishDecisionIgnoresLaterCommentsAndPendingReviews(t *testing.T) {
	h := publishable(t, shaA, nil)
	approve(h, shaA)
	h.clock.advance(time.Second)
	h.gh.addReview(prNumber, testOwner, "COMMENTED", shaA)
	h.gh.addReview(prNumber, testOwner, "PENDING", shaA)
	h.gh.addReview(prNumber, "someone", "CHANGES_REQUESTED", shaA)
	wantVerdict(t, decidePR(t, h), verdictPublish, "")
}

func TestPublishDecisionReadsEveryReviewPage(t *testing.T) {
	t.Run("approval on page two", func(t *testing.T) {
		h := publishable(t, shaA, nil)
		for range 110 {
			h.gh.addReview(prNumber, "someone", "COMMENTED", shaA)
		}
		approve(h, shaA)
		wantVerdict(t, decidePR(t, h), verdictPublish, "")
	})
	t.Run("changes requested on page two", func(t *testing.T) {
		h := publishable(t, shaA, nil)
		approve(h, shaA)
		for range 110 {
			h.gh.addReview(prNumber, "someone", "COMMENTED", shaA)
		}
		h.gh.addReview(prNumber, testOwner, "CHANGES_REQUESTED", shaA)
		wantVerdict(t, decidePR(t, h), verdictNone, "")
	})
}

func TestPublishDecisionRefusesAReviewOfNoFullSHA(t *testing.T) {
	h := publishable(t, shaA, nil)
	approve(h, "abc123")
	wantVerdict(t, decidePR(t, h), verdictHard, "full SHA")
}

func TestPublishDecisionChecksThePullRequest(t *testing.T) {
	cases := []struct {
		name   string
		mutate func(*fakePR)
		want   verdict
		reason string
	}{
		{"no ready label", func(p *fakePR) { p.labels = []string{"upstream-issue"} }, verdictTransient, readyLabel},
		{"another base", func(p *fakePR) { p.base = "release" }, verdictHard, "base"},
		{"a head branch in the publish namespace", func(p *fakePR) { p.headRef = "upstream/pr-3" }, verdictHard, "publish branch"},
		{"an upper-case publish namespace", func(p *fakePR) { p.headRef = "Upstream/pr-3" }, verdictHard, "publish branch"},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			h := publishable(t, shaA, c.mutate)
			approve(h, shaA)
			wantVerdict(t, decidePR(t, h), c.want, c.reason)
		})
	}
	t.Run("a head from another repository", func(t *testing.T) {
		h := newHarness(t)
		h.gh.addRepo("someone/omnigent", testUpstream, "main", shaMain)
		h.gh.addPull(testFork, fakePR{
			fakeIssue: &fakeIssue{number: prNumber, title: prTitle, body: prBody, user: testAgent, labels: []string{readyLabel}},
			headRef:   "feature-7", headRepo: "someone/omnigent", base: "main", sha: shaA,
			changes: []map[string]any{{"filename": "web/src/thing.ts"}},
		})
		greenCI(h, shaA)
		h.clock.advance(time.Minute)
		approve(h, shaA)
		wantVerdict(t, decidePR(t, h), verdictHard, "head")
	})
	t.Run("a closed pull request", func(t *testing.T) {
		h := publishable(t, shaA, nil)
		approve(h, shaA)
		h.clock.advance(time.Minute)
		h.gh.closeIssue(testFork, prNumber, testAgent)
		wantVerdict(t, decidePR(t, h), verdictHard, "not open")
	})
}

func TestPublishDecisionRefusesTitlesOutsideTheUpstreamConvention(t *testing.T) {
	for _, title := range []string{"fix: no scope", "Fix the sidebar", "fix(web) missing colon", "fix(web):", ""} {
		t.Run(title, func(t *testing.T) {
			h := publishable(t, shaA, func(p *fakePR) { p.title = title })
			approve(h, shaA)
			wantVerdict(t, decidePR(t, h), verdictHard, "title")
		})
	}
}

func TestPublishDecisionRefusesABaseChangeAtAnyTime(t *testing.T) {
	for name, afterReview := range map[string]bool{"before the review": false, "after the review": true} {
		t.Run(name, func(t *testing.T) {
			h := publishable(t, shaA, nil)
			if afterReview {
				approve(h, shaA)
				h.clock.advance(time.Minute)
			}
			h.gh.changeBase(prNumber, "staging", testAgent)
			h.gh.changeBase(prNumber, "main", testAgent)
			if !afterReview {
				h.clock.advance(time.Minute)
				approve(h, shaA)
			}
			wantVerdict(t, decidePR(t, h), verdictHard, "base")
		})
	}
}

func TestPublishDecisionRefusesACloseAndReopenAfterTheApproval(t *testing.T) {
	t.Run("after", func(t *testing.T) {
		h := publishable(t, shaA, nil)
		approve(h, shaA)
		h.clock.advance(time.Minute)
		h.gh.closeIssue(testFork, prNumber, testAgent)
		h.gh.reopen(testFork, prNumber, testAgent)
		wantVerdict(t, decidePR(t, h), verdictHard, "reopened")
	})
	t.Run("same second", func(t *testing.T) {
		h := publishable(t, shaA, nil)
		approve(h, shaA)
		h.gh.closeIssue(testFork, prNumber, testAgent)
		h.gh.reopen(testFork, prNumber, testAgent)
		wantVerdict(t, decidePR(t, h), verdictHard, "reopened")
	})
	t.Run("before", func(t *testing.T) {
		h := publishable(t, shaA, nil)
		h.gh.closeIssue(testFork, prNumber, testAgent)
		h.gh.reopen(testFork, prNumber, testAgent)
		h.clock.advance(time.Minute)
		approve(h, shaA)
		wantVerdict(t, decidePR(t, h), verdictPublish, "")
	})
}

func TestPublishDecisionBindsTheTitleAndBodyToTheReview(t *testing.T) {
	cases := map[string]struct {
		edit   func(h *harness)
		gap    time.Duration
		reason string
	}{
		"rename after":          {func(h *harness) { h.gh.rename(testFork, prNumber, "feat(web): something else", testAgent) }, time.Minute, "title"},
		"rename same second":    {func(h *harness) { h.gh.rename(testFork, prNumber, "feat(web): something else", testAgent) }, 0, "title"},
		"body edit after":       {func(h *harness) { h.gh.editBody(testFork, prNumber, "rewritten", testAgent) }, time.Minute, "body"},
		"body edit same second": {func(h *harness) { h.gh.editBody(testFork, prNumber, "rewritten", testAgent) }, 0, "body"},
	}
	for name, c := range cases {
		t.Run(name, func(t *testing.T) {
			h := publishable(t, shaA, nil)
			approve(h, shaA)
			h.clock.advance(c.gap)
			c.edit(h)
			wantVerdict(t, decidePR(t, h), verdictHard, c.reason)
		})
	}
	t.Run("edits before the review", func(t *testing.T) {
		h := publishable(t, shaA, nil)
		h.gh.rename(testFork, prNumber, "feat(web): add the better thing", testAgent)
		h.gh.editBody(testFork, prNumber, "Adds the thing, better.", testAgent)
		h.clock.advance(time.Second)
		approve(h, shaA)
		d := decidePR(t, h)
		wantVerdict(t, d, verdictPublish, "")
		if d.title != "feat(web): add the better thing" || d.body != "Adds the thing, better." || d.lastEditedAt == nil {
			t.Fatalf("decision = %+v, want the edited title and body with their edit time", d)
		}
	})
}

func TestPublishDecisionCatchesADeletedRevision(t *testing.T) {
	type scenario struct {
		setup  func(h *harness)
		index  int
		reason string
	}
	edit := func(body string) func(h *harness) {
		return func(h *harness) { h.gh.editBody(testFork, prNumber, body, testAgent) }
	}
	editedBeforeAndAfter := func(h *harness) {
		edit("before")(h)
		h.clock.advance(time.Minute)
		approve(h, shaA)
		h.clock.advance(time.Minute)
		edit("after")(h)
	}
	scenarios := map[string]scenario{
		"the post-review revision after a pre-review edit": {editedBeforeAndAfter, 2, "body"},
		"the pre-review revision":                          {editedBeforeAndAfter, 1, "body"},
		"the original revision":                            {editedBeforeAndAfter, 0, "body"},
		"one of two post-review revisions": {func(h *harness) {
			approve(h, shaA)
			h.clock.advance(time.Minute)
			edit("sneaky")(h)
			h.clock.advance(time.Minute)
			edit("sneakier")(h)
		}, 2, "body"},
	}
	for name, s := range scenarios {
		for mode, label := range map[fakeRevisionDeletion]string{fakeEditSoftDeleted: "soft-deleted", fakeEditDropped: "dropped"} {
			t.Run(name+"/"+label, func(t *testing.T) {
				h := publishable(t, shaA, nil)
				s.setup(h)
				h.clock.advance(time.Minute)
				h.gh.deleteRevision(testFork, prNumber, s.index, testAgent, mode)
				wantVerdict(t, decidePR(t, h), verdictHard, s.reason)
			})
		}
	}
	t.Run("the only post-review revision, soft-deleted", func(t *testing.T) {
		h := publishable(t, shaA, nil)
		approve(h, shaA)
		h.clock.advance(time.Minute)
		edit("after")(h)
		h.clock.advance(time.Minute)
		h.gh.deleteRevision(testFork, prNumber, 1, testAgent, fakeEditSoftDeleted)
		wantVerdict(t, decidePR(t, h), verdictHard, "body")
	})
	t.Run("a pre-review revision soft-deleted after the review", func(t *testing.T) {
		h := publishable(t, shaA, nil)
		edit("before")(h)
		h.clock.advance(time.Minute)
		approve(h, shaA)
		h.clock.advance(time.Minute)
		h.gh.deleteRevision(testFork, prNumber, 0, testAgent, fakeEditSoftDeleted)
		wantVerdict(t, decidePR(t, h), verdictHard, "revision")
	})
	t.Run("a revision deleted before the review", func(t *testing.T) {
		for mode, label := range map[fakeRevisionDeletion]string{fakeEditSoftDeleted: "soft-deleted", fakeEditDropped: "dropped"} {
			t.Run(label, func(t *testing.T) {
				h := publishable(t, shaA, nil)
				edit("first")(h)
				h.clock.advance(time.Minute)
				edit("second")(h)
				h.clock.advance(time.Minute)
				h.gh.deleteRevision(testFork, prNumber, 1, testAgent, mode)
				h.clock.advance(time.Minute)
				approve(h, shaA)
				wantVerdict(t, decidePR(t, h), verdictPublish, "")
			})
		}
	})
}

func TestPublishDecisionReadsEveryEditAndEventPage(t *testing.T) {
	t.Run("edits beyond one page", func(t *testing.T) {
		h := publishable(t, shaA, nil)
		for i := range 105 {
			h.gh.editBody(testFork, prNumber, fmt.Sprintf("draft %d", i), testAgent)
		}
		h.clock.advance(time.Second)
		approve(h, shaA)
		wantVerdict(t, decidePR(t, h), verdictPublish, "")
		if n := h.gh.callCount(http.MethodPost, "/graphql"); n != 2 {
			t.Fatalf("graphql calls = %d, want 2 pages", n)
		}
	})
	t.Run("an edit history past the page cap", func(t *testing.T) {
		h := publishable(t, shaA, nil)
		for i := range maxPages*pageSize + 1 {
			h.gh.editBody(testFork, prNumber, fmt.Sprintf("draft %d", i), testAgent)
		}
		h.clock.advance(time.Second)
		approve(h, shaA)
		wantVerdict(t, decidePR(t, h), verdictHard, "more than")
	})
	t.Run("a rename on the second event page", func(t *testing.T) {
		h := publishable(t, shaA, nil)
		for range 60 {
			h.gh.label(testFork, prNumber, "noise", testAgent)
			h.gh.unlabel(testFork, prNumber, "noise", testAgent)
		}
		approve(h, shaA)
		h.clock.advance(time.Minute)
		h.gh.rename(testFork, prNumber, "feat(web): something else", testAgent)
		wantVerdict(t, decidePR(t, h), verdictHard, "title")
	})
}

func TestPublishDecisionRefusesWhenGraphQLFails(t *testing.T) {
	h := publishable(t, shaA, nil)
	approve(h, shaA)
	h.gh.failGraphQL("Something went wrong while executing your query.")
	wantVerdict(t, decidePR(t, h), verdictTransient, "GraphQL")
}

func TestPublishDecisionRequiresGreenCIForTheReviewedCommit(t *testing.T) {
	green := fakeRun{status: "completed", conclusion: "success", path: ciPath}
	cases := []struct {
		name   string
		sha    string
		runs   []fakeRun
		checks []fakeCheckRun
		want   verdict
		reason string
	}{
		{"failure", shaA, []fakeRun{{status: "completed", conclusion: "failure", path: ciPath}}, []fakeCheckRun{ciCheck("test", "completed", "failure")}, verdictHard, "CI"},
		{"cancelled", shaA, []fakeRun{{status: "completed", conclusion: "cancelled", path: ciPath}}, []fakeCheckRun{ciCheck("test", "completed", "cancelled")}, verdictHard, "CI"},
		{"in progress", shaA, []fakeRun{{status: "in_progress", path: ciPath}}, []fakeCheckRun{ciCheck("test", "in_progress", "")}, verdictTransient, "CI"},
		{"queued without check runs", shaA, []fakeRun{{status: "queued", path: ciPath}}, nil, verdictTransient, "CI"},
		{"missing", shaA, nil, nil, verdictTransient, "CI"},
		{"only the later head", shaB, []fakeRun{green}, []fakeCheckRun{ciCheck("test", "completed", "success")}, verdictTransient, "CI"},
		{"one red among green", shaA, []fakeRun{green, {status: "completed", conclusion: "failure", path: ciPath}}, []fakeCheckRun{ciCheck("test", "completed", "success")}, verdictHard, "CI"},
		{"another workflow", shaA, []fakeRun{{status: "completed", conclusion: "success", path: ".github/workflows/other.yml"}}, []fakeCheckRun{{name: "test", status: "completed", conclusion: "success", path: ".github/workflows/other.yml"}}, verdictTransient, "CI"},
		{"a red check run under a green run", shaA, []fakeRun{green}, []fakeCheckRun{ciCheck("test", "completed", "success"), ciCheck("lint", "completed", "failure")}, verdictHard, "CI"},
		{"a running check run under a green run", shaA, []fakeRun{green}, []fakeCheckRun{ciCheck("test", "completed", "success"), ciCheck("lint", "in_progress", "")}, verdictTransient, "CI"},
		{"a green run without check runs", shaA, []fakeRun{green}, nil, verdictTransient, "CI"},
		{"a run from another event", shaA, []fakeRun{{status: "completed", conclusion: "success", path: ciPath, event: "workflow_dispatch"}}, []fakeCheckRun{ciCheck("test", "completed", "success")}, verdictHard, "event"},
		{"a check run from another app", shaA, []fakeRun{green}, []fakeCheckRun{ciCheck("test", "completed", "success"), {name: "test", status: "completed", conclusion: "success", path: ciPath, app: "impostor"}}, verdictHard, "app"},
		{"a check suite from another event", shaA, []fakeRun{green}, []fakeCheckRun{{name: "test", status: "completed", conclusion: "success", path: ciPath, event: "push"}}, verdictHard, "event"},
		{"a rerun that went green", shaA, []fakeRun{green}, []fakeCheckRun{ciCheck("test", "completed", "failure"), ciCheck("test", "completed", "success")}, verdictPublish, ""},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			h := publishable(t, shaA, nil)
			setCI(h, shaA, nil)
			setCI(h, c.sha, c.runs, c.checks...)
			approve(h, shaA)
			wantVerdict(t, decidePR(t, h), c.want, c.reason)
		})
	}
	t.Run("check runs beyond one page", func(t *testing.T) {
		h := publishable(t, shaA, nil)
		checks := []fakeCheckRun{}
		for i := range 130 {
			checks = append(checks, ciCheck(fmt.Sprintf("job-%d", i), "completed", "success"))
		}
		checks[125].conclusion = "failure"
		setCI(h, shaA, []fakeRun{green}, checks...)
		approve(h, shaA)
		wantVerdict(t, decidePR(t, h), verdictHard, "CI")
	})
}

func TestPublishDecisionChecksTheDiff(t *testing.T) {
	thing := map[string]any{"filename": "web/src/thing.ts"}
	cases := []struct {
		name   string
		setup  func(h *harness)
		want   verdict
		reason string
	}{
		{"identical", func(h *harness) { setBothCompares(h, shaA, "identical", 0) }, verdictHard, "ahead"},
		{"behind", func(h *harness) { setBothCompares(h, shaA, "behind", 0) }, verdictHard, "ahead"},
		{"diverged", func(h *harness) { setBothCompares(h, shaA, "diverged", 2, thing) }, verdictPublish, ""},
		{"empty", func(h *harness) { setBothCompares(h, shaA, "ahead", 1) }, verdictHard, "empty"},
		{"300 files", func(h *harness) {
			var files []map[string]any
			for n := range maxCompareFiles {
				files = append(files, map[string]any{"filename": fmt.Sprintf("web/%d.ts", n)})
			}
			setBothCompares(h, shaA, "ahead", 1, files...)
		}, verdictHard, "too large"},
		{"a workflow", func(h *harness) {
			setBothCompares(h, shaA, "ahead", 1, map[string]any{"filename": ".github/workflows/publish-upstream.yml"})
		}, verdictHard, ".github/"},
		{"CODEOWNERS", func(h *harness) {
			setBothCompares(h, shaA, "ahead", 1, map[string]any{"filename": ".github/CODEOWNERS"})
		}, verdictHard, ".github/"},
		{"the gate", func(h *harness) { setBothCompares(h, shaA, "ahead", 1, map[string]any{"filename": "publish/gate.py"}) }, verdictHard, "publish/"},
		{"the gate config among other files", func(h *harness) {
			setBothCompares(h, shaA, "ahead", 1, thing, map[string]any{"filename": "publish/contribution.json"})
		}, verdictHard, "publish/"},
		{"a workflow renamed away", func(h *harness) {
			setBothCompares(h, shaA, "ahead", 1, map[string]any{"filename": "web/moved.yml", "previous_filename": ".github/workflows/ci.yml", "status": "renamed"})
		}, verdictHard, ".github/"},
		{"different blobs", func(h *harness) {
			h.gh.setCompare(testFork, upCompare+shaA, "ahead", 1, 0, map[string]any{"filename": "web/src/thing.ts", "sha": shaB})
			h.gh.setCompare(testFork, fkCompare+shaA, "ahead", 1, 0, map[string]any{"filename": "web/src/thing.ts", "sha": shaC})
		}, verdictHard, "differ"},
		{"an extra upstream file", func(h *harness) {
			h.gh.setCompare(testFork, upCompare+shaA, "ahead", 2, 0, thing, map[string]any{"filename": "web/src/extra.ts"})
		}, verdictHard, "differ"},
		{"a fork-relative diff that is not ahead", func(h *harness) {
			h.gh.setCompare(testFork, fkCompare+shaA, "identical", 0, 0)
		}, verdictHard, "ahead"},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			h := publishable(t, shaA, nil)
			c.setup(h)
			approve(h, shaA)
			wantVerdict(t, decidePR(t, h), c.want, c.reason)
		})
	}
}

func TestPublishDecisionComparesAgainstBothDefaultBranches(t *testing.T) {
	graph := func(h *harness, forkMain string) {
		h.gh.addCommit(shaUpMain, nil, map[string]string{"README.md": fakeSHA("readme")})
		if forkMain != shaUpMain {
			h.gh.addCommit(forkMain, []string{shaUpMain}, map[string]string{"web/src/sneaky.ts": fakeSHA("sneaky")})
		}
		h.gh.setRef(testFork, "main", forkMain)
		h.gh.addCommit(shaA, []string{forkMain}, map[string]string{"web/src/thing.ts": fakeSHA("thing")})
	}
	t.Run("fork main equals upstream main", func(t *testing.T) {
		h := publishable(t, shaA, nil)
		graph(h, shaUpMain)
		approve(h, shaA)
		wantVerdict(t, decidePR(t, h), verdictPublish, "")
	})
	t.Run("fork main ahead of upstream main", func(t *testing.T) {
		h := publishable(t, shaA, nil)
		graph(h, shaC)
		approve(h, shaA)
		wantVerdict(t, decidePR(t, h), verdictHard, "differ")
	})
}

func TestPublishDecisionFailsClosedOnAPIErrors(t *testing.T) {
	fork := "/repos/lkshrk/omnigent"
	paths := []string{
		fork,
		fork + "/pulls/7/reviews",
		fork + "/pulls/7",
		fork + "/issues/7/timeline",
		"/graphql",
		fork + "/actions/workflows/contribution-ci.yml/runs",
		fork + "/commits/" + shaA + "/check-runs",
		fork + "/compare/" + upCompare + shaA,
		fork + "/compare/" + fkCompare + shaA,
	}
	for _, path := range paths {
		for _, status := range []int{http.StatusBadGateway, http.StatusNotFound, fakeDrop} {
			t.Run(fmt.Sprintf("%s/%d", path, status), func(t *testing.T) {
				h := publishable(t, shaA, nil)
				approve(h, shaA)
				method := http.MethodGet
				if path == "/graphql" {
					method = http.MethodPost
				}
				h.gh.fail(method, path, status)
				d := decidePR(t, h)
				wantVerdict(t, d, verdictTransient, "")
				if d.reason == "" {
					t.Fatal("a refusal needs a reason")
				}
			})
		}
	}
}

var prK = prKey{fork: testFork, number: prNumber}

func wantReannounce(t *testing.T, h *harness, d publishDecision, want bool) {
	t.Helper()
	if d.reannounce != want || h.codes.reannouncing(prK) != want {
		t.Fatalf("reannounce = %v, store = %v, want %v", d.reannounce, h.codes.reannouncing(prK), want)
	}
}

func readyMessages(h *harness) int {
	n := 0
	for _, m := range h.signal.messages() {
		if strings.HasPrefix(m, "Contribution ready for upstream: "+testFork+"#7") {
			n++
		}
	}
	return n
}

func TestPublishDecisionRefusesADroppedSinglePostReviewEdit(t *testing.T) {
	for _, index := range []int{0, 1} {
		t.Run(fmt.Sprint(index), func(t *testing.T) {
			h := publishable(t, shaA, nil)
			approve(h, shaA)
			h.clock.advance(time.Minute)
			h.gh.editBody(testFork, prNumber, "after", testAgent)
			h.clock.advance(time.Minute)
			h.gh.deleteRevision(testFork, prNumber, index, testAgent, fakeEditDropped)
			d := decidePR(t, h)
			wantVerdict(t, d, verdictTransient, "changed since")
			wantReannounce(t, h, d, true)
		})
	}
}

func TestPublishDecisionNeedsAnAnnouncementBeforeTheApproval(t *testing.T) {
	t.Run("never announced", func(t *testing.T) {
		h := publishable(t, shaA, nil)
		h.gh.addReview(prNumber, testOwner, "APPROVED", shaA)
		d := decidePR(t, h)
		wantVerdict(t, d, verdictTransient, "no announcement")
		wantReannounce(t, h, d, true)
	})
	t.Run("announced after the approval", func(t *testing.T) {
		h := publishable(t, shaA, nil)
		h.gh.addReview(prNumber, testOwner, "APPROVED", shaA)
		h.clock.advance(time.Second)
		announceText(h, shaA)
		d := decidePR(t, h)
		wantVerdict(t, d, verdictTransient, "predates")
		wantReannounce(t, h, d, false)
	})
	t.Run("another commit announced", func(t *testing.T) {
		h := publishable(t, shaA, nil)
		announceText(h, shaB)
		h.gh.addReview(prNumber, testOwner, "APPROVED", shaA)
		wantVerdict(t, decidePR(t, h), verdictTransient, "no announcement")
	})
	t.Run("text changed between announcement and approval", func(t *testing.T) {
		h := publishable(t, shaA, nil)
		announceText(h, shaA)
		h.clock.advance(time.Minute)
		h.gh.rename(testFork, prNumber, "feat(web): add another thing", testAgent)
		h.clock.advance(time.Minute)
		h.gh.addReview(prNumber, testOwner, "APPROVED", shaA)
		d := decidePR(t, h)
		wantVerdict(t, d, verdictTransient, "changed since")
		wantReannounce(t, h, d, true)
	})
	t.Run("a publish asks for nothing", func(t *testing.T) {
		h := publishable(t, shaA, nil)
		approve(h, shaA)
		d := decidePR(t, h)
		wantVerdict(t, d, verdictPublish, "")
		wantReannounce(t, h, d, false)
	})
}

func TestPublishDecisionAfterARestartReannouncesOnce(t *testing.T) {
	h := publishable(t, shaA, nil)
	approve(h, shaA)
	h.codes = newCodeStore(codeTTL, h.clock.now)
	h.poller.codes = h.codes
	d := decidePR(t, h)
	wantVerdict(t, d, verdictTransient, "no announcement")
	wantReannounce(t, h, d, true)

	h.clock.advance(time.Minute)
	h.poller.poll(context.Background())
	h.poller.poll(context.Background())
	if n := readyMessages(h); n != 1 {
		t.Fatalf("announcements after the restart = %d, want 1: %q", n, h.signal.messages())
	}
	d = decidePR(t, h)
	wantVerdict(t, d, verdictTransient, "predates")
	wantReannounce(t, h, d, false)

	h.clock.advance(time.Minute)
	h.gh.addReview(prNumber, testOwner, "APPROVED", shaA)
	wantVerdict(t, decidePR(t, h), verdictPublish, "")
}

func TestPublishDecisionPublishesANewApprovalOfReannouncedText(t *testing.T) {
	h := publishable(t, shaA, nil)
	approve(h, shaA)
	h.clock.advance(time.Minute)
	h.gh.editBody(testFork, prNumber, "Adds the thing, reworded.", testAgent)
	d := decidePR(t, h)
	wantVerdict(t, d, verdictHard, "body")
	wantReannounce(t, h, d, true)

	h.poller.poll(context.Background())
	if n := readyMessages(h); n != 1 {
		t.Fatalf("announcements = %d, want 1: %q", n, h.signal.messages())
	}
	h.clock.advance(time.Minute)
	h.gh.addReview(prNumber, testOwner, "APPROVED", shaA)
	d = decidePR(t, h)
	wantVerdict(t, d, verdictPublish, "")
	if d.body != "Adds the thing, reworded." {
		t.Fatalf("body = %q, want the re-announced text", d.body)
	}
}

func TestASignalApprovalIsBoundToTheRecordedAnnouncement(t *testing.T) {
	t.Run("publishes", func(t *testing.T) {
		h := newHarness(t)
		code := announce(t, h, prNumber, shaA)
		h.gh.addCheckRun(testFork, shaA, ciCheck("test", "completed", "success"))
		h.clock.advance(time.Minute)
		if rec := approveWith(h, code); rec.Code != http.StatusOK {
			t.Fatalf("approve answered %d %s", rec.Code, rec.Body)
		}
		wantVerdict(t, decidePR(t, h), verdictPublish, "")
	})
	t.Run("refuses a code whose recorded text differs", func(t *testing.T) {
		h := newHarness(t)
		code := announce(t, h, prNumber, shaA)
		h.codes.recordAnnouncement(prK, shaA, contentDigest("feat(web): other", "other"), 0)
		if rec := approveWith(h, code); rec.Code != http.StatusConflict {
			t.Fatalf("approve answered %d %s, want 409", rec.Code, rec.Body)
		}
		if r := h.gh.reviewCalls(); len(r) != 0 {
			t.Fatalf("reviews = %v, want none", r)
		}
	})
}

func TestAnAnnouncedHeadWhoseTextChangedIsAnnouncedAgainOnce(t *testing.T) {
	h := publishable(t, shaA, nil)
	h.poller.poll(context.Background())
	h.clock.advance(time.Minute)
	h.gh.addReview(prNumber, testOwner, "APPROVED", shaA)
	h.poller.poll(context.Background())
	h.clock.advance(time.Minute)
	h.gh.editBody(testFork, prNumber, "Adds the thing, reworded.", testAgent)
	wantVerdict(t, decidePR(t, h), verdictHard, "body")

	h.clock.advance(time.Minute)
	h.poller.poll(context.Background())
	h.poller.poll(context.Background())
	if n := readyMessages(h); n != 2 {
		t.Fatalf("announcements = %d, want 2: %q", n, h.signal.messages())
	}
	if a, ok := h.codes.announcementOf(prK, shaA); !ok || a.digest != contentDigest(prTitle, "Adds the thing, reworded.") {
		t.Fatalf("announcement = %+v, %v; want the reworded text", a, ok)
	}
}

const hostile = "@lkshrk [click](https://evil.example) **evil**"

func TestPublishDecisionReasonsCarryNoAttackerText(t *testing.T) {
	cases := map[string]func(h *harness){
		"a protected filename": func(h *harness) {
			setBothCompares(h, shaA, "ahead", 1, map[string]any{"filename": ".github/" + hostile})
		},
		"a protected previous filename": func(h *harness) {
			setBothCompares(h, shaA, "ahead", 1, map[string]any{"filename": "web/x", "previous_filename": "publish/" + hostile})
		},
		"a mismatched filename": func(h *harness) {
			h.gh.setCompare(testFork, upCompare+shaA, "ahead", 1, 0, map[string]any{"filename": "web/" + hostile})
		},
		"a failing check name": func(h *harness) {
			setCI(h, shaA, []fakeRun{{status: "completed", conclusion: "success", path: ciPath}}, ciCheck(hostile, "completed", "failure"))
		},
		"an unknown conclusion": func(h *harness) {
			setCI(h, shaA, []fakeRun{{status: "completed", conclusion: hostile, path: ciPath}}, ciCheck("test", "completed", "success"))
		},
		"a run event": func(h *harness) {
			setCI(h, shaA, []fakeRun{{status: "completed", conclusion: "success", path: ciPath, event: hostile}}, ciCheck("test", "completed", "success"))
		},
		"a suite event": func(h *harness) {
			setCI(h, shaA, []fakeRun{{status: "completed", conclusion: "success", path: ciPath}}, fakeCheckRun{name: "test", status: "completed", conclusion: "success", path: ciPath, event: hostile})
		},
		"a fork owner":  func(h *harness) { h.gh.update(func() { h.gh.fork.owner = "evil" }) },
		"a fork parent": func(h *harness) { h.gh.update(func() { h.gh.fork.parent = "evil/omnigent" }) },
	}
	for name, setup := range cases {
		t.Run(name, func(t *testing.T) {
			h := publishable(t, shaA, nil)
			approve(h, shaA)
			setup(h)
			if d := decidePR(t, h); d.verdict == verdictPublish || d.reason == "" {
				t.Fatalf("decision = %v %q, want a refusal", d.verdict, d.reason)
			}
		})
	}
	for name, mutate := range map[string]func(*fakePR){
		"a title":       func(p *fakePR) { p.title = hostile },
		"a head branch": func(p *fakePR) { p.headRef = "upstream/" + hostile },
		"a base branch": func(p *fakePR) { p.base = hostile },
		"a label":       func(p *fakePR) { p.labels = []string{hostile} },
	} {
		t.Run(name, func(t *testing.T) {
			h := publishable(t, shaA, mutate)
			approve(h, shaA)
			if d := decidePR(t, h); d.verdict == verdictPublish {
				t.Fatalf("published with %s", name)
			}
		})
	}
}

type tamperTransport struct {
	base http.RoundTripper
	path string
	edit func(map[string]any)
}

func (tt *tamperTransport) RoundTrip(r *http.Request) (*http.Response, error) {
	if r.URL.Path != tt.path {
		return tt.base.RoundTrip(r)
	}
	r = r.Clone(r.Context())
	r.Header.Del("If-None-Match")
	res, err := tt.base.RoundTrip(r)
	if err != nil {
		return res, err
	}
	res.Header.Del("ETag")
	raw, err := io.ReadAll(res.Body)
	_ = res.Body.Close()
	if err != nil {
		return nil, err
	}
	var v map[string]any
	if err := json.Unmarshal(raw, &v); err != nil {
		return nil, err
	}
	tt.edit(v)
	out, _ := json.Marshal(v)
	res.Body, res.ContentLength = io.NopCloser(bytes.NewReader(out)), int64(len(out))
	res.Header.Del("Content-Length")
	return res, nil
}

func tamper(h *harness, path string, edit func(map[string]any)) {
	gh := h.poller.gh
	gh.http = &http.Client{Transport: &tamperTransport{base: gh.http.Transport, path: path, edit: edit}}
}

func gqlPull(v map[string]any) map[string]any {
	return v["data"].(map[string]any)["repository"].(map[string]any)["pullRequest"].(map[string]any)
}

func TestPublishDecisionRefusesInconsistentReads(t *testing.T) {
	checks := "/repos/lkshrk/omnigent/commits/" + shaA + "/check-runs"
	cases := []struct {
		name   string
		setup  func(h *harness)
		want   verdict
		reason string
	}{
		{"a fork-relative diff with an extra file", func(h *harness) {
			h.gh.setCompare(testFork, fkCompare+shaA, "ahead", 2, 0, map[string]any{"filename": "web/src/thing.ts"}, map[string]any{"filename": "web/src/extra.ts"})
		}, verdictHard, "differ"},
		{"GraphQL and REST titles differ", func(h *harness) {
			tamper(h, "/graphql", func(v map[string]any) { gqlPull(v)["title"] = "feat(web): other" })
		}, verdictTransient, "changed while it was read"},
		{"GraphQL and REST bodies differ", func(h *harness) {
			tamper(h, "/graphql", func(v map[string]any) { gqlPull(v)["body"] = "other" })
		}, verdictTransient, "changed while it was read"},
		{"edit nodes short of totalCount", func(h *harness) {
			h.gh.editBody(testFork, prNumber, prBody+" ", testAgent)
			h.gh.editBody(testFork, prNumber, prBody, testAgent)
			h.clock.advance(time.Second)
			approve(h, shaA)
			tamper(h, "/graphql", func(v map[string]any) {
				e := gqlPull(v)["userContentEdits"].(map[string]any)
				e["totalCount"] = e["totalCount"].(float64) + 1
			})
		}, verdictTransient, "edit history changed"},
		{"a short page below total_count", func(h *harness) {
			tamper(h, checks, func(v map[string]any) { v["total_count"] = v["total_count"].(float64) + 1 })
		}, verdictTransient, "changed while they were read"},
		{"more check runs than total_count", func(h *harness) {
			tamper(h, checks, func(v map[string]any) { v["total_count"] = v["total_count"].(float64) - 1 })
		}, verdictTransient, "changed while they were read"},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			h := publishable(t, shaA, nil)
			approve(h, shaA)
			c.setup(h)
			wantVerdict(t, decidePR(t, h), c.want, c.reason)
		})
	}
}

func TestPublishDecisionBindsByReviewIDNotClock(t *testing.T) {
	t.Run("a review with the recorded ID does not bind", func(t *testing.T) {
		h := publishable(t, shaA, nil)
		rv := h.gh.addReview(prNumber, testOwner, "APPROVED", shaA)
		h.codes.recordAnnouncement(prK, shaA, contentDigest(prTitle, prBody), rv.id)
		wantVerdict(t, decidePR(t, h), verdictTransient, "predates")
	})
	t.Run("a review with a higher ID binds", func(t *testing.T) {
		h := publishable(t, shaA, nil)
		rv := h.gh.addReview(prNumber, testOwner, "APPROVED", shaA)
		h.codes.recordAnnouncement(prK, shaA, contentDigest(prTitle, prBody), rv.id-1)
		wantVerdict(t, decidePR(t, h), verdictPublish, "")
	})
	for name, skew := range map[string]time.Duration{"cluster clock ahead": time.Hour, "cluster clock behind": -time.Hour} {
		t.Run(name, func(t *testing.T) {
			h := publishable(t, shaA, nil)
			h.codes.now = func() time.Time { return h.clock.now().Add(skew) }
			early := h.gh.addReview(prNumber, testOwner, "APPROVED", shaA)
			wantVerdict(t, decidePR(t, h), verdictTransient, "no announcement")
			h.poller.poll(context.Background())
			if a, ok := h.codes.announcementOf(prK, shaA); !ok || a.lastReview != early.id {
				t.Fatalf("announcement = %+v, %v; want the last review %d", a, ok, early.id)
			}
			wantVerdict(t, decidePR(t, h), verdictTransient, "predates")
			h.gh.addReview(prNumber, testOwner, "APPROVED", shaA)
			wantVerdict(t, decidePR(t, h), verdictPublish, "")
		})
	}
}

func TestAnAnnouncementWhoseReviewsCannotBeListedIsNotRecorded(t *testing.T) {
	h := publishable(t, shaA, nil)
	h.gh.failWith(http.MethodGet, "/repos/lkshrk/omnigent/pulls/7/reviews", fakeFailure{Status: http.StatusBadGateway, Skip: 2, Times: 1})
	h.poller.poll(context.Background())
	if _, ok := h.codes.announcementOf(prK, shaA); ok || !h.codes.reannouncing(prK) || readyMessages(h) != 1 {
		t.Fatalf("recorded = %v, reannouncing = %v, messages = %q", ok, h.codes.reannouncing(prK), h.signal.messages())
	}
	h.poller.poll(context.Background())
	if _, ok := h.codes.announcementOf(prK, shaA); !ok || readyMessages(h) != 2 {
		t.Fatalf("recorded = %v, messages = %q", ok, h.signal.messages())
	}
}

func TestPollDoesNotAnnounceWhatThePublisherWouldRefuse(t *testing.T) {
	for name, setup := range map[string]func(h *harness){
		"a base change": func(h *harness) {
			h.gh.changeBase(prNumber, "staging", testAgent)
			h.gh.changeBase(prNumber, "main", testAgent)
		},
		"a fork-relative diff mismatch": func(h *harness) {
			h.gh.setCompare(testFork, fkCompare+shaA, "ahead", 1, 0, map[string]any{"filename": "web/src/thing.ts", "sha": shaC})
		},
		"a fork-relative diff that is not ahead": func(h *harness) { h.gh.setCompare(testFork, fkCompare+shaA, "identical", 0, 0) },
	} {
		t.Run(name, func(t *testing.T) {
			h := publishable(t, shaA, nil)
			setup(h)
			h.poller.poll(context.Background())
			if n := readyMessages(h); n != 0 {
				t.Fatalf("announced %q", h.signal.messages())
			}
			if !strings.Contains(h.logs(), "not announced") {
				t.Fatalf("no refusal logged:\n%s", h.logs())
			}
		})
	}
}
