package main

import (
	"bytes"
	"encoding/json"
	"io"
	"net/http"
	"strconv"
	"strings"
	"testing"
	"time"
)

type fakeResp struct {
	status int
	header http.Header
	raw    []byte
}

func (g *fakeGitHub) do(t *testing.T, method, path string, body any, headers ...string) fakeResp {
	t.Helper()
	var rd io.Reader
	if body != nil {
		raw, _ := json.Marshal(body)
		rd = bytes.NewReader(raw)
	}
	req, err := http.NewRequest(method, g.srv.URL+path, rd)
	if err != nil {
		t.Fatal(err)
	}
	req.Header.Set("Authorization", "Bearer "+testPAT)
	for i := 0; i+1 < len(headers); i += 2 {
		req.Header.Set(headers[i], headers[i+1])
	}
	res, err := g.srv.Client().Do(req)
	if err != nil {
		t.Fatal(err)
	}
	defer func() { _ = res.Body.Close() }()
	raw, _ := io.ReadAll(res.Body)
	return fakeResp{res.StatusCode, res.Header, raw}
}

func (r fakeResp) list(t *testing.T) []map[string]any {
	t.Helper()
	var out []map[string]any
	decodeJSON(t, r.raw, &out)
	return out
}

func (r fakeResp) object(t *testing.T) map[string]any {
	t.Helper()
	var out map[string]any
	decodeJSON(t, r.raw, &out)
	return out
}

func want(t *testing.T, r fakeResp, status int) fakeResp {
	t.Helper()
	if r.status != status {
		t.Fatalf("status = %d, want %d: %s", r.status, status, r.raw)
	}
	return r
}

func TestFakeGitHubPaginatesWithLinkHeaders(t *testing.T) {
	g := newFakeGitHub(t)
	g.readyPR(7, shaA)
	for i := 0; i < 250; i++ {
		g.addReview(7, "lkshrk", "COMMENTED", shaA)
	}
	sizes := []int{}
	for _, page := range []string{"1", "2", "3", "4"} {
		r := want(t, g.do(t, "GET", "/repos/"+testFork+"/pulls/7/reviews?per_page=100&page="+page, nil), 200)
		sizes = append(sizes, len(r.list(t)))
		link := r.header.Get("Link")
		if (page == "1" || page == "2") != strings.Contains(link, `rel="next"`) {
			t.Errorf("page %s Link = %q", page, link)
		}
	}
	if got := sizes; got[0] != 100 || got[1] != 100 || got[2] != 50 || got[3] != 0 {
		t.Fatalf("page sizes = %v", got)
	}
	if n := len(want(t, g.do(t, "GET", "/repos/"+testFork+"/pulls/7/reviews", nil), 200).list(t)); n != 30 {
		t.Fatalf("default page = %d, want 30", n)
	}
	r := want(t, g.do(t, "GET", "/repos/"+testFork+"/pulls/7/reviews?per_page=100", nil), 200)
	if !strings.Contains(r.header.Get("Link"), "page=3&per_page=100>; rel=\"last\"") {
		t.Fatalf("Link = %q", r.header.Get("Link"))
	}
}

func TestFakeGitHubAnswersIfNoneMatchWith304(t *testing.T) {
	g := newFakeGitHub(t)
	g.readyPR(7, shaA)
	first := want(t, g.do(t, "GET", "/repos/"+testFork+"/pulls/7", nil), 200)
	etag := first.header.Get("ETag")
	if !strings.HasPrefix(etag, `W/"`) {
		t.Fatalf("ETag = %q", etag)
	}
	billed := g.billedRequests()
	again := want(t, g.do(t, "GET", "/repos/"+testFork+"/pulls/7", nil, "If-None-Match", etag), 304)
	if len(again.raw) != 0 || g.billedRequests() != billed {
		t.Fatalf("304 body %q, billed %d -> %d", again.raw, billed, g.billedRequests())
	}
	g.rename(testFork, 7, "feat(web): renamed", "lkshrk")
	changed := want(t, g.do(t, "GET", "/repos/"+testFork+"/pulls/7", nil, "If-None-Match", etag), 200)
	if changed.header.Get("ETag") == etag || changed.object(t)["title"] != "feat(web): renamed" {
		t.Fatal("a changed resource kept its ETag")
	}
	if calls := g.allCalls(); calls[1].Status != 304 || calls[1].IfNoneMatch != etag {
		t.Fatalf("call log = %+v", calls[1])
	}
}

func TestFakeGitHubRefsRefuseNonFastForwardUnlessForced(t *testing.T) {
	g := newFakeGitHub(t)
	branch := "/repos/" + testFork + "/git/refs/heads/upstream/pr-7"
	want(t, g.do(t, "GET", "/repos/"+testFork+"/git/ref/heads/upstream/pr-7", nil), 404)
	want(t, g.do(t, "POST", "/repos/"+testFork+"/git/refs", map[string]any{"ref": "refs/heads/upstream/pr-7", "sha": shaA}), 201)
	want(t, g.do(t, "POST", "/repos/"+testFork+"/git/refs", map[string]any{"ref": "refs/heads/upstream/pr-7", "sha": shaA}), 422)
	want(t, g.do(t, "POST", "/repos/"+testUpstream+"/pulls", map[string]any{"title": "feat(web): x", "body": "b", "head": "lkshrk:upstream/pr-7", "base": "main", "maintainer_can_modify": false}), 201)

	want(t, g.do(t, "PATCH", branch, map[string]any{"sha": shaB}), 422)
	want(t, g.do(t, "PATCH", branch, map[string]any{"sha": shaB, "force": true}), 200)
	got := want(t, g.do(t, "GET", "/repos/"+testFork+"/git/ref/heads/upstream/pr-7", nil), 200).object(t)
	if got["object"].(map[string]any)["sha"] != shaB || got["ref"] != "refs/heads/upstream/pr-7" {
		t.Fatalf("ref = %v", got)
	}
	up := g.pullOf(testUpstream, 1)
	if up.sha != shaB || up.events[len(up.events)-1].event != "head_ref_force_pushed" {
		t.Fatalf("upstream PR head = %s, events %v", up.sha, up.events)
	}

	shaC := strings.Repeat("c", 40)
	g.commit(shaC, shaB)
	want(t, g.do(t, "PATCH", branch, map[string]any{"sha": shaC}), 200)
	if sha, _ := g.ref(testFork, "upstream/pr-7"); sha != shaC {
		t.Fatalf("fast-forward left %s", sha)
	}
	want(t, g.do(t, "PATCH", "/repos/"+testFork+"/git/refs/heads/missing", map[string]any{"sha": shaC, "force": true}), 422)
}

func TestFakeGitHubUpstreamPullsByHeadAndState(t *testing.T) {
	g := newFakeGitHub(t)
	g.setRef(testFork, "upstream/pr-7", shaA)
	create := map[string]any{"title": "feat(web): x", "body": "b", "head": "lkshrk:upstream/pr-7", "base": "main", "maintainer_can_modify": false}
	first := want(t, g.do(t, "POST", "/repos/"+testUpstream+"/pulls", create), 201).object(t)
	if first["maintainer_can_modify"] != false || first["head"].(map[string]any)["label"] != "lkshrk:upstream/pr-7" {
		t.Fatalf("created = %v", first)
	}
	want(t, g.do(t, "POST", "/repos/"+testUpstream+"/pulls", create), 422)
	g.closeIssue(testUpstream, 1, "maintainer")
	want(t, g.do(t, "POST", "/repos/"+testUpstream+"/pulls", create), 201)
	g.merge(testUpstream, 2, "maintainer")

	q := "/repos/" + testUpstream + "/pulls?head=lkshrk:upstream/pr-7&per_page=100"
	if n := len(want(t, g.do(t, "GET", q, nil), 200).list(t)); n != 0 {
		t.Fatalf("open = %d", n)
	}
	all := want(t, g.do(t, "GET", q+"&state=all", nil), 200).list(t)
	if len(all) != 2 || all[0]["number"] != 2.0 || all[0]["merged_at"] == nil || all[1]["merged_at"] != nil || all[1]["closed_at"] == nil {
		t.Fatalf("state=all = %v", all)
	}
	if n := len(want(t, g.do(t, "GET", "/repos/"+testUpstream+"/pulls?head=lkshrk:other&state=all", nil), 200).list(t)); n != 0 {
		t.Fatalf("other head = %d", n)
	}
	want(t, g.do(t, "PATCH", "/repos/"+testUpstream+"/pulls/2", map[string]any{"state": "open"}), 422)
}

func TestFakeGitHubReviewsAndDismissals(t *testing.T) {
	g := newFakeGitHub(t)
	g.readyPR(7, shaA)
	approved := want(t, g.do(t, "POST", "/repos/"+testFork+"/pulls/7/reviews", map[string]any{"commit_id": shaA, "event": "APPROVE", "body": ""}), 200).object(t)
	id := int64(approved["id"].(float64))
	commented := g.addReview(7, "lkshrk", "COMMENTED", shaA)
	g.addReview(7, "someone", "PENDING", shaA)

	dismissals := func(id int64) string {
		return "/repos/" + testFork + "/pulls/7/reviews/" + strconv.FormatInt(id, 10) + "/dismissals"
	}
	want(t, g.do(t, "PUT", dismissals(commented.id), map[string]any{"message": "no", "event": "DISMISS"}), 422)
	want(t, g.do(t, "PUT", dismissals(id), map[string]any{"event": "DISMISS"}), 422)
	got := want(t, g.do(t, "PUT", dismissals(id), map[string]any{"message": "edited after the review", "event": "DISMISS"}), 200).object(t)
	if got["state"] != "DISMISSED" || got["submitted_at"] == nil || got["commit_id"] != shaA {
		t.Fatalf("dismissed = %v", got)
	}
	list := want(t, g.do(t, "GET", "/repos/"+testFork+"/pulls/7/reviews?per_page=100", nil), 200).list(t)
	if len(list) != 2 || list[0]["state"] != "DISMISSED" || list[1]["state"] != "COMMENTED" {
		t.Fatalf("reviews = %v", list)
	}
	events := want(t, g.do(t, "GET", "/repos/"+testFork+"/issues/7/events", nil), 200).list(t)
	last := events[len(events)-1]
	if last["event"] != "review_dismissed" || last["dismissed_review"].(map[string]any)["state"] != "approved" {
		t.Fatalf("events = %v", events)
	}

	g.update(func() { g.prs[7].user = "lkshrk" })
	want(t, g.do(t, "POST", "/repos/"+testFork+"/pulls/7/reviews", map[string]any{"commit_id": shaA, "event": "APPROVE"}), 422)
}

func TestFakeGitHubEventsTimelineAndComments(t *testing.T) {
	g := newFakeGitHub(t)
	g.readyPR(7, shaA)
	g.rename(testFork, 7, "feat(web): other", "lkshrk")
	g.label(testFork, 7, "approved-upstream", "lkshrk")
	g.unlabel(testFork, 7, readyLabel, "lkshrk")
	g.tick(time.Second)
	g.changeBase(7, "next", "lkshrk")
	g.closeIssue(testFork, 7, "lkshrk")
	g.reopen(testFork, 7, "lkshrk")
	g.comment(testFork, 7, "npa-bot", "hello")
	g.addReview(7, "lkshrk", "APPROVED", shaA)

	var kinds []string
	for _, e := range want(t, g.do(t, "GET", "/repos/"+testFork+"/issues/7/events?per_page=100", nil), 200).list(t) {
		kinds = append(kinds, e["event"].(string))
		if e["event"] == "renamed" && e["rename"].(map[string]any)["from"] != "feat(web): add the thing" {
			t.Errorf("rename = %v", e["rename"])
		}
		if e["event"] == "labeled" && (e["label"].(map[string]any)["name"] != "approved-upstream" || e["actor"].(map[string]any)["login"] != "lkshrk") {
			t.Errorf("labeled = %v", e)
		}
	}
	if strings.Join(kinds, ",") != "renamed,labeled,unlabeled,base_ref_changed,closed,reopened" {
		t.Fatalf("events = %v", kinds)
	}
	var tl []string
	for _, e := range want(t, g.do(t, "GET", "/repos/"+testFork+"/issues/7/timeline?per_page=100", nil), 200).list(t) {
		s := e["event"].(string)
		if s == "reviewed" {
			s += ":" + e["state"].(string)
		}
		tl = append(tl, s)
	}
	if strings.Join(tl, ",") != "renamed,labeled,unlabeled,base_ref_changed,closed,reopened,commented,reviewed:approved" {
		t.Fatalf("timeline = %v", tl)
	}
	pr := want(t, g.do(t, "GET", "/repos/"+testFork+"/pulls/7", nil), 200).object(t)
	if pr["base"].(map[string]any)["ref"] != "next" || pr["state"] != "open" || pr["closed_at"] != nil {
		t.Fatalf("pull = %v", pr)
	}
	is := want(t, g.do(t, "GET", "/repos/"+testFork+"/issues/7", nil), 200).object(t)
	if is["pull_request"] == nil {
		t.Fatal("a pull request's issue lacks pull_request")
	}
	posted := want(t, g.do(t, "POST", "/repos/"+testFork+"/issues/7/comments", map[string]any{"body": "marker"}), 201).object(t)
	if posted["user"].(map[string]any)["login"] != "lkshrk" {
		t.Fatalf("comment = %v", posted)
	}
}

func TestFakeGitHubGraphQLEditHistory(t *testing.T) {
	g := newFakeGitHub(t)
	g.readyPR(7, shaA)
	created := g.prs[7].createdAt
	query := `query($owner:String!,$name:String!,$number:Int!,$after:String){repository(owner:$owner,name:$name){pullRequest(number:$number){lastEditedAt userContentEdits(first:2,after:$after){totalCount pageInfo{hasNextPage endCursor} nodes{editedAt deletedAt editor{login}}}}}}`
	ask := func(q string, vars map[string]any) map[string]any {
		return want(t, g.do(t, "POST", "/graphql", map[string]any{"query": q, "variables": vars}), 200).object(t)
	}
	vars := map[string]any{"owner": "lkshrk", "name": "omnigent", "number": 7}
	node := ask(query, vars)["data"].(map[string]any)["repository"].(map[string]any)["pullRequest"].(map[string]any)
	if node["lastEditedAt"] != nil || node["userContentEdits"].(map[string]any)["totalCount"] != 0.0 {
		t.Fatalf("unedited = %v", node)
	}

	g.tick(time.Minute)
	g.editBody(testFork, 7, "second", "npa-bot")
	g.tick(time.Minute)
	g.editBody(testFork, 7, "third", "npa-bot")
	g.deleteRevision(testFork, 7, 1, "npa-bot")
	node = ask(query, vars)["data"].(map[string]any)["repository"].(map[string]any)["pullRequest"].(map[string]any)
	edits := node["userContentEdits"].(map[string]any)
	nodes := edits["nodes"].([]any)
	if node["lastEditedAt"] != ts(created.Add(2*time.Minute)) || edits["totalCount"] != 3.0 || len(nodes) != 2 {
		t.Fatalf("edited = %v", node)
	}
	if nodes[0].(map[string]any)["editedAt"] != ts(created.Add(2*time.Minute)) || nodes[1].(map[string]any)["deletedAt"] == nil {
		t.Fatalf("nodes not newest first or deletion lost: %v", nodes)
	}
	info := edits["pageInfo"].(map[string]any)
	vars["after"] = info["endCursor"]
	rest := ask(query, vars)["data"].(map[string]any)["repository"].(map[string]any)["pullRequest"].(map[string]any)["userContentEdits"].(map[string]any)
	if info["hasNextPage"] != true || len(rest["nodes"].([]any)) != 1 || rest["nodes"].([]any)[0].(map[string]any)["editedAt"] != ts(created) {
		t.Fatalf("second page = %v", rest)
	}

	wrong := ask(`{repository(owner:"lkshrk",name:"omnigent"){issue(number:7){lastEditedAt}}}`, nil)
	if wrong["errors"] == nil || wrong["data"].(map[string]any)["repository"].(map[string]any)["issue"] != nil {
		t.Fatalf("issue() on a pull request = %v", wrong)
	}
	g.failGraphQL("Something went wrong while executing your query.")
	if failed := ask(query, vars); failed["errors"] == nil || failed["data"] != nil {
		t.Fatalf("failure = %v", failed)
	}
}

func TestFakeGitHubCompareAndCheckRuns(t *testing.T) {
	g := newFakeGitHub(t)
	g.readyPR(7, shaA)
	g.setCompare(testUpstream, "main..."+shaA, "diverged", 1, 4, map[string]any{"filename": "web/src/thing.ts", "sha": "deadbeef"}, map[string]any{"filename": "b.ts", "previous_filename": "a.ts", "status": "renamed"})
	up := want(t, g.do(t, "GET", "/repos/"+testUpstream+"/compare/main..."+shaA, nil), 200).object(t)
	files := up["files"].([]any)
	if up["status"] != "diverged" || up["behind_by"] != 4.0 || len(files) != 2 || files[1].(map[string]any)["previous_filename"] != "a.ts" {
		t.Fatalf("compare = %v", up)
	}
	fork := want(t, g.do(t, "GET", "/repos/"+testFork+"/compare/lkshrk:main..."+shaA, nil), 200).object(t)
	if f := fork["files"].([]any)[0].(map[string]any); f["filename"] != "web/src/thing.ts" || len(f["sha"].(string)) != 40 {
		t.Fatalf("fork compare = %v", fork)
	}
	if page2 := want(t, g.do(t, "GET", "/repos/"+testUpstream+"/compare/main..."+shaA+"?page=2", nil), 200).object(t); len(page2["files"].([]any)) != 0 {
		t.Fatal("files repeated after the first page")
	}

	g.addCheckRun(testFork, shaA, "test", "completed", "success", ".github/workflows/contribution-ci.yml")
	g.addCheckRun(testFork, shaA, "lint", "in_progress", "", ".github/workflows/other.yml")
	checks := want(t, g.do(t, "GET", "/repos/"+testFork+"/commits/"+shaA+"/check-runs?per_page=1", nil), 200).object(t)
	if checks["total_count"] != 2.0 || len(checks["check_runs"].([]any)) != 1 {
		t.Fatalf("check runs = %v", checks)
	}
	runs := want(t, g.do(t, "GET", "/repos/"+testFork+"/actions/workflows/contribution-ci.yml/runs?head_sha="+shaA, nil), 200).object(t)
	suite := checks["check_runs"].([]any)[0].(map[string]any)["check_suite"].(map[string]any)["id"]
	if runs["total_count"] != 1.0 || runs["workflow_runs"].([]any)[0].(map[string]any)["check_suite_id"] != suite {
		t.Fatalf("workflow runs = %v, suite %v", runs, suite)
	}
}

func TestFakeGitHubRejectsOtherTokensWithoutEchoingThem(t *testing.T) {
	g := newFakeGitHub(t)
	req, _ := http.NewRequest("GET", g.srv.URL+"/user", nil)
	req.Header.Set("Authorization", "Bearer wrong-token-value")
	res, err := g.srv.Client().Do(req)
	if err != nil {
		t.Fatal(err)
	}
	raw, _ := io.ReadAll(res.Body)
	_ = res.Body.Close()
	if res.StatusCode != 401 || strings.Contains(string(raw), "wrong-token-value") {
		t.Fatalf("status %d body %s", res.StatusCode, raw)
	}
	g.fail("GET", "/user", 502)
	if r := g.do(t, "GET", "/user", nil); r.status != 502 || strings.Contains(string(r.raw), testPAT) {
		t.Fatalf("injected failure = %d %s", r.status, r.raw)
	}
	if len(g.writes()) != 0 {
		t.Fatalf("writes = %v", g.writes())
	}
}

func TestFakeGitHubDraftsAcrossRepositories(t *testing.T) {
	g := newFakeGitHub(t)
	g.addRepo("lkshrk/other", "other-org/other", "trunk", shaMain)
	g.addRepo("other-org/other", "", "trunk", shaUpMain)
	if r := g.repo("lkshrk/other"); r.defaultBranch != "trunk" {
		t.Fatalf("repo = %+v", r)
	}
	draft := g.addIssue(testFork, 0, "bug(web): sidebar", "Steps.", testAgent, "upstream-issue")
	g.label(testFork, draft.number, "approved-upstream", "lkshrk")
	g.addIssue(testUpstream, 40, "older", "x", "someone")
	filed := want(t, g.do(t, "POST", "/repos/"+testUpstream+"/issues", map[string]any{"title": "bug(web): sidebar", "body": "Steps."}), 201).object(t)
	if filed["number"] != 41.0 || filed["pull_request"] != nil {
		t.Fatalf("filed = %v", filed)
	}
	mine := want(t, g.do(t, "GET", "/repos/"+testUpstream+"/issues?creator=lkshrk&state=all", nil), 200).list(t)
	if len(mine) != 1 || mine[0]["number"] != 41.0 {
		t.Fatalf("creator filter = %v", mine)
	}
	want(t, g.do(t, "POST", "/repos/"+testUpstream+"/issues/99/comments", map[string]any{"body": "hi"}), 404)
	want(t, g.do(t, "POST", "/repos/"+testUpstream+"/issues/40/comments", map[string]any{"body": "hi"}), 201)
	want(t, g.do(t, "PATCH", "/repos/"+testFork+"/issues/"+strconv.Itoa(draft.number), map[string]any{"state": "closed"}), 200)
	if is := g.issue(testFork, draft.number); is.state != "closed" || is.stateReason != "completed" {
		t.Fatalf("draft = %+v", is)
	}
	labelled := want(t, g.do(t, "GET", "/repos/"+testFork+"/issues?labels=approved-upstream&state=all", nil), 200).list(t)
	if len(labelled) != 1 {
		t.Fatalf("label filter = %v", labelled)
	}

	pr := g.addPull("other-org/other", &fakePR{fakeIssue: &fakeIssue{title: "t", user: "lkshrk"}, headRepo: "lkshrk/other", headRef: "upstream/pr-3", sha: shaA})
	if pr.base != "trunk" || pr.number != 1 {
		t.Fatalf("pull = %+v", pr)
	}
	g.readyPR(7, shaA)
	rv := g.addReview(7, "lkshrk", "APPROVED", shaA)
	g.dismissReview(7, rv.id, "lkshrk", "spent")
	if rv.state != "DISMISSED" || g.callCount("POST", "/repos/"+testUpstream+"/issues") != 1 {
		t.Fatalf("review %s, calls %v", rv.state, g.writes())
	}
}
