package main

import (
	"bytes"
	"context"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"testing"
	"time"
)

const (
	forkRepo     = "/repos/lkshrk/omnigent"
	forkPulls    = "/repos/lkshrk/omnigent/pulls"
	forkPR       = "/repos/lkshrk/omnigent/pulls/7"
	forkReviews  = "/repos/lkshrk/omnigent/pulls/7/reviews"
	hostileTitle = "feat(web): @lkshrk [click](https://evil.example) **evil**"
)

func pollTimes(h *harness, n int) {
	for range n {
		h.poller.poll(context.Background())
		h.clock.advance(time.Minute)
	}
}

func messagesSince(h *harness, before int) []string {
	return h.signal.messages()[before:]
}

func ownerNotices(msgs []string) []string {
	var out []string
	for _, m := range msgs {
		if !strings.HasPrefix(m, "Contribution ready") {
			out = append(out, m)
		}
	}
	return out
}

func wantNoAttackerText(t *testing.T, msgs []string) {
	t.Helper()
	for _, m := range msgs {
		for _, bad := range []string{testPAT, "Adds the thing", "Tested with the web suite", "@", "evil", "add the thing", "mallory"} {
			if strings.Contains(m, bad) {
				t.Errorf("owner message %q contains %q", m, bad)
			}
		}
	}
}

// announcedAndApproved announces PR #7 through the poll loop and has the owner approve it in the GitHub UI.
func announcedAndApproved(t *testing.T, h *harness) fakeReview {
	t.Helper()
	h.poller.poll(context.Background())
	if readyMessages(h) != 1 {
		t.Fatalf("messages = %q, want one announcement", h.signal.messages())
	}
	h.clock.advance(time.Minute)
	return approve(h, shaA)
}

func TestPollPublishesAHeadThatAnnounceSkips(t *testing.T) {
	h := publishable(t, shaA, nil)
	announcedAndApproved(t, h)
	before := len(h.signal.messages())

	pollTimes(h, 1)

	up := onlyUpstream(t, h)
	msgs := messagesSince(h, before)
	if len(msgs) != 1 || !strings.Contains(msgs[0], "Published "+testFork+"#7 at "+shaA) || !strings.Contains(msgs[0], "https://github.com/omnigent-ai/omnigent/pull/"+itoa(up.number)) {
		t.Fatalf("messages = %q, want one naming upstream #%d", msgs, up.number)
	}
	wantNoAttackerText(t, msgs)
	writes := len(h.gh.writes())
	pollTimes(h, 3)
	wantNoWritesSince(t, h, writes)
	if msgs := messagesSince(h, before); len(msgs) != 1 {
		t.Fatalf("messages = %q, want no repeat", msgs)
	}
}

func TestASettledPullRequestNeedsOnlyItsReviewsAndThePullRequest(t *testing.T) {
	h := publishable(t, shaA, nil)
	announcedAndApproved(t, h)
	pollTimes(h, 2)
	calls, billed := len(h.gh.allCalls()), h.gh.billedRequests()

	pollTimes(h, 1)

	var got []string
	for _, c := range h.gh.allCalls()[calls:] {
		got = append(got, c.Method+" "+c.Path)
		if c.IfNoneMatch == "" || c.Status != http.StatusNotModified {
			t.Errorf("%s %s sent If-None-Match %q and got %d, want a 304 revalidation", c.Method, c.Path, c.IfNoneMatch, c.Status)
		}
	}
	want := []string{"GET " + forkRepo, "GET " + forkPulls, "GET " + forkReviews, "GET " + forkPR}
	if strings.Join(got, ",") != strings.Join(want, ",") {
		t.Fatalf("calls = %v, want %v", got, want)
	}
	if h.gh.billedRequests() != billed {
		t.Fatalf("billed requests grew from %d to %d", billed, h.gh.billedRequests())
	}
}

func TestA304ReusesTheCachedResponse(t *testing.T) {
	h := publishable(t, shaA, nil)
	gh := h.poller.gh
	first, err := gh.pull(context.Background(), testFork, prNumber)
	if err != nil {
		t.Fatal(err)
	}
	second, err := gh.pull(context.Background(), testFork, prNumber)
	if err != nil {
		t.Fatal(err)
	}
	calls := h.gh.allCalls()
	if last := calls[len(calls)-1]; last.Status != http.StatusNotModified || second.Title != first.Title || second.Head.SHA != shaA {
		t.Fatalf("second read = %+v (status %d), want the cached pull request", second, last.Status)
	}
	h.gh.rename(testFork, prNumber, "feat(web): renamed", testAgent)
	third, err := gh.pull(context.Background(), testFork, prNumber)
	if err != nil || third.Title != "feat(web): renamed" {
		t.Fatalf("third read = %q, %v; want the new title", third.Title, err)
	}
}

func TestRefusalsAreToldOncePerReviewAndReason(t *testing.T) {
	t.Run("a hard refusal whose dismissal keeps failing", func(t *testing.T) {
		h := publishable(t, shaA, nil)
		rv := announcedAndApproved(t, h)
		h.gh.rename(testFork, prNumber, "feat(web): renamed after the approval", testAgent)
		h.clock.advance(time.Minute)
		dismissals := "/repos/lkshrk/omnigent/pulls/7/reviews/" + itoa(int(rv.id)) + "/dismissals"
		h.gh.fail(http.MethodPut, dismissals, http.StatusBadGateway)
		before := len(h.signal.messages())

		pollTimes(h, 3)

		msgs := ownerNotices(messagesSince(h, before))
		if len(msgs) != 1 || !strings.Contains(msgs[0], "Not publishing "+testFork+"#7") || !strings.Contains(msgs[0], "the title was renamed at or after the approval") || !strings.Contains(msgs[0], "could not be dismissed") {
			t.Fatalf("messages = %q, want one refusal", msgs)
		}
		if h.gh.callCount(http.MethodPut, dismissals) != 3 {
			t.Fatalf("dismissal attempts = %d, want one per poll", h.gh.callCount(http.MethodPut, dismissals))
		}
		wantNoAttackerText(t, msgs)
	})
	t.Run("a hard refusal that dismisses", func(t *testing.T) {
		h := publishable(t, shaA, nil)
		announcedAndApproved(t, h)
		h.gh.rename(testFork, prNumber, "feat(web): renamed after the approval", testAgent)
		h.clock.advance(time.Minute)
		before := len(h.signal.messages())

		pollTimes(h, 3)

		msgs := ownerNotices(messagesSince(h, before))
		if len(msgs) != 1 || !strings.Contains(msgs[0], "the title was renamed at or after the approval") || !strings.Contains(msgs[0], "The approval was dismissed") {
			t.Fatalf("messages = %q, want one refusal naming the dismissal", msgs)
		}
	})
	t.Run("a taken upstream head", func(t *testing.T) {
		h := publishable(t, shaA, nil)
		announcedAndApproved(t, h)
		thirdPartyPull(h, "main")
		before := len(h.signal.messages())

		pollTimes(h, 3)

		msgs := ownerNotices(messagesSince(h, before))
		if len(msgs) != 1 || !strings.Contains(msgs[0], headTakenReason) {
			t.Fatalf("messages = %q, want one notice", msgs)
		}
		if n := h.gh.callCount(http.MethodPost, upPulls); n != 3 {
			t.Fatalf("upstream POSTs = %d, want one per poll", n)
		}
		wantNoAttackerText(t, msgs)
	})
	t.Run("a transient refusal is not told", func(t *testing.T) {
		h := publishable(t, shaA, nil)
		announcedAndApproved(t, h)
		setCI(h, shaA, []fakeRun{{status: "in_progress", path: ciPath}})
		before := len(h.signal.messages())
		pollTimes(h, 3)
		if msgs := messagesSince(h, before); len(msgs) != 0 {
			t.Fatalf("messages = %q, want none while CI runs", msgs)
		}
	})
}

func closedUpstreamAfterReapproval(t *testing.T, h *harness) {
	t.Helper()
	announcedAndApproved(t, h)
	pollTimes(h, 1)
	up := onlyUpstream(t, h)
	h.gh.closeIssue(testUpstream, up.number, maintainer)
	h.clock.advance(time.Minute)
	approve(h, shaA)
}

func TestAClosedUpstreamPullRequestIsToldOnce(t *testing.T) {
	h := publishable(t, shaA, nil)
	closedUpstreamAfterReapproval(t, h)
	up := onlyUpstream(t, h)
	before := len(h.signal.messages())

	pollTimes(h, 3)

	msgs := messagesSince(h, before)
	if len(msgs) != 1 || !strings.Contains(msgs[0], "closed or merged") || !strings.Contains(msgs[0], "https://github.com/omnigent-ai/omnigent/pull/"+itoa(up.number)) {
		t.Fatalf("messages = %q, want one naming the closed upstream pull request", msgs)
	}
	wantNoAttackerText(t, msgs)
}

func TestCurrentAfterARetryTellsTheOwnerTheCodeIsPublished(t *testing.T) {
	h := publishable(t, shaA, nil)
	announcedAndApproved(t, h)
	h.gh.failWith(http.MethodPost, upPulls, fakeFailure{Status: http.StatusBadGateway, After: true})
	before := len(h.signal.messages())
	pollTimes(h, 1)
	if msgs := messagesSince(h, before); len(msgs) != 0 {
		t.Fatalf("messages = %q, want none for the failed write", msgs)
	}
	h.gh.fail(http.MethodPost, upPulls, 0)

	pollTimes(h, 3)

	up := onlyUpstream(t, h)
	msgs := messagesSince(h, before)
	if len(msgs) != 1 || !strings.Contains(msgs[0], "code is published") || !strings.Contains(msgs[0], "may not be updated") || !strings.Contains(msgs[0], "https://github.com/omnigent-ai/omnigent/pull/"+itoa(up.number)) {
		t.Fatalf("messages = %q, want one saying the code is published", msgs)
	}
}

func TestANewApprovalOfAPublishedCommitSaysTheTextWasNotChanged(t *testing.T) {
	h := publishable(t, shaA, nil)
	announcedAndApproved(t, h)
	pollTimes(h, 1)
	up := onlyUpstream(t, h)
	h.clock.advance(time.Minute)
	approve(h, shaA)
	before := len(h.signal.messages())
	pollTimes(h, 3)
	msgs := messagesSince(h, before)
	if len(msgs) != 1 || msgs[0] != testFork+"#7 at "+shaA+" is already upstream: https://github.com/omnigent-ai/omnigent/pull/"+itoa(up.number)+"; the title and body were not changed." {
		t.Fatalf("messages = %q, want one saying the text was not changed", msgs)
	}
}

func TestARestartDoesNotReannounceOrRetellASettledPullRequest(t *testing.T) {
	for name, setup := range map[string]func(t *testing.T, h *harness){
		"published": func(t *testing.T, h *harness) {
			announcedAndApproved(t, h)
			pollTimes(h, 1)
		},
		"closed upstream": func(t *testing.T, h *harness) {
			closedUpstreamAfterReapproval(t, h)
			pollTimes(h, 1)
		},
	} {
		t.Run(name, func(t *testing.T) {
			h := publishable(t, shaA, nil)
			setup(t, h)
			before, writes := len(h.signal.messages()), len(h.gh.writes())
			restart(h)

			pollTimes(h, 3)

			if msgs := messagesSince(h, before); len(msgs) != 0 {
				t.Fatalf("messages after the restart = %q, want none", msgs)
			}
			wantNoWritesSince(t, h, writes)
		})
	}
	t.Run("an approval that was not published is announced again", func(t *testing.T) {
		h := publishable(t, shaA, nil)
		announcedAndApproved(t, h)
		restart(h)
		pollTimes(h, 2)
		if n := readyMessages(h); n != 2 {
			t.Fatalf("announcements = %d, want one more after the restart", n)
		}
	})
}

func TestOwnerMessagesCarryOnlyAShapeCheckedURL(t *testing.T) {
	h := publishable(t, shaA, func(pr *fakePR) { pr.title = hostileTitle })
	announcedAndApproved(t, h)
	tamperPOST(h, upPulls, func(v map[string]any) { v["number"] = 0 })
	before := len(h.signal.messages())

	pollTimes(h, 1)

	msgs := messagesSince(h, before)
	if len(msgs) != 1 || !strings.Contains(msgs[0], "Published "+testFork+"#7") || strings.Contains(msgs[0], "://") {
		t.Fatalf("messages = %q, want one without a malformed link", msgs)
	}
	wantNoAttackerText(t, msgs)
}

func TestDecideSaysPublishingFollows(t *testing.T) {
	h := publishable(t, shaA, nil)
	h.poller.poll(context.Background())
	code := codeIn(t, h.signal.messages()[0])

	if rec := approveWith(h, code); rec.Code != http.StatusOK {
		t.Fatalf("answer = %d", rec.Code)
	}

	msgs := h.signal.messages()
	done := msgs[len(msgs)-1]
	if !strings.Contains(done, "Approved "+testFork+"#7 at "+shaA) || !strings.Contains(done, "Publishing upstream follows") || strings.Contains(done, "publish-upstream.yml") {
		t.Fatalf("done text = %q", done)
	}
}

func TestDecideNamesAKnownUpstreamPullRequest(t *testing.T) {
	h := publishable(t, shaA, nil)
	announcedAndApproved(t, h)
	pollTimes(h, 1)
	up := onlyUpstream(t, h)
	moveHead(h, shaB)
	setBothCompares(h, shaB, "ahead", 1, map[string]any{"filename": "web/src/thing.ts"})
	pollTimes(h, 1)
	msgs := h.signal.messages()
	code := codeIn(t, msgs[len(msgs)-1])

	if rec := approveWith(h, code); rec.Code != http.StatusOK {
		t.Fatalf("answer = %d", rec.Code)
	}

	msgs = h.signal.messages()
	done := msgs[len(msgs)-1]
	if !strings.Contains(done, "Approved "+testFork+"#7 at "+shaB) || !strings.Contains(done, "the last known upstream pull request is https://github.com/omnigent-ai/omnigent/pull/"+itoa(up.number)) {
		t.Fatalf("done text = %q, want the last known upstream pull request", done)
	}
}

func waitFor(t *testing.T, what string, cond func() bool) {
	t.Helper()
	deadline := time.Now().Add(10 * time.Second)
	for !cond() {
		if time.Now().After(deadline) {
			t.Fatalf("timed out waiting for %s", what)
		}
		time.Sleep(5 * time.Millisecond)
	}
}

func published(h *harness) int {
	n := 0
	for _, m := range h.signal.messages() {
		if strings.HasPrefix(m, "Published ") {
			n++
		}
	}
	return n
}

func TestASignalApprovalWakesTheLoopWhichPublishesOnce(t *testing.T) {
	h := publishable(t, shaA, nil)
	ctx, cancel := context.WithCancel(context.Background())
	done := make(chan struct{})
	go func() { defer close(done); h.poller.run(ctx, time.Hour) }()
	defer func() { cancel(); <-done }()
	waitFor(t, "the announcement", func() bool { return readyMessages(h) == 1 })
	code := codeIn(t, h.signal.messages()[0])

	var wg sync.WaitGroup
	for range 4 {
		wg.Add(1)
		go func() { defer wg.Done(); h.poller.wake() }()
	}
	wg.Add(1)
	go func() {
		defer wg.Done()
		if rec := approveWith(h, code); rec.Code != http.StatusOK {
			t.Errorf("answer = %d", rec.Code)
		}
	}()
	wg.Wait()
	waitFor(t, "the publish", func() bool { return published(h) == 1 })
	h.poller.wake()
	waitFor(t, "a later poll", func() bool { return h.gh.callCount(http.MethodGet, forkPulls) >= 3 })

	if n := h.gh.callCount(http.MethodPost, upPulls); n != 1 || published(h) != 1 {
		t.Fatalf("upstream POSTs = %d, published messages = %d; want one each", n, published(h))
	}
	onlyUpstream(t, h)
}

func TestASignalApprovalKicksTheLoop(t *testing.T) {
	h := publishable(t, shaA, nil)
	ctx, cancel := context.WithCancel(context.Background())
	done := make(chan struct{})
	go func() { defer close(done); h.poller.run(ctx, time.Hour) }()
	defer func() { cancel(); <-done }()
	waitFor(t, "the announcement", func() bool { return readyMessages(h) == 1 })

	if rec := approveWith(h, codeIn(t, h.signal.messages()[0])); rec.Code != http.StatusOK {
		t.Fatalf("answer = %d", rec.Code)
	}

	waitFor(t, "the publish", func() bool { return published(h) == 1 })
}

func TestWakeNeverBlocks(t *testing.T) {
	h := newHarness(t)
	done := make(chan struct{})
	go func() {
		defer close(done)
		for range 10 {
			h.poller.wake()
		}
	}()
	select {
	case <-done:
	case <-time.After(2 * time.Second):
		t.Fatal("wake blocked without a running loop")
	}
}

type stallTransport struct {
	base http.RoundTripper
	path string
}

func (st *stallTransport) RoundTrip(r *http.Request) (*http.Response, error) {
	if r.URL.Path == st.path {
		<-r.Context().Done()
		return nil, r.Context().Err()
	}
	return st.base.RoundTrip(r)
}

func TestOneSlowForkDoesNotStarveTheOthers(t *testing.T) {
	h := newHarness(t)
	h.gh.readyPR(7, shaA)
	slow := h.poller.forks[0]
	slow.Fork, slow.Upstream = "lkshrk/slow", "slow-ai/slow"
	h.poller.forks = append([]forkConfig{slow}, h.poller.forks...)
	h.poller.forkTimeout = 100 * time.Millisecond
	gh := h.poller.gh
	gh.http = &http.Client{Transport: &stallTransport{base: gh.http.Transport, path: "/repos/lkshrk/slow"}}
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()

	h.poller.poll(ctx)

	if n := announcements(h); n != 1 {
		t.Fatalf("announcements = %d, want the healthy fork announced", n)
	}
}

type hookNotifier struct {
	inner notifier
	after func()
}

func (n *hookNotifier) Notify(ctx context.Context, text string) error {
	err := n.inner.Notify(ctx, text)
	if err == nil && strings.HasPrefix(text, "Contribution ready") {
		n.after()
	}
	return err
}

type dropReviewTransport struct {
	base http.RoundTripper
	mu   sync.Mutex
	arm  bool
	drop int64
}

func (dt *dropReviewTransport) RoundTrip(r *http.Request) (*http.Response, error) {
	dt.mu.Lock()
	armed := dt.arm && r.Method == http.MethodGet && r.URL.Path == forkReviews
	if armed {
		dt.arm = false
	}
	dt.mu.Unlock()
	if !armed {
		return dt.base.RoundTrip(r)
	}
	r = r.Clone(r.Context())
	r.Header.Del("If-None-Match")
	res, err := dt.base.RoundTrip(r)
	if err != nil {
		return res, err
	}
	raw, err := io.ReadAll(res.Body)
	_ = res.Body.Close()
	if err != nil {
		return nil, err
	}
	var items []map[string]any
	if err := json.Unmarshal(raw, &items); err != nil {
		return nil, err
	}
	var kept []map[string]any
	for _, it := range items {
		if int64(it["id"].(float64)) != dt.drop {
			kept = append(kept, it)
		}
	}
	out, _ := json.Marshal(kept)
	res.Body, res.ContentLength = io.NopCloser(bytes.NewReader(out)), int64(len(out))
	res.Header.Del("Content-Length")
	res.Header.Del("ETag")
	return res, nil
}

func TestAReannouncementBindsOnlyAboveTheReviewThatAskedForIt(t *testing.T) {
	h := publishable(t, shaA, nil)
	h.poller.poll(context.Background())
	h.clock.advance(time.Minute)
	h.gh.editBody(testFork, prNumber, "Adds the thing, edited before the approval.", testAgent)
	h.clock.advance(time.Minute)
	rv := h.gh.addReview(prNumber, testOwner, "APPROVED", shaA)
	h.clock.advance(time.Minute)
	gh := h.poller.gh
	dt := &dropReviewTransport{base: gh.http.Transport, drop: rv.id}
	gh.http = &http.Client{Transport: dt}
	h.poller.notify = &hookNotifier{inner: h.poller.notify, after: func() { dt.mu.Lock(); dt.arm = true; dt.mu.Unlock() }}

	pollTimes(h, 3)

	if readyMessages(h) != 2 {
		t.Fatalf("announcements = %d, want one re-announcement", readyMessages(h))
	}
	if a, ok := h.codes.announcementOf(prK, shaA); !ok || a.lastReview < rv.id {
		t.Fatalf("announcement = %+v, %v; want it bound above review %d", a, ok, rv.id)
	}
	if n := len(upstreamPulls(h)); n != 0 {
		t.Fatalf("published %d upstream pull requests from an approval that predates the re-announcement", n)
	}
}

func TestAFailingBindListingDoesNotReannounceEveryPoll(t *testing.T) {
	h := publishable(t, shaA, nil)
	failing := true
	h.poller.notify = &hookNotifier{inner: h.poller.notify, after: func() {
		if failing {
			h.gh.failWith(http.MethodGet, forkReviews, fakeFailure{Status: http.StatusBadGateway, Times: 1})
		}
	}}

	pollTimes(h, 10)
	if n := readyMessages(h); n != 2 {
		t.Fatalf("announcements over ten polls = %d, want the first and one retry", n)
	}
	h.clock.advance(codeTTL)
	pollTimes(h, 1)
	if n := readyMessages(h); n != 3 {
		t.Fatalf("announcements after the hold = %d, want 3", n)
	}
	failing = false
	h.clock.advance(2 * codeTTL)
	pollTimes(h, 5)
	if n := readyMessages(h); n != 4 {
		t.Fatalf("announcements after recovery = %d, want 4", n)
	}
	if _, ok := h.codes.announcementOf(prK, shaA); !ok {
		t.Fatal("the recovered announcement was not recorded")
	}
}

func TestPublishDecisionSaysWhenTheForkCouldNotBeChecked(t *testing.T) {
	for name, tc := range map[string]struct {
		setup  func(h *harness)
		reason string
	}{
		"dropped connection": {func(h *harness) { h.gh.fail(http.MethodGet, forkRepo, fakeDrop) }, "could not check the fork"},
		"undecodable":        {func(h *harness) { tamperRaw(h, forkRepo, "not json") }, "could not check the fork"},
		"another owner":      {func(h *harness) { h.gh.update(func() { h.gh.fork.owner = "someone" }) }, "the fork does not match the registry"},
		"another parent":     {func(h *harness) { h.gh.update(func() { h.gh.fork.parent = "someone/omnigent" }) }, "the fork does not match the registry"},
	} {
		t.Run(name, func(t *testing.T) {
			h := publishable(t, shaA, nil)
			approve(h, shaA)
			tc.setup(h)
			d := decidePR(t, h)
			if d.verdict != verdictTransient || d.reason != tc.reason {
				t.Fatalf("decision = %v %q, want transient %q", d.verdict, d.reason, tc.reason)
			}
		})
	}
}

type rawTransport struct {
	base http.RoundTripper
	path string
	body string
}

func (rt *rawTransport) RoundTrip(r *http.Request) (*http.Response, error) {
	if r.URL.Path != rt.path {
		return rt.base.RoundTrip(r)
	}
	return &http.Response{StatusCode: http.StatusOK, Header: http.Header{}, Body: io.NopCloser(strings.NewReader(rt.body)), Request: r}, nil
}

func tamperRaw(h *harness, path, body string) {
	gh := h.poller.gh
	gh.http = &http.Client{Transport: &rawTransport{base: gh.http.Transport, path: path, body: body}}
}

type postTamper struct {
	base http.RoundTripper
	path string
	edit func(map[string]any)
}

func (pt *postTamper) RoundTrip(r *http.Request) (*http.Response, error) {
	if r.Method != http.MethodPost || r.URL.Path != pt.path {
		return pt.base.RoundTrip(r)
	}
	return (&tamperTransport{base: pt.base, path: pt.path, edit: pt.edit}).RoundTrip(r)
}

func tamperPOST(h *harness, path string, edit func(map[string]any)) {
	gh := h.poller.gh
	gh.http = &http.Client{Transport: &postTamper{base: gh.http.Transport, path: path, edit: edit}}
}

func TestAPublishedNoticeThatFailedIsSentOnTheNextPoll(t *testing.T) {
	h := publishable(t, shaA, nil)
	announcedAndApproved(t, h)
	before := len(h.signal.messages())
	h.signal.mu.Lock()
	h.signal.fail = true
	h.signal.mu.Unlock()
	pollTimes(h, 1)
	h.signal.mu.Lock()
	h.signal.fail = false
	h.signal.mu.Unlock()

	pollTimes(h, 2)

	if msgs := messagesSince(h, before); len(msgs) != 1 || !strings.HasPrefix(msgs[0], "Published ") {
		t.Fatalf("messages = %q, want the publish notice once", msgs)
	}
}

func setSignalDown(h *harness, down bool) {
	h.signal.mu.Lock()
	defer h.signal.mu.Unlock()
	h.signal.fail = down
}

func countContaining(msgs []string, part string) int {
	n := 0
	for _, m := range msgs {
		if strings.Contains(m, part) {
			n++
		}
	}
	return n
}

func TestOwnerNoticesWaitForSignal(t *testing.T) {
	t.Run("a dismissed refusal", func(t *testing.T) {
		h := publishable(t, shaA, nil)
		announcedAndApproved(t, h)
		h.gh.rename(testFork, prNumber, "feat(web): renamed after the approval", testAgent)
		h.clock.advance(time.Minute)
		before := len(h.signal.messages())
		setSignalDown(h, true)
		pollTimes(h, 1)
		setSignalDown(h, false)

		pollTimes(h, 3)

		if n := countContaining(messagesSince(h, before), "The approval was dismissed"); n != 1 {
			t.Fatalf("messages = %q, want the refusal once", messagesSince(h, before))
		}
	})
	t.Run("a closed upstream pull request", func(t *testing.T) {
		h := publishable(t, shaA, nil)
		closedUpstreamAfterReapproval(t, h)
		before := len(h.signal.messages())
		setSignalDown(h, true)
		pollTimes(h, 1)
		setSignalDown(h, false)

		pollTimes(h, 3)

		if n := countContaining(messagesSince(h, before), "closed or merged"); n != 1 {
			t.Fatalf("messages = %q, want the close once", messagesSince(h, before))
		}
	})
	t.Run("a recurring notice while Signal is down", func(t *testing.T) {
		h := publishable(t, shaA, nil)
		announcedAndApproved(t, h)
		thirdPartyPull(h, "main")
		before := len(h.signal.messages())
		setSignalDown(h, true)
		pollTimes(h, 3)
		setSignalDown(h, false)

		pollTimes(h, 2)

		if n := countContaining(messagesSince(h, before), headTakenReason); n != 1 {
			t.Fatalf("messages = %q, want the notice once", messagesSince(h, before))
		}
	})
	t.Run("a pull request that stopped being ready", func(t *testing.T) {
		h := publishable(t, shaA, nil)
		announcedAndApproved(t, h)
		h.gh.rename(testFork, prNumber, "feat(web): renamed after the approval", testAgent)
		h.clock.advance(time.Minute)
		before := len(h.signal.messages())
		setSignalDown(h, true)
		pollTimes(h, 1)
		h.gh.unlabel(testFork, prNumber, readyLabel, testAgent)
		setSignalDown(h, false)

		pollTimes(h, 2)

		if n := countContaining(messagesSince(h, before), "The approval was dismissed"); n != 1 {
			t.Fatalf("messages = %q, want the refusal once", messagesSince(h, before))
		}
	})
}

func TestAChangeAfterPublishingNeverDismissesOrRefuses(t *testing.T) {
	const later = "; later changes to the pull request are not published."
	const unchanged = "; the title and body were not changed."
	for name, tc := range map[string]struct {
		change           func(h *harness)
		first, reapprove string
	}{
		"body edit": {func(h *harness) { h.gh.editBody(testFork, prNumber, "Adds the thing, edited later.", testAgent) }, later, unchanged},
		"rename":    {func(h *harness) { h.gh.rename(testFork, prNumber, "feat(web): renamed later", testAgent) }, later, unchanged},
		"base changed and back": {func(h *harness) {
			h.gh.setRef(testFork, "next", shaMain)
			h.gh.changeBase(prNumber, "next", testAgent)
			h.gh.changeBase(prNumber, "main", testAgent)
		}, "", later},
	} {
		t.Run(name, func(t *testing.T) {
			h := publishable(t, shaA, nil)
			announcedAndApproved(t, h)
			pollTimes(h, 1)
			up := onlyUpstream(t, h)
			already := testFork + "#7 at " + shaA + " is already upstream: https://github.com/omnigent-ai/omnigent/pull/" + itoa(up.number)
			tc.change(h)
			h.clock.advance(time.Minute)
			before, writes := len(h.signal.messages()), len(h.gh.writes())

			pollTimes(h, 3)

			wantNoWritesSince(t, h, writes)
			var want []string
			if tc.first != "" {
				want = append(want, already+tc.first)
			}
			if msgs := messagesSince(h, before); strings.Join(msgs, "|") != strings.Join(want, "|") {
				t.Fatalf("messages = %q, want %q", msgs, want)
			}
			if h.gh.reviewsOf(prNumber)[0].state != "APPROVED" {
				t.Fatal("the published approval was dismissed")
			}

			h.clock.advance(time.Minute)
			approve(h, shaA)
			pollTimes(h, 2)
			wantNoWritesSince(t, h, writes)
			want = append(want, already+tc.reapprove)
			if msgs := messagesSince(h, before); strings.Join(msgs, "|") != strings.Join(want, "|") {
				t.Fatalf("messages after the re-approval = %q, want %q", msgs, want)
			}
		})
	}
}

func TestARestartRetellsAnUpstreamCloseAfterTheApprovalOnce(t *testing.T) {
	h := publishable(t, shaA, nil)
	announcedAndApproved(t, h)
	pollTimes(h, 1)
	up := onlyUpstream(t, h)
	moveHead(h, shaB)
	setBothCompares(h, shaB, "ahead", 1, map[string]any{"filename": "web/src/thing.ts"})
	approve(h, shaB)
	h.clock.advance(time.Minute)
	h.gh.closeIssue(testUpstream, up.number, maintainer)
	h.clock.advance(time.Minute)
	before := len(h.signal.messages())
	pollTimes(h, 2)
	if n := countContaining(messagesSince(h, before), "closed or merged after the approval"); n != 1 {
		t.Fatalf("messages = %q, want the close once", messagesSince(h, before))
	}
	restart(h)

	pollTimes(h, 3)

	if n := countContaining(messagesSince(h, before), "closed or merged after the approval"); n != 2 {
		t.Fatalf("messages = %q, want one resend after the restart", messagesSince(h, before))
	}
}

func TestAListingHoldDoesNotDelayANewHead(t *testing.T) {
	h := publishable(t, shaA, nil)
	h.poller.notify = &hookNotifier{inner: h.poller.notify, after: func() {
		h.gh.failWith(http.MethodGet, forkReviews, fakeFailure{Status: http.StatusBadGateway, Times: 1})
	}}
	pollTimes(h, 3)
	if n := readyMessages(h); n != 2 {
		t.Fatalf("announcements = %d, want 2 before the hold", n)
	}
	moveHead(h, shaB)
	setBothCompares(h, shaB, "ahead", 1, map[string]any{"filename": "web/src/thing.ts"})

	pollTimes(h, 1)

	if n := countContaining(h.signal.messages(), "Head: "+shaB); n != 1 {
		t.Fatalf("messages = %q, want the new head announced at once", h.signal.messages())
	}
}

func TestDecideKicksAfterTellingTheOwner(t *testing.T) {
	h := publishable(t, shaA, nil)
	h.poller.poll(context.Background())
	code := codeIn(t, h.signal.messages()[0])
	told := -1
	gh := h.poller.gh
	api := (&decider{token: testShared, codes: h.codes, gh: gh, forks: h.poller.forks, notify: h.poller.notify, links: h.poller.links,
		kick: func() { told = countContaining(h.signal.messages(), "Approved ") }}).routes()
	req := httptest.NewRequest(http.MethodPost, "/v1/decisions", strings.NewReader(`{"action":"approve","code":"`+code+`"}`))
	req.Header.Set("Authorization", "Bearer "+testShared)
	rec := httptest.NewRecorder()
	api.ServeHTTP(rec, req)
	if rec.Code != http.StatusOK || told != 1 {
		t.Fatalf("answer = %d, done messages at the kick = %d; want the owner told first", rec.Code, told)
	}
}

func TestARecurringReadErrorIsLoggedAgainAfterARecovery(t *testing.T) {
	h := publishable(t, shaA, nil)
	h.gh.fail(http.MethodGet, forkReviews, http.StatusBadGateway)
	pollTimes(h, 2)
	h.gh.fail(http.MethodGet, forkReviews, 0)
	pollTimes(h, 1)
	h.gh.fail(http.MethodGet, forkReviews, http.StatusBadGateway)
	pollTimes(h, 2)
	if n := strings.Count(h.logs(), "publish "+testFork+"#7: GitHub GET "+forkReviews); n != 2 {
		t.Fatalf("read error logged %d times, want once per outage:\n%s", n, h.logs())
	}
}

func TestOneSlowPullRequestDoesNotStarveTheOthers(t *testing.T) {
	h := newHarness(t)
	h.gh.readyPR(7, shaA)
	h.gh.readyPR(8, shaA)
	h.poller.prTimeout = 100 * time.Millisecond
	h.poller.forkTimeout = time.Second
	gh := h.poller.gh
	gh.http = &http.Client{Transport: &stallTransport{base: gh.http.Transport, path: "/repos/lkshrk/omnigent/pulls/8/reviews"}}

	h.poller.poll(context.Background())

	if n := countContaining(h.signal.messages(), testFork+"#7"); n != 1 {
		t.Fatalf("messages = %q, want #7 announced", h.signal.messages())
	}
}

func failBindListing(h *harness) {
	h.poller.notify = &hookNotifier{inner: h.poller.notify, after: func() {
		h.gh.failWith(http.MethodGet, forkReviews, fakeFailure{Status: http.StatusBadGateway, Times: 1})
	}}
}

func lastCode(t *testing.T, h *harness) string {
	t.Helper()
	msgs := h.signal.messages()
	for i := len(msgs) - 1; i >= 0; i-- {
		if strings.HasPrefix(msgs[i], "Contribution ready") {
			return codeIn(t, msgs[i])
		}
	}
	t.Fatal("no announcement")
	return ""
}

func TestRepliesPromiseANewCodeTruthfully(t *testing.T) {
	const minute = "a new code follows within a minute"
	t.Run("expired, held", func(t *testing.T) {
		h := publishable(t, shaA, nil)
		failBindListing(h)
		pollTimes(h, 2)
		h.clock.advance(codeTTL)
		pollTimes(h, 1)
		if readyMessages(h) != 3 || !h.codes.held(prK, shaA) {
			t.Fatalf("announcements = %d, held = %v; want 3 and a hold", readyMessages(h), h.codes.held(prK, shaA))
		}
		h.clock.advance(codeTTL)
		approveWith(h, lastCode(t, h))
		msgs := h.signal.messages()
		if done := msgs[len(msgs)-1]; !strings.Contains(done, "expired") || !strings.HasSuffix(done, heldPromise) || strings.Contains(done, minute) {
			t.Fatalf("reply = %q, want the held promise", done)
		}
	})
	t.Run("expired, not held", func(t *testing.T) {
		h := publishable(t, shaA, nil)
		pollTimes(h, 1)
		h.clock.advance(codeTTL)
		approveWith(h, lastCode(t, h))
		msgs := h.signal.messages()
		if done := msgs[len(msgs)-1]; !strings.Contains(done, "expired") || !strings.Contains(done, "follows within a minute") || strings.Contains(done, heldPromise) {
			t.Fatalf("reply = %q, want the minute promise", done)
		}
	})
	t.Run("changed, held", func(t *testing.T) {
		h := publishable(t, shaA, nil)
		failBindListing(h)
		pollTimes(h, 2)
		h.gh.editBody(testFork, prNumber, "Adds the thing, edited.", testAgent)
		approveWith(h, lastCode(t, h))
		msgs := h.signal.messages()
		if done := msgs[len(msgs)-1]; !strings.Contains(done, "changed since the announcement") || !strings.HasSuffix(done, heldPromise) || strings.Contains(done, minute) {
			t.Fatalf("reply = %q, want the held promise", done)
		}
	})
	t.Run("changed, not held", func(t *testing.T) {
		h := publishable(t, shaA, nil)
		pollTimes(h, 1)
		h.gh.editBody(testFork, prNumber, "Adds the thing, edited.", testAgent)
		approveWith(h, lastCode(t, h))
		msgs := h.signal.messages()
		if done := msgs[len(msgs)-1]; !strings.Contains(done, "changed since the announcement") || !strings.Contains(done, minute) || strings.Contains(done, heldPromise) {
			t.Fatalf("reply = %q, want the minute promise", done)
		}
	})
}

func TestARefusalWaitsWhenUpstreamCannotBeRead(t *testing.T) {
	h := publishable(t, shaA, nil)
	rv := announcedAndApproved(t, h)
	pollTimes(h, 1)
	up := onlyUpstream(t, h)
	h.gh.editBody(testFork, prNumber, "Adds the thing, edited later.", testAgent)
	h.clock.advance(time.Minute)
	h.gh.failWith(http.MethodGet, upPulls, fakeFailure{Status: http.StatusBadGateway, Times: 1})
	before, writes := len(h.signal.messages()), len(h.gh.writes())

	pollTimes(h, 1)

	wantNoWritesSince(t, h, writes)
	if msgs := messagesSince(h, before); len(msgs) != 0 {
		t.Fatalf("messages = %q, want none while upstream is unreadable", msgs)
	}
	if h.gh.reviewsOf(prNumber)[0].state != "APPROVED" || h.gh.reviewsOf(prNumber)[0].id != rv.id {
		t.Fatal("the approval was dismissed")
	}
	pollTimes(h, 2)
	wantNoWritesSince(t, h, writes)
	want := testFork + "#7 at " + shaA + " is already upstream: https://github.com/omnigent-ai/omnigent/pull/" + itoa(up.number) + "; later changes to the pull request are not published."
	if msgs := messagesSince(h, before); len(msgs) != 1 || msgs[0] != want {
		t.Fatalf("messages = %q, want only %q", msgs, want)
	}
}

func TestAChangedReplyPromisesTheHeldWordingOnlyForTheHeldHead(t *testing.T) {
	h := publishable(t, shaA, nil)
	failBindListing(h)
	pollTimes(h, 2)
	code := lastCode(t, h)
	moveHead(h, shaB)

	approveWith(h, code)

	msgs := h.signal.messages()
	if done := msgs[len(msgs)-1]; !strings.Contains(done, "changed since the announcement") || strings.Contains(done, heldPromise) || !strings.Contains(done, "within a minute") {
		t.Fatalf("reply = %q, want the minute promise for a new head", done)
	}
}

func TestAGitHubReapprovalAfterTheProcessStartedIsTold(t *testing.T) {
	h := publishable(t, shaA, nil)
	announcedAndApproved(t, h)
	pollTimes(h, 1)
	up := onlyUpstream(t, h)
	h.gh.editBody(testFork, prNumber, "Adds the thing, edited later.", testAgent)
	h.clock.advance(time.Minute)
	pollTimes(h, 1)
	before := len(h.signal.messages())
	h.gh.addReview(prNumber, testOwner, "APPROVED", shaA)
	h.clock.advance(time.Minute)

	pollTimes(h, 3)

	want := testFork + "#7 at " + shaA + " is already upstream: https://github.com/omnigent-ai/omnigent/pull/" + itoa(up.number) + "; the title and body were not changed."
	if msgs := messagesSince(h, before); len(msgs) != 1 || msgs[0] != want {
		t.Fatalf("messages = %q, want only %q", msgs, want)
	}
}

func TestAnUndeliveredNoticeIsLoggedOnceAndSentDespiteAFailingFork(t *testing.T) {
	h := publishable(t, shaA, nil)
	announcedAndApproved(t, h)
	h.gh.rename(testFork, prNumber, "feat(web): renamed after the approval", testAgent)
	h.clock.advance(time.Minute)
	before := len(h.signal.messages())
	setSignalDown(h, true)
	pollTimes(h, 3)
	if n := strings.Count(h.logs(), "owner notice not sent"); n != 1 {
		t.Fatalf("undelivered notice logged %d times, want once:\n%s", n, h.logs())
	}
	h.gh.update(func() { h.gh.fork.parent = "someone/omnigent" })
	setSignalDown(h, false)

	pollTimes(h, 1)

	if n := countContaining(messagesSince(h, before), "The approval was dismissed"); n != 1 {
		t.Fatalf("messages = %q, want the queued refusal delivered", messagesSince(h, before))
	}
}

func TestAHardRefusalWithTwoOpenUpstreamPullRequestsStillDismisses(t *testing.T) {
	h := publishable(t, shaA, nil)
	announcedAndApproved(t, h)
	pollTimes(h, 1)
	h.gh.setRef(testUpstream, "next", shaUpMain)
	h.gh.addPull(testUpstream, fakePR{fakeIssue: &fakeIssue{title: "feat(web): second", user: testOwner}, headRepo: testFork, headRef: publishRef, base: "next", sha: shaA})
	h.gh.editBody(testFork, prNumber, "Adds the thing, edited later.", testAgent)
	h.clock.advance(time.Minute)

	pollTimes(h, 2)

	if h.gh.reviewsOf(prNumber)[0].state != "DISMISSED" {
		t.Fatal("a hard refusal that is not upstream did not dismiss")
	}
}
