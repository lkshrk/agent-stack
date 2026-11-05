package main

import (
	"fmt"
	"maps"
	"net/http"
	"regexp"
	"slices"
	"sort"
	"strings"
)

func (g *fakeGitHub) routeTable() []fakeRoute {
	const repo = `/repos/(?P<repo>[^/]+/[^/]+)`
	table := []struct {
		method, pattern string
		handle          func(*fakeReq)
	}{
		{"GET", `/user`, func(c *fakeReq) { c.send(http.StatusOK, fakeUser(g.login)) }},
		{"POST", `/graphql`, g.graphql},
		{"GET", repo, g.getRepo},
		{"GET", repo + `/pulls`, g.listPulls},
		{"POST", repo + `/pulls`, g.createPull},
		{"GET", repo + `/pulls/(?P<n>\d+)`, g.getPull},
		{"PATCH", repo + `/pulls/(?P<n>\d+)`, g.patchPull},
		{"GET", repo + `/pulls/(?P<n>\d+)/reviews`, g.listReviews},
		{"POST", repo + `/pulls/(?P<n>\d+)/reviews`, g.createReview},
		{"GET", repo + `/pulls/(?P<n>\d+)/reviews/(?P<id>\d+)`, g.getReview},
		{"PUT", repo + `/pulls/(?P<n>\d+)/reviews/(?P<id>\d+)/dismissals`, g.dismissal},
		{"GET", repo + `/issues`, g.listIssues},
		{"POST", repo + `/issues`, g.createIssue},
		{"GET", repo + `/issues/(?P<n>\d+)`, g.getIssue},
		{"PATCH", repo + `/issues/(?P<n>\d+)`, g.patchIssue},
		{"GET", repo + `/issues/(?P<n>\d+)/events`, g.listEvents},
		{"GET", repo + `/issues/(?P<n>\d+)/timeline`, g.listTimeline},
		{"GET", repo + `/issues/(?P<n>\d+)/comments`, g.listComments},
		{"POST", repo + `/issues/(?P<n>\d+)/comments`, g.createComment},
		{"POST", repo + `/issues/(?P<n>\d+)/labels`, g.addLabels},
		{"DELETE", repo + `/issues/(?P<n>\d+)/labels/(?P<name>[^/]+)`, g.deleteLabel},
		{"GET", repo + `/git/ref/heads/(?P<branch>.+)`, g.getRef},
		{"POST", repo + `/git/refs`, g.createRef},
		{"PATCH", repo + `/git/refs/heads/(?P<branch>.+)`, g.updateRef},
		{"DELETE", repo + `/git/refs/heads/(?P<branch>.+)`, g.deleteRef},
		{"GET", repo + `/compare/(?P<base>.+)\.\.\.(?P<head>[^.]+)`, g.compare},
		{"GET", repo + `/actions/workflows/(?P<file>[^/]+)/runs`, g.workflowRuns},
		{"GET", repo + `/commits/(?P<ref>.+)/check-runs`, g.checkRuns},
	}
	out := make([]fakeRoute, 0, len(table))
	for _, r := range table {
		out = append(out, fakeRoute{r.method, regexp.MustCompile("^" + r.pattern + "$"), r.handle})
	}
	return out
}

func (g *fakeGitHub) getRepo(c *fakeReq) {
	if r := c.repo(); r != nil {
		c.send(http.StatusOK, g.repoJSON(r))
	}
}

func (c *fakeReq) pull() (*fakeRepo, *fakePR) {
	r := c.repo()
	if r == nil {
		return nil, nil
	}
	pr, ok := r.pulls[c.num("n")]
	if !ok {
		c.error(http.StatusNotFound, "Not Found")
		return nil, nil
	}
	return r, pr
}

func (c *fakeReq) issue() (*fakeRepo, *fakeIssue) {
	r := c.repo()
	if r == nil {
		return nil, nil
	}
	is, ok := r.issues[c.num("n")]
	if !ok {
		c.error(http.StatusNotFound, "Not Found")
		return nil, nil
	}
	return r, is
}

func fakeHeadLabel(pr *fakePR) string {
	return strings.SplitN(pr.headRepo, "/", 2)[0] + ":" + pr.headRef
}

func fakeNumbersDesc[T any](m map[int]T) []int {
	var ns []int
	for n := range m {
		ns = append(ns, n)
	}
	sort.Sort(sort.Reverse(sort.IntSlice(ns)))
	return ns
}

func (g *fakeGitHub) listPulls(c *fakeReq) {
	r := c.repo()
	if r == nil {
		return
	}
	state := c.query.Get("state")
	if state == "" {
		state = "open"
	}
	head, base := c.query.Get("head"), c.query.Get("base")
	if !strings.Contains(head, ":") {
		head = ""
	}
	ns := fakeNumbersDesc(r.pulls)
	if c.query.Get("direction") == "asc" {
		sort.Ints(ns)
	}
	items := []any{}
	for _, n := range ns {
		pr := r.pulls[n]
		if (state != "all" && pr.state != state) || (head != "" && !strings.EqualFold(fakeHeadLabel(pr), head)) || (base != "" && pr.base != base) {
			continue
		}
		items = append(items, g.prJSON(r, pr, false))
	}
	c.list(items)
}

func (g *fakeGitHub) getPull(c *fakeReq) {
	if r, pr := c.pull(); pr != nil {
		c.send(http.StatusOK, g.prJSON(r, pr, true))
	}
}

func (g *fakeGitHub) createPull(c *fakeReq) {
	r := c.repo()
	if r == nil {
		return
	}
	title, _ := c.str("title")
	body, _ := c.str("body")
	head, _ := c.str("head")
	base, _ := c.str("base")
	if title == "" || head == "" || base == "" {
		c.invalid("title, head and base are required")
		return
	}
	owner, branch, found := strings.Cut(head, ":")
	if !found {
		owner, branch = r.owner, head
	}
	var headRepo *fakeRepo
	for _, cand := range g.repos {
		if strings.EqualFold(cand.owner, owner) && (cand == r || strings.EqualFold(cand.parent, r.fullName)) {
			headRepo = cand
		}
	}
	if headRepo == nil {
		c.invalid("head " + head + " is not in the repository network")
		return
	}
	sha, ok := headRepo.refs[branch]
	if !ok {
		c.invalid("head " + head + " does not exist")
		return
	}
	if _, ok := r.refs[base]; !ok {
		c.invalid("base " + base + " does not exist")
		return
	}
	for _, pr := range r.pulls {
		if pr.state == "open" && pr.base == base && strings.EqualFold(fakeHeadLabel(pr), owner+":"+branch) {
			c.invalid("A pull request already exists for " + owner + ":" + branch + ".")
			return
		}
	}
	draft, _ := c.body["draft"].(bool)
	mcm, _ := c.body["maintainer_can_modify"].(bool)
	pr := g.newPull(r, &fakePR{
		fakeIssue: &fakeIssue{title: title, body: body, user: g.login},
		headRef:   branch, headRepo: headRepo.fullName, base: base, sha: sha, draft: draft, maintainerCanModify: mcm,
	})
	c.send(http.StatusCreated, g.prJSON(r, pr, true))
}

func (g *fakeGitHub) patchPull(c *fakeReq) {
	r, pr := c.pull()
	if pr == nil {
		return
	}
	title, hasTitle := c.str("title")
	body, hasBody := c.str("body")
	base, hasBase := c.str("base")
	state, hasState := c.str("state")
	mcm, hasMCM := c.body["maintainer_can_modify"].(bool)
	switch {
	case hasTitle && title == "":
		c.invalid("title cannot be blank")
		return
	case hasBase && r.refs[base] == "":
		c.invalid("base " + base + " does not exist")
		return
	case hasState && state != "open" && state != "closed":
		c.invalid("state must be open or closed")
		return
	case hasState && state == "open" && pr.mergedAt != nil:
		c.invalid("merged pull requests cannot be reopened")
		return
	}
	if hasTitle {
		g.retitle(pr.fakeIssue, title, g.login)
	}
	if hasBody {
		g.rewrite(pr.fakeIssue, body, g.login)
	}
	if hasBase {
		g.rebase(pr, base, g.login)
	}
	if hasState {
		g.setState(pr.fakeIssue, state, "", g.login)
	}
	if hasMCM {
		pr.maintainerCanModify = mcm
	}
	c.send(http.StatusOK, g.prJSON(r, pr, true))
}

func (g *fakeGitHub) visibleReviews(pr *fakePR) []*fakeReview {
	var out []*fakeReview
	for _, rv := range pr.reviews {
		if rv.state != "PENDING" || rv.user == g.login {
			out = append(out, rv)
		}
	}
	return out
}

func (g *fakeGitHub) listReviews(c *fakeReq) {
	r, pr := c.pull()
	if pr == nil {
		return
	}
	items := []any{}
	for _, rv := range g.visibleReviews(pr) {
		items = append(items, g.reviewJSON(r, pr, rv))
	}
	c.list(items)
}

func (c *fakeReq) review(pr *fakePR) *fakeReview {
	for _, rv := range c.g.visibleReviews(pr) {
		if fmt.Sprint(rv.id) == c.m["id"] {
			return rv
		}
	}
	c.error(http.StatusNotFound, "Not Found")
	return nil
}

func (g *fakeGitHub) getReview(c *fakeReq) {
	r, pr := c.pull()
	if pr == nil {
		return
	}
	if rv := c.review(pr); rv != nil {
		c.send(http.StatusOK, g.reviewJSON(r, pr, rv))
	}
}

func (g *fakeGitHub) createReview(c *fakeReq) {
	r, pr := c.pull()
	if pr == nil {
		return
	}
	g.reviews = append(g.reviews, review{number: pr.number, body: c.body})
	if g.failPost {
		c.error(http.StatusUnprocessableEntity, "Unprocessable Entity")
		return
	}
	event, _ := c.str("event")
	body, _ := c.str("body")
	state, ok := map[string]string{"APPROVE": "APPROVED", "REQUEST_CHANGES": "CHANGES_REQUESTED", "COMMENT": "COMMENTED", "": "PENDING"}[event]
	switch {
	case !ok:
		c.invalid("unknown review event " + event)
		return
	case (event == "REQUEST_CHANGES" || event == "COMMENT") && body == "":
		c.invalid("body is required for " + event)
		return
	case (event == "APPROVE" || event == "REQUEST_CHANGES") && pr.user == g.login:
		c.invalid("Can not approve your own pull request")
		return
	}
	sha, _ := c.str("commit_id")
	if sha == "" {
		sha = pr.sha
	}
	if sha != pr.sha && !slices.Contains(pr.heads, sha) && !g.knownCommit(sha) {
		c.invalid("No commit found for SHA: " + sha)
		return
	}
	rv := g.newReview(pr, g.login, state, sha, body)
	c.send(http.StatusOK, g.reviewJSON(r, pr, rv))
}

func (g *fakeGitHub) dismissal(c *fakeReq) {
	r, pr := c.pull()
	if pr == nil {
		return
	}
	rv := c.review(pr)
	if rv == nil {
		return
	}
	message, _ := c.str("message")
	if message == "" {
		c.invalid("message is required")
		return
	}
	if rv.state != "APPROVED" && rv.state != "CHANGES_REQUESTED" {
		c.invalid("Can not dismiss a " + strings.ToLower(rv.state) + " pull request review")
		return
	}
	g.dismiss(pr, rv, g.login, message)
	c.send(http.StatusOK, g.reviewJSON(r, pr, rv))
}

func (g *fakeGitHub) listIssues(c *fakeReq) {
	r := c.repo()
	if r == nil {
		return
	}
	state := c.query.Get("state")
	if state == "" {
		state = "open"
	}
	var want []string
	if l := c.query.Get("labels"); l != "" {
		want = strings.Split(l, ",")
	}
	creator := c.query.Get("creator")
	items := []any{}
	for _, n := range fakeNumbersDesc(r.issues) {
		is := r.issues[n]
		if (state != "all" && is.state != state) || (creator != "" && !strings.EqualFold(is.user, creator)) || !fakeHasLabels(is.labels, want) {
			continue
		}
		items = append(items, g.issueJSON(r, is))
	}
	c.list(items)
}

func fakeHasLabels(have, want []string) bool {
	for _, w := range want {
		found := false
		for _, h := range have {
			found = found || strings.EqualFold(h, strings.TrimSpace(w))
		}
		if !found {
			return false
		}
	}
	return true
}

func (g *fakeGitHub) getIssue(c *fakeReq) {
	if r, is := c.issue(); is != nil {
		c.send(http.StatusOK, g.issueJSON(r, is))
	}
}

func (g *fakeGitHub) createIssue(c *fakeReq) {
	r := c.repo()
	if r == nil {
		return
	}
	title, _ := c.str("title")
	body, _ := c.str("body")
	if title == "" {
		c.invalid("title is required")
		return
	}
	is := g.newIssue(r, 0, title, body, g.login, nil)
	c.send(http.StatusCreated, g.issueJSON(r, is))
}

func (g *fakeGitHub) patchIssue(c *fakeReq) {
	r, is := c.issue()
	if is == nil {
		return
	}
	if title, ok := c.str("title"); ok {
		g.retitle(is, title, g.login)
	}
	if body, ok := c.str("body"); ok {
		g.rewrite(is, body, g.login)
	}
	if state, ok := c.str("state"); ok {
		if state == "open" && is.pull != nil && is.pull.mergedAt != nil {
			c.invalid("merged pull requests cannot be reopened")
			return
		}
		reason, _ := c.str("state_reason")
		g.setState(is, state, reason, g.login)
	}
	c.send(http.StatusOK, g.issueJSON(r, is))
}

func (g *fakeGitHub) listEvents(c *fakeReq) {
	r, is := c.issue()
	if is == nil {
		return
	}
	items := []any{}
	for _, e := range is.events {
		items = append(items, g.eventJSON(r, e))
	}
	c.list(items)
}

func (g *fakeGitHub) listTimeline(c *fakeReq) {
	if r, is := c.issue(); is != nil {
		c.list(g.timeline(r, is))
	}
}

func (g *fakeGitHub) listComments(c *fakeReq) {
	r, is := c.issue()
	if is == nil {
		return
	}
	items := []any{}
	for _, cm := range is.comments {
		items = append(items, g.commentJSON(r, is, cm))
	}
	c.list(items)
}

func (g *fakeGitHub) createComment(c *fakeReq) {
	r, is := c.issue()
	if is == nil {
		return
	}
	body, _ := c.str("body")
	if body == "" {
		c.invalid("body is required")
		return
	}
	c.send(http.StatusCreated, g.commentJSON(r, is, g.newComment(is, g.login, body)))
}

func (g *fakeGitHub) addLabels(c *fakeReq) {
	_, is := c.issue()
	if is == nil {
		return
	}
	names, _ := c.body["labels"].([]any)
	for _, n := range names {
		if s, ok := n.(string); ok {
			g.addLabel(is, s, g.login)
		}
	}
	c.send(http.StatusOK, fakeLabelsJSON(is.labels))
}

func (g *fakeGitHub) deleteLabel(c *fakeReq) {
	_, is := c.issue()
	if is == nil {
		return
	}
	if !g.removeLabel(is, c.m["name"], g.login) {
		c.error(http.StatusNotFound, "Label does not exist")
		return
	}
	c.send(http.StatusOK, fakeLabelsJSON(is.labels))
}

func fakeRefJSON(r *fakeRepo, branch, sha string) map[string]any {
	return map[string]any{
		"ref": "refs/heads/" + branch, "node_id": "REF_" + branch,
		"url":    fmt.Sprintf("https://api.github.com/repos/%s/git/refs/heads/%s", r.fullName, branch),
		"object": map[string]any{"sha": sha, "type": "commit", "url": fmt.Sprintf("https://api.github.com/repos/%s/git/commits/%s", r.fullName, sha)},
	}
}

func (g *fakeGitHub) getRef(c *fakeReq) {
	r := c.repo()
	if r == nil {
		return
	}
	sha, ok := r.refs[c.m["branch"]]
	if !ok {
		c.error(http.StatusNotFound, "Not Found")
		return
	}
	c.send(http.StatusOK, fakeRefJSON(r, c.m["branch"], sha))
}

func (g *fakeGitHub) createRef(c *fakeReq) {
	r := c.repo()
	if r == nil {
		return
	}
	ref, _ := c.str("ref")
	sha, _ := c.str("sha")
	branch, ok := strings.CutPrefix(ref, "refs/heads/")
	if !ok || branch == "" || !fullSHA.MatchString(sha) {
		c.invalid("Reference name or sha is invalid")
		return
	}
	if _, exists := r.refs[branch]; exists {
		c.invalid("Reference already exists")
		return
	}
	r.refs[branch] = sha
	c.send(http.StatusCreated, fakeRefJSON(r, branch, sha))
}

func (g *fakeGitHub) updateRef(c *fakeReq) {
	r := c.repo()
	if r == nil {
		return
	}
	branch := c.m["branch"]
	old, ok := r.refs[branch]
	if !ok {
		c.invalid("Reference does not exist")
		return
	}
	sha, _ := c.str("sha")
	force, _ := c.body["force"].(bool)
	if !fullSHA.MatchString(sha) {
		c.invalid("sha is invalid")
		return
	}
	if !g.moveRef(r, branch, old, sha, force, g.login) {
		c.invalid("Update is not a fast forward")
		return
	}
	c.send(http.StatusOK, fakeRefJSON(r, branch, sha))
}

func (g *fakeGitHub) moveRef(r *fakeRepo, branch, old, sha string, force bool, actor string) bool {
	fastForward := old == sha || g.isAncestor(old, sha)
	if !force && !fastForward {
		return false
	}
	r.refs[branch] = sha
	for _, rr := range g.repos {
		for _, pr := range rr.pulls {
			if pr.state == "open" && strings.EqualFold(pr.headRepo, r.fullName) && pr.headRef == branch && pr.sha != sha {
				pr.sha, pr.heads = sha, append(pr.heads, sha)
				if !fastForward {
					g.event(pr.fakeIssue, &fakeEvent{event: "head_ref_force_pushed", actor: actor})
				}
			}
		}
	}
	return true
}

// push moves an existing branch as a git push by actor would, updating every open PR on it.
func (g *fakeGitHub) push(repo, branch, sha string, force bool, actor string) {
	g.mu.Lock()
	defer g.mu.Unlock()
	r := g.mustRepo(repo)
	old, ok := r.refs[branch]
	if !ok {
		g.t.Fatalf("%s has no branch %s", repo, branch)
	}
	if !g.moveRef(r, branch, old, sha, force, actor) {
		g.t.Fatalf("pushing %s to %s:%s is not a fast forward", sha, repo, branch)
	}
}

func (g *fakeGitHub) deleteRef(c *fakeReq) {
	r := c.repo()
	if r == nil {
		return
	}
	if _, ok := r.refs[c.m["branch"]]; !ok {
		c.invalid("Reference does not exist")
		return
	}
	delete(r.refs, c.m["branch"])
	c.send(http.StatusNoContent, nil)
}

func (g *fakeGitHub) resolve(r *fakeRepo, spec string) (string, bool) {
	if fullSHA.MatchString(spec) {
		return spec, true
	}
	owner, branch, found := strings.Cut(spec, ":")
	if !found {
		sha, ok := r.refs[spec]
		return sha, ok
	}
	for _, cand := range g.repos {
		related := cand == r || strings.EqualFold(cand.fullName, r.parent) || strings.EqualFold(cand.parent, r.fullName) || (r.parent != "" && strings.EqualFold(cand.parent, r.parent))
		if related && strings.EqualFold(cand.owner, owner) {
			sha, ok := cand.refs[branch]
			return sha, ok
		}
	}
	return "", false
}

// compare prefers setCompare entries, then the commit graph, then the legacy per-PR changes for heads outside the graph.
func (g *fakeGitHub) compare(c *fakeReq) {
	r := c.repo()
	if r == nil {
		return
	}
	base, head := c.m["base"], c.m["head"]
	cmp, ok := r.compares[base+"..."+head]
	baseSHA, baseOK := g.resolve(r, base)
	headSHA, headOK := g.resolve(r, head)
	if !ok && headOK && g.knownCommit(headSHA) {
		if !baseOK {
			c.error(http.StatusNotFound, "Not Found")
			return
		}
		if cmp, ok = g.computeCompare(baseSHA, headSHA); !ok {
			c.error(http.StatusNotFound, "No common ancestor between "+base+" and "+head+".")
			return
		}
	}
	if !ok && r == g.fork {
		for _, pr := range g.fork.pulls {
			if pr.sha == head {
				cmp = &fakeCompare{status: pr.compareStatus, aheadBy: 1, files: pr.changes}
			}
		}
	}
	if cmp == nil {
		c.error(http.StatusNotFound, "Not Found")
		return
	}
	files := []any{}
	if p := c.query.Get("page"); p == "" || p == "1" {
		for i, f := range cmp.files {
			if i == maxCompareFiles {
				break
			}
			out := map[string]any{"status": "modified", "additions": 1, "deletions": 0, "changes": 1}
			maps.Copy(out, f)
			if _, ok := out["sha"]; !ok {
				out["sha"] = fakeSHA("blob", fmt.Sprint(out["filename"]), head)
			}
			files = append(files, out)
		}
	}
	c.send(http.StatusOK, map[string]any{
		"status": cmp.status, "ahead_by": cmp.aheadBy, "behind_by": cmp.behindBy, "total_commits": cmp.aheadBy,
		"base_commit": map[string]any{"sha": baseSHA}, "merge_base_commit": map[string]any{},
		"commits": []any{}, "files": files,
	})
}

func (c *fakeReq) objectPage(key string, items []any) {
	c.send(http.StatusOK, map[string]any{"total_count": len(items), key: c.page(items)})
}

func (g *fakeGitHub) workflowRuns(c *fakeReq) {
	r := c.repo()
	if r == nil {
		return
	}
	path := ".github/workflows/" + c.m["file"]
	sha, status := c.query.Get("head_sha"), c.query.Get("status")
	var shas []string
	for s := range r.runs {
		if sha == "" || s == sha {
			shas = append(shas, s)
		}
	}
	sort.Strings(shas)
	items := []any{}
	for _, s := range shas {
		for i, run := range r.runs[s] {
			if run.path != path || (status != "" && run.status != status && run.conclusion != status) {
				continue
			}
			var conclusion any
			if run.conclusion != "" {
				conclusion = run.conclusion
			}
			items = append(items, map[string]any{
				"id": fakeSuiteID(r.fullName, s, run.path) + int64(i), "head_sha": s, "path": run.path, "status": run.status, "conclusion": conclusion,
				"event": fakeOr(run.event, "pull_request"), "head_branch": run.headBranch, "check_suite_id": fakeSuiteID(r.fullName, s, run.path), "run_attempt": 1,
			})
		}
	}
	c.objectPage("workflow_runs", items)
}

func fakeOr(v, fallback string) string {
	if v == "" {
		return fallback
	}
	return v
}

func (g *fakeGitHub) checkRuns(c *fakeReq) {
	r := c.repo()
	if r == nil {
		return
	}
	sha := c.m["ref"]
	if s, ok := r.refs[strings.TrimPrefix(sha, "heads/")]; ok {
		sha = s
	}
	filter := fakeOr(c.query.Get("filter"), "latest")
	if filter != "latest" && filter != "all" {
		c.invalid("filter must be latest or all")
		return
	}
	name, status := c.query.Get("check_name"), c.query.Get("status")
	all := r.checks[sha]
	newest := map[string]int{}
	for i, cr := range all {
		newest[cr.app+"\x00"+cr.name] = i
	}
	items := []any{}
	for i, cr := range all {
		if filter == "latest" && newest[cr.app+"\x00"+cr.name] != i {
			continue
		}
		if (name != "" && cr.name != name) || (status != "" && cr.status != status) {
			continue
		}
		var conclusion any
		if cr.conclusion != "" {
			conclusion = cr.conclusion
		}
		suite := fakeSuiteID(r.fullName, sha, cr.path)
		items = append(items, map[string]any{
			"id": suite*100 + int64(i), "name": cr.name, "head_sha": sha, "status": cr.status, "conclusion": conclusion,
			"check_suite": map[string]any{"id": suite, "head_branch": cr.headBranch, "event": fakeOr(cr.event, "pull_request")},
			"app":         map[string]any{"slug": cr.app, "owner": fakeUser(cr.app)},
		})
	}
	c.objectPage("check_runs", items)
}
