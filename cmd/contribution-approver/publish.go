package main

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"net/url"
	"strconv"
	"strings"
	"time"
)

type verdict int

const (
	verdictNone verdict = iota
	verdictPublish
	verdictHard
	verdictTransient
)

const (
	ciEvent = "pull_request"
	ciApp   = "github-actions"
)

func (v verdict) String() string {
	return [...]string{"none", "publish", "refuse-hard", "refuse-transient"}[v]
}

// publishDecision is what may go upstream; a refusal's reason never holds the token or the PR body.
type publishDecision struct {
	verdict      verdict
	reason       string
	number       int
	reviewID     int64
	reviewedAt   time.Time
	sha          string
	title, body  string
	lastEditedAt *time.Time
	reannounce   bool
	// unannounced: the only binding failure is that no announcement of sha is known, as after a restart.
	unannounced bool
	pull        string
}

type announcementLog interface {
	announcementOf(k prKey, sha string) (sentAnnouncement, bool)
}

type refusal struct {
	verdict verdict
	reason  string
}

func (r *refusal) Error() string { return r.reason }

func hard(format string, args ...any) error {
	return &refusal{verdictHard, fmt.Sprintf(format, args...)}
}

type pageCapError struct{ what string }

func (e *pageCapError) Error() string { return e.what }

func (d publishDecision) refuse(err error) publishDecision {
	var r *refusal
	var capped *pageCapError
	switch {
	case errors.As(err, &r):
		d.verdict, d.reason = r.verdict, r.reason
	case errors.As(err, &capped):
		d.verdict, d.reason = verdictHard, capped.what
	default:
		d.verdict, d.reason = verdictTransient, err.Error()
	}
	return d
}

type ownerReview struct {
	ID          int64      `json:"id"`
	State       string     `json:"state"`
	CommitID    string     `json:"commit_id"`
	SubmittedAt *time.Time `json:"submitted_at"`
	User        *struct {
		Login string `json:"login"`
	} `json:"user"`
}

func (p *poller) decidePublish(ctx context.Context, cfg forkConfig, number int) publishDecision {
	d := p.gh.evaluatePublish(ctx, cfg, number, p.codes)
	if d.reannounce && (d.verdict == verdictHard || d.verdict == verdictTransient) {
		p.codes.requestReannouncement(prKey{fork: cfg.Fork, number: number}, d.reviewID)
	}
	return d
}

func (g *githubClient) evaluatePublish(ctx context.Context, cfg forkConfig, number int, sent announcementLog) publishDecision {
	d := publishDecision{number: number}
	if err := g.verifyFork(ctx, cfg); err != nil {
		var api *apiError
		var mismatch *forkMismatch
		switch {
		case errors.As(err, &mismatch):
			err = &refusal{verdictTransient, "the fork does not match the registry"}
		case !errors.As(err, &api):
			err = &refusal{verdictTransient, "could not check the fork"}
		}
		return d.refuse(err)
	}
	rv, err := g.decidingReview(ctx, cfg, number)
	if err != nil {
		return d.refuse(err)
	}
	if rv == nil {
		d.reason = "no owner review approves it"
		return d
	}
	if rv.State != "APPROVED" {
		d.reason = "the newest owner review is " + rv.State
		return d
	}
	d.reviewID, d.reviewedAt, d.sha = rv.ID, *rv.SubmittedAt, rv.CommitID
	if !fullSHA.MatchString(rv.CommitID) {
		return d.refuse(hard("the approved commit is not one full SHA"))
	}
	pr, err := g.pull(ctx, cfg.Fork, number)
	if err != nil {
		return d.refuse(err)
	}
	if err := checkPull(cfg, pr); err != nil {
		return d.refuse(err)
	}
	d.title, d.body, d.pull = pr.Title, pr.Body, pullFingerprint(pr)
	k, digest := prKey{fork: cfg.Fork, number: number}, contentDigest(pr.Title, pr.Body)
	if head, ok := sent.announcementOf(k, pr.Head.SHA); !ok || head.digest != digest {
		d.reannounce = true
	}
	if d.lastEditedAt, err = g.checkEdits(ctx, cfg, number, pr, d.reviewedAt); err != nil {
		return d.refuse(err)
	}
	if err := g.checkTimeline(ctx, cfg, number, &d.reviewedAt); err != nil {
		return d.refuse(err)
	}
	if err := checkAnnounced(sent, k, d.sha, digest, d.reviewID); err != nil {
		_, known := sent.announcementOf(k, d.sha)
		d.unannounced = !known
		return d.refuse(err)
	}
	if err := g.checkCI(ctx, cfg, d.sha); err != nil {
		return d.refuse(err)
	}
	if err := g.checkDiff(ctx, cfg, d.sha); err != nil {
		return d.refuse(err)
	}
	d.verdict = verdictPublish
	return d
}

// pullFingerprint covers every pull request field the decision reads, so an unchanged fingerprint needs no new decision.
func pullFingerprint(pr pullRequest) string {
	return contentDigest(pr.Title, strings.Join([]string{
		pr.Body, pr.State, pr.Head.SHA, pr.Head.Ref, pr.Head.repo(), pr.Base.Ref, pr.Base.repo(), strconv.FormatBool(pr.hasLabel(readyLabel)),
	}, "\x00"))
}

func (g *githubClient) decidingReview(ctx context.Context, cfg forkConfig, number int) (*ownerReview, error) {
	var latest *ownerReview
	for page := 1; ; page++ {
		if page > maxPages {
			return nil, &pageCapError{fmt.Sprintf("%s#%d has more than %d reviews", cfg.Fork, number, maxPages*pageSize)}
		}
		var batch []ownerReview
		q := url.Values{"per_page": {strconv.Itoa(pageSize)}, "page": {strconv.Itoa(page)}}
		if err := g.get(ctx, fmt.Sprintf("/repos/%s/pulls/%d/reviews", cfg.Fork, number), q, &batch); err != nil {
			return nil, err
		}
		for i := range batch {
			r := &batch[i]
			deciding := r.State == "APPROVED" || r.State == "CHANGES_REQUESTED" || r.State == "DISMISSED"
			if !deciding || r.User == nil || r.User.Login != cfg.Owner || r.SubmittedAt == nil {
				continue
			}
			if latest == nil || !r.SubmittedAt.Before(*latest.SubmittedAt) {
				latest = r
			}
		}
		if len(batch) < pageSize {
			return latest, nil
		}
	}
}

func checkAnnounced(sent announcementLog, k prKey, sha, digest string, reviewID int64) error {
	a, ok := sent.announcementOf(k, sha)
	switch {
	case !ok:
		return &refusal{verdictTransient, fmt.Sprintf("no announcement of %s is known, so the owner must approve it again", sha)}
	case a.digest != digest:
		return &refusal{verdictTransient, fmt.Sprintf("the title or body changed since %s was announced", sha)}
	case reviewID <= a.lastReview:
		return &refusal{verdictTransient, fmt.Sprintf("the approval predates the announcement of %s", sha)}
	}
	return nil
}

func checkPull(cfg forkConfig, pr pullRequest) error {
	switch {
	case pr.State != "open":
		return hard("the pull request is not open")
	case !pr.hasLabel(readyLabel):
		return &refusal{verdictTransient, "the pull request lacks the " + readyLabel + " label"}
	case pr.Base.repo() != cfg.Fork || pr.Base.Ref != cfg.DefaultBranch:
		return hard("the pull request base is not %s:%s", cfg.Fork, cfg.DefaultBranch)
	case pr.Head.repo() != cfg.Fork:
		return hard("the pull request head is not a branch of %s", cfg.Fork)
	case strings.HasPrefix(strings.ToLower(pr.Head.Ref), "upstream/"):
		return hard("the pull request head is a publish branch")
	case !titleFormats[cfg.TitleFormat].MatchString(pr.Title):
		return hard("the title does not follow %s", cfg.TitleFormat)
	}
	return nil
}

const editsQuery = `query($owner: String!, $name: String!, $number: Int!, $after: String) {
  repository(owner: $owner, name: $name) {
    pullRequest(number: $number) {
      title
      body
      lastEditedAt
      userContentEdits(first: 100, after: $after) {
        totalCount
        pageInfo { hasNextPage endCursor }
        nodes { editedAt deletedAt diff }
      }
    }
  }
}`

type contentEdit struct {
	EditedAt  time.Time  `json:"editedAt"`
	DeletedAt *time.Time `json:"deletedAt"`
	Diff      *string    `json:"diff"`
}

type editHistory struct {
	Title            string     `json:"title"`
	Body             string     `json:"body"`
	LastEditedAt     *time.Time `json:"lastEditedAt"`
	UserContentEdits struct {
		TotalCount int `json:"totalCount"`
		PageInfo   struct {
			HasNextPage bool    `json:"hasNextPage"`
			EndCursor   *string `json:"endCursor"`
		} `json:"pageInfo"`
		Nodes []contentEdit `json:"nodes"`
	} `json:"userContentEdits"`
}

func (g *githubClient) editPage(ctx context.Context, cfg forkConfig, number int, after *string) (*editHistory, error) {
	owner, name, _ := strings.Cut(cfg.Fork, "/")
	vars := map[string]any{"owner": owner, "name": name, "number": number, "after": after}
	var res struct {
		Data *struct {
			Repository *struct {
				PullRequest *editHistory `json:"pullRequest"`
			} `json:"repository"`
		} `json:"data"`
		Errors []json.RawMessage `json:"errors"`
	}
	err := g.do(ctx, http.MethodPost, "/graphql", nil, map[string]any{"query": editsQuery, "variables": vars}, "application/json", &res)
	if err != nil {
		return nil, err
	}
	if len(res.Errors) > 0 || res.Data == nil || res.Data.Repository == nil || res.Data.Repository.PullRequest == nil {
		return nil, fmt.Errorf("GitHub GraphQL returned an error for the edit history of %s#%d", cfg.Fork, number)
	}
	return res.Data.Repository.PullRequest, nil
}

// The newest revision's diff is the full body, so a dropped post-review revision leaves a mismatch.
func (g *githubClient) checkEdits(ctx context.Context, cfg forkConfig, number int, pr pullRequest, reviewed time.Time) (*time.Time, error) {
	var first *editHistory
	var edits []contentEdit
	var after *string
	for page := 1; ; page++ {
		if page > maxPages {
			return nil, &pageCapError{fmt.Sprintf("%s#%d has more than %d body revisions", cfg.Fork, number, maxPages*pageSize)}
		}
		h, err := g.editPage(ctx, cfg, number, after)
		if err != nil {
			return nil, err
		}
		if first == nil {
			first = h
		} else if h.Body != first.Body || h.Title != first.Title || !sameTime(h.LastEditedAt, first.LastEditedAt) {
			return nil, fmt.Errorf("%s#%d changed while its edit history was read", cfg.Fork, number)
		}
		edits = append(edits, h.UserContentEdits.Nodes...)
		if !h.UserContentEdits.PageInfo.HasNextPage {
			if len(edits) != h.UserContentEdits.TotalCount {
				return nil, fmt.Errorf("%s#%d edit history changed while it was read", cfg.Fork, number)
			}
			break
		}
		if after = h.UserContentEdits.PageInfo.EndCursor; after == nil {
			return nil, fmt.Errorf("%s#%d edit history has no cursor", cfg.Fork, number)
		}
	}
	if first.Title != pr.Title || first.Body != pr.Body {
		return nil, fmt.Errorf("%s#%d changed while it was read", cfg.Fork, number)
	}
	if first.LastEditedAt != nil && !first.LastEditedAt.Before(reviewed) {
		return nil, hard("the body was edited at or after the approval")
	}
	for _, e := range edits {
		if !e.EditedAt.Before(reviewed) {
			return nil, hard("the body was edited at or after the approval")
		}
	}
	for _, e := range edits {
		if e.DeletedAt != nil && !e.DeletedAt.Before(reviewed) {
			return nil, hard("a body revision was deleted after the approval")
		}
	}
	if len(edits) > 0 && edits[0].DeletedAt == nil && (edits[0].Diff == nil || *edits[0].Diff != pr.Body) {
		return nil, hard("the body differs from its newest revision, so an edit is missing from its history")
	}
	return first.LastEditedAt, nil
}

func sameTime(a, b *time.Time) bool {
	return (a == nil) == (b == nil) && (a == nil || a.Equal(*b))
}

func (g *githubClient) checkTimeline(ctx context.Context, cfg forkConfig, number int, reviewed *time.Time) error {
	for page := 1; ; page++ {
		if page > maxPages {
			return &pageCapError{fmt.Sprintf("%s#%d has more than %d timeline items", cfg.Fork, number, maxPages*pageSize)}
		}
		var batch []struct {
			Event     string     `json:"event"`
			CreatedAt *time.Time `json:"created_at"`
		}
		q := url.Values{"per_page": {strconv.Itoa(pageSize)}, "page": {strconv.Itoa(page)}}
		if err := g.get(ctx, fmt.Sprintf("/repos/%s/issues/%d/timeline", cfg.Fork, number), q, &batch); err != nil {
			return err
		}
		for _, e := range batch {
			after := reviewed != nil && (e.CreatedAt == nil || !e.CreatedAt.Before(*reviewed))
			switch {
			case e.Event == "base_ref_changed":
				return hard("the pull request base was changed")
			case e.Event == "renamed" && after:
				return hard("the title was renamed at or after the approval")
			case (e.Event == "closed" || e.Event == "reopened") && after:
				return hard("the pull request was closed or reopened after the approval")
			}
		}
		if len(batch) < pageSize {
			return nil
		}
	}
}

var conclusions = map[string]bool{
	"action_required": true, "cancelled": true, "failure": true, "neutral": true,
	"skipped": true, "stale": true, "startup_failure": true, "timed_out": true,
}

func conclusionName(c string) string {
	if conclusions[c] {
		return c
	}
	return "unsuccessfully"
}

func (g *githubClient) lastReviewID(ctx context.Context, fork string, number int) (int64, error) {
	var last int64
	for page := 1; page <= maxPages; page++ {
		var batch []struct {
			ID int64 `json:"id"`
		}
		q := url.Values{"per_page": {strconv.Itoa(pageSize)}, "page": {strconv.Itoa(page)}}
		if err := g.get(ctx, fmt.Sprintf("/repos/%s/pulls/%d/reviews", fork, number), q, &batch); err != nil {
			return 0, err
		}
		for _, r := range batch {
			last = max(last, r.ID)
		}
		if len(batch) < pageSize {
			return last, nil
		}
	}
	return 0, fmt.Errorf("%s#%d has more than %d reviews", fork, number, maxPages*pageSize)
}

type workflowRun struct {
	HeadSHA      string `json:"head_sha"`
	Path         string `json:"path"`
	Event        string `json:"event"`
	Status       string `json:"status"`
	Conclusion   string `json:"conclusion"`
	CheckSuiteID int64  `json:"check_suite_id"`
}

type checkRun struct {
	Name       string `json:"name"`
	Status     string `json:"status"`
	Conclusion string `json:"conclusion"`
	CheckSuite struct {
		ID    int64  `json:"id"`
		Event string `json:"event"`
	} `json:"check_suite"`
	App *struct {
		Slug string `json:"slug"`
	} `json:"app"`
}

func listCounted[T any](ctx context.Context, g *githubClient, path string, query url.Values, key, what string) ([]T, error) {
	var all []T
	for page := 1; ; page++ {
		if page > maxPages {
			return nil, &pageCapError{fmt.Sprintf("more than %d %s", maxPages*pageSize, what)}
		}
		q := url.Values{"per_page": {strconv.Itoa(pageSize)}, "page": {strconv.Itoa(page)}}
		for k, v := range query {
			q[k] = v
		}
		var res map[string]json.RawMessage
		if err := g.get(ctx, path, q, &res); err != nil {
			return nil, err
		}
		var total int
		var batch []T
		if json.Unmarshal(res["total_count"], &total) != nil || json.Unmarshal(res[key], &batch) != nil {
			return nil, fmt.Errorf("GitHub GET %s: undecodable %s", path, what)
		}
		all = append(all, batch...)
		if len(all) == total {
			return all, nil
		}
		if len(batch) < pageSize {
			return nil, fmt.Errorf("the %s changed while they were read", what)
		}
	}
}

func (g *githubClient) checkCI(ctx context.Context, cfg forkConfig, sha string) error {
	path := ".github/workflows/" + cfg.CIWorkflow
	runs, err := listCounted[workflowRun](ctx, g, "/repos/"+cfg.Fork+"/actions/workflows/"+url.PathEscape(cfg.CIWorkflow)+"/runs", url.Values{"head_sha": {sha}}, "workflow_runs", "CI workflow runs")
	if err != nil {
		return err
	}
	checks, err := listCounted[checkRun](ctx, g, "/repos/"+cfg.Fork+"/commits/"+sha+"/check-runs", url.Values{"filter": {"latest"}}, "check_runs", "CI check runs")
	if err != nil {
		return err
	}
	suites := map[int64]int{}
	pending := false
	for _, r := range runs {
		if r.HeadSHA != sha || r.Path != path {
			continue
		}
		suites[r.CheckSuiteID] = 0
		switch {
		case r.Event != ciEvent:
			return hard("a %s CI run for %s came from an event other than %s", cfg.CIWorkflow, sha, ciEvent)
		case r.Status != "completed":
			pending = true
		case r.Conclusion != "success":
			return hard("CI for %s concluded %s", sha, conclusionName(r.Conclusion))
		}
	}
	if len(suites) == 0 {
		return &refusal{verdictTransient, fmt.Sprintf("no %s CI run exists for %s yet", cfg.CIWorkflow, sha)}
	}
	for _, c := range checks {
		if _, ours := suites[c.CheckSuite.ID]; !ours {
			continue
		}
		suites[c.CheckSuite.ID]++
		switch {
		case c.App == nil || c.App.Slug != ciApp:
			return hard("a CI check run for %s comes from an app other than %s", sha, ciApp)
		case c.CheckSuite.Event != "" && c.CheckSuite.Event != ciEvent:
			return hard("a CI check suite for %s came from an event other than %s", sha, ciEvent)
		case c.Status != "completed":
			pending = true
		case c.Conclusion != "success":
			return hard("a CI check for %s concluded %s", sha, conclusionName(c.Conclusion))
		}
	}
	for _, n := range suites {
		if n == 0 {
			pending = true
		}
	}
	if pending {
		return &refusal{verdictTransient, fmt.Sprintf("CI for %s has not finished", sha)}
	}
	return nil
}

func (g *githubClient) diffFiles(ctx context.Context, cfg forkConfig, base, sha string) (map[string]compareFile, error) {
	var res struct {
		Status  string         `json:"status"`
		AheadBy int            `json:"ahead_by"`
		Files   *[]compareFile `json:"files"`
	}
	if err := g.get(ctx, "/repos/"+cfg.Fork+"/compare/"+base+"..."+sha, nil, &res); err != nil {
		return nil, err
	}
	if (res.Status != "ahead" && res.Status != "diverged") || res.AheadBy <= 0 {
		return nil, hard("%s is not ahead of %s", sha, base)
	}
	if res.Files == nil || len(*res.Files) == 0 || len(*res.Files) >= maxCompareFiles {
		return nil, hard("the diff against %s is empty or too large to inspect", base)
	}
	files := map[string]compareFile{}
	for _, f := range *res.Files {
		for _, name := range []string{f.Filename, f.PreviousFilename} {
			for _, prefix := range protectedPrefixes {
				if strings.HasPrefix(name, prefix) {
					return nil, hard("the diff changes a file under .github/ or publish/, which never go upstream")
				}
			}
		}
		files[f.Filename] = f
	}
	return files, nil
}

func (g *githubClient) checkDiff(ctx context.Context, cfg forkConfig, sha string) error {
	upstreamOwner, _, _ := strings.Cut(cfg.Upstream, "/")
	upstream, err := g.diffFiles(ctx, cfg, upstreamOwner+":"+cfg.DefaultBranch, sha)
	if err != nil {
		return err
	}
	fork, err := g.diffFiles(ctx, cfg, cfg.Owner+":"+cfg.DefaultBranch, sha)
	if err != nil {
		return err
	}
	if len(upstream) != len(fork) {
		return hard("the fork-relative and upstream-relative diffs differ")
	}
	for name, f := range upstream {
		if other, ok := fork[name]; !ok || other != f {
			return hard("the fork-relative and upstream-relative diffs differ")
		}
	}
	return nil
}
