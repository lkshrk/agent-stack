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
	h.gh.update(func() { h.gh.prs[7].sha = shaB })

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
	if len(msgs) != 3 || !strings.Contains(msgs[1], "Approved lkshrk/omnigent#7 at "+shaA) || !strings.Contains(msgs[2], "unknown, already used or expired") {
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

	if unknown.Code != http.StatusNotFound || expired.Code != http.StatusNotFound {
		t.Fatalf("answers = %d, %d; want 404, 404", unknown.Code, expired.Code)
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
