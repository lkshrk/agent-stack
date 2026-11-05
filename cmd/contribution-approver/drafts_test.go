package main

import (
	"bytes"
	"context"
	"fmt"
	"io"
	"net/http"
	"strings"
	"testing"
	"time"
)

const (
	draftNumber       = 9
	draftTitle        = "bug(web): the sidebar drops archived sessions"
	draftBody         = "Archive a session, then reload.\n\nThe session is gone from the sidebar."
	commentBody       = "Reproduced on main as well.\n\nThe sidebar drops archived sessions after a reload."
	upstreamIssues    = "/repos/omnigent-ai/omnigent/issues"
	upstreamComments  = "/repos/omnigent-ai/omnigent/issues/42/comments"
	forkIssues        = "/repos/lkshrk/omnigent/issues"
	forkDraft         = "/repos/lkshrk/omnigent/issues/9"
	forkDraftComments = forkDraft + "/comments"
	forkDraftEvents   = forkDraft + "/events"
	forkDraftApproval = forkDraft + "/labels/" + approvedLabel
	hostileText       = "@lkshrk [click](https://evil.example) **evil** mallory"
)

func newDraft(t *testing.T, kind, title, body string) *harness {
	t.Helper()
	h := newHarness(t)
	h.gh.addIssue(testUpstream, 42, "bug(web): sidebar", "The sidebar drops sessions.", "someone")
	labels := []string{}
	if kind != "" {
		labels = append(labels, kind)
	}
	h.gh.addIssue(testFork, draftNumber, title, body, testAgent, labels...)
	h.clock.advance(time.Minute)
	return h
}

func approveDraft(h *harness) { h.gh.label(testFork, draftNumber, approvedLabel, testOwner) }

func issueDraft(t *testing.T) *harness {
	t.Helper()
	h := newDraft(t, issueLabel, draftTitle, draftBody)
	approveDraft(h)
	h.clock.advance(time.Minute)
	return h
}

func commentDraft(t *testing.T) *harness {
	t.Helper()
	h := newDraft(t, commentLabel, "Comment on #42", commentBody)
	approveDraft(h)
	h.clock.advance(time.Minute)
	return h
}

func upstreamIssueURL(n int) string {
	return fmt.Sprintf("https://github.com/%s/issues/%d", testUpstream, n)
}

func writesSince(h *harness, before int) []fakeCall { return h.gh.writes()[before:] }

func upstreamWrites(calls []fakeCall) []fakeCall {
	var out []fakeCall
	for _, c := range calls {
		if strings.HasPrefix(c.Path, "/repos/"+testUpstream) {
			out = append(out, c)
		}
	}
	return out
}

func draftState(h *harness) fakeIssue { return h.gh.issue(testFork, draftNumber) }

func hasLabel(is fakeIssue, name string) bool {
	for _, l := range is.labels {
		if l == name {
			return true
		}
	}
	return false
}

func wantPublishedOnce(t *testing.T, h *harness, before int, path string, body map[string]any, url, marker string) {
	t.Helper()
	w := writesSince(h, before)
	got := writeSummary(w)
	want := []string{"POST " + path, "POST " + forkDraftComments, "DELETE " + forkDraftApproval, "PATCH " + forkDraft}
	if strings.Join(got, ",") != strings.Join(want, ",") {
		t.Fatalf("writes = %v, want %v", got, want)
	}
	if fmt.Sprint(w[0].Body) != fmt.Sprint(body) {
		t.Fatalf("upstream body = %v, want exactly %v", w[0].Body, body)
	}
	if w[1].Body["body"] != marker+"\n"+url {
		t.Fatalf("marker = %q, want %q", w[1].Body["body"], marker+"\n"+url)
	}
	if w[3].Body["state"] != "closed" {
		t.Fatalf("close = %v, want state closed", w[3].Body)
	}
	if is := draftState(h); is.state != "closed" || hasLabel(is, approvedLabel) {
		t.Fatalf("the fork issue is %s with labels %v, want closed and the approval spent", is.state, is.labels)
	}
}

func wantRefused(t *testing.T, h *harness, before int, reason string) {
	t.Helper()
	w := writesSince(h, before)
	if got := writeSummary(w); strings.Join(got, ",") != "DELETE "+forkDraftApproval {
		t.Fatalf("writes = %v, want only the approval label removed", got)
	}
	if hasLabel(draftState(h), approvedLabel) {
		t.Fatal("the approval label is still on the draft")
	}
	msgs := h.signal.messages()
	if len(msgs) != 1 || !strings.HasPrefix(msgs[0], "Not publishing the draft "+testFork+"#9: ") || !strings.Contains(msgs[0], reason) || !strings.Contains(msgs[0], "The approval label was removed") {
		t.Fatalf("messages = %q, want one refusal naming %q", msgs, reason)
	}
	wantNoAttackerText(t, msgs)
}

func wantNothingWritten(t *testing.T, h *harness, before int) {
	t.Helper()
	if w := writesSince(h, before); len(w) != 0 {
		t.Fatalf("writes = %v, want none", writeSummary(w))
	}
}

func TestAnOwnerApprovedIssueDraftIsFiledOnce(t *testing.T) {
	h := issueDraft(t)

	pollTimes(h, 1)

	url := upstreamIssueURL(43)
	wantPublishedOnce(t, h, 0, upstreamIssues, map[string]any{"title": draftTitle, "body": draftBody}, url, issueMarker)
	filed := h.gh.issue(testUpstream, 43)
	if filed.title != draftTitle || filed.body != draftBody || filed.user != testOwner {
		t.Fatalf("upstream issue = %q %q by %s", filed.title, filed.body, filed.user)
	}
	msgs := h.signal.messages()
	if len(msgs) != 1 || msgs[0] != "Filed the draft "+testFork+"#9 upstream as an issue: "+url {
		t.Fatalf("messages = %q", msgs)
	}

	before := len(h.gh.writes())
	pollTimes(h, 1)
	calls, billed := len(h.gh.allCalls()), h.gh.billedRequests()
	pollTimes(h, 3)
	wantNothingWritten(t, h, before)
	if len(h.signal.messages()) != 1 {
		t.Fatalf("messages = %q, want no repeat", h.signal.messages())
	}
	for _, c := range h.gh.allCalls()[calls:] {
		if c.Path != forkRepo && c.Path != forkPulls && c.Path != forkIssues {
			t.Errorf("a published draft still costs %s %s", c.Method, c.Path)
		}
	}
	if h.gh.billedRequests() != billed {
		t.Fatalf("billed requests grew from %d to %d", billed, h.gh.billedRequests())
	}
}

func TestAnOwnerApprovedCommentDraftPostsExactlyTheBodyOnce(t *testing.T) {
	h := commentDraft(t)

	pollTimes(h, 1)

	comments := h.gh.issue(testUpstream, 42).comments
	if len(comments) != 1 || comments[0].body != commentBody || comments[0].user != testOwner {
		t.Fatalf("upstream comments = %+v, want exactly the body once", comments)
	}
	url := fmt.Sprintf("%s#issuecomment-%d", upstreamIssueURL(42), comments[0].id)
	wantPublishedOnce(t, h, 0, upstreamComments, map[string]any{"body": commentBody}, url, commentMarker)
	msgs := h.signal.messages()
	if len(msgs) != 1 || msgs[0] != "Posted the draft "+testFork+"#9 upstream as a comment: "+url {
		t.Fatalf("messages = %q", msgs)
	}
	before := len(h.gh.writes())
	pollTimes(h, 2)
	wantNothingWritten(t, h, before)
}

func TestAReopenedAndRelabelledDraftIsNotPublishedAgain(t *testing.T) {
	for _, mk := range []func(*testing.T) *harness{issueDraft, commentDraft} {
		h := mk(t)
		pollTimes(h, 1)
		h.gh.reopen(testFork, draftNumber, testOwner)
		h.gh.unlabel(testFork, draftNumber, approvedLabel, testOwner)
		approveDraft(h)
		h.clock.advance(time.Minute)
		before := len(h.gh.writes())

		pollTimes(h, 2)

		w := writesSince(h, before)
		if got := writeSummary(w); strings.Join(got, ",") != "DELETE "+forkDraftApproval+",PATCH "+forkDraft {
			t.Fatalf("writes = %v, want the approval spent and the fork issue closed", got)
		}
		if msgs := h.signal.messages(); len(msgs) != 2 || !strings.Contains(msgs[1], "already has a publish record") || strings.Contains(msgs[1], "https://") {
			t.Fatalf("messages = %q, want one link-free record notice", msgs)
		}
	}
}

func TestALostRecordDoesNotPublishTwice(t *testing.T) {
	cases := []struct {
		name string
		mk   func(*testing.T) *harness
		path string
		fail fakeFailure
	}{
		{"issue: record comment fails", issueDraft, forkDraftComments, fakeFailure{Status: http.StatusBadGateway, Times: 1}},
		{"issue: upstream response lost", issueDraft, upstreamIssues, fakeFailure{Status: http.StatusBadGateway, After: true, Times: 1}},
		{"issue: upstream connection dropped", issueDraft, upstreamIssues, fakeFailure{Status: fakeDrop, After: true, Times: 1}},
		{"comment: record comment fails", commentDraft, forkDraftComments, fakeFailure{Status: http.StatusBadGateway, Times: 1}},
		{"comment: upstream response lost", commentDraft, upstreamComments, fakeFailure{Status: http.StatusBadGateway, After: true, Times: 1}},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			h := tc.mk(t)
			h.gh.failWith("POST", tc.path, tc.fail)

			pollTimes(h, 1)
			if !hasLabel(draftState(h), approvedLabel) || draftState(h).state != "open" || len(h.signal.messages()) != 0 {
				t.Fatalf("a failed write was treated as final: %q", h.signal.messages())
			}
			before := len(h.gh.writes())
			pollTimes(h, 2)

			w := writesSince(h, before)
			if up := upstreamWrites(w); len(up) != 0 {
				t.Fatalf("upstream written again: %v", writeSummary(up))
			}
			if got := writeSummary(w); strings.Join(got, ",") != "POST "+forkDraftComments+",DELETE "+forkDraftApproval+",PATCH "+forkDraft {
				t.Fatalf("writes = %v, want the record and the close", got)
			}
			msgs := h.signal.messages()
			if len(msgs) != 1 || !strings.Contains(msgs[0], "upstream as a") || !strings.Contains(msgs[0], "https://github.com/"+testUpstream+"/issues/") {
				t.Fatalf("messages = %q, want one publish notice with the link", msgs)
			}
			if link := msgs[0][strings.Index(msgs[0], "https://"):]; !strings.HasSuffix(w[0].Body["body"].(string), "\n"+link) {
				t.Fatalf("record %q does not carry the recovered link %q", w[0].Body["body"], link)
			}
		})
	}
}

func TestRecoveryReadsEveryUpstreamPage(t *testing.T) {
	t.Run("issue", func(t *testing.T) {
		h := issueDraft(t)
		for i := range 150 {
			h.gh.addIssue(testUpstream, 100+i, "noise", "noise", testOwner)
		}
		found := h.gh.addIssue(testUpstream, 50, draftTitle, draftBody, testOwner)
		pollTimes(h, 1)
		w := h.gh.writes()
		if len(upstreamWrites(w)) != 0 || len(w) != 3 || w[0].Body["body"] != issueMarker+"\n"+upstreamIssueURL(found.number) {
			t.Fatalf("writes = %v, want the recovered issue recorded", w)
		}
	})
	t.Run("comment", func(t *testing.T) {
		h := commentDraft(t)
		for range 150 {
			h.gh.comment(testUpstream, 42, "someone", "noise")
		}
		found := h.gh.comment(testUpstream, 42, testOwner, commentBody)
		pollTimes(h, 1)
		w := h.gh.writes()
		want := fmt.Sprintf("%s\n%s#issuecomment-%d", commentMarker, upstreamIssueURL(42), found.id)
		if len(upstreamWrites(w)) != 0 || len(w) != 3 || w[0].Body["body"] != want {
			t.Fatalf("writes = %v, want the recovered comment recorded", w)
		}
	})
}

func TestOnlyAnIdenticalOwnerIssueCountsAsFiled(t *testing.T) {
	for name, add := range map[string]func(*harness){
		"other author": func(h *harness) { h.gh.addIssue(testUpstream, 0, draftTitle, draftBody, "someone") },
		"other body":   func(h *harness) { h.gh.addIssue(testUpstream, 0, draftTitle, "Different steps.", testOwner) },
		"pull request": func(h *harness) {
			h.gh.addPull(testUpstream, fakePR{fakeIssue: &fakeIssue{title: draftTitle, body: draftBody, user: testOwner}, headRepo: testFork, headRef: "x", sha: shaA})
		},
	} {
		t.Run(name, func(t *testing.T) {
			h := issueDraft(t)
			add(h)
			pollTimes(h, 1)
			if up := upstreamWrites(h.gh.writes()); len(up) != 1 || up[0].Method != "POST" || up[0].Path != upstreamIssues {
				t.Fatalf("upstream writes = %v, want one new issue", writeSummary(up))
			}
		})
	}
}

func TestAnIdenticalClosedOwnerIssueCountsAsFiled(t *testing.T) {
	h := issueDraft(t)
	closed := h.gh.addIssue(testUpstream, 600, draftTitle, draftBody, testOwner)
	h.gh.closeIssue(testUpstream, closed.number, testOwner)
	pollTimes(h, 1)
	w := h.gh.writes()
	if len(upstreamWrites(w)) != 0 || len(w) == 0 || w[0].Body["body"] != issueMarker+"\n"+upstreamIssueURL(600) {
		t.Fatalf("writes = %v, want the closed issue recorded", writeSummary(w))
	}
}

func TestOnlyAnIdenticalOwnerCommentCountsAsPosted(t *testing.T) {
	for name, add := range map[string]func(*harness){
		"other author": func(h *harness) { h.gh.comment(testUpstream, 42, "someone", commentBody) },
		"other body":   func(h *harness) { h.gh.comment(testUpstream, 42, testOwner, "Reproduced on main as well.") },
	} {
		t.Run(name, func(t *testing.T) {
			h := commentDraft(t)
			add(h)
			pollTimes(h, 1)
			if up := upstreamWrites(h.gh.writes()); len(up) != 1 || up[0].Path != upstreamComments {
				t.Fatalf("upstream writes = %v, want one new comment", writeSummary(up))
			}
		})
	}
}

func TestOnlyAnOwnerRecordCounts(t *testing.T) {
	h := newDraft(t, issueLabel, draftTitle, draftBody)
	h.gh.comment(testFork, draftNumber, testOwner, "Looks good, filing it.")
	h.gh.comment(testFork, draftNumber, testAgent, issueMarker+"\n"+upstreamIssueURL(1))
	h.clock.advance(time.Minute)
	approveDraft(h)
	h.clock.advance(time.Minute)
	pollTimes(h, 1)
	if up := upstreamWrites(h.gh.writes()); len(up) != 1 {
		t.Fatalf("upstream writes = %v, want the draft filed", writeSummary(up))
	}
}

func TestAnOwnerRecordIsFoundOnEveryPageAndItsLinkIsNotTrusted(t *testing.T) {
	for _, marker := range []string{issueMarker, commentMarker} {
		for _, mk := range []func(*testing.T) *harness{issueDraft, commentDraft} {
			h := mk(t)
			for range 150 {
				h.gh.comment(testFork, draftNumber, testAgent, "noise")
			}
			h.gh.comment(testFork, draftNumber, testOwner, marker+"\nhttps://evil.example/@lkshrk")
			pollTimes(h, 1)
			if got := writeSummary(h.gh.writes()); strings.Join(got, ",") != "DELETE "+forkDraftApproval+",PATCH "+forkDraft {
				t.Fatalf("writes = %v, want only the spend and the close", got)
			}
			msgs := h.signal.messages()
			if len(msgs) != 1 || strings.Contains(msgs[0], "https://") {
				t.Fatalf("messages = %q, want one link-free record notice", msgs)
			}
			wantNoAttackerText(t, msgs)
		}
	}
}

func TestADraftApprovedByAnyoneButTheOwnerIsNotPublished(t *testing.T) {
	cases := map[string]func(*harness){
		"labelled by the agent": func(h *harness) { h.gh.label(testFork, draftNumber, approvedLabel, testAgent) },
		"the owner's label replaced by the agent's": func(h *harness) {
			approveDraft(h)
			h.clock.advance(time.Minute)
			h.gh.unlabel(testFork, draftNumber, approvedLabel, testOwner)
			h.gh.label(testFork, draftNumber, approvedLabel, testAgent)
		},
		"a case variant of the owner": func(h *harness) { h.gh.label(testFork, draftNumber, approvedLabel, "LKSHRK") },
	}
	for name, setup := range cases {
		t.Run(name, func(t *testing.T) {
			h := newDraft(t, issueLabel, draftTitle, draftBody)
			setup(h)
			h.clock.advance(time.Minute)
			pollTimes(h, 3)
			if got := writeSummary(h.gh.writes()); strings.Join(got, ",") != "DELETE "+forkDraftApproval {
				t.Fatalf("writes = %v, want only the foreign label removed", got)
			}
			if len(h.signal.messages()) != 0 {
				t.Fatalf("messages = %q, want none", h.signal.messages())
			}
		})
	}
}

func TestADraftNeedsExactlyOneKind(t *testing.T) {
	for name, kinds := range map[string][]string{"no kind": nil, "both kinds": {issueLabel, commentLabel}} {
		t.Run(name, func(t *testing.T) {
			h := newDraft(t, "", draftTitle, draftBody)
			for _, k := range kinds {
				h.gh.label(testFork, draftNumber, k, testAgent)
			}
			h.clock.advance(time.Minute)
			approveDraft(h)
			h.clock.advance(time.Minute)
			pollTimes(h, 2)
			wantRefused(t, h, 0, "exactly one of upstream-issue or upstream-comment")
		})
	}
}

func TestADraftKindChangedAfterTheApprovalIsRefused(t *testing.T) {
	cases := map[string]func(*harness){
		"issue swapped to comment": func(h *harness) {
			h.clock.advance(time.Minute)
			h.gh.unlabel(testFork, draftNumber, issueLabel, testAgent)
			h.gh.label(testFork, draftNumber, commentLabel, testAgent)
			h.gh.rename(testFork, draftNumber, "Comment on #42", testAgent)
		},
		"comment label added and removed": func(h *harness) {
			h.clock.advance(time.Minute)
			h.gh.label(testFork, draftNumber, commentLabel, testAgent)
			h.gh.unlabel(testFork, draftNumber, commentLabel, testAgent)
		},
		"a kind change by the owner after the approval": func(h *harness) {
			h.clock.advance(time.Minute)
			h.gh.unlabel(testFork, draftNumber, issueLabel, testOwner)
			h.gh.label(testFork, draftNumber, issueLabel, testOwner)
		},
		"the owner in the same second after the approval": func(h *harness) {
			h.gh.unlabel(testFork, draftNumber, issueLabel, testOwner)
			h.gh.label(testFork, draftNumber, issueLabel, testOwner)
		},
		"the agent in the same second": func(h *harness) {
			h.gh.unlabel(testFork, draftNumber, issueLabel, testAgent)
			h.gh.label(testFork, draftNumber, issueLabel, testAgent)
		},
	}
	for name, change := range cases {
		t.Run(name, func(t *testing.T) {
			h := newDraft(t, issueLabel, draftTitle, draftBody)
			if name == "the agent in the same second" {
				change(h)
				approveDraft(h)
			} else {
				approveDraft(h)
				change(h)
			}
			h.clock.advance(time.Minute)
			pollTimes(h, 2)
			wantRefused(t, h, 0, "the draft kind changed after the approval")
		})
	}
	t.Run("the owner fixes the kind before approving", func(t *testing.T) {
		h := newDraft(t, commentLabel, draftTitle, draftBody)
		h.gh.unlabel(testFork, draftNumber, commentLabel, testOwner)
		h.gh.label(testFork, draftNumber, issueLabel, testOwner)
		approveDraft(h)
		h.clock.advance(time.Minute)
		pollTimes(h, 1)
		wantPublishedOnce(t, h, 0, upstreamIssues, map[string]any{"title": draftTitle, "body": draftBody}, upstreamIssueURL(43), issueMarker)
	})
}

func TestADraftEditedAtOrAfterTheApprovalIsRefused(t *testing.T) {
	cases := map[string]struct {
		edit   func(*harness)
		reason string
	}{
		"body after": {func(h *harness) {
			h.clock.advance(time.Minute)
			h.gh.editBody(testFork, draftNumber, "rewritten", testAgent)
		}, "edited at or after the approval"},
		"body in the second": {func(h *harness) { h.gh.editBody(testFork, draftNumber, "rewritten", testAgent) }, "edited at or after the approval"},
		"body by the owner": {func(h *harness) {
			h.clock.advance(time.Minute)
			h.gh.editBody(testFork, draftNumber, "rewritten", testOwner)
		}, "edited at or after the approval"},
		"title after": {func(h *harness) {
			h.clock.advance(time.Minute)
			h.gh.rename(testFork, draftNumber, "bug(web): other", testAgent)
		}, "renamed at or after the approval"},
		"title in the second": {func(h *harness) { h.gh.rename(testFork, draftNumber, "bug(web): other", testAgent) }, "renamed at or after the approval"},
		"body edited and back": {func(h *harness) {
			h.clock.advance(time.Minute)
			h.gh.editBody(testFork, draftNumber, "x", testAgent)
			h.gh.editBody(testFork, draftNumber, draftBody, testAgent)
		}, "edited at or after the approval"},
	}
	for name, tc := range cases {
		t.Run(name, func(t *testing.T) {
			h := newDraft(t, issueLabel, draftTitle, draftBody)
			approveDraft(h)
			tc.edit(h)
			h.clock.advance(time.Minute)
			pollTimes(h, 2)
			wantRefused(t, h, 0, tc.reason)
		})
	}
	t.Run("title renamed just before the approval in the same second", func(t *testing.T) {
		h := newDraft(t, issueLabel, "bug(web): old", draftBody)
		h.gh.rename(testFork, draftNumber, draftTitle, testAgent)
		approveDraft(h)
		h.clock.advance(time.Minute)
		pollTimes(h, 1)
		wantRefused(t, h, 0, "renamed at or after the approval")
	})
	t.Run("a comment draft retargeted after the approval", func(t *testing.T) {
		h := newDraft(t, commentLabel, "Comment on #42", commentBody)
		approveDraft(h)
		h.clock.advance(time.Minute)
		h.gh.rename(testFork, draftNumber, "Comment on #43", testAgent)
		h.clock.advance(time.Minute)
		pollTimes(h, 1)
		wantRefused(t, h, 0, "renamed at or after the approval")
	})
	t.Run("edits before the approval publish the edited text", func(t *testing.T) {
		h := newDraft(t, issueLabel, "bug(web): old", "old")
		h.gh.rename(testFork, draftNumber, draftTitle, testAgent)
		h.gh.editBody(testFork, draftNumber, draftBody, testAgent)
		h.clock.advance(time.Minute)
		approveDraft(h)
		h.clock.advance(time.Minute)
		pollTimes(h, 1)
		wantPublishedOnce(t, h, 0, upstreamIssues, map[string]any{"title": draftTitle, "body": draftBody}, upstreamIssueURL(43), issueMarker)
	})
}

func TestADraftRevisionDeletedAfterTheApprovalIsRefused(t *testing.T) {
	for _, mode := range []fakeRevisionDeletion{fakeEditSoftDeleted, fakeEditDropped} {
		for _, index := range []int{1, 2} {
			t.Run(fmt.Sprintf("mode %d revision %d", mode, index), func(t *testing.T) {
				h := newDraft(t, issueLabel, draftTitle, "first")
				h.gh.editBody(testFork, draftNumber, "second", testAgent)
				h.clock.advance(time.Minute)
				approveDraft(h)
				h.clock.advance(time.Minute)
				h.gh.editBody(testFork, draftNumber, draftBody, testAgent)
				h.clock.advance(time.Minute)
				h.gh.deleteRevision(testFork, draftNumber, index, testAgent, mode)
				h.clock.advance(time.Minute)
				pollTimes(h, 1)
				if up := upstreamWrites(h.gh.writes()); len(up) != 0 {
					t.Fatalf("published despite an edit after the approval: %v", writeSummary(up))
				}
				if hasLabel(draftState(h), approvedLabel) {
					t.Fatal("the approval was not spent")
				}
			})
		}
	}
}

func TestACommentDraftTitleMustBeExact(t *testing.T) {
	for _, title := range []string{
		"Comment on 42", "comment on #42", "Comment on #0", "Comment on #042", "Comment on #42 ", " Comment on #42",
		"Comment on #42 and #43", "Comment on #-1", "Comment on #4x", "Re: Comment on #42", "Comment on #",
		"Comment on #٤٢", "Comment on #99999999999999999999999",
	} {
		t.Run(title, func(t *testing.T) {
			h := newDraft(t, commentLabel, title, commentBody)
			approveDraft(h)
			h.clock.advance(time.Minute)
			pollTimes(h, 1)
			wantRefused(t, h, 0, "a comment draft's title must be exactly Comment on #<number>")
		})
	}
}

func TestADraftBodyMustNotBeEmpty(t *testing.T) {
	for _, mk := range []struct{ kind, title string }{{commentLabel, "Comment on #42"}, {issueLabel, draftTitle}} {
		for _, body := range []string{"", "  \n\t"} {
			t.Run(mk.kind+fmt.Sprintf("%q", body), func(t *testing.T) {
				h := newDraft(t, mk.kind, mk.title, body)
				approveDraft(h)
				h.clock.advance(time.Minute)
				pollTimes(h, 1)
				wantRefused(t, h, 0, "the draft body is empty")
			})
		}
	}
}

func TestACommentOnAMissingUpstreamIssueIsRefused(t *testing.T) {
	h := newDraft(t, commentLabel, "Comment on #4242", commentBody)
	approveDraft(h)
	h.clock.advance(time.Minute)
	pollTimes(h, 1)
	wantRefused(t, h, 0, "the upstream issue to comment on cannot be found")
}

func TestAnUpstreamRejectionIsRefused(t *testing.T) {
	for _, status := range []int{http.StatusNotFound, http.StatusGone, http.StatusUnprocessableEntity} {
		t.Run(fmt.Sprint(status), func(t *testing.T) {
			h := issueDraft(t)
			h.gh.fail("POST", upstreamIssues, status)
			pollTimes(h, 1)
			w := writeSummary(h.gh.writes())
			if strings.Join(w, ",") != "POST "+upstreamIssues+",DELETE "+forkDraftApproval {
				t.Fatalf("writes = %v", w)
			}
			if msgs := h.signal.messages(); len(msgs) != 1 || !strings.Contains(msgs[0], "GitHub refused to file the issue upstream") {
				t.Fatalf("messages = %q", msgs)
			}
		})
	}
}

func TestDraftsThatAreNotOpenIssuesAreIgnored(t *testing.T) {
	h := newHarness(t)
	h.gh.addPull(testFork, fakePR{fakeIssue: &fakeIssue{number: 11, title: draftTitle, body: draftBody, user: testAgent, labels: []string{issueLabel}}, headRef: "x", sha: shaA})
	h.gh.label(testFork, 11, approvedLabel, testOwner)
	h.gh.addIssue(testFork, 12, draftTitle, draftBody, testAgent, issueLabel)
	h.gh.label(testFork, 12, approvedLabel, testOwner)
	h.gh.closeIssue(testFork, 12, testOwner)
	h.gh.addIssue(testFork, 13, draftTitle, draftBody, testAgent, issueLabel)
	h.clock.advance(time.Minute)
	pollTimes(h, 2)
	wantNothingWritten(t, h, 0)
	for _, c := range h.gh.allCalls() {
		if strings.HasPrefix(c.Path, forkIssues+"/") {
			t.Errorf("an ignored item was read: %s %s", c.Method, c.Path)
		}
	}
}

func TestADraftFailsClosedOnReadErrors(t *testing.T) {
	cases := map[string]func(*harness){
		"issue":           func(h *harness) { h.gh.fail("GET", forkDraft, http.StatusBadGateway) },
		"events":          func(h *harness) { h.gh.fail("GET", forkDraftEvents, http.StatusBadGateway) },
		"events dropped":  func(h *harness) { h.gh.fail("GET", forkDraftEvents, fakeDrop) },
		"record comments": func(h *harness) { h.gh.fail("GET", forkDraftComments, http.StatusBadGateway) },
		"edit history":    func(h *harness) { h.gh.failGraphQL("boom") },
		"upstream search": func(h *harness) { h.gh.fail("GET", upstreamIssues, http.StatusBadGateway) },
		"upstream filing": func(h *harness) { h.gh.fail("POST", upstreamIssues, http.StatusBadGateway) },
	}
	for name, breakIt := range cases {
		t.Run(name, func(t *testing.T) {
			h := issueDraft(t)
			breakIt(h)
			pollTimes(h, 2)
			for _, w := range h.gh.writes() {
				if w.Path != upstreamIssues {
					t.Fatalf("wrote %s %s on a read error", w.Method, w.Path)
				}
			}
			if !hasLabel(draftState(h), approvedLabel) || len(h.signal.messages()) != 0 {
				t.Fatalf("a transient error spent the approval or told the owner: %q", h.signal.messages())
			}
			h.gh.fail("GET", forkDraft, 0)
			h.gh.fail("GET", forkDraftEvents, 0)
			h.gh.fail("GET", forkDraftComments, 0)
			h.gh.fail("GET", upstreamIssues, 0)
			h.gh.fail("POST", upstreamIssues, 0)
			h.gh.failGraphQL("")
			pollTimes(h, 1)
			if up := upstreamWrites(h.gh.writes()); len(up) == 0 || len(h.signal.messages()) != 1 {
				t.Fatalf("not published after recovery: %v %q", writeSummary(up), h.signal.messages())
			}
		})
	}
	t.Run("comments target", func(t *testing.T) {
		h := commentDraft(t)
		h.gh.fail("GET", upstreamComments, http.StatusBadGateway)
		pollTimes(h, 2)
		wantNothingWritten(t, h, 0)
	})
}

func TestADraftRefusesAtAPageCap(t *testing.T) {
	h := issueDraft(t)
	for range maxPages*pageSize + 1 {
		h.gh.comment(testFork, draftNumber, testAgent, "noise")
	}
	pollTimes(h, 1)
	wantRefused(t, h, 0, "more than 1000 comments")
}

func TestAnUpstreamSearchPageCapRefuses(t *testing.T) {
	h := issueDraft(t)
	for i := range maxPages*pageSize + 1 {
		h.gh.addIssue(testUpstream, 1000+i, "noise", "noise", testOwner)
	}
	pollTimes(h, 1)
	if up := upstreamWrites(h.gh.writes()); len(up) != 0 {
		t.Fatalf("published past a page cap: %v", writeSummary(up))
	}
	if hasLabel(draftState(h), approvedLabel) {
		t.Fatal("a page cap did not spend the approval")
	}
}

func TestADraftRefusalIsToldOnceWhileTheLabelCannotBeRemoved(t *testing.T) {
	h := newDraft(t, issueLabel, draftTitle, draftBody)
	approveDraft(h)
	h.clock.advance(time.Minute)
	h.gh.editBody(testFork, draftNumber, "rewritten", testAgent)
	h.gh.fail("DELETE", forkDraftApproval, http.StatusBadGateway)
	pollTimes(h, 3)
	if n := h.gh.callCount("DELETE", forkDraftApproval); n != 3 {
		t.Fatalf("label removals = %d, want one per poll", n)
	}
	msgs := h.signal.messages()
	if len(msgs) != 1 || !strings.Contains(msgs[0], "could not be removed yet") {
		t.Fatalf("messages = %q, want one", msgs)
	}
	h.gh.fail("DELETE", forkDraftApproval, 0)
	pollTimes(h, 2)
	if hasLabel(draftState(h), approvedLabel) || len(h.signal.messages()) != 1 {
		t.Fatalf("label still on or told again: %q", h.signal.messages())
	}
}

func TestADraftWithAHostileTitleAndBodyTellsNoAttackerText(t *testing.T) {
	h := newDraft(t, issueLabel, hostileText, hostileText)
	approveDraft(h)
	h.clock.advance(time.Minute)
	pollTimes(h, 1)
	h2 := newDraft(t, commentLabel, "Comment on #42", hostileText)
	approveDraft(h2)
	h2.clock.advance(time.Minute)
	h2.gh.rename(testFork, draftNumber, hostileText, testAgent)
	pollTimes(h2, 1)
	for _, msgs := range [][]string{h.signal.messages(), h2.signal.messages()} {
		if len(msgs) != 1 {
			t.Fatalf("messages = %q, want one", msgs)
		}
		wantNoAttackerText(t, msgs)
	}
}

func TestADraftNoticeWaitsForSignal(t *testing.T) {
	h := issueDraft(t)
	h.signal.mu.Lock()
	h.signal.fail = true
	h.signal.mu.Unlock()
	pollTimes(h, 2)
	if up := upstreamWrites(h.gh.writes()); len(up) != 1 {
		t.Fatalf("upstream writes = %v, want one", writeSummary(up))
	}
	h.signal.mu.Lock()
	h.signal.fail = false
	h.signal.mu.Unlock()
	pollTimes(h, 2)
	if msgs := h.signal.messages(); len(msgs) != 1 || !strings.HasPrefix(msgs[0], "Filed the draft") {
		t.Fatalf("messages = %q, want the queued notice once", msgs)
	}
}

func TestARestartBeforeTheApprovalIsSpentDoesNotPublishAgain(t *testing.T) {
	h := issueDraft(t)
	h.gh.fail("DELETE", forkDraftApproval, http.StatusBadGateway)
	pollTimes(h, 2)
	if n := len(upstreamWrites(h.gh.writes())); n != 1 || len(h.signal.messages()) != 1 || draftState(h).state != "open" {
		t.Fatalf("upstream writes %d, messages %q, state %s", n, h.signal.messages(), draftState(h).state)
	}
	restart(h)
	h.gh.fail("DELETE", forkDraftApproval, 0)
	pollTimes(h, 2)
	if n := len(upstreamWrites(h.gh.writes())); n != 1 || draftState(h).state != "closed" {
		t.Fatalf("upstream writes %d, state %s", n, draftState(h).state)
	}
	if msgs := h.signal.messages(); len(msgs) != 2 || !strings.Contains(msgs[1], "already has a publish record") {
		t.Fatalf("messages = %q, want one record notice after the restart", msgs)
	}
}

func TestOneStalledDraftDoesNotStarveTheOthers(t *testing.T) {
	h := newHarness(t)
	for _, n := range []int{8, 9} {
		h.gh.addIssue(testFork, n, draftTitle+fmt.Sprint(n), draftBody, testAgent, issueLabel)
	}
	h.clock.advance(time.Minute)
	for _, n := range []int{8, 9} {
		h.gh.label(testFork, n, approvedLabel, testOwner)
	}
	h.clock.advance(time.Minute)
	h.poller.draftTimeout = 100 * time.Millisecond
	h.poller.forkTimeout = time.Second
	gh := h.poller.gh
	gh.http = &http.Client{Transport: &stallTransport{base: gh.http.Transport, path: forkDraft + "/events"}}
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()

	h.poller.poll(ctx)

	if up := upstreamWrites(h.gh.writes()); len(up) != 1 {
		t.Fatalf("upstream writes = %v, want the healthy draft filed", writeSummary(up))
	}
}

func TestAStalledPullRequestLoopDoesNotStarveDrafts(t *testing.T) {
	h := issueDraft(t)
	h.gh.readyPR(7, shaA)
	h.poller.forkTimeout = 200 * time.Millisecond
	gh := h.poller.gh
	gh.http = &http.Client{Transport: &stallTransport{base: gh.http.Transport, path: forkPulls}}
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()

	h.poller.poll(ctx)

	if up := upstreamWrites(h.gh.writes()); len(up) != 1 {
		t.Fatalf("upstream writes = %v, want the draft filed", writeSummary(up))
	}
}

func TestEvaluateDraftOnlyActsOnOpenApprovedIssues(t *testing.T) {
	h := newHarness(t)
	h.gh.addPull(testFork, fakePR{fakeIssue: &fakeIssue{number: 11, title: draftTitle, body: draftBody, user: testAgent, labels: []string{issueLabel}}, headRef: "x", sha: shaA})
	h.gh.label(testFork, 11, approvedLabel, testOwner)
	h.gh.addIssue(testFork, 12, draftTitle, draftBody, testAgent, issueLabel)
	h.gh.label(testFork, 12, approvedLabel, testOwner)
	h.gh.closeIssue(testFork, 12, testOwner)
	h.gh.addIssue(testFork, 13, draftTitle, draftBody, testAgent, issueLabel)
	for _, n := range []int{11, 12, 13} {
		d := h.poller.gh.evaluateDraft(context.Background(), h.poller.forks[0], n, nil)
		if d.verdict != verdictNone || d.foreign {
			t.Errorf("#%d: verdict %s, want none", n, d.verdict)
		}
		if c := h.gh.callCount("GET", fmt.Sprintf("%s/%d/events", forkIssues, n)); c != 0 {
			t.Errorf("#%d: events read %d times", n, c)
		}
	}
}

func TestAStaleApprovalEventListWaits(t *testing.T) {
	label := `{"id":1,"event":"labeled","label":{"name":"approved-upstream"},"actor":{"login":"lkshrk"},"created_at":"2026-11-05T00:00:00Z"}`
	for name, events := range map[string]string{
		"no event":           `[]`,
		"newest is unlabel":  `[` + label + `,{"id":2,"event":"unlabeled","label":{"name":"approved-upstream"},"actor":{"login":"lkshrk"},"created_at":"2026-11-05T00:01:00Z"}]`,
		"labeled without at": `[{"id":1,"event":"labeled","label":{"name":"approved-upstream"},"actor":{"login":"lkshrk"}}]`,
	} {
		t.Run(name, func(t *testing.T) {
			h := issueDraft(t)
			tamperRaw(h, forkDraftEvents, events)
			pollTimes(h, 2)
			wantNothingWritten(t, h, 0)
			if len(h.signal.messages()) != 0 {
				t.Fatalf("messages = %q", h.signal.messages())
			}
		})
	}
}

func TestADraftNoticeCarriesOnlyAShapeCheckedLink(t *testing.T) {
	t.Run("issue", func(t *testing.T) {
		h := issueDraft(t)
		tamperPOST(h, upstreamIssues, func(m map[string]any) { m["number"] = 0 })
		pollTimes(h, 1)
		if msgs := h.signal.messages(); len(msgs) != 1 || msgs[0] != "Filed the draft "+testFork+"#9 upstream as an issue." {
			t.Fatalf("messages = %q", msgs)
		}
	})
	t.Run("comment", func(t *testing.T) {
		h := commentDraft(t)
		tamperPOST(h, upstreamComments, func(m map[string]any) { m["id"] = 0 })
		pollTimes(h, 1)
		if msgs := h.signal.messages(); len(msgs) != 1 || msgs[0] != "Posted the draft "+testFork+"#9 upstream as a comment." {
			t.Fatalf("messages = %q", msgs)
		}
	})
}

func TestDraftsWaitForAVerifiedFork(t *testing.T) {
	h := issueDraft(t)
	h.gh.update(func() { h.gh.fork.parent = "someone/else" })
	pollTimes(h, 2)
	if c := h.gh.callCount("GET", forkIssues); c != 0 {
		t.Fatalf("drafts listed %d times on an unverified fork", c)
	}
	wantNothingWritten(t, h, 0)
}

func TestANewApprovalWithTheSameRefusalIsToldAgain(t *testing.T) {
	h := newDraft(t, issueLabel, draftTitle, draftBody)
	h.gh.label(testFork, draftNumber, commentLabel, testAgent)
	h.clock.advance(time.Minute)
	approveDraft(h)
	h.clock.advance(time.Minute)
	pollTimes(h, 2)
	approveDraft(h)
	h.clock.advance(time.Minute)
	pollTimes(h, 2)
	msgs := h.signal.messages()
	if len(msgs) != 2 || msgs[0] != msgs[1] || !strings.Contains(msgs[0], "exactly one of") {
		t.Fatalf("messages = %q, want the refusal once per approval", msgs)
	}
}

func TestALabelThatIsAlreadyGoneCountsAsRemoved(t *testing.T) {
	h := newDraft(t, "", draftTitle, draftBody)
	approveDraft(h)
	h.clock.advance(time.Minute)
	h.gh.fail("DELETE", forkDraftApproval, http.StatusNotFound)
	pollTimes(h, 1)
	if msgs := h.signal.messages(); len(msgs) != 1 || !strings.Contains(msgs[0], "The approval label was removed") {
		t.Fatalf("messages = %q", msgs)
	}
}

func TestAFreshApprovalWhileTheLabelCannotBeRemovedIsToldAgain(t *testing.T) {
	h := newDraft(t, "", draftTitle, draftBody)
	approveDraft(h)
	h.clock.advance(time.Minute)
	h.gh.fail("DELETE", forkDraftApproval, http.StatusBadGateway)
	pollTimes(h, 2)
	h.gh.fail("DELETE", forkDraftApproval, 0)
	h.gh.unlabel(testFork, draftNumber, approvedLabel, testOwner)
	approveDraft(h)
	h.clock.advance(time.Minute)
	h.gh.fail("DELETE", forkDraftApproval, http.StatusBadGateway)
	pollTimes(h, 2)
	if msgs := h.signal.messages(); len(msgs) != 2 {
		t.Fatalf("messages = %q, want one per approval", msgs)
	}
}

func TestANeverEditedDraftWithActivityAfterTheApprovalIsRefused(t *testing.T) {
	cases := map[string]func(*harness){
		"a dropped revision of the only edit": func(h *harness) {
			h.gh.editBody(testFork, draftNumber, "evil body", testAgent)
			h.gh.deleteRevision(testFork, draftNumber, 1, testAgent, fakeEditDropped)
		},
		"a dropped original of the only edit": func(h *harness) {
			h.gh.editBody(testFork, draftNumber, "evil body", testAgent)
			h.gh.deleteRevision(testFork, draftNumber, 0, testAgent, fakeEditDropped)
		},
		"a comment":          func(h *harness) { h.gh.comment(testFork, draftNumber, testAgent, "note") },
		"an unrelated label": func(h *harness) { h.gh.label(testFork, draftNumber, "triage", testAgent) },
	}
	for name, activity := range cases {
		t.Run(name, func(t *testing.T) {
			h := newDraft(t, issueLabel, draftTitle, draftBody)
			approveDraft(h)
			h.clock.advance(time.Second)
			activity(h)
			h.clock.advance(time.Minute)
			pollTimes(h, 1)
			wantRefused(t, h, 0, "the draft changed after the approval")
		})
	}
	t.Run("activity before the approval publishes", func(t *testing.T) {
		h := newDraft(t, issueLabel, draftTitle, draftBody)
		h.gh.comment(testFork, draftNumber, testAgent, "note")
		h.clock.advance(time.Second)
		approveDraft(h)
		h.clock.advance(time.Minute)
		pollTimes(h, 1)
		wantPublishedOnce(t, h, 0, upstreamIssues, map[string]any{"title": draftTitle, "body": draftBody}, upstreamIssueURL(43), issueMarker)
	})
}

func TestAnEditedDraftIgnoresActivityAfterTheApproval(t *testing.T) {
	h := newDraft(t, issueLabel, draftTitle, "old")
	h.gh.editBody(testFork, draftNumber, draftBody, testAgent)
	h.clock.advance(time.Minute)
	approveDraft(h)
	h.clock.advance(time.Minute)
	h.gh.comment(testFork, draftNumber, testAgent, "note")
	h.clock.advance(time.Minute)
	pollTimes(h, 1)
	wantPublishedOnce(t, h, 0, upstreamIssues, map[string]any{"title": draftTitle, "body": draftBody}, upstreamIssueURL(43), issueMarker)
}

func ownerRecord(t *testing.T, h *harness) fakeComment {
	t.Helper()
	for _, c := range draftState(h).comments {
		if c.user == testOwner && (strings.HasPrefix(c.body, issueMarker) || strings.HasPrefix(c.body, commentMarker)) {
			return *c
		}
	}
	t.Fatal("no owner record on the draft")
	return fakeComment{}
}

func TestTheAgentCannotRepublishAPublishedDraft(t *testing.T) {
	for name, mk := range map[string]func(*testing.T) *harness{"issue": issueDraft, "comment": commentDraft} {
		t.Run(name, func(t *testing.T) {
			h := mk(t)
			pollTimes(h, 1)
			if name == "issue" {
				h.gh.rename(testUpstream, 43, "bug(web): retitled by a maintainer", "maintainer")
			}
			h.gh.deleteComment(testFork, draftNumber, ownerRecord(t, h).id)
			h.gh.reopen(testFork, draftNumber, testAgent)
			h.clock.advance(time.Minute)
			before, told := len(h.gh.writes()), len(h.signal.messages())

			pollTimes(h, 3)

			wantNothingWritten(t, h, before)
			if msgs := h.signal.messages()[told:]; len(msgs) != 0 {
				t.Fatalf("an agent reopen told the owner %q", msgs)
			}
		})
	}
}

func TestAFailedCloseAfterTheSpendNeverPublishesAgain(t *testing.T) {
	h := issueDraft(t)
	h.gh.fail("PATCH", forkDraft, http.StatusBadGateway)
	pollTimes(h, 3)
	if n := len(upstreamWrites(h.gh.writes())); n != 1 || len(h.signal.messages()) != 1 || hasLabel(draftState(h), approvedLabel) {
		t.Fatalf("upstream writes %d, messages %q", n, h.signal.messages())
	}
}

func TestDraftLabelsMustMatchTheirHistory(t *testing.T) {
	cases := map[string]struct {
		change func(*harness)
		reason string
	}{
		"a kind swapped without an event": {func(h *harness) {
			h.gh.update(func() { h.gh.fork.issues[draftNumber].labels = []string{commentLabel, approvedLabel} })
		}, "the draft labels do not match their history"},
		"a repository label renamed into the kind": {func(h *harness) {
			h.gh.renameRepoLabel(testFork, issueLabel, "old-kind")
			h.gh.renameRepoLabel(testFork, "triage", commentLabel)
		}, "the draft labels do not match their history"},
		"a kind label renamed away and back": {func(h *harness) {
			h.gh.renameRepoLabel(testFork, issueLabel, "tmp")
			h.gh.renameRepoLabel(testFork, "tmp", issueLabel)
		}, "a draft label was changed at or after the approval"},
		"the approval label edited in its second": {func(h *harness) {
			h.clock.advance(-time.Minute)
			h.gh.renameRepoLabel(testFork, approvedLabel, approvedLabel)
			h.clock.advance(time.Minute)
		}, "a draft label was changed at or after the approval"},
	}
	for name, tc := range cases {
		t.Run(name, func(t *testing.T) {
			h := newDraft(t, issueLabel, "Comment on #42", commentBody)
			h.gh.label(testFork, draftNumber, "triage", testAgent)
			h.clock.advance(time.Minute)
			approveDraft(h)
			h.clock.advance(time.Minute)
			tc.change(h)
			pollTimes(h, 1)
			wantRefused(t, h, 0, tc.reason)
		})
	}
	t.Run("a label renamed into the approval is no approval", func(t *testing.T) {
		h := newDraft(t, issueLabel, draftTitle, draftBody)
		h.gh.label(testFork, draftNumber, "looks-fine", testAgent)
		h.clock.advance(time.Minute)
		h.gh.renameRepoLabel(testFork, "looks-fine", approvedLabel)
		h.clock.advance(time.Minute)
		pollTimes(h, 2)
		wantNothingWritten(t, h, 0)
	})
	t.Run("a label changed long before the approval is fine", func(t *testing.T) {
		h := newDraft(t, issueLabel, draftTitle, draftBody)
		h.gh.renameRepoLabel(testFork, issueLabel, issueLabel)
		h.clock.advance(time.Minute)
		approveDraft(h)
		h.clock.advance(time.Minute)
		pollTimes(h, 1)
		wantPublishedOnce(t, h, 0, upstreamIssues, map[string]any{"title": draftTitle, "body": draftBody}, upstreamIssueURL(43), issueMarker)
	})
	t.Run("an unreadable label waits", func(t *testing.T) {
		h := issueDraft(t)
		h.gh.failGraphQL("boom")
		pollTimes(h, 2)
		wantNothingWritten(t, h, 0)
	})
	for name, answer := range map[string]map[string]any{
		"no data":       {"data": nil},
		"no repository": {"data": map[string]any{"repository": nil}},
		"a label missing from the answer": {"data": map[string]any{"repository": map[string]any{
			"approved": map[string]any{"updatedAt": "2026-01-01T00:00:00Z"}, "issue": nil,
		}}},
	} {
		t.Run("a label answer with "+name+" waits", func(t *testing.T) {
			h := issueDraft(t)
			h.gh.mu.Lock()
			h.gh.gqlLabelAnswer = answer
			h.gh.mu.Unlock()
			pollTimes(h, 2)
			wantNothingWritten(t, h, 0)
		})
	}
	t.Run("a partly failed label read waits", func(t *testing.T) {
		h := issueDraft(t)
		h.gh.failLabelGraphQL("boom")
		pollTimes(h, 2)
		wantNothingWritten(t, h, 0)
	})
}

func TestOnlyUpstreamLinksAreTold(t *testing.T) {
	cfg := forkConfig{Upstream: testUpstream}
	for link, want := range map[string]bool{
		upstreamIssueURL(43):                                 true,
		upstreamIssueURL(43) + "#issuecomment-7":             true,
		"https://github.com/other/repo/issues/43":            false,
		"https://github.com/" + testUpstream + "x/issues/43": false,
		upstreamIssueURL(0):                                  false,
	} {
		if got := ownDraftURL(cfg, link) == link; got != want {
			t.Errorf("ownDraftURL(%q) kept = %v, want %v", link, got, want)
		}
	}
}

func TestAnUpstream403IsHardUnlessRateLimited(t *testing.T) {
	for name, f := range map[string]fakeFailure{
		"forbidden":            {Status: http.StatusForbidden},
		"retry-after":          {Status: http.StatusForbidden, Header: map[string]string{"Retry-After": "60"}},
		"no requests left":     {Status: http.StatusForbidden, Header: map[string]string{"X-Ratelimit-Remaining": "0"}},
		"secondary rate limit": {Status: http.StatusForbidden, Message: "You have exceeded a secondary rate limit. Please wait a few minutes before you try again."},
		"abuse detection":      {Status: http.StatusForbidden, Message: "You have triggered an abuse detection mechanism. Please wait a few minutes before you try again."},
	} {
		t.Run(name, func(t *testing.T) {
			h := commentDraft(t)
			h.gh.failWith("POST", upstreamComments, f)
			pollTimes(h, 1)
			spent := !hasLabel(draftState(h), approvedLabel)
			if spent != (name == "forbidden") {
				t.Fatalf("spent = %v, messages %q", spent, h.signal.messages())
			}
		})
	}
}

func TestAHardRefusalOfAPostAlreadyUpstreamRecordsIt(t *testing.T) {
	h := issueDraft(t)
	h.gh.failWith("POST", forkDraftComments, fakeFailure{Status: http.StatusBadGateway, Times: 1})
	pollTimes(h, 1)
	restart(h)
	h.gh.comment(testFork, draftNumber, testAgent, "note")
	h.clock.advance(time.Minute)
	before := len(h.gh.writes())

	pollTimes(h, 1)

	w := writesSince(h, before)
	if got := writeSummary(w); strings.Join(got, ",") != "POST "+forkDraftComments+",DELETE "+forkDraftApproval+",PATCH "+forkDraft {
		t.Fatalf("writes = %v, want the post recorded instead of refused", got)
	}
	if msgs := h.signal.messages(); len(msgs) != 1 || msgs[0] != "Filed the draft "+testFork+"#9 upstream as an issue: "+upstreamIssueURL(43) {
		t.Fatalf("messages = %q", msgs)
	}
}

func TestAHardRefusalWaitsWhenUpstreamCannotBeSearched(t *testing.T) {
	h := newDraft(t, issueLabel, draftTitle, draftBody)
	approveDraft(h)
	h.clock.advance(time.Minute)
	h.gh.editBody(testFork, draftNumber, "rewritten", testAgent)
	h.clock.advance(time.Minute)
	h.gh.fail("GET", upstreamIssues, http.StatusBadGateway)
	pollTimes(h, 3)
	wantNothingWritten(t, h, 0)
	msgs := h.signal.messages()
	if len(msgs) != 1 || msgs[0] != "Not publishing the draft "+testFork+"#9 yet: the body was edited at or after the approval. Upstream cannot be searched for an earlier copy right now, so the approval label stays until it can." {
		t.Fatalf("messages = %q, want one waiting notice", msgs)
	}
	h.gh.fail("GET", upstreamIssues, 0)
	pollTimes(h, 1)
	if got := writeSummary(h.gh.writes()); strings.Join(got, ",") != "DELETE "+forkDraftApproval {
		t.Fatalf("writes = %v, want only the approval label removed", got)
	}
	if msgs := h.signal.messages(); len(msgs) != 2 || !strings.HasPrefix(msgs[1], "Not publishing the draft "+testFork+"#9: ") {
		t.Fatalf("messages = %q, want the refusal after the waiting notice", msgs)
	}
}

func TestARefusalWithoutOneKindSearchesNothing(t *testing.T) {
	h := newDraft(t, "", draftTitle, draftBody)
	approveDraft(h)
	h.clock.advance(time.Minute)
	pollTimes(h, 1)
	wantRefused(t, h, 0, "exactly one of")
	if c := h.gh.callCount("GET", upstreamIssues); c != 0 {
		t.Fatalf("searched upstream %d times", c)
	}
}

func TestAnOlderOwnerPostIsNeverTakenAsThePublish(t *testing.T) {
	t.Run("a refusal renamed onto an older issue", func(t *testing.T) {
		h := newDraft(t, issueLabel, draftTitle, draftBody)
		h.gh.addIssue(testUpstream, 600, "old owner issue", "old owner body", testOwner)
		h.clock.advance(time.Minute)
		approveDraft(h)
		h.clock.advance(time.Minute)
		h.gh.rename(testFork, draftNumber, "old owner issue", testAgent)
		h.gh.editBody(testFork, draftNumber, "old owner body", testAgent)
		h.clock.advance(time.Minute)
		pollTimes(h, 1)
		wantRefused(t, h, 0, "at or after the approval")
	})
	t.Run("a refusal redirected onto an older comment", func(t *testing.T) {
		h := newDraft(t, issueLabel, draftTitle, draftBody)
		h.gh.comment(testUpstream, 42, testOwner, "old owner comment")
		h.clock.advance(time.Minute)
		approveDraft(h)
		h.clock.advance(time.Minute)
		h.gh.unlabel(testFork, draftNumber, issueLabel, testAgent)
		h.gh.label(testFork, draftNumber, commentLabel, testAgent)
		h.gh.rename(testFork, draftNumber, "Comment on #42", testAgent)
		h.gh.editBody(testFork, draftNumber, "old owner comment", testAgent)
		h.clock.advance(time.Minute)
		pollTimes(h, 1)
		wantRefused(t, h, 0, "after the approval")
	})
	for name, mk := range map[string]func(*harness){
		"issue":   func(h *harness) { h.gh.addIssue(testUpstream, 600, draftTitle, draftBody, testOwner) },
		"comment": func(h *harness) { h.gh.comment(testUpstream, 42, testOwner, commentBody) },
	} {
		t.Run("an identical older "+name+" is posted again", func(t *testing.T) {
			h := newDraft(t, map[string]string{"issue": issueLabel, "comment": commentLabel}[name], map[string]string{"issue": draftTitle, "comment": "Comment on #42"}[name], map[string]string{"issue": draftBody, "comment": commentBody}[name])
			mk(h)
			h.clock.advance(time.Minute)
			approveDraft(h)
			h.clock.advance(time.Minute)
			pollTimes(h, 1)
			if up := upstreamWrites(h.gh.writes()); len(up) != 1 || up[0].Method != "POST" {
				t.Fatalf("upstream writes = %v, want one new post", writeSummary(up))
			}
		})
	}
}

func TestIssueRecoveryIgnoresAMaintainerRetitle(t *testing.T) {
	h := issueDraft(t)
	h.gh.failWith("POST", forkDraftComments, fakeFailure{Status: http.StatusBadGateway, Times: 1})
	pollTimes(h, 1)
	restart(h)
	h.gh.rename(testUpstream, 43, "bug(web): retitled by a maintainer", "maintainer")
	h.clock.advance(time.Minute)
	before := len(h.gh.writes())
	pollTimes(h, 2)
	w := writesSince(h, before)
	if got := writeSummary(w); strings.Join(got, ",") != "POST "+forkDraftComments+",DELETE "+forkDraftApproval+",PATCH "+forkDraft {
		t.Fatalf("writes = %v, want the retitled issue recorded", got)
	}
	if w[0].Body["body"] != issueMarker+"\n"+upstreamIssueURL(43) {
		t.Fatalf("record = %q", w[0].Body["body"])
	}
}

func wantOnePostRecorded(t *testing.T, h *harness, link string) {
	t.Helper()
	if up := upstreamWrites(h.gh.writes()); len(up) != 1 {
		t.Fatalf("upstream writes = %v, want one", writeSummary(up))
	}
	is := draftState(h)
	if is.state != "closed" || hasLabel(is, approvedLabel) {
		t.Fatalf("the draft is %s with labels %v, want closed and spent", is.state, is.labels)
	}
	if r := ownerRecord(t, h); !strings.HasSuffix(r.body, "\n"+link) {
		t.Fatalf("record = %q, want the link %s", r.body, link)
	}
	if msgs := h.signal.messages(); len(msgs) != 1 || !strings.Contains(msgs[0], "upstream as a") || !strings.HasSuffix(msgs[0], link) {
		t.Fatalf("messages = %q, want one publish notice", msgs)
	}
}

func TestAFailedRecordAndAnUpstreamRetitleDoNotPostTwice(t *testing.T) {
	for name, tc := range map[string]struct {
		mk   func(*testing.T) *harness
		link string
	}{"issue": {issueDraft, upstreamIssueURL(43)}, "comment": {commentDraft, ""}} {
		t.Run(name, func(t *testing.T) {
			h := tc.mk(t)
			h.gh.failWith("POST", forkDraftComments, fakeFailure{Status: http.StatusBadGateway, Times: 1})
			pollTimes(h, 1)
			h.gh.rename(testUpstream, 42, "bug(web): retitled by a maintainer", "maintainer")
			if name == "issue" {
				h.gh.rename(testUpstream, 43, "bug(web): retitled by a maintainer", "maintainer")
			}
			h.clock.advance(time.Minute)
			pollTimes(h, 2)
			link := tc.link
			if link == "" {
				link = fmt.Sprintf("%s#issuecomment-%d", upstreamIssueURL(42), h.gh.issue(testUpstream, 42).comments[0].id)
			}
			wantOnePostRecorded(t, h, link)
		})
	}
}

func TestAFailedSpendThenADeletedRecordDoesNotPostTwice(t *testing.T) {
	h := newDraft(t, issueLabel, draftTitle, "first text")
	h.gh.editBody(testFork, draftNumber, draftBody, testAgent)
	h.clock.advance(time.Minute)
	approveDraft(h)
	h.clock.advance(time.Minute)
	h.gh.failWith("DELETE", forkDraftApproval, fakeFailure{Status: http.StatusBadGateway, Times: 1})
	pollTimes(h, 1)
	h.gh.deleteComment(testFork, draftNumber, ownerRecord(t, h).id)
	h.gh.rename(testUpstream, 43, "bug(web): retitled by a maintainer", "maintainer")
	h.clock.advance(time.Minute)
	pollTimes(h, 2)
	wantOnePostRecorded(t, h, upstreamIssueURL(43))
}

func TestALostRecordThenAnAgentEditIsNotReportedAsRefused(t *testing.T) {
	h := issueDraft(t)
	h.gh.failWith("POST", forkDraftComments, fakeFailure{Status: http.StatusBadGateway, Times: 1})
	pollTimes(h, 1)
	h.gh.editBody(testFork, draftNumber, "agent text", testAgent)
	h.clock.advance(time.Minute)
	pollTimes(h, 2)
	wantOnePostRecorded(t, h, upstreamIssueURL(43))
}

// answerAfterDeadline delivers the matching response only once the request's context has expired.
type answerAfterDeadline struct {
	base         http.RoundTripper
	method, path string
}

func (a *answerAfterDeadline) RoundTrip(r *http.Request) (*http.Response, error) {
	if r.Method != a.method || r.URL.Path != a.path {
		return a.base.RoundTrip(r)
	}
	res, err := a.base.RoundTrip(r.WithContext(context.WithoutCancel(r.Context())))
	if err != nil {
		return nil, err
	}
	body, err := io.ReadAll(res.Body)
	_ = res.Body.Close()
	if err != nil {
		return nil, err
	}
	res.Body = io.NopCloser(bytes.NewReader(body))
	<-r.Context().Done()
	return res, nil
}

func TestTheCleanupOutlivesTheDraftTimeout(t *testing.T) {
	h := issueDraft(t)
	h.poller.draftTimeout = 100 * time.Millisecond
	gh := h.poller.gh
	gh.http = &http.Client{Transport: &answerAfterDeadline{base: gh.http.Transport, method: http.MethodPost, path: upstreamIssues}}
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()

	h.poller.poll(ctx)

	wantPublishedOnce(t, h, 0, upstreamIssues, map[string]any{"title": draftTitle, "body": draftBody}, upstreamIssueURL(43), issueMarker)
}

func TestTheDraftLabelsAreReadInOneQuery(t *testing.T) {
	h := issueDraft(t)
	pollTimes(h, 1)
	n := 0
	for _, c := range h.gh.allCalls() {
		if q, _ := c.Body["query"].(string); c.Path == "/graphql" && strings.Contains(q, "label(name:") {
			n++
		}
	}
	if n != 1 {
		t.Fatalf("label queries = %d, want 1", n)
	}
}

func TestTheLedgerForgetsDraftsThatLeaveTheList(t *testing.T) {
	h := issueDraft(t)
	h.gh.fail("DELETE", forkDraftApproval, http.StatusBadGateway)
	pollTimes(h, 1)
	if len(h.poller.posted) != 1 {
		t.Fatalf("ledger = %v, want the post kept while the approval is unspent", h.poller.posted)
	}
	h.gh.unlabel(testFork, draftNumber, approvedLabel, testAgent)
	pollTimes(h, 1)
	if len(h.poller.posted) != 0 {
		t.Fatalf("ledger = %v, want it pruned", h.poller.posted)
	}
}

func TestRecoveryFindsAPostFromTheApprovalsSecond(t *testing.T) {
	for name, kind := range map[string][]string{"issue": {issueLabel, draftTitle, draftBody}, "comment": {commentLabel, "Comment on #42", commentBody}} {
		t.Run(name, func(t *testing.T) {
			h := newDraft(t, kind[0], kind[1], kind[2])
			approveDraft(h)
			h.gh.failWith("POST", forkDraftComments, fakeFailure{Status: http.StatusBadGateway, Times: 1})
			pollTimes(h, 1)
			restart(h)
			pollTimes(h, 1)
			if up := upstreamWrites(h.gh.writes()); len(up) != 1 {
				t.Fatalf("upstream writes = %v, want one", writeSummary(up))
			}
			if is := draftState(h); is.state != "closed" {
				t.Fatalf("the draft is %s, want it recorded and closed", is.state)
			}
		})
	}
}
