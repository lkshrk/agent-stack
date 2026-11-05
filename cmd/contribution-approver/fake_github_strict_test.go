package main

import (
	"fmt"
	"net/http"
	"slices"
	"strings"
	"sync"
	"testing"
	"time"
)

type fakeRecorderT struct {
	testing.TB
	mu   sync.Mutex
	errs []string
}

type fakeFatal struct{}

func (r *fakeRecorderT) Errorf(format string, args ...any) {
	r.mu.Lock()
	defer r.mu.Unlock()
	r.errs = append(r.errs, fmt.Sprintf(format, args...))
}

func (r *fakeRecorderT) Fatalf(format string, args ...any) {
	r.Errorf(format, args...)
	panic(fakeFatal{})
}

func (r *fakeRecorderT) take() []string {
	r.mu.Lock()
	defer r.mu.Unlock()
	out := r.errs
	r.errs = nil
	return out
}

func recordFake(t *testing.T) (*fakeGitHub, *fakeRecorderT) {
	g := newFakeGitHub(t)
	rec := &fakeRecorderT{TB: t}
	g.t = rec
	return g, rec
}

func expectFatal(t *testing.T, rec *fakeRecorderT, want string, f func()) {
	t.Helper()
	func() {
		defer func() {
			if _, ok := recover().(fakeFatal); !ok {
				t.Errorf("no Fatalf for %q", want)
			}
		}()
		f()
	}()
	if errs := rec.take(); len(errs) != 1 || !strings.Contains(errs[0], want) {
		t.Errorf("Fatalf = %v, want %q", errs, want)
	}
}

func upstreamPull(t *testing.T, g *fakeGitHub, branch string) {
	t.Helper()
	g.setRef(testFork, branch, shaA)
	wantStatus(t, g.do(t, "POST", "/repos/"+testUpstream+"/pulls", map[string]any{"title": "feat(web): x", "head": "lkshrk:" + branch, "base": "main"}), 201)
}

func TestFakeGitHubIgnoresHeadWithoutOwner(t *testing.T) {
	g := newFakeGitHub(t)
	upstreamPull(t, g, "upstream/pr-7")
	upstreamPull(t, g, "upstream/pr-8")
	if n := len(wantStatus(t, g.do(t, "GET", "/repos/"+testUpstream+"/pulls?head=upstream/pr-7&state=all", nil), 200).list(t)); n != 2 {
		t.Fatalf("head without owner returned %d pulls, want all 2", n)
	}
	if n := len(wantStatus(t, g.do(t, "GET", "/repos/"+testUpstream+"/pulls?head=lkshrk:upstream/pr-7&state=all", nil), 200).list(t)); n != 1 {
		t.Fatalf("owner:branch returned %d pulls", n)
	}
}

func TestFakeGitHubGraphQLReturnsOnlySelectedFieldsAndRejectsUnsafeQueries(t *testing.T) {
	g, rec := recordFake(t)
	g.readyPR(7, shaA)
	g.editBody(testFork, 7, "second", testAgent)
	ask := func(q string) map[string]any {
		return wantStatus(t, g.do(t, "POST", "/graphql", map[string]any{"query": q}), 200).object(t)
	}
	got := ask(`{repository(owner:"lkshrk",name:"omnigent"){pullRequest(number:7){lastEditedAt userContentEdits(first:1){nodes{editedAt deletedAt}}}}}`)
	node := got["data"].(map[string]any)["repository"].(map[string]any)["pullRequest"].(map[string]any)
	edit := node["userContentEdits"].(map[string]any)["nodes"].([]any)[0].(map[string]any)
	if len(node) != 2 || len(node["userContentEdits"].(map[string]any)) != 1 || len(edit) != 2 {
		t.Fatalf("unselected fields returned: %v", node)
	}
	if errs := rec.take(); len(errs) != 0 {
		t.Fatalf("a safe query failed: %v", errs)
	}
	for q, why := range map[string]string{
		`{repository(owner:"lkshrk",name:"omnigent"){pullRequest(number:7){body}}}`:                                                                                    "lastEditedAt",
		`{repository(owner:"lkshrk",name:"omnigent"){pullRequest(number:7){lastEditedAt userContentEdits(first:1){nodes{editedAt}}}}}`:                                 "deletedAt",
		`{repository(owner:"lkshrk",name:"omnigent"){pullRequest(number:7){lastEditedAt userContentEdits(first:50){nodes{editedAt deletedAt}}}}}`:                      "pageInfo",
		`{repository(owner:"lkshrk",name:"omnigent"){pullRequest(number:7){lastEditedAt userContentEdits(first:1){pageInfo{hasNextPage} nodes{editedAt deletedAt}}}}}`: "",
		`{repository(owner:"lkshrk",name:"omnigent"){pullRequest(number:7){lastEditedAt mergeable}}}`:                                                                  "mergeable",
		`{repository(owner:"lkshrk",name:"omnigent"){pr:pullRequest(number:7){lastEditedAt}}}`:                                                                         "alias",
	} {
		resp := ask(q)
		errs := rec.take()
		if why == "" {
			if len(errs) != 0 || resp["errors"] != nil {
				t.Errorf("%s: %v %v", q, errs, resp)
			}
			continue
		}
		if len(errs) != 1 || !strings.Contains(errs[0], why) || resp["errors"] == nil {
			t.Errorf("%s: test errors %v, response %v, want %q", q, errs, resp, why)
		}
	}
}

func TestFakeGitHubDerivesAuthorAssociation(t *testing.T) {
	g := newFakeGitHub(t)
	g.readyPR(7, shaA)
	g.addReview(7, "lkshrk", "COMMENTED", shaA)
	g.addReview(7, "stranger", "APPROVED", shaA)
	g.addMember(testFork, "friend")
	g.addReview(7, "friend", "COMMENTED", shaA)
	g.comment(testFork, 7, "stranger", "hi")
	g.addPull(testFork, fakePR{fakeIssue: &fakeIssue{title: "t", user: "helper"}, headRef: "h", sha: shaB})
	g.merge(testFork, 8, "lkshrk")
	g.comment(testFork, 7, "helper", "thanks")

	var got []string
	for _, rv := range wantStatus(t, g.do(t, "GET", "/repos/"+testFork+"/pulls/7/reviews", nil), 200).list(t) {
		got = append(got, rv["user"].(map[string]any)["login"].(string)+"="+rv["author_association"].(string))
	}
	for _, c := range wantStatus(t, g.do(t, "GET", "/repos/"+testFork+"/issues/7/comments", nil), 200).list(t) {
		got = append(got, c["user"].(map[string]any)["login"].(string)+"="+c["author_association"].(string))
	}
	pr := wantStatus(t, g.do(t, "GET", "/repos/"+testFork+"/pulls/7", nil), 200).object(t)
	got = append(got, "author="+pr["author_association"].(string))
	if strings.Join(got, ",") != "lkshrk=OWNER,stranger=NONE,friend=MEMBER,stranger=NONE,helper=CONTRIBUTOR,author=NONE" {
		t.Fatalf("associations = %v", got)
	}
	posted := wantStatus(t, g.do(t, "POST", "/repos/"+testUpstream+"/issues/"+fmt.Sprint(g.addIssue(testUpstream, 0, "t", "b", "maint").number)+"/comments", map[string]any{"body": "x"}), 201).object(t)
	if posted["author_association"] != "NONE" {
		t.Fatalf("the token user upstream = %v", posted["author_association"])
	}
}

func TestFakeGitHubCheckRunsFilterLatestByDefault(t *testing.T) {
	g := newFakeGitHub(t)
	ci := ".github/workflows/contribution-ci.yml"
	g.addCheckRun(testFork, shaA, fakeCheckRun{name: "test", status: "completed", conclusion: "failure", path: ci})
	g.addCheckRun(testFork, shaA, fakeCheckRun{name: "test", status: "completed", conclusion: "success", path: ci})
	base := "/repos/" + testFork + "/commits/" + shaA + "/check-runs"
	latest := wantStatus(t, g.do(t, "GET", base, nil), 200).object(t)
	runs := latest["check_runs"].([]any)
	if latest["total_count"] != 1.0 || len(runs) != 1 || runs[0].(map[string]any)["conclusion"] != "success" {
		t.Fatalf("latest = %v", latest)
	}
	if all := wantStatus(t, g.do(t, "GET", base+"?filter=all", nil), 200).object(t); all["total_count"] != 2.0 {
		t.Fatalf("all = %v", all)
	}
	wantStatus(t, g.do(t, "GET", base+"?filter=newest", nil), 422)
}

func TestFakeGitHubDeletedRevisionModes(t *testing.T) {
	q := `query($after:String){repository(owner:"lkshrk",name:"omnigent"){pullRequest(number:7){lastEditedAt userContentEdits(first:10,after:$after){totalCount pageInfo{hasNextPage endCursor} nodes{editedAt deletedAt}}}}}`
	for mode, wantTotal := range map[fakeRevisionDeletion]float64{fakeEditSoftDeleted: 3, fakeEditDropped: 2} {
		g := newFakeGitHub(t)
		pr := g.readyPR(7, shaA)
		created := pr.createdAt
		g.tick(time.Minute)
		g.editBody(testFork, 7, "second", testAgent)
		g.tick(time.Minute)
		g.editBody(testFork, 7, "third", testAgent)
		g.deleteRevision(testFork, 7, 2, testAgent, mode)
		node := wantStatus(t, g.do(t, "POST", "/graphql", map[string]any{"query": q}), 200).object(t)["data"].(map[string]any)["repository"].(map[string]any)["pullRequest"].(map[string]any)
		edits := node["userContentEdits"].(map[string]any)
		wantLast := fakeTS(created.Add(2 * time.Minute))
		if mode == fakeEditDropped {
			wantLast = fakeTS(created.Add(time.Minute))
		}
		if edits["totalCount"] != wantTotal || node["lastEditedAt"] != wantLast {
			t.Errorf("mode %d: %v", mode, node)
		}
		if mode == fakeEditSoftDeleted && edits["nodes"].([]any)[0].(map[string]any)["deletedAt"] == nil {
			t.Errorf("soft delete lost deletedAt: %v", edits)
		}
	}
	g := newFakeGitHub(t)
	g.readyPR(7, shaA)
	g.editBody(testFork, 7, "second", testAgent)
	g.deleteRevision(testFork, 7, 1, testAgent, fakeEditDropped)
	if is := g.issue(testFork, 7); is.lastEditedAt != nil || len(is.edits) != 0 {
		t.Fatalf("dropping the only edit left %v %v", is.lastEditedAt, is.edits)
	}
}

func TestFakeGitHubCompareFollowsRefsAndCommitGraph(t *testing.T) {
	g := newFakeGitHub(t)
	root, slipped, head := strings.Repeat("0", 40), strings.Repeat("5", 40), strings.Repeat("6", 40)
	g.addCommit(root, nil, map[string]string{"README.md": "r1"})
	g.setRef(testUpstream, "main", root)
	g.setRef(testFork, "main", root)
	g.addCommit(slipped, []string{root}, map[string]string{"web/evil.ts": "e1"})
	g.addCommit(head, []string{slipped}, map[string]string{"web/thing.ts": "t1"})
	names := func(path string) (string, []string) {
		cmp := wantStatus(t, g.do(t, "GET", path, nil), 200).object(t)
		var out []string
		for _, f := range cmp["files"].([]any) {
			out = append(out, f.(map[string]any)["filename"].(string)+"@"+f.(map[string]any)["sha"].(string))
		}
		return cmp["status"].(string), out
	}
	forkCmp := "/repos/" + testFork + "/compare/lkshrk:main..." + head
	upCmp := "/repos/" + testFork + "/compare/omnigent-ai:main..." + head
	if st, f := names(forkCmp); st != "ahead" || !slices.Equal(f, []string{"web/evil.ts@e1", "web/thing.ts@t1"}) {
		t.Fatalf("fork compare before = %s %v", st, f)
	}
	g.setRef(testFork, "main", slipped)
	if st, f := names(forkCmp); st != "ahead" || !slices.Equal(f, []string{"web/thing.ts@t1"}) {
		t.Fatalf("fork compare after main moved = %s %v", st, f)
	}
	if st, f := names(upCmp); st != "ahead" || !slices.Equal(f, []string{"web/evil.ts@e1", "web/thing.ts@t1"}) {
		t.Fatalf("upstream compare = %s %v", st, f)
	}
	other := strings.Repeat("7", 40)
	g.addCommit(other, []string{root}, map[string]string{"README.md": "r2"})
	g.setRef(testUpstream, "main", other)
	if st, _ := names(upCmp); st != "diverged" {
		t.Fatalf("upstream moved = %s", st)
	}
	g.addCommit(strings.Repeat("8", 40), nil, nil)
	wantStatus(t, g.do(t, "GET", "/repos/"+testFork+"/compare/lkshrk:main..."+strings.Repeat("8", 40), nil), 404)
}

func TestFakeGitHubFailureModes(t *testing.T) {
	g := newFakeGitHub(t)
	g.setRef(testFork, "upstream/pr-7", shaA)
	create := map[string]any{"title": "feat(web): x", "head": "lkshrk:upstream/pr-7", "base": "main"}
	g.failWith("POST", "/repos/"+testUpstream+"/pulls", fakeFailure{Status: 502, After: true, Times: 1})
	wantStatus(t, g.do(t, "POST", "/repos/"+testUpstream+"/pulls", create), 502)
	if n := len(wantStatus(t, g.do(t, "GET", "/repos/"+testUpstream+"/pulls?head=lkshrk:upstream/pr-7", nil), 200).list(t)); n != 1 {
		t.Fatalf("an After failure did not apply the write: %d pulls", n)
	}
	wantStatus(t, g.do(t, "POST", "/repos/"+testUpstream+"/pulls", create), 422)

	g.failWith("POST", "/repos/"+testFork+"/git/refs", fakeFailure{Status: fakeDrop, After: true})
	req, _ := http.NewRequest("POST", g.srv.URL+"/repos/"+testFork+"/git/refs", strings.NewReader(`{"ref":"refs/heads/x","sha":"`+shaB+`"}`))
	req.Header.Set("Authorization", "Bearer "+testPAT)
	if res, err := g.srv.Client().Do(req); err == nil {
		_ = res.Body.Close()
		t.Fatalf("a dropped connection returned HTTP %d", res.StatusCode)
	}
	if sha, ok := g.ref(testFork, "x"); !ok || sha != shaB {
		t.Fatalf("the dropped write was not applied: %q", sha)
	}

	g.failWith("GET", "/user", fakeFailure{Status: 503, Skip: 1, Times: 1})
	var got []int
	for range 3 {
		got = append(got, g.do(t, "GET", "/user", nil).status)
	}
	if !slices.Equal(got, []int{200, 503, 200}) {
		t.Fatalf("counted failure = %v", got)
	}
}

func TestFakeGitHubSpoofableRunMetadata(t *testing.T) {
	g := newFakeGitHub(t)
	g.addCheckRun(testFork, shaA, fakeCheckRun{name: "test", status: "completed", conclusion: "success", path: ".github/workflows/contribution-ci.yml", app: "spoof-app", event: "push", headBranch: "evil"})
	g.update(func() {
		g.runs[shaA] = []fakeRun{{status: "completed", conclusion: "success", path: ".github/workflows/contribution-ci.yml", event: "workflow_dispatch", headBranch: "evil"}}
	})
	cr := wantStatus(t, g.do(t, "GET", "/repos/"+testFork+"/commits/"+shaA+"/check-runs", nil), 200).object(t)["check_runs"].([]any)[0].(map[string]any)
	suite := cr["check_suite"].(map[string]any)
	if cr["app"].(map[string]any)["slug"] != "spoof-app" || suite["event"] != "push" || suite["head_branch"] != "evil" {
		t.Fatalf("check run = %v", cr)
	}
	run := wantStatus(t, g.do(t, "GET", "/repos/"+testFork+"/actions/workflows/contribution-ci.yml/runs?head_sha="+shaA, nil), 200).object(t)["workflow_runs"].([]any)[0].(map[string]any)
	if run["event"] != "workflow_dispatch" || run["head_branch"] != "evil" {
		t.Fatalf("workflow run = %v", run)
	}
}

func TestFakeGitHubHelpersFailLoudly(t *testing.T) {
	g, rec := recordFake(t)
	expectFatal(t, rec, "no pull request", func() { g.addReview(99, "lkshrk", "APPROVED", shaA) })
	expectFatal(t, rec, "no pull request", func() { g.changeBase(99, "next", "lkshrk") })
	expectFatal(t, rec, "no pull request", func() { g.merge(testUpstream, 5, "x") })
	g.readyPR(7, shaA)
	expectFatal(t, rec, "no revision", func() { g.deleteRevision(testFork, 7, 0, "x", fakeEditDropped) })
	g.merge(testFork, 7, "lkshrk")
	expectFatal(t, rec, "cannot be reopened", func() { g.reopen(testFork, 7, "lkshrk") })
	wantStatus(t, g.do(t, "PATCH", "/repos/"+testFork+"/issues/7", map[string]any{"state": "open"}), 422)
	if g.issue(testFork, 7).state != "closed" {
		t.Fatal("a merged PR was reopened through the issues API")
	}
}

func TestFakeGitHubPatchPullIsAtomic(t *testing.T) {
	g := newFakeGitHub(t)
	g.readyPR(7, shaA)
	wantStatus(t, g.do(t, "PATCH", "/repos/"+testFork+"/pulls/7", map[string]any{"title": "feat(web): new", "body": "new", "base": "missing"}), 422)
	pr := g.pullOf(testFork, 7)
	if pr.title != "feat(web): add the thing" || pr.lastEditedAt != nil || len(pr.events) != 0 {
		t.Fatalf("a rejected PATCH changed the PR: %q %v %v", pr.title, pr.lastEditedAt, pr.events)
	}
}

func TestFakeGitHubReviewNeedsAKnownCommit(t *testing.T) {
	g := newFakeGitHub(t)
	g.readyPR(7, shaA)
	wantStatus(t, g.do(t, "POST", "/repos/"+testFork+"/pulls/7/reviews", map[string]any{"commit_id": strings.Repeat("9", 40), "event": "APPROVE"}), 422)
	if n := len(g.reviewsOf(7)); n != 0 {
		t.Fatalf("reviews = %d", n)
	}
	g.update(func() { g.prs[7].sha = shaB })
	wantStatus(t, g.do(t, "POST", "/repos/"+testFork+"/pulls/7/reviews", map[string]any{"commit_id": shaA, "event": "APPROVE"}), 200)
}

func TestFakeGitHubHelpersReturnCopies(t *testing.T) {
	g := newFakeGitHub(t)
	g.readyPR(7, shaA)
	snap := g.pullOf(testFork, 7)
	snap.title, snap.labels[0] = "changed", "changed"
	rv := g.addReview(7, "lkshrk", "APPROVED", shaA)
	rv.state = "DISMISSED"
	is := g.issue(testFork, 7)
	is.labels = append(is.labels, "x")
	r := g.repo(testFork)
	r.refs["main"] = shaB
	if pr := g.pullOf(testFork, 7); pr.title != "feat(web): add the thing" || pr.labels[0] != readyLabel || pr.reviews[0].state != "APPROVED" || len(pr.labels) != 1 {
		t.Fatalf("a snapshot aliased live state: %+v", pr)
	}
	if sha, _ := g.ref(testFork, "main"); sha != shaMain {
		t.Fatalf("repo snapshot aliased refs: %s", sha)
	}
}
