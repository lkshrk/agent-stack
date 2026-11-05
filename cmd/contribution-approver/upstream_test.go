package main

import (
	"bytes"
	"context"
	"encoding/json"
	"io"
	"net/http"
	"sort"
	"strconv"
	"strings"
	"testing"
	"time"
)

const (
	publishRef     = "upstream/pr-7"
	publishHead    = "lkshrk:upstream/pr-7"
	upPulls        = "/repos/omnigent-ai/omnigent/pulls"
	forkRefs       = "/repos/lkshrk/omnigent/git/refs"
	forkPublishRef = "/repos/lkshrk/omnigent/git/refs/heads/upstream/pr-7"
	maintainer     = "upstream-maintainer"
)

func publishPR(t *testing.T, h *harness) publishResult {
	t.Helper()
	r := h.poller.publish(context.Background(), h.poller.forks[0], prNumber)
	for _, secret := range []string{testPAT, "Adds the thing", "Tested with the web suite", "@", "://", "evil"} {
		if strings.Contains(r.reason, secret) {
			t.Errorf("reason %q leaks %q", r.reason, secret)
		}
	}
	for _, c := range h.gh.allCalls() {
		raw, _ := json.Marshal(c.Body)
		if strings.Contains(string(raw), testPAT) || strings.Contains(c.Query.Encode(), testPAT) || strings.Contains(c.Path, testPAT) {
			t.Errorf("a call carries the token outside its header: %s %s", c.Method, c.Path)
		}
	}
	return r
}

func wantOutcome(t *testing.T, r publishResult, want publishOutcome) {
	t.Helper()
	if r.outcome != want {
		t.Fatalf("outcome = %v (%q), want %v", r.outcome, r.reason, want)
	}
}

func publishedOnce(t *testing.T, h *harness) publishResult {
	t.Helper()
	approve(h, shaA)
	r := publishPR(t, h)
	wantOutcome(t, r, outcomePublished)
	return r
}

func upstreamPulls(h *harness) []fakePR {
	var out []fakePR
	h.gh.update(func() {
		for _, pr := range h.gh.upstream.pulls {
			out = append(out, clonePR(pr))
		}
	})
	sort.Slice(out, func(i, j int) bool { return out[i].number < out[j].number })
	return out
}

func onlyUpstream(t *testing.T, h *harness) fakePR {
	t.Helper()
	pulls := upstreamPulls(h)
	if len(pulls) != 1 {
		t.Fatalf("upstream has %d pull requests, want 1", len(pulls))
	}
	return pulls[0]
}

func wantNoWritesSince(t *testing.T, h *harness, before int) {
	t.Helper()
	if w := h.gh.writes(); len(w) != before {
		t.Fatalf("unexpected writes: %+v", w[before:])
	}
}

func wantRef(t *testing.T, h *harness, sha string) {
	t.Helper()
	if got, ok := h.gh.ref(testFork, publishRef); !ok || got != sha {
		t.Fatalf("%s = %q (exists %v), want %s", publishRef, got, ok, sha)
	}
}

func moveHead(h *harness, sha string) {
	h.gh.update(func() {
		pr := h.gh.fork.pulls[prNumber]
		pr.sha, pr.heads = sha, append(pr.heads, sha)
		h.gh.fork.refs[pr.headRef] = sha
	})
	greenCI(h, sha)
	h.clock.advance(time.Minute)
}

func restart(h *harness) {
	h.codes = newCodeStore(codeTTL, h.clock.now)
	h.poller = &poller{gh: h.poller.gh, forks: h.poller.forks, codes: h.codes, notify: h.poller.notify}
}

func writeSummary(calls []fakeCall) []string {
	var out []string
	for _, c := range calls {
		out = append(out, c.Method+" "+c.Path)
	}
	return out
}

func TestPublishOpensTheUpstreamPullRequest(t *testing.T) {
	h := publishable(t, shaA, nil)
	r := publishedOnce(t, h)
	wantRef(t, h, shaA)
	up := onlyUpstream(t, h)
	if up.state != "open" || up.title != prTitle || up.body != prBody || up.base != "main" || fakeHeadLabel(&up) != publishHead || up.maintainerCanModify || up.sha != shaA {
		t.Fatalf("upstream PR = %+v", up)
	}
	if r.upstream != up.number || r.upstreamURL != "https://github.com/omnigent-ai/omnigent/pull/"+itoa(up.number) {
		t.Fatalf("result names %d %q, want upstream #%d", r.upstream, r.upstreamURL, up.number)
	}
	w := h.gh.writes()
	if got := writeSummary(w); strings.Join(got, ",") != "POST "+forkRefs+",POST "+upPulls {
		t.Fatalf("writes = %v, want the ref then the pull request", got)
	}
	if v, ok := w[1].Body["maintainer_can_modify"]; !ok || v != false {
		t.Fatalf("POST body = %v, want maintainer_can_modify: false", w[1].Body)
	}
	if w[1].Body["head"] != publishHead || w[1].Body["base"] != "main" {
		t.Fatalf("POST body = %v", w[1].Body)
	}
	var lookup *fakeCall
	for _, c := range h.gh.allCalls() {
		if c.Method == http.MethodGet && c.Path == upPulls {
			lookup = &c
			break
		}
	}
	if lookup == nil || lookup.Query.Get("state") != "all" || lookup.Query.Get("head") != publishHead {
		t.Fatalf("upstream lookup = %+v, want state=all and head=%s", lookup, publishHead)
	}
}

func itoa(n int) string { return strconv.Itoa(n) }

func TestPublishTwiceChangesNothing(t *testing.T) {
	h := publishable(t, shaA, nil)
	first := publishedOnce(t, h)
	before := len(h.gh.writes())
	r := publishPR(t, h)
	wantOutcome(t, r, outcomeCurrent)
	wantNoWritesSince(t, h, before)
	if r.upstream != first.upstream || r.upstreamURL != first.upstreamURL {
		t.Fatalf("second result names %d, want %d", r.upstream, first.upstream)
	}
	onlyUpstream(t, h)
}

func TestPublishAfterARestartChangesNothing(t *testing.T) {
	h := publishable(t, shaA, nil)
	publishedOnce(t, h)
	before := len(h.gh.writes())
	restart(h)
	wantOutcome(t, publishPR(t, h), outcomeRetry)
	wantNoWritesSince(t, h, before)
	h.clock.advance(time.Minute)
	approve(h, shaA)
	wantOutcome(t, publishPR(t, h), outcomeCurrent)
	wantNoWritesSince(t, h, before)
	onlyUpstream(t, h)
	wantRef(t, h, shaA)
}

func TestPublishRecoversFromAWriteThatFailed(t *testing.T) {
	cases := map[string]struct {
		method, path string
		failure      fakeFailure
		then         publishOutcome
	}{
		"ref created, then an error":      {"POST", forkRefs, fakeFailure{Status: 502, After: true}, outcomePublished},
		"ref not created":                 {"POST", forkRefs, fakeFailure{Status: 502}, outcomePublished},
		"pull request opened, then error": {"POST", upPulls, fakeFailure{Status: 502, After: true}, outcomeCurrent},
		"pull request opened, then drop":  {"POST", upPulls, fakeFailure{Status: fakeDrop, After: true}, outcomeCurrent},
		"pull request not opened":         {"POST", upPulls, fakeFailure{Status: 422}, outcomePublished},
	}
	for name, c := range cases {
		t.Run(name, func(t *testing.T) {
			h := publishable(t, shaA, nil)
			approve(h, shaA)
			h.gh.failWith(c.method, c.path, c.failure)
			r := publishPR(t, h)
			wantOutcome(t, r, outcomeRetry)
			if r.dismissed || h.gh.reviewsOf(prNumber)[0].state != "APPROVED" {
				t.Fatal("a failed write dismissed the approval")
			}
			h.gh.fail(c.method, c.path, 0)
			wantOutcome(t, publishPR(t, h), c.then)
			before := len(h.gh.writes())
			wantOutcome(t, publishPR(t, h), outcomeCurrent)
			wantNoWritesSince(t, h, before)
			up := onlyUpstream(t, h)
			wantRef(t, h, shaA)
			if up.state != "open" || up.title != prTitle {
				t.Fatalf("upstream PR = %+v", up)
			}
		})
	}
}

// approveNewHead moves the fork PR to sha with a new title and has the owner approve it.
func approveNewHead(h *harness, sha, title string) {
	moveHead(h, sha)
	if title != "" {
		h.gh.rename(testFork, prNumber, title, testAgent)
		h.clock.advance(time.Minute)
	}
	approve(h, sha)
}

func TestPublishMovesTheBranchAndUpdatesTheTextForANewApproval(t *testing.T) {
	const newTitle = "feat(web): add the better thing"
	h := publishable(t, shaA, nil)
	first := publishedOnce(t, h)
	approveNewHead(h, shaB, newTitle)
	before := len(h.gh.writes())
	r := publishPR(t, h)
	wantOutcome(t, r, outcomePublished)
	w := h.gh.writes()[before:]
	upPatch := upPulls + "/" + itoa(first.upstream)
	if got := writeSummary(w); strings.Join(got, ",") != "PATCH "+upPatch+",PATCH "+forkPublishRef {
		t.Fatalf("writes = %v, want the text, then the branch", got)
	}
	if w[0].Body["title"] != newTitle || w[0].Body["body"] != prBody || len(w[0].Body) != 2 {
		t.Fatalf("PATCH body = %v, want exactly the approved title and body", w[0].Body)
	}
	if w[1].Body["sha"] != shaB || w[1].Body["force"] != true {
		t.Fatalf("ref PATCH = %v", w[1].Body)
	}
	up := onlyUpstream(t, h)
	wantRef(t, h, shaB)
	if up.title != newTitle || up.sha != shaB || up.number != first.upstream {
		t.Fatalf("upstream PR = %+v", up)
	}
	before = len(h.gh.writes())
	wantOutcome(t, publishPR(t, h), outcomeCurrent)
	wantNoWritesSince(t, h, before)
}

func TestPublishLeavesTheBranchWhenTheUpstreamEditFails(t *testing.T) {
	for name, f := range map[string]fakeFailure{
		"rejected":         {Status: 502},
		"applied, errored": {Status: 502, After: true},
	} {
		t.Run(name, func(t *testing.T) {
			h := publishable(t, shaA, nil)
			first := publishedOnce(t, h)
			approveNewHead(h, shaB, "")
			upPatch := upPulls + "/" + itoa(first.upstream)
			h.gh.failWith("PATCH", upPatch, f)
			wantOutcome(t, publishPR(t, h), outcomeRetry)
			wantRef(t, h, shaA)
			if h.gh.callCount("PATCH", forkPublishRef) != 0 {
				t.Fatal("the branch moved after the upstream edit failed")
			}
			h.gh.fail("PATCH", upPatch, 0)
			wantOutcome(t, publishPR(t, h), outcomePublished)
			wantRef(t, h, shaB)
			wantOutcome(t, publishPR(t, h), outcomeCurrent)
			onlyUpstream(t, h)
		})
	}
	t.Run("branch moved, then an error", func(t *testing.T) {
		h := publishable(t, shaA, nil)
		publishedOnce(t, h)
		approveNewHead(h, shaB, "")
		h.gh.failWith("PATCH", forkPublishRef, fakeFailure{Status: 502, After: true})
		wantOutcome(t, publishPR(t, h), outcomeRetry)
		h.gh.fail("PATCH", forkPublishRef, 0)
		before := len(h.gh.writes())
		wantOutcome(t, publishPR(t, h), outcomeCurrent)
		wantNoWritesSince(t, h, before)
		wantRef(t, h, shaB)
	})
}

func TestPublishDoesNotRevertAMaintainerRetitle(t *testing.T) {
	h := publishable(t, shaA, nil)
	first := publishedOnce(t, h)
	h.gh.rename(testUpstream, first.upstream, "feat(web): maintainer wording", maintainer)
	h.gh.editBody(testUpstream, first.upstream, "maintainer body", maintainer)
	h.clock.advance(time.Minute)
	before := len(h.gh.writes())
	wantOutcome(t, publishPR(t, h), outcomeCurrent)
	approve(h, shaA)
	wantOutcome(t, publishPR(t, h), outcomeCurrent)
	wantNoWritesSince(t, h, before)
	up := onlyUpstream(t, h)
	if up.title != "feat(web): maintainer wording" || up.body != "maintainer body" {
		t.Fatalf("upstream PR = %q / %q, want the maintainer's text", up.title, up.body)
	}
}

func TestPublishRefusesMoreThanOneOpenUpstreamPullRequest(t *testing.T) {
	h := publishable(t, shaA, nil)
	h.gh.setRef(testUpstream, "next", shaUpMain)
	for _, base := range []string{"main", "next"} {
		h.gh.addPull(testUpstream, fakePR{fakeIssue: &fakeIssue{title: prTitle, user: testOwner}, headRepo: testFork, headRef: publishRef, base: base, sha: shaB})
	}
	h.clock.advance(time.Minute)
	rv := approve(h, shaA)
	before := len(h.gh.writes())
	r := publishPR(t, h)
	wantOutcome(t, r, outcomeRefused)
	w := h.gh.writes()[before:]
	if got := writeSummary(w); len(got) != 1 || got[0] != "PUT /repos/lkshrk/omnigent/pulls/7/reviews/"+itoa(int(rv.id))+"/dismissals" {
		t.Fatalf("writes = %v, want only the dismissal", got)
	}
	if !r.dismissed || w[0].Body["message"] != r.reason || !strings.Contains(r.reason, "more than one open upstream pull request") {
		t.Fatalf("result = %+v, dismissal = %v", r, w[0].Body)
	}
	wantRef(t, h, shaB)
	before = len(h.gh.writes())
	wantOutcome(t, publishPR(t, h), outcomeNothing)
	wantNoWritesSince(t, h, before)
}

func TestPublishDoesNotReopenAClosedUpstreamPullRequest(t *testing.T) {
	setup := func(t *testing.T) (*harness, publishResult) {
		h := publishable(t, shaA, nil)
		first := publishedOnce(t, h)
		h.clock.advance(time.Minute)
		h.gh.closeIssue(testUpstream, first.upstream, maintainer)
		return h, first
	}
	t.Run("the same approval", func(t *testing.T) {
		h, first := setup(t)
		before := len(h.gh.writes())
		r := publishPR(t, h)
		wantOutcome(t, r, outcomeClosedUpstream)
		if r.upstream != first.upstream {
			t.Fatalf("result names #%d, want the closed #%d", r.upstream, first.upstream)
		}
		wantNoWritesSince(t, h, before)
		if up := onlyUpstream(t, h); up.state != "closed" {
			t.Fatalf("upstream state = %s", up.state)
		}
	})
	t.Run("a new head approved before the close", func(t *testing.T) {
		h := publishable(t, shaA, nil)
		first := publishedOnce(t, h)
		approveNewHead(h, shaB, "")
		h.clock.advance(time.Minute)
		h.gh.closeIssue(testUpstream, first.upstream, maintainer)
		before := len(h.gh.writes())
		wantOutcome(t, publishPR(t, h), outcomeClosedUpstream)
		wantNoWritesSince(t, h, before)
		wantRef(t, h, shaA)
	})
	t.Run("an approval in the same second as the close", func(t *testing.T) {
		h, _ := setup(t)
		approve(h, shaA)
		before := len(h.gh.writes())
		wantOutcome(t, publishPR(t, h), outcomeClosedUpstream)
		wantNoWritesSince(t, h, before)
	})
	t.Run("a newer approval opens a new pull request", func(t *testing.T) {
		h, first := setup(t)
		h.clock.advance(time.Second)
		approve(h, shaA)
		before := len(h.gh.writes())
		r := publishPR(t, h)
		wantOutcome(t, r, outcomePublished)
		if got := writeSummary(h.gh.writes()[before:]); len(got) != 1 || got[0] != "POST "+upPulls {
			t.Fatalf("writes = %v, want one new pull request and no ref move", got)
		}
		pulls := upstreamPulls(h)
		if len(pulls) != 2 || pulls[0].state != "closed" || pulls[1].state != "open" || r.upstream != pulls[1].number || r.upstream == first.upstream {
			t.Fatalf("upstream = %+v, result #%d", pulls, r.upstream)
		}
		before = len(h.gh.writes())
		wantOutcome(t, publishPR(t, h), outcomeCurrent)
		wantNoWritesSince(t, h, before)
	})
}

func TestPublishDoesNotRepublishAMergedUpstreamPullRequest(t *testing.T) {
	t.Run("the same approval", func(t *testing.T) {
		h := publishable(t, shaA, nil)
		first := publishedOnce(t, h)
		h.clock.advance(time.Minute)
		h.gh.merge(testUpstream, first.upstream, maintainer)
		before := len(h.gh.writes())
		wantOutcome(t, publishPR(t, h), outcomeClosedUpstream)
		wantNoWritesSince(t, h, before)
	})
	t.Run("a new head approved before the merge", func(t *testing.T) {
		h := publishable(t, shaA, nil)
		first := publishedOnce(t, h)
		approveNewHead(h, shaB, "feat(web): add more")
		h.clock.advance(time.Minute)
		h.gh.merge(testUpstream, first.upstream, maintainer)
		before := len(h.gh.writes())
		wantOutcome(t, publishPR(t, h), outcomeClosedUpstream)
		wantNoWritesSince(t, h, before)
		wantRef(t, h, shaA)
		if up := onlyUpstream(t, h); up.title != prTitle || up.mergedAt == nil {
			t.Fatalf("upstream PR = %+v", up)
		}
	})
}

func TestPublishDismissesTheApprovalOnAHardRefusal(t *testing.T) {
	h := publishable(t, shaA, nil)
	rv := approve(h, shaA)
	h.clock.advance(time.Minute)
	h.gh.rename(testFork, prNumber, "feat(web): something else", testAgent)
	r := publishPR(t, h)
	wantOutcome(t, r, outcomeRefused)
	w := h.gh.writes()
	if got := writeSummary(w); len(got) != 1 || got[0] != "PUT /repos/lkshrk/omnigent/pulls/7/reviews/"+itoa(int(rv.id))+"/dismissals" {
		t.Fatalf("writes = %v, want only the dismissal", got)
	}
	if !r.dismissed || r.reason != r.decision.reason || w[0].Body["message"] != r.reason || w[0].Body["event"] != "DISMISS" {
		t.Fatalf("result = %+v, dismissal = %v", r, w[0].Body)
	}
	if got := h.gh.reviewsOf(prNumber)[0].state; got != "DISMISSED" {
		t.Fatalf("review state = %s", got)
	}
	if _, ok := h.gh.ref(testFork, publishRef); ok || len(upstreamPulls(h)) != 0 {
		t.Fatal("a refused approval published")
	}
	before := len(h.gh.writes())
	wantOutcome(t, publishPR(t, h), outcomeNothing)
	wantNoWritesSince(t, h, before)
}

func TestPublishRetriesWhenTheDismissalFails(t *testing.T) {
	h := publishable(t, shaA, nil)
	rv := approve(h, shaA)
	h.clock.advance(time.Minute)
	h.gh.rename(testFork, prNumber, "feat(web): something else", testAgent)
	path := "/repos/lkshrk/omnigent/pulls/7/reviews/" + itoa(int(rv.id)) + "/dismissals"
	h.gh.fail("PUT", path, 502)
	r := publishPR(t, h)
	wantOutcome(t, r, outcomeRefused)
	if r.dismissed || strings.Contains(r.reason, "502") {
		t.Fatalf("result = %+v", r)
	}
	h.gh.fail("PUT", path, 0)
	if r := publishPR(t, h); r.outcome != outcomeRefused || !r.dismissed {
		t.Fatalf("result = %+v, want the dismissal retried", r)
	}
}

func TestPublishNeverDismissesOnATransientRefusal(t *testing.T) {
	cases := map[string]func(h *harness){
		"CI running": func(h *harness) {
			setCI(h, shaA, []fakeRun{{status: "in_progress", path: ciPath}}, ciCheck("test", "in_progress", ""))
		},
		"decision read fails":    func(h *harness) { h.gh.fail("GET", "/repos/lkshrk/omnigent/pulls/7", 502) },
		"upstream lookup fails":  func(h *harness) { h.gh.fail("GET", upPulls, 502) },
		"upstream lookup drops":  func(h *harness) { h.gh.fail("GET", upPulls, fakeDrop) },
		"branch read fails":      func(h *harness) { h.gh.fail("GET", "/repos/lkshrk/omnigent/git/ref/heads/upstream/pr-7", 502) },
		"branch read is a 403":   func(h *harness) { h.gh.fail("GET", "/repos/lkshrk/omnigent/git/ref/heads/upstream/pr-7", 403) },
		"pull request POST 422s": func(h *harness) { h.gh.fail("POST", upPulls, 422) },
	}
	for name, setup := range cases {
		t.Run(name, func(t *testing.T) {
			h := publishable(t, shaA, nil)
			approve(h, shaA)
			setup(h)
			r := publishPR(t, h)
			wantOutcome(t, r, outcomeRetry)
			if r.dismissed || h.gh.callCount("PUT", "/repos/lkshrk/omnigent/pulls/7/reviews/"+itoa(int(h.gh.reviewsOf(prNumber)[0].id))+"/dismissals") != 0 {
				t.Fatal("a transient refusal dismissed the approval")
			}
			if len(upstreamPulls(h)) != 0 {
				t.Fatal("a transient refusal published")
			}
		})
	}
}

func TestPublishDoesNotDismissWithoutAReview(t *testing.T) {
	h := publishable(t, shaA, nil)
	for range maxPages*pageSize + 1 {
		h.gh.addReview(prNumber, "someone", "COMMENTED", shaA)
	}
	approve(h, shaA)
	r := publishPR(t, h)
	wantOutcome(t, r, outcomeRefused)
	if r.dismissed || r.decision.reviewID != 0 || len(h.gh.writes()) != 0 {
		t.Fatalf("result = %+v, writes %v", r, writeSummary(h.gh.writes()))
	}
}

func TestPublishReadsEveryUpstreamPage(t *testing.T) {
	closedPulls := func(h *harness, n int) {
		for range n {
			pr := h.gh.addPull(testUpstream, fakePR{fakeIssue: &fakeIssue{title: prTitle, user: testOwner}, headRepo: testFork, headRef: publishRef, base: "main", sha: shaB})
			h.gh.closeIssue(testUpstream, pr.number, maintainer)
		}
	}
	t.Run("an open pull request on page two", func(t *testing.T) {
		h := publishable(t, shaA, nil)
		open := h.gh.addPull(testUpstream, fakePR{fakeIssue: &fakeIssue{title: prTitle, user: testOwner}, headRepo: testFork, headRef: publishRef, base: "main", sha: shaB})
		closedPulls(h, pageSize+5)
		h.clock.advance(time.Minute)
		approve(h, shaA)
		r := publishPR(t, h)
		wantOutcome(t, r, outcomePublished)
		if r.upstream != open.number || h.gh.callCount("POST", upPulls) != 0 {
			t.Fatalf("result #%d, want the open #%d updated", r.upstream, open.number)
		}
		wantRef(t, h, shaA)
	})
	t.Run("a close after the approval on page two", func(t *testing.T) {
		h := publishable(t, shaA, nil)
		late := h.gh.addPull(testUpstream, fakePR{fakeIssue: &fakeIssue{title: prTitle, user: testOwner}, headRepo: testFork, headRef: publishRef, base: "main", sha: shaB})
		closedPulls(h, pageSize+5)
		h.clock.advance(time.Minute)
		approve(h, shaA)
		h.clock.advance(time.Minute)
		h.gh.closeIssue(testUpstream, late.number, maintainer)
		before := len(h.gh.writes())
		r := publishPR(t, h)
		wantOutcome(t, r, outcomeClosedUpstream)
		wantNoWritesSince(t, h, before)
		if r.upstream != late.number {
			t.Fatalf("result #%d, want #%d", r.upstream, late.number)
		}
	})
	t.Run("more than the page cap", func(t *testing.T) {
		h := publishable(t, shaA, nil)
		closedPulls(h, maxPages*pageSize+1)
		h.clock.advance(time.Minute)
		approve(h, shaA)
		r := publishPR(t, h)
		wantOutcome(t, r, outcomeRefused)
		if !r.dismissed || h.gh.callCount("POST", upPulls) != 0 || h.gh.callCount("POST", forkRefs) != 0 {
			t.Fatalf("result = %+v, writes %v", r, writeSummary(h.gh.writes()))
		}
	})
}

func TestPublishForcesTheBranchOnlyOnTheFork(t *testing.T) {
	h := publishable(t, shaA, nil)
	h.gh.setRef(testFork, publishRef, shaB)
	h.clock.advance(time.Minute)
	approve(h, shaA)
	r := publishPR(t, h)
	wantOutcome(t, r, outcomePublished)
	w := h.gh.writes()
	if got := writeSummary(w); strings.Join(got, ",") != "PATCH "+forkPublishRef+",POST "+upPulls {
		t.Fatalf("writes = %v, want a forced move of the leftover branch, then the pull request", got)
	}
	if w[0].Body["force"] != true || w[0].Body["sha"] != shaA {
		t.Fatalf("ref PATCH = %v", w[0].Body)
	}
	for _, c := range w {
		if strings.HasPrefix(c.Path, "/repos/omnigent-ai/") && strings.Contains(c.Path, "/git/") {
			t.Fatalf("a ref write went upstream: %s %s", c.Method, c.Path)
		}
	}
	wantRef(t, h, shaA)
}

type listTamper struct {
	base http.RoundTripper
	path string
	add  map[string]any
}

func (lt *listTamper) RoundTrip(r *http.Request) (*http.Response, error) {
	res, err := lt.base.RoundTrip(r)
	if err != nil || r.URL.Path != lt.path || r.Method != http.MethodGet {
		return res, err
	}
	raw, err := io.ReadAll(res.Body)
	_ = res.Body.Close()
	if err != nil {
		return nil, err
	}
	var items []any
	if err := json.Unmarshal(raw, &items); err != nil {
		return nil, err
	}
	out, _ := json.Marshal(append(items, lt.add))
	res.Body, res.ContentLength = io.NopCloser(bytes.NewReader(out)), int64(len(out))
	res.Header.Del("Content-Length")
	return res, nil
}

func TestPublishIgnoresUpstreamPullRequestsFromAnotherHead(t *testing.T) {
	for name, c := range map[string]struct {
		existing bool
		decoy    map[string]any
	}{
		"open":   {true, map[string]any{"number": 900, "state": "open", "head": map[string]any{"label": "someone:upstream/pr-7"}}},
		"closed": {false, map[string]any{"number": 901, "state": "closed", "closed_at": "2030-01-01T00:00:00Z", "head": map[string]any{"label": "lkshrk:upstream/pr-70"}}},
	} {
		t.Run(name, func(t *testing.T) {
			h := publishable(t, shaA, nil)
			if c.existing {
				h.gh.addPull(testUpstream, fakePR{fakeIssue: &fakeIssue{title: prTitle, user: testOwner}, headRepo: testFork, headRef: publishRef, base: "main", sha: shaB})
			}
			h.clock.advance(time.Minute)
			approve(h, shaA)
			gh := h.poller.gh
			gh.http = &http.Client{Transport: &listTamper{base: gh.http.Transport, path: upPulls, add: c.decoy}}
			wantOutcome(t, publishPR(t, h), outcomePublished)
			wantRef(t, h, shaA)
		})
	}
}

func TestPublishTreatsAnUndatedCloseAsAfterTheApproval(t *testing.T) {
	for name, older := range map[string]bool{"alone": false, "after a dated close": true} {
		t.Run(name, func(t *testing.T) {
			h := publishable(t, shaA, nil)
			if older {
				pr := h.gh.addPull(testUpstream, fakePR{fakeIssue: &fakeIssue{title: prTitle, user: testOwner}, headRepo: testFork, headRef: publishRef, base: "main", sha: shaB})
				h.gh.closeIssue(testUpstream, pr.number, maintainer)
			}
			h.clock.advance(time.Minute)
			approve(h, shaA)
			gh := h.poller.gh
			undated := map[string]any{"number": 902, "state": "closed", "closed_at": nil, "head": map[string]any{"label": publishHead}}
			gh.http = &http.Client{Transport: &listTamper{base: gh.http.Transport, path: upPulls, add: undated}}
			before := len(h.gh.writes())
			r := publishPR(t, h)
			wantOutcome(t, r, outcomeClosedUpstream)
			wantNoWritesSince(t, h, before)
			if r.upstream != 902 {
				t.Fatalf("result names #%d, want the undated #902", r.upstream)
			}
		})
	}
}

func TestPublishRetriesOnAnUnexpectedBranchRead(t *testing.T) {
	for name, edit := range map[string]func(map[string]any){
		"another ref":  func(v map[string]any) { v["ref"] = "refs/heads/upstream/pr-70" },
		"no full SHA":  func(v map[string]any) { v["object"].(map[string]any)["sha"] = "abc" },
		"an empty SHA": func(v map[string]any) { v["object"].(map[string]any)["sha"] = "" },
	} {
		t.Run(name, func(t *testing.T) {
			h := publishable(t, shaA, nil)
			first := publishedOnce(t, h)
			approveNewHead(h, shaB, "")
			tamper(h, "/repos/lkshrk/omnigent/git/ref/heads/upstream/pr-7", edit)
			before := len(h.gh.writes())
			wantOutcome(t, publishPR(t, h), outcomeRetry)
			wantNoWritesSince(t, h, before)
			wantRef(t, h, shaA)
			if up := onlyUpstream(t, h); up.number != first.upstream || up.sha != shaA {
				t.Fatalf("upstream PR = %+v", up)
			}
		})
	}
}
