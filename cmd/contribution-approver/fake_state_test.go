package main

import (
	"crypto/sha1"
	"encoding/hex"
	"fmt"
	"sort"
	"strings"
	"time"
)

const (
	testUpstream = "omnigent-ai/omnigent"
	testAgent    = "npa-bot"
	shaMain      = "1111111111111111111111111111111111111111"
	shaUpMain    = "2222222222222222222222222222222222222222"
)

type fakeRepo struct {
	fullName, owner, parent, defaultBranch string
	issues                                 map[int]*fakeIssue
	pulls                                  map[int]*fakePR
	refs                                   map[string]string
	runs                                   map[string][]fakeRun
	checks                                 map[string][]fakeCheckRun
	compares                               map[string]*fakeCompare
	nextNumber                             int
}

type fakeIssue struct {
	number             int
	title, body, user  string
	labels             []string
	state, stateReason string
	createdAt          time.Time
	updatedAt          time.Time
	closedAt           *time.Time
	lastEditedAt       *time.Time
	events             []*fakeEvent
	comments           []*fakeComment
	edits              []*fakeEdit
	pull               *fakePR
}

type fakePR struct {
	*fakeIssue
	headRef, headRepo, base string
	sha                     string
	additions, dels, files  int
	changes                 []map[string]any
	compareStatus           string
	reviews                 []*fakeReview
	mergedAt                *time.Time
	mergeSHA                string
	maintainerCanModify     bool
	draft                   bool
}

type fakeRun struct{ status, conclusion, path string }

type fakeCheckRun struct{ name, status, conclusion, path string }

type fakeCompare struct {
	status            string
	aheadBy, behindBy int
	files             []map[string]any
}

type fakeReview struct {
	id                          int64
	user, state, commitID, body string
	submittedAt                 time.Time
}

type fakeEvent struct {
	id                   int64
	event, actor         string
	createdAt            time.Time
	label                string
	renameFrom, renameTo string
	commitID             string
	stateReason          string
	dismissed            map[string]any
}

type fakeComment struct {
	id                   int64
	user, body           string
	createdAt, updatedAt time.Time
}

type fakeEdit struct {
	id                  int64
	editor              string
	editedAt, createdAt time.Time
	deletedAt           *time.Time
	deletedBy           string
}

func newFakeRepo(fullName, parent, defaultBranch, headSHA string) *fakeRepo {
	return &fakeRepo{
		fullName: fullName, owner: strings.SplitN(fullName, "/", 2)[0], parent: parent, defaultBranch: defaultBranch,
		issues: map[int]*fakeIssue{}, pulls: map[int]*fakePR{}, refs: map[string]string{defaultBranch: headSHA},
		runs: map[string][]fakeRun{}, checks: map[string][]fakeCheckRun{}, compares: map[string]*fakeCompare{}, nextNumber: 1,
	}
}

func (g *fakeGitHub) now() time.Time { return g.clock.now().UTC().Truncate(time.Second) }

func (g *fakeGitHub) id() int64 {
	g.nextID++
	return g.nextID
}

func (g *fakeGitHub) tick(d time.Duration) { g.clock.advance(d) }

func (g *fakeGitHub) repo(name string) *fakeRepo {
	g.mu.Lock()
	defer g.mu.Unlock()
	return g.mustRepo(name)
}

func (g *fakeGitHub) mustRepo(name string) *fakeRepo {
	r, ok := g.repos[strings.ToLower(name)]
	if !ok {
		g.t.Fatalf("fake GitHub has no repository %s", name)
	}
	return r
}

func (g *fakeGitHub) addRepo(fullName, parent, defaultBranch, headSHA string) *fakeRepo {
	g.mu.Lock()
	defer g.mu.Unlock()
	r := newFakeRepo(fullName, parent, defaultBranch, headSHA)
	g.repos[strings.ToLower(fullName)] = r
	return r
}

func (r *fakeRepo) number(n int) int {
	if n == 0 {
		n = r.nextNumber
	}
	if n >= r.nextNumber {
		r.nextNumber = n + 1
	}
	return n
}

func (g *fakeGitHub) newIssue(r *fakeRepo, number int, title, body, user string, labels []string) *fakeIssue {
	at := g.now()
	is := &fakeIssue{number: r.number(number), title: title, body: body, user: user, labels: labels, state: "open", createdAt: at, updatedAt: at}
	r.issues[is.number] = is
	return is
}

func (g *fakeGitHub) readyPR(number int, sha string) *fakePR {
	g.mu.Lock()
	defer g.mu.Unlock()
	pr := g.newPull(g.fork, &fakePR{
		fakeIssue: &fakeIssue{number: number, title: "feat(web): add the thing", body: "Adds the thing.\n\nTested with the web suite.", user: testAgent, labels: []string{readyLabel}},
		headRef:   "feature-" + fmt.Sprint(number), headRepo: testFork, base: "main", sha: sha, additions: 40, dels: 2, files: 3,
		changes: []map[string]any{{"filename": "web/src/thing.ts"}}, compareStatus: "ahead",
	})
	g.fork.runs[sha] = []fakeRun{{"completed", "success", ".github/workflows/contribution-ci.yml"}}
	return pr
}

func (g *fakeGitHub) addPull(repo string, pr *fakePR) *fakePR {
	g.mu.Lock()
	defer g.mu.Unlock()
	return g.newPull(g.mustRepo(repo), pr)
}

func (g *fakeGitHub) newPull(r *fakeRepo, pr *fakePR) *fakePR {
	if pr.fakeIssue == nil {
		pr.fakeIssue = &fakeIssue{}
	}
	is := g.newIssue(r, pr.number, pr.title, pr.body, pr.user, pr.labels)
	pr.fakeIssue, is.pull = is, pr
	if pr.base == "" {
		pr.base = r.defaultBranch
	}
	if pr.headRepo == "" {
		pr.headRepo = r.fullName
	}
	if pr.compareStatus == "" {
		pr.compareStatus = "ahead"
	}
	r.pulls[is.number] = pr
	if head, ok := g.repos[strings.ToLower(pr.headRepo)]; ok && pr.headRef != "" {
		head.refs[pr.headRef] = pr.sha
	}
	return pr
}

func (g *fakeGitHub) addIssue(repo string, number int, title, body, user string, labels ...string) *fakeIssue {
	g.mu.Lock()
	defer g.mu.Unlock()
	return g.newIssue(g.mustRepo(repo), number, title, body, user, labels)
}

func (g *fakeGitHub) issue(repo string, number int) *fakeIssue {
	g.mu.Lock()
	defer g.mu.Unlock()
	return g.mustIssue(g.mustRepo(repo), number)
}

func (g *fakeGitHub) mustIssue(r *fakeRepo, number int) *fakeIssue {
	is, ok := r.issues[number]
	if !ok {
		g.t.Fatalf("fake GitHub has no issue %s#%d", r.fullName, number)
	}
	return is
}

func (g *fakeGitHub) pullOf(repo string, number int) *fakePR {
	g.mu.Lock()
	defer g.mu.Unlock()
	pr, ok := g.mustRepo(repo).pulls[number]
	if !ok {
		g.t.Fatalf("fake GitHub has no pull request %s#%d", repo, number)
	}
	return pr
}

func (g *fakeGitHub) event(is *fakeIssue, e *fakeEvent) *fakeEvent {
	e.id, e.createdAt = g.id(), g.now()
	is.events = append(is.events, e)
	is.updatedAt = e.createdAt
	return e
}

func (g *fakeGitHub) addReview(number int, user, state, sha string) *fakeReview {
	g.mu.Lock()
	defer g.mu.Unlock()
	return g.newReview(g.fork.pulls[number], user, state, sha, "")
}

func (g *fakeGitHub) newReview(pr *fakePR, user, state, sha, body string) *fakeReview {
	rv := &fakeReview{id: g.id(), user: user, state: state, commitID: sha, body: body}
	if state != "PENDING" {
		rv.submittedAt = g.now()
		pr.updatedAt = rv.submittedAt
	}
	pr.reviews = append(pr.reviews, rv)
	return rv
}

func (g *fakeGitHub) dismissReview(number int, id int64, actor, message string) {
	g.mu.Lock()
	defer g.mu.Unlock()
	pr := g.fork.pulls[number]
	for _, rv := range pr.reviews {
		if rv.id == id {
			g.dismiss(pr, rv, actor, message)
			return
		}
	}
	g.t.Fatalf("no review %d on #%d", id, number)
}

func (g *fakeGitHub) dismiss(pr *fakePR, rv *fakeReview, actor, message string) {
	g.event(pr.fakeIssue, &fakeEvent{event: "review_dismissed", actor: actor, dismissed: map[string]any{
		"state": strings.ToLower(rv.state), "review_id": rv.id, "dismissal_message": message, "dismissal_commit_id": nil,
	}})
	rv.state = "DISMISSED"
}

func (g *fakeGitHub) rename(repo string, number int, title, actor string) {
	g.mu.Lock()
	defer g.mu.Unlock()
	g.retitle(g.mustIssue(g.mustRepo(repo), number), title, actor)
}

func (g *fakeGitHub) retitle(is *fakeIssue, title, actor string) {
	if is.title == title {
		return
	}
	g.event(is, &fakeEvent{event: "renamed", actor: actor, renameFrom: is.title, renameTo: title})
	is.title = title
}

func (g *fakeGitHub) editBody(repo string, number int, body, actor string) {
	g.mu.Lock()
	defer g.mu.Unlock()
	g.rewrite(g.mustIssue(g.mustRepo(repo), number), body, actor)
}

func (g *fakeGitHub) rewrite(is *fakeIssue, body, actor string) {
	if is.body == body {
		return
	}
	at := g.now()
	if len(is.edits) == 0 {
		is.edits = append(is.edits, &fakeEdit{id: g.id(), editor: is.user, editedAt: is.createdAt, createdAt: at})
	}
	is.edits = append(is.edits, &fakeEdit{id: g.id(), editor: actor, editedAt: at, createdAt: at})
	is.body, is.lastEditedAt, is.updatedAt = body, &at, at
}

func (g *fakeGitHub) deleteRevision(repo string, number, index int, actor string) {
	g.mu.Lock()
	defer g.mu.Unlock()
	is := g.mustIssue(g.mustRepo(repo), number)
	at := g.now()
	is.edits[index].deletedAt, is.edits[index].deletedBy = &at, actor
}

func (g *fakeGitHub) label(repo string, number int, name, actor string) {
	g.mu.Lock()
	defer g.mu.Unlock()
	g.addLabel(g.mustIssue(g.mustRepo(repo), number), name, actor)
}

func (g *fakeGitHub) addLabel(is *fakeIssue, name, actor string) {
	for _, l := range is.labels {
		if l == name {
			return
		}
	}
	is.labels = append(is.labels, name)
	g.event(is, &fakeEvent{event: "labeled", actor: actor, label: name})
}

func (g *fakeGitHub) unlabel(repo string, number int, name, actor string) {
	g.mu.Lock()
	defer g.mu.Unlock()
	g.removeLabel(g.mustIssue(g.mustRepo(repo), number), name, actor)
}

func (g *fakeGitHub) removeLabel(is *fakeIssue, name, actor string) bool {
	for i, l := range is.labels {
		if l == name {
			is.labels = append(is.labels[:i:i], is.labels[i+1:]...)
			g.event(is, &fakeEvent{event: "unlabeled", actor: actor, label: name})
			return true
		}
	}
	return false
}

func (g *fakeGitHub) changeBase(number int, base, actor string) {
	g.mu.Lock()
	defer g.mu.Unlock()
	g.rebase(g.fork.pulls[number], base, actor)
}

func (g *fakeGitHub) rebase(pr *fakePR, base, actor string) {
	if pr.base == base {
		return
	}
	pr.base = base
	g.event(pr.fakeIssue, &fakeEvent{event: "base_ref_changed", actor: actor})
}

func (g *fakeGitHub) closeIssue(repo string, number int, actor string) {
	g.mu.Lock()
	defer g.mu.Unlock()
	g.setState(g.mustIssue(g.mustRepo(repo), number), "closed", "", actor)
}

func (g *fakeGitHub) reopen(repo string, number int, actor string) {
	g.mu.Lock()
	defer g.mu.Unlock()
	g.setState(g.mustIssue(g.mustRepo(repo), number), "open", "", actor)
}

func (g *fakeGitHub) setState(is *fakeIssue, state, reason, actor string) {
	if is.state == state {
		return
	}
	is.state = state
	if state == "closed" {
		at := g.now()
		is.closedAt = &at
		if reason == "" && is.pull == nil {
			reason = "completed"
		}
		is.stateReason = reason
		g.event(is, &fakeEvent{event: "closed", actor: actor, stateReason: reason})
		return
	}
	is.closedAt, is.stateReason = nil, "reopened"
	if is.pull != nil {
		is.stateReason = ""
	}
	g.event(is, &fakeEvent{event: "reopened", actor: actor})
}

func (g *fakeGitHub) merge(repo string, number int, actor string) {
	g.mu.Lock()
	defer g.mu.Unlock()
	pr := g.mustRepo(repo).pulls[number]
	at := g.now()
	pr.mergedAt, pr.mergeSHA = &at, fakeSHA("merge", repo, fmt.Sprint(number))
	g.event(pr.fakeIssue, &fakeEvent{event: "merged", actor: actor, commitID: pr.mergeSHA})
	g.setState(pr.fakeIssue, "closed", "", actor)
}

func (g *fakeGitHub) comment(repo string, number int, user, body string) *fakeComment {
	g.mu.Lock()
	defer g.mu.Unlock()
	return g.newComment(g.mustIssue(g.mustRepo(repo), number), user, body)
}

func (g *fakeGitHub) newComment(is *fakeIssue, user, body string) *fakeComment {
	at := g.now()
	c := &fakeComment{id: g.id(), user: user, body: body, createdAt: at, updatedAt: at}
	is.comments = append(is.comments, c)
	is.updatedAt = at
	return c
}

func (g *fakeGitHub) setRef(repo, branch, sha string) {
	g.mu.Lock()
	defer g.mu.Unlock()
	g.mustRepo(repo).refs[branch] = sha
}

func (g *fakeGitHub) ref(repo, branch string) (string, bool) {
	g.mu.Lock()
	defer g.mu.Unlock()
	sha, ok := g.mustRepo(repo).refs[branch]
	return sha, ok
}

func (g *fakeGitHub) commit(sha string, parents ...string) {
	g.mu.Lock()
	defer g.mu.Unlock()
	g.parents[sha] = parents
}

func (g *fakeGitHub) isAncestor(old, sha string) bool {
	seen := map[string]bool{}
	queue := []string{sha}
	for len(queue) > 0 {
		c := queue[0]
		queue = queue[1:]
		if c == old {
			return true
		}
		if !seen[c] {
			seen[c] = true
			queue = append(queue, g.parents[c]...)
		}
	}
	return false
}

func (g *fakeGitHub) setCompare(repo, basehead, status string, aheadBy, behindBy int, files ...map[string]any) {
	g.mu.Lock()
	defer g.mu.Unlock()
	g.mustRepo(repo).compares[basehead] = &fakeCompare{status: status, aheadBy: aheadBy, behindBy: behindBy, files: files}
}

func (g *fakeGitHub) addCheckRun(repo, sha, name, status, conclusion, workflowPath string) {
	g.mu.Lock()
	defer g.mu.Unlock()
	r := g.mustRepo(repo)
	r.checks[sha] = append(r.checks[sha], fakeCheckRun{name, status, conclusion, workflowPath})
}

func fakeSHA(parts ...string) string {
	sum := sha1.Sum([]byte(strings.Join(parts, "\x00")))
	return hex.EncodeToString(sum[:])
}

func fakeSuiteID(repo, sha, path string) int64 {
	sum := sha1.Sum([]byte(repo + "\x00" + sha + "\x00" + path))
	return int64(sum[0])<<24 | int64(sum[1])<<16 | int64(sum[2])<<8 | int64(sum[3]) + 1
}

func ts(t time.Time) string { return t.UTC().Format(time.RFC3339) }

func tsPtr(t *time.Time) any {
	if t == nil {
		return nil
	}
	return ts(*t)
}

func user(login string) map[string]any {
	if login == "" {
		return nil
	}
	return map[string]any{"login": login, "type": "User"}
}

func labelsJSON(names []string) []any {
	out := []any{}
	for _, n := range names {
		out = append(out, map[string]any{"name": n, "color": "ededed"})
	}
	return out
}

func (g *fakeGitHub) repoJSON(r *fakeRepo) map[string]any {
	out := map[string]any{
		"full_name": r.fullName, "name": strings.SplitN(r.fullName, "/", 2)[1], "owner": user(r.owner),
		"default_branch": r.defaultBranch, "fork": r.parent != "", "private": false,
		"html_url": "https://github.com/" + r.fullName,
	}
	if r.parent != "" {
		out["parent"] = map[string]any{"full_name": r.parent}
		out["source"] = map[string]any{"full_name": r.parent}
	}
	return out
}

func (g *fakeGitHub) branchJSON(repo, ref, sha string) map[string]any {
	owner := strings.SplitN(repo, "/", 2)[0]
	return map[string]any{"ref": ref, "sha": sha, "label": owner + ":" + ref, "user": user(owner), "repo": map[string]any{"full_name": repo, "owner": user(owner)}}
}

func (g *fakeGitHub) prJSON(r *fakeRepo, pr *fakePR, detail bool) map[string]any {
	baseSHA := r.refs[pr.base]
	out := map[string]any{
		"id": int64(pr.number) + 9000, "number": pr.number, "state": pr.state, "title": pr.title, "body": pr.body,
		"labels": labelsJSON(pr.labels), "user": user(pr.user), "draft": pr.draft,
		"html_url":   fmt.Sprintf("https://github.com/%s/pull/%d", r.fullName, pr.number),
		"head":       g.branchJSON(pr.headRepo, pr.headRef, pr.sha),
		"base":       g.branchJSON(r.fullName, pr.base, baseSHA),
		"created_at": ts(pr.createdAt), "updated_at": ts(pr.updatedAt), "closed_at": tsPtr(pr.closedAt), "merged_at": tsPtr(pr.mergedAt),
		"merge_commit_sha": nil,
	}
	if pr.mergeSHA != "" {
		out["merge_commit_sha"] = pr.mergeSHA
	}
	if detail {
		out["merged"] = pr.mergedAt != nil
		out["maintainer_can_modify"] = pr.maintainerCanModify
		out["additions"], out["deletions"], out["changed_files"] = pr.additions, pr.dels, pr.files
	}
	return out
}

func (g *fakeGitHub) issueJSON(r *fakeRepo, is *fakeIssue) map[string]any {
	kind := "issues"
	if is.pull != nil {
		kind = "pull"
	}
	out := map[string]any{
		"id": int64(is.number) + 7000, "number": is.number, "state": is.state, "state_reason": nil, "title": is.title, "body": is.body,
		"labels": labelsJSON(is.labels), "user": user(is.user), "comments": len(is.comments),
		"html_url":   fmt.Sprintf("https://github.com/%s/%s/%d", r.fullName, kind, is.number),
		"created_at": ts(is.createdAt), "updated_at": ts(is.updatedAt), "closed_at": tsPtr(is.closedAt),
	}
	if is.stateReason != "" {
		out["state_reason"] = is.stateReason
	}
	if is.pull != nil {
		out["pull_request"] = map[string]any{
			"url":       fmt.Sprintf("https://api.github.com/repos/%s/pulls/%d", r.fullName, is.number),
			"html_url":  out["html_url"],
			"merged_at": tsPtr(is.pull.mergedAt),
		}
	}
	return out
}

func (g *fakeGitHub) reviewJSON(r *fakeRepo, pr *fakePR, rv *fakeReview) map[string]any {
	out := map[string]any{
		"id": rv.id, "node_id": fmt.Sprintf("PRR_%d", rv.id), "user": user(rv.user), "body": rv.body, "state": rv.state,
		"commit_id": rv.commitID, "author_association": "OWNER",
		"html_url":         fmt.Sprintf("https://github.com/%s/pull/%d#pullrequestreview-%d", r.fullName, pr.number, rv.id),
		"pull_request_url": fmt.Sprintf("https://api.github.com/repos/%s/pulls/%d", r.fullName, pr.number),
	}
	if rv.state != "PENDING" {
		out["submitted_at"] = ts(rv.submittedAt)
	}
	return out
}

func (g *fakeGitHub) eventJSON(r *fakeRepo, e *fakeEvent) map[string]any {
	out := map[string]any{
		"id": e.id, "node_id": fmt.Sprintf("E_%d", e.id), "url": fmt.Sprintf("https://api.github.com/repos/%s/issues/events/%d", r.fullName, e.id),
		"actor": user(e.actor), "event": e.event, "commit_id": nil, "commit_url": nil, "created_at": ts(e.createdAt),
		"performed_via_github_app": nil,
	}
	if e.commitID != "" {
		out["commit_id"] = e.commitID
	}
	switch e.event {
	case "labeled", "unlabeled":
		out["label"] = map[string]any{"name": e.label, "color": "ededed"}
	case "renamed":
		out["rename"] = map[string]any{"from": e.renameFrom, "to": e.renameTo}
	case "review_dismissed":
		out["dismissed_review"] = e.dismissed
	case "closed":
		out["state_reason"] = nil
		if e.stateReason != "" {
			out["state_reason"] = e.stateReason
		}
	}
	return out
}

func (g *fakeGitHub) commentJSON(r *fakeRepo, is *fakeIssue, c *fakeComment) map[string]any {
	return map[string]any{
		"id": c.id, "node_id": fmt.Sprintf("IC_%d", c.id), "user": user(c.user), "body": c.body, "author_association": "OWNER",
		"html_url":   fmt.Sprintf("https://github.com/%s/issues/%d#issuecomment-%d", r.fullName, is.number, c.id),
		"issue_url":  fmt.Sprintf("https://api.github.com/repos/%s/issues/%d", r.fullName, is.number),
		"created_at": ts(c.createdAt), "updated_at": ts(c.updatedAt),
	}
}

func (g *fakeGitHub) timeline(r *fakeRepo, is *fakeIssue) []any {
	type item struct {
		at  time.Time
		seq int64
		v   map[string]any
	}
	var items []item
	for _, e := range is.events {
		items = append(items, item{e.createdAt, e.id, g.eventJSON(r, e)})
	}
	for _, c := range is.comments {
		v := g.commentJSON(r, is, c)
		v["event"], v["actor"] = "commented", user(c.user)
		items = append(items, item{c.createdAt, c.id, v})
	}
	if is.pull != nil {
		for _, rv := range is.pull.reviews {
			if rv.state == "PENDING" {
				continue
			}
			v := g.reviewJSON(r, is.pull, rv)
			v["event"], v["state"] = "reviewed", strings.ToLower(rv.state)
			items = append(items, item{rv.submittedAt, rv.id, v})
		}
	}
	sort.SliceStable(items, func(i, j int) bool {
		if !items[i].at.Equal(items[j].at) {
			return items[i].at.Before(items[j].at)
		}
		return items[i].seq < items[j].seq
	})
	out := []any{}
	for _, it := range items {
		out = append(out, it.v)
	}
	return out
}
