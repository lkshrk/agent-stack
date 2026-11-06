package main

import (
	"context"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"
)

func decide(h *harness, auth, body string) *httptest.ResponseRecorder {
	req := httptest.NewRequest(http.MethodPost, "/v1/decisions", strings.NewReader(body))
	if auth != "" {
		req.Header.Set("Authorization", auth)
	}
	rec := httptest.NewRecorder()
	h.api.ServeHTTP(rec, req)
	return rec
}

func announce(t *testing.T, h *harness, number int, sha string) string {
	t.Helper()
	h.gh.readyPR(number, sha)
	h.poller.poll(context.Background())
	msgs := h.signal.messages()
	return codeIn(t, msgs[len(msgs)-1])
}

func TestDecisionsNeedTheSharedToken(t *testing.T) {
	h := newHarness(t)
	code := announce(t, h, 7, shaA)
	body := `{"action":"approve","code":"` + code + `"}`
	for _, auth := range []string{"", "Bearer", "Bearer ", "Bearer wrong", "Bearer " + testShared[:len(testShared)-1], "Bearer " + testShared + "x", "Basic " + testShared, "bearer " + testShared, testShared} {
		if rec := decide(h, auth, body); rec.Code != http.StatusUnauthorized {
			t.Fatalf("auth %q answered %d, want 401", auth, rec.Code)
		}
	}
	if got := h.gh.reviewCalls(); len(got) != 0 {
		t.Fatalf("reviews posted without the token: %v", got)
	}
	if rec := decide(h, "Bearer "+testShared, body); rec.Code != http.StatusOK {
		t.Fatalf("the code was consumed by a rejected call: %d %s", rec.Code, rec.Body)
	}
}

func TestTokenMatchesComparesWholeValues(t *testing.T) {
	if !tokenMatches(testShared, testShared) {
		t.Fatal("equal tokens do not match")
	}
	for _, got := range []string{"", testShared[:10], testShared + "0", strings.ToUpper(testShared)} {
		if tokenMatches(got, testShared) {
			t.Fatalf("%q matched", got)
		}
	}
}

func TestApproveReviewsTheAnnouncedHeadOnce(t *testing.T) {
	h := newHarness(t)
	code := announce(t, h, 7, shaA)

	first := decide(h, "Bearer "+testShared, `{"action":"approve","code":"`+code+`"}`)
	second := decide(h, "Bearer "+testShared, `{"action":"approve","code":"`+code+`"}`)

	if first.Code != http.StatusOK || second.Code != http.StatusNotFound {
		t.Fatalf("answers = %d, %d; want 200, 404", first.Code, second.Code)
	}
	reviews := h.gh.reviewCalls()
	if len(reviews) != 1 {
		t.Fatalf("reviews = %v, want one", reviews)
	}
	r := reviews[0]
	if r.number != 7 || r.body["commit_id"] != shaA || r.body["event"] != "APPROVE" {
		t.Fatalf("review = %+v, want APPROVE on %s", r, shaA)
	}
	msgs := h.signal.messages()
	if len(msgs) != 3 || !strings.Contains(msgs[1], "Approved lkshrk/omnigent#7 at "+shaA) || !strings.Contains(msgs[1], "Publishing upstream follows") || !strings.Contains(msgs[2], "unknown or already used") {
		t.Fatalf("confirmations = %q", msgs)
	}
}

func TestRejectRequestsChangesOnTheAnnouncedHead(t *testing.T) {
	h := newHarness(t)
	code := announce(t, h, 7, shaA)

	if rec := decide(h, "Bearer "+testShared, `{"action":"reject","code":"`+code+`"}`); rec.Code != http.StatusOK {
		t.Fatalf("answer = %d %s", rec.Code, rec.Body)
	}
	reviews := h.gh.reviewCalls()
	if len(reviews) != 1 || reviews[0].body["event"] != "REQUEST_CHANGES" || reviews[0].body["commit_id"] != shaA || reviews[0].body["body"] == "" {
		t.Fatalf("reviews = %+v", reviews)
	}
}

func TestUnknownAndExpiredCodesMakeNoGitHubCall(t *testing.T) {
	h := newHarness(t)
	code := announce(t, h, 7, shaA)

	unknown := decide(h, "Bearer "+testShared, `{"action":"approve","code":"00000000"}`)
	h.clock.advance(codeTTL + time.Second)
	expired := decide(h, "Bearer "+testShared, `{"action":"approve","code":"`+code+`"}`)

	if unknown.Code != http.StatusNotFound || expired.Code != http.StatusGone {
		t.Fatalf("answers = %d, %d; want 404, 410", unknown.Code, expired.Code)
	}
	if got := h.gh.reviewCalls(); len(got) != 0 {
		t.Fatalf("reviews = %v, want none", got)
	}
}

func TestMalformedDecisionsAreRefused(t *testing.T) {
	h := newHarness(t)
	code := announce(t, h, 7, shaA)
	for _, body := range []string{
		``, `{}`, `nope`,
		`{"action":"merge","code":"` + code + `"}`,
		`{"action":"approve","code":"1234"}`,
		`{"action":"approve","code":"` + code + `","sha":"` + shaB + `"}`,
		`{"action":"approve","code":"` + code + `"} {}`,
	} {
		if rec := decide(h, "Bearer "+testShared, body); rec.Code != http.StatusBadRequest {
			t.Fatalf("body %q answered %d, want 400", body, rec.Code)
		}
	}
	if got := h.gh.reviewCalls(); len(got) != 0 {
		t.Fatalf("reviews = %v, want none", got)
	}
}

func TestAFailedReviewIsAnnouncedAgainWithANewCode(t *testing.T) {
	h := newHarness(t)
	code := announce(t, h, 7, shaA)
	h.gh.update(func() { h.gh.failPost = true })

	if rec := decide(h, "Bearer "+testShared, `{"action":"approve","code":"`+code+`"}`); rec.Code != http.StatusBadGateway {
		t.Fatalf("answer = %d, want 502", rec.Code)
	}
	h.gh.update(func() { h.gh.failPost = false })
	h.poller.poll(context.Background())

	msgs := h.signal.messages()
	again := codeIn(t, msgs[len(msgs)-1])
	if again == code {
		t.Fatalf("re-announced with the spent code: %q", msgs)
	}
	if rec := decide(h, "Bearer "+testShared, `{"action":"approve","code":"`+code+`"}`); rec.Code != http.StatusNotFound {
		t.Fatalf("spent code answered %d", rec.Code)
	}
}

func TestHealthz(t *testing.T) {
	h := newHarness(t)
	rec := httptest.NewRecorder()
	h.api.ServeHTTP(rec, httptest.NewRequest(http.MethodGet, "/healthz", nil))
	if rec.Code != http.StatusOK {
		t.Fatalf("healthz = %d", rec.Code)
	}
}

func TestAnExpiredCodeIsAnnouncedAgainWithAFreshCode(t *testing.T) {
	h := newHarness(t)
	code := announce(t, h, 7, shaA)
	h.clock.advance(codeTTL + time.Second)
	h.poller.poll(context.Background())
	if n := len(h.signal.messages()); n != 1 {
		t.Fatalf("messages before the late reply = %d, want 1", n)
	}

	late := decide(h, "Bearer "+testShared, `{"action":"approve","code":"`+code+`"}`)
	h.poller.poll(context.Background())

	msgs := h.signal.messages()
	if late.Code != http.StatusGone || len(msgs) != 3 || !strings.Contains(msgs[1], "new code") {
		t.Fatalf("late reply = %d, messages = %q", late.Code, msgs)
	}
	fresh := codeIn(t, msgs[2])
	if fresh == code || !strings.Contains(msgs[2], shaA) {
		t.Fatalf("re-announcement = %q", msgs[2])
	}
	if rec := decide(h, "Bearer "+testShared, `{"action":"approve","code":"`+fresh+`"}`); rec.Code != http.StatusOK {
		t.Fatalf("fresh code answered %d", rec.Code)
	}
	if r := h.gh.reviewCalls(); len(r) != 1 || r[0].body["commit_id"] != shaA {
		t.Fatalf("reviews = %v", r)
	}
}

func TestAConsumedCodeIsNeverAnnouncedAgainForTheSameHead(t *testing.T) {
	h := newHarness(t)
	code := announce(t, h, 7, shaA)
	if rec := decide(h, "Bearer "+testShared, `{"action":"reject","code":"`+code+`"}`); rec.Code != http.StatusOK {
		t.Fatalf("reject answered %d", rec.Code)
	}
	h.clock.advance(2 * codeTTL)
	h.poller.poll(context.Background())
	h.codes.forget(prKey{testFork, 7})
	h.poller.poll(context.Background())

	for _, m := range h.signal.messages()[2:] {
		if strings.Contains(m, "Contribution ready") {
			t.Fatalf("re-announced a decided head: %q", m)
		}
	}
}

func approveWith(h *harness, code string) *httptest.ResponseRecorder {
	return decide(h, "Bearer "+testShared, `{"action":"approve","code":"`+code+`"}`)
}

func announcements(h *harness) int {
	n := 0
	for _, m := range h.signal.messages() {
		if strings.HasPrefix(m, "Contribution ready") {
			n++
		}
	}
	return n
}

func TestATombstoneMapsAnExpiredCodeBackForADay(t *testing.T) {
	for name, tc := range map[string]struct {
		age          time.Duration
		status, want int
	}{
		"within a day": {23 * time.Hour, http.StatusGone, 2},
		"after a day":  {codeTTL + tombstoneTTL + time.Second, http.StatusNotFound, 1},
	} {
		t.Run(name, func(t *testing.T) {
			h := newHarness(t)
			code := announce(t, h, 7, shaA)
			h.clock.advance(tc.age)
			h.poller.poll(context.Background())

			rec := approveWith(h, code)
			h.poller.poll(context.Background())

			if rec.Code != tc.status || announcements(h) != tc.want {
				t.Fatalf("reply = %d, announcements = %d; want %d, %d", rec.Code, announcements(h), tc.status, tc.want)
			}
		})
	}
}

func TestATombstoneReannouncesAtMostOnce(t *testing.T) {
	h := newHarness(t)
	code := announce(t, h, 7, shaA)
	h.clock.advance(codeTTL + time.Second)

	first, second := approveWith(h, code), approveWith(h, code)
	h.poller.poll(context.Background())
	h.poller.poll(context.Background())

	if first.Code != http.StatusGone || second.Code != http.StatusNotFound || announcements(h) != 2 {
		t.Fatalf("replies = %d, %d, announcements = %d; want 410, 404, 2", first.Code, second.Code, announcements(h))
	}
}

func TestAnExpiredCodeForAChangedPullRequestReannouncesNothing(t *testing.T) {
	for name, change := range map[string]func(h *harness){
		"new head": func(h *harness) {
			h.gh.update(func() { h.gh.prs[7].sha = shaB; h.gh.runs[shaB] = h.gh.runs[shaA] })
		},
		"no longer ready": func(h *harness) {
			h.gh.update(func() { h.gh.prs[7].labels = nil })
		},
	} {
		t.Run(name, func(t *testing.T) {
			h := newHarness(t)
			code := announce(t, h, 7, shaA)
			h.clock.advance(codeTTL + time.Second)
			change(h)
			h.poller.poll(context.Background())
			before := announcements(h)

			rec := approveWith(h, code)
			h.poller.poll(context.Background())

			msgs := h.signal.messages()
			if rec.Code != http.StatusGone || !strings.Contains(msgs[len(msgs)-1], "has changed or been decided") || announcements(h) != before {
				t.Fatalf("reply = %d, announcements %d -> %d, messages = %q", rec.Code, before, announcements(h), msgs)
			}
			if r := h.gh.reviewCalls(); len(r) != 0 {
				t.Fatalf("reviews = %v", r)
			}
		})
	}
}

func TestUnknownAndConsumedCodesTriggerNoAnnouncement(t *testing.T) {
	h := newHarness(t)
	code := announce(t, h, 7, shaA)
	if rec := approveWith(h, code); rec.Code != http.StatusOK {
		t.Fatalf("approve = %d", rec.Code)
	}
	h.clock.advance(codeTTL + time.Second)

	consumed, unknown := approveWith(h, code), approveWith(h, "00000000")
	h.poller.poll(context.Background())

	if consumed.Code != http.StatusNotFound || unknown.Code != http.StatusNotFound || announcements(h) != 1 {
		t.Fatalf("replies = %d, %d, announcements = %d; want 404, 404, 1", consumed.Code, unknown.Code, announcements(h))
	}
}

func TestADecisionIsRefusedWhenThePullRequestChangedSinceTheAnnouncement(t *testing.T) {
	for name, tc := range map[string]struct {
		change     func(g *fakeGitHub, pr *fakePR)
		reannounce bool
	}{
		"head moved": {func(g *fakeGitHub, pr *fakePR) { pr.sha = shaB; g.runs[shaB] = g.runs[shaA] }, true},
		"title":      {func(_ *fakeGitHub, pr *fakePR) { pr.title = "feat(web): add another thing" }, true},
		"body":       {func(_ *fakeGitHub, pr *fakePR) { pr.body = "Something else entirely." }, true},
		"label":      {func(_ *fakeGitHub, pr *fakePR) { pr.labels = nil }, false},
	} {
		for _, action := range []string{"approve", "reject"} {
			t.Run(name+"/"+action, func(t *testing.T) {
				h := newHarness(t)
				code := announce(t, h, 7, shaA)
				h.gh.update(func() { tc.change(h.gh, h.gh.prs[7]) })

				rec := decide(h, "Bearer "+testShared, `{"action":"`+action+`","code":"`+code+`"}`)
				h.poller.poll(context.Background())

				if rec.Code != http.StatusConflict {
					t.Fatalf("answer = %d %s, want 409", rec.Code, rec.Body)
				}
				if r := h.gh.reviewCalls(); len(r) != 0 {
					t.Fatalf("reviews = %v, want none", r)
				}
				msgs := h.signal.messages()
				if len(msgs) < 2 || !strings.Contains(msgs[1], "changed since the announcement") || !strings.Contains(msgs[1], "Nothing was submitted") {
					t.Fatalf("messages = %q", msgs)
				}
				if got, want := announcements(h), map[bool]int{true: 2, false: 1}[tc.reannounce]; got != want {
					t.Fatalf("announcements = %d, want %d: %q", got, want, msgs)
				}
				if tc.reannounce {
					fresh := codeIn(t, msgs[len(msgs)-1])
					if fresh == code {
						t.Fatal("re-announced with the refused code")
					}
					if rec := approveWith(h, fresh); rec.Code != http.StatusOK {
						t.Fatalf("fresh code answered %d", rec.Code)
					}
				}
			})
		}
	}
}

func TestADecisionIsRefusedWhenTheForkNoLongerMatchesTheRegistry(t *testing.T) {
	for name, change := range map[string]func(g *fakeGitHub){
		"parent": func(g *fakeGitHub) { g.fork.parent = "someone-else/omnigent" },
		"owner":  func(g *fakeGitHub) { g.fork.owner = "someone" },
	} {
		t.Run(name, func(t *testing.T) {
			h := newHarness(t)
			code := announce(t, h, 7, shaA)
			h.gh.update(func() { change(h.gh) })

			if rec := decide(h, "Bearer "+testShared, `{"action":"approve","code":"`+code+`"}`); rec.Code != http.StatusBadGateway {
				t.Fatalf("answer = %d, want 502", rec.Code)
			}
			if calls := h.gh.reviewCalls(); len(calls) != 0 {
				t.Fatalf("submitted %v", calls)
			}
		})
	}
}
