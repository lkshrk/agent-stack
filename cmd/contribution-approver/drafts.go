package main

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"net/url"
	"regexp"
	"slices"
	"strconv"
	"strings"
	"time"
)

const (
	approvedLabel = "approved-upstream"
	issueLabel    = "upstream-issue"
	commentLabel  = "upstream-comment"
	issueMarker   = "<!-- contribution-fork:upstream-issue -->"
	commentMarker = "<!-- contribution-fork:upstream-comment -->"
)

var commentTitle = regexp.MustCompile(`^Comment on #([1-9][0-9]*)$`)

type ghUser struct {
	Login string `json:"login"`
}

type issueItem struct {
	ID          int64           `json:"id"`
	Number      int             `json:"number"`
	State       string          `json:"state"`
	Title       string          `json:"title"`
	Body        string          `json:"body"`
	CreatedAt   *time.Time      `json:"created_at"`
	UpdatedAt   *time.Time      `json:"updated_at"`
	User        *ghUser         `json:"user"`
	PullRequest json.RawMessage `json:"pull_request"`
	Labels      []struct {
		Name string `json:"name"`
	} `json:"labels"`
}

func (i issueItem) isPull() bool { return len(i.PullRequest) > 0 && string(i.PullRequest) != "null" }

func (i issueItem) by(owner string) bool { return i.User != nil && i.User.Login == owner }

func (i issueItem) hasLabel(name string) bool {
	return slices.ContainsFunc(i.Labels, func(l struct {
		Name string `json:"name"`
	}) bool {
		return l.Name == name
	})
}

type issueEvent struct {
	ID        int64      `json:"id"`
	Event     string     `json:"event"`
	CreatedAt *time.Time `json:"created_at"`
	Actor     *ghUser    `json:"actor"`
	Label     *struct {
		Name string `json:"name"`
	} `json:"label"`
}

func (e issueEvent) labelled(names ...string) bool {
	return (e.Event == "labeled" || e.Event == "unlabeled") && e.Label != nil && slices.Contains(names, e.Label.Name)
}

func (e issueEvent) by(owner string) bool { return e.Actor != nil && e.Actor.Login == owner }

type draftDecision struct {
	verdict    verdict
	reason     string
	number     int
	approval   int64
	approvedAt time.Time
	// foreign: the newest approval label was applied by someone other than the owner.
	foreign  bool
	recorded bool
	// searchable: the draft names one kind and a target, so an identical upstream post can be looked up.
	searchable bool
	// posted: this approval's upstream link from the ledger; nothing is written upstream again.
	posted  string
	comment bool
	target  int
	title   string
	body    string
}

func (d draftDecision) refuse(err error) draftDecision {
	d.verdict, d.reason = classify(err)
	return d
}

type draftOutcome int

const (
	draftNothing draftOutcome = iota
	draftRetry
	draftRefused
	draftPublished
	draftRecorded
	draftWaiting
)

func (o draftOutcome) String() string {
	return [...]string{"nothing", "retry", "refused", "published", "recorded", "waiting"}[o]
}

type draftKey struct {
	fork     string
	number   int
	approval int64
}

type draftPost struct {
	link    string
	comment bool
}

type draftResult struct {
	outcome   draftOutcome
	decision  draftDecision
	reason    string
	unlabeled bool
	closed    bool
	url       string
}

func (g *githubClient) approvedDrafts(ctx context.Context, cfg forkConfig) ([]issueItem, error) {
	var all []issueItem
	for page := 1; page <= maxPages; page++ {
		var batch []issueItem
		q := url.Values{"state": {"open"}, "labels": {approvedLabel}, "per_page": {strconv.Itoa(pageSize)}, "page": {strconv.Itoa(page)}}
		if err := g.get(ctx, "/repos/"+cfg.Fork+"/issues", q, &batch); err != nil {
			return nil, err
		}
		all = append(all, batch...)
		if len(batch) < pageSize {
			return all, nil
		}
	}
	return nil, fmt.Errorf("%s has more than %d open approved drafts", cfg.Fork, maxPages*pageSize)
}

func listAll[T any](ctx context.Context, g *githubClient, path string, query url.Values, what string, keep func(T) bool) (*T, error) {
	for page := 1; ; page++ {
		if page > maxPages {
			return nil, &pageCapError{fmt.Sprintf("more than %d %s", maxPages*pageSize, what)}
		}
		q := url.Values{"per_page": {strconv.Itoa(pageSize)}, "page": {strconv.Itoa(page)}}
		for k, v := range query {
			q[k] = v
		}
		var batch []T
		if err := g.get(ctx, path, q, &batch); err != nil {
			return nil, err
		}
		for i := range batch {
			if keep(batch[i]) {
				return &batch[i], nil
			}
		}
		if len(batch) < pageSize {
			return nil, nil
		}
	}
}

func (g *githubClient) issueEvents(ctx context.Context, cfg forkConfig, number int) ([]issueEvent, error) {
	var all []issueEvent
	_, err := listAll(ctx, g, fmt.Sprintf("/repos/%s/issues/%d/events", cfg.Fork, number), nil, "events on the draft", func(e issueEvent) bool {
		all = append(all, e)
		return false
	})
	return all, err
}

func (g *githubClient) evaluateDraft(ctx context.Context, cfg forkConfig, number int, ledger map[draftKey]draftPost) draftDecision {
	d := draftDecision{number: number}
	var is issueItem
	if err := g.get(ctx, fmt.Sprintf("/repos/%s/issues/%d", cfg.Fork, number), nil, &is); err != nil {
		return d.refuse(err)
	}
	if is.isPull() || is.State != "open" || !is.hasLabel(approvedLabel) {
		d.reason = "not an open approved draft"
		return d
	}
	events, err := g.issueEvents(ctx, cfg, number)
	if err != nil {
		return d.refuse(err)
	}
	at := -1
	for i, e := range events {
		if e.labelled(approvedLabel) {
			at = i
		}
	}
	if at < 0 || events[at].Event != "labeled" || events[at].CreatedAt == nil {
		return d.refuse(&refusal{verdictTransient, "the approval label has no labeled event yet"})
	}
	if !events[at].by(cfg.Owner) {
		d.foreign, d.reason = true, "the newest approval label is not the owner's"
		return d
	}
	d.approval, d.approvedAt = events[at].ID, *events[at].CreatedAt
	record, err := listAll(ctx, g, fmt.Sprintf("/repos/%s/issues/%d/comments", cfg.Fork, number), nil, "comments on the draft", func(c issueItem) bool {
		return c.by(cfg.Owner) && (strings.HasPrefix(c.Body, issueMarker) || strings.HasPrefix(c.Body, commentMarker))
	})
	if err != nil {
		return d.refuse(err)
	}
	if record != nil {
		d.verdict, d.recorded = verdictPublish, true
		return d
	}
	if post, ok := ledger[draftKey{cfg.Fork, number, d.approval}]; ok {
		d.verdict, d.posted, d.comment = verdictPublish, post.link, post.comment
		return d
	}
	oneKind := is.hasLabel(issueLabel) != is.hasLabel(commentLabel)
	d.title, d.body, d.comment = is.Title, is.Body, is.hasLabel(commentLabel)
	titled := true
	if d.comment {
		m := commentTitle.FindStringSubmatch(is.Title)
		titled = m != nil
		if titled {
			d.target, err = strconv.Atoi(m[1])
			titled = err == nil
		}
	}
	blank := strings.TrimSpace(is.Body) == ""
	d.searchable = oneKind && titled && !blank
	if err := checkDraftEvents(cfg, events, at); err != nil {
		return d.refuse(err)
	}
	if err := checkLabelHistory(is, events); err != nil {
		return d.refuse(err)
	}
	if err := g.checkLabelsUnchanged(ctx, cfg, d.approvedAt); err != nil {
		return d.refuse(err)
	}
	switch {
	case !oneKind:
		return d.refuse(hard("the draft needs exactly one of %s or %s", issueLabel, commentLabel))
	case !titled:
		return d.refuse(hard("a comment draft's title must be exactly Comment on #<number>"))
	case blank:
		return d.refuse(hard("the draft body is empty"))
	}
	last, err := g.checkEdits(ctx, cfg, "issue", number, is.Title, is.Body, d.approvedAt)
	if err != nil {
		return d.refuse(err)
	}
	// An empty history cannot show a dropped revision, so any activity after the label refuses; updated_at never allows.
	if last == nil && (is.UpdatedAt == nil || is.UpdatedAt.After(d.approvedAt)) {
		return d.refuse(hard("the draft changed after the approval"))
	}
	d.verdict = verdictPublish
	return d
}

// checkDraftEvents refuses a rename or kind change at or after the approval; an owner kind change in the same second but listed before it is the owner preparing the approval.
func checkDraftEvents(cfg forkConfig, events []issueEvent, at int) error {
	t := *events[at].CreatedAt
	for i, e := range events {
		if i == at {
			continue
		}
		after := i > at || e.CreatedAt == nil || e.CreatedAt.After(t)
		same := e.CreatedAt != nil && e.CreatedAt.Equal(t)
		switch {
		case e.Event == "renamed" && (after || same):
			return hard("the title was renamed at or after the approval")
		case e.labelled(issueLabel, commentLabel) && (after || (same && !e.by(cfg.Owner))):
			return hard("the draft kind changed after the approval")
		}
	}
	return nil
}

var draftLabels = []string{approvedLabel, issueLabel, commentLabel}

// checkLabelHistory replays the draft labels' events; a repository label renamed into one of them leaves no event, so the replay disagrees.
func checkLabelHistory(is issueItem, events []issueEvent) error {
	replayed := map[string]bool{}
	for _, e := range events {
		if e.labelled(draftLabels...) {
			replayed[e.Label.Name] = e.Event == "labeled"
		}
	}
	for _, name := range draftLabels {
		if replayed[name] != is.hasLabel(name) {
			return hard("the draft labels do not match their history")
		}
	}
	return nil
}

var labelQuery = fmt.Sprintf(`query($owner: String!, $name: String!) {
  repository(owner: $owner, name: $name) {
    approved: label(name: %q) { updatedAt }
    issue: label(name: %q) { updatedAt }
    comment: label(name: %q) { updatedAt }
  }
}`, approvedLabel, issueLabel, commentLabel)

func (g *githubClient) checkLabelsUnchanged(ctx context.Context, cfg forkConfig, approvedAt time.Time) error {
	owner, name, _ := strings.Cut(cfg.Fork, "/")
	var res struct {
		Data *struct {
			Repository map[string]*struct {
				UpdatedAt time.Time `json:"updatedAt"`
			} `json:"repository"`
		} `json:"data"`
		Errors []json.RawMessage `json:"errors"`
	}
	vars := map[string]any{"owner": owner, "name": name}
	if err := g.do(ctx, http.MethodPost, "/graphql", nil, map[string]any{"query": labelQuery, "variables": vars}, "application/json", &res); err != nil {
		return err
	}
	if len(res.Errors) > 0 || res.Data == nil || len(res.Data.Repository) != len(draftLabels) {
		return fmt.Errorf("GitHub GraphQL returned an error for the labels of %s", cfg.Fork)
	}
	for _, l := range res.Data.Repository {
		if l != nil && !l.UpdatedAt.Before(approvedAt) {
			return hard("a draft label was changed at or after the approval")
		}
	}
	return nil
}

func (p *poller) publishDraft(ctx context.Context, cfg forkConfig, number int) draftResult {
	d := p.gh.evaluateDraft(ctx, cfg, number, p.posted)
	res := draftResult{decision: d, reason: d.reason}
	switch {
	case d.foreign:
		res.unlabeled = p.gh.unlabelApproval(ctx, cfg, number)
		return res
	case d.verdict == verdictNone:
		return res
	case d.verdict == verdictTransient:
		res.outcome = draftRetry
		return res
	case d.verdict == verdictHard:
		return p.refuseDraft(ctx, cfg, res)
	case d.recorded:
		res.outcome = draftRecorded
		return p.settleDraft(ctx, cfg, res, "")
	case d.posted != "":
		return p.settleDraft(ctx, cfg, res, d.posted)
	}
	link, found, err := p.gh.findPublished(ctx, cfg, d)
	if err == nil && !found {
		post := p.gh.fileIssue
		if d.comment {
			post = p.gh.postComment
		}
		link, err = post(ctx, cfg, d)
	}
	if err != nil {
		v, reason := classify(err)
		res.reason, res.outcome = reason, draftRetry
		if v == verdictHard {
			return p.gh.spendDraft(ctx, cfg, res)
		}
		return res
	}
	return p.settleDraft(ctx, cfg, p.ledger(cfg, res, link), link)
}

func (p *poller) ledger(cfg forkConfig, res draftResult, link string) draftResult {
	d := res.decision
	p.posted[draftKey{cfg.Fork, d.number, d.approval}] = draftPost{link: link, comment: d.comment}
	return res
}

// refuseDraft spends a hard refusal unless this approval's post is already upstream, which is recorded instead.
func (p *poller) refuseDraft(ctx context.Context, cfg forkConfig, res draftResult) draftResult {
	if !res.decision.searchable {
		return p.gh.spendDraft(ctx, cfg, res)
	}
	link, found, err := p.gh.findPublished(ctx, cfg, res.decision)
	switch {
	case err == nil && found:
		return p.settleDraft(ctx, cfg, p.ledger(cfg, res, link), link)
	case err != nil:
		if v, _ := classify(err); v != verdictHard {
			res.outcome = draftWaiting
			return res
		}
	}
	return p.gh.spendDraft(ctx, cfg, res)
}

// settleDraft records link, if any, and spends the approval before closing, on its own deadline so an expired evaluation cannot strand a post.
func (p *poller) settleDraft(ctx context.Context, cfg forkConfig, res draftResult, link string) draftResult {
	ctx, cancel := context.WithTimeout(context.WithoutCancel(ctx), p.settleTimeout)
	defer cancel()
	d := res.decision
	if link != "" {
		marker := issueMarker
		if d.comment {
			marker = commentMarker
		}
		record := map[string]any{"body": marker + "\n" + link}
		if err := p.gh.do(ctx, http.MethodPost, fmt.Sprintf("/repos/%s/issues/%d/comments", cfg.Fork, d.number), nil, record, "application/vnd.github+json", nil); err != nil {
			res.outcome, res.reason = draftRetry, "the publish record could not be written: "+err.Error()
			return res
		}
		res.outcome, res.url = draftPublished, link
	}
	if res.unlabeled = p.gh.unlabelApproval(ctx, cfg, d.number); res.unlabeled {
		res.closed = p.gh.closeDraft(ctx, cfg, d.number) == nil
	}
	return res
}

// since: only a post created at or after this approval can be its publish.
func (d draftDecision) since(c issueItem) bool {
	return c.CreatedAt != nil && !c.CreatedAt.Before(d.approvedAt)
}

// findPublished ignores issue titles because maintainers retitle.
func (g *githubClient) findPublished(ctx context.Context, cfg forkConfig, d draftDecision) (string, bool, error) {
	if d.comment {
		found, err := listAll(ctx, g, fmt.Sprintf("/repos/%s/issues/%d/comments", cfg.Upstream, d.target), nil, "upstream comments to search", func(c issueItem) bool {
			return c.by(cfg.Owner) && c.Body == d.body && c.ID > 0 && d.since(c)
		})
		if err := rejected(err, "the upstream issue to comment on cannot be found"); err != nil || found == nil {
			return "", false, err
		}
		return commentURL(cfg, d.target, found.ID), true, nil
	}
	q := url.Values{"creator": {cfg.Owner}, "state": {"all"}}
	found, err := listAll(ctx, g, "/repos/"+cfg.Upstream+"/issues", q, "upstream issues to search", func(c issueItem) bool {
		return !c.isPull() && c.by(cfg.Owner) && c.Body == d.body && c.Number > 0 && d.since(c)
	})
	if err != nil || found == nil {
		return "", false, err
	}
	return issueURL(cfg, found.Number), true, nil
}

func (g *githubClient) fileIssue(ctx context.Context, cfg forkConfig, d draftDecision) (string, error) {
	var created issueItem
	err := g.do(ctx, http.MethodPost, "/repos/"+cfg.Upstream+"/issues", nil, map[string]any{"title": d.title, "body": d.body}, "application/vnd.github+json", &created)
	if err := rejected(err, "GitHub refused to file the issue upstream"); err != nil {
		return "", err
	}
	return issueURL(cfg, created.Number), nil
}

func (g *githubClient) postComment(ctx context.Context, cfg forkConfig, d draftDecision) (string, error) {
	path := fmt.Sprintf("/repos/%s/issues/%d/comments", cfg.Upstream, d.target)
	var created issueItem
	err := g.do(ctx, http.MethodPost, path, nil, map[string]any{"body": d.body}, "application/vnd.github+json", &created)
	if err := rejected(err, "GitHub refused the upstream comment"); err != nil {
		return "", err
	}
	return commentURL(cfg, d.target, created.ID), nil
}

// rejected turns the answers that a retry cannot change into a hard refusal.
func rejected(err error, reason string) error {
	var api *apiError
	if !errors.As(err, &api) {
		return err
	}
	switch api.status {
	case http.StatusNotFound, http.StatusGone, http.StatusUnprocessableEntity:
		return hard("%s", reason)
	case http.StatusForbidden:
		if !rateLimited(api) {
			return hard("%s", reason)
		}
	}
	return err
}

func rateLimited(api *apiError) bool {
	return api.header.Get("Retry-After") != "" || api.header.Get("X-Ratelimit-Remaining") == "0" ||
		strings.Contains(strings.ToLower(string(api.body)), "secondary rate limit") ||
		strings.Contains(strings.ToLower(string(api.body)), "abuse detection")
}

func issueURL(cfg forkConfig, number int) string {
	return fmt.Sprintf("https://github.com/%s/issues/%d", cfg.Upstream, number)
}

func commentURL(cfg forkConfig, number int, id int64) string {
	return fmt.Sprintf("%s#issuecomment-%d", issueURL(cfg, number), id)
}

var draftURL = regexp.MustCompile(`^https://github\.com/([^/]+/[^/]+)/issues/[1-9][0-9]*(#issuecomment-[1-9][0-9]*)?$`)

func ownDraftURL(cfg forkConfig, link string) string {
	if m := draftURL.FindStringSubmatch(link); m != nil && m[1] == cfg.Upstream {
		return link
	}
	return ""
}

func (g *githubClient) spendDraft(ctx context.Context, cfg forkConfig, res draftResult) draftResult {
	res.outcome = draftRefused
	res.unlabeled = g.unlabelApproval(ctx, cfg, res.decision.number)
	return res
}

func (g *githubClient) unlabelApproval(ctx context.Context, cfg forkConfig, number int) bool {
	err := g.do(ctx, http.MethodDelete, fmt.Sprintf("/repos/%s/issues/%d/labels/%s", cfg.Fork, number, approvedLabel), nil, nil, "application/vnd.github+json", nil)
	var api *apiError
	return err == nil || (errors.As(err, &api) && api.status == http.StatusNotFound)
}

func (g *githubClient) closeDraft(ctx context.Context, cfg forkConfig, number int) error {
	state := map[string]any{"state": "closed", "state_reason": "completed"}
	return g.do(ctx, http.MethodPatch, fmt.Sprintf("/repos/%s/issues/%d", cfg.Fork, number), nil, state, "application/vnd.github+json", nil)
}

func (p *poller) pollDrafts(ctx context.Context, cfg forkConfig) error {
	items, err := p.gh.approvedDrafts(ctx, cfg)
	if err != nil {
		return err
	}
	live := map[prKey]bool{}
	for _, it := range items {
		k := prKey{fork: cfg.Fork, number: it.Number}
		live[k] = true
		if it.isPull() {
			continue
		}
		dctx, cancel := context.WithTimeout(ctx, p.draftTimeout)
		res := p.publishDraft(dctx, cfg, it.Number)
		p.reportDraft(dctx, cfg, k, res)
		cancel()
	}
	p.liveDrafts[cfg.Fork] = live
	for k := range p.posted {
		if k.fork == cfg.Fork && !live[prKey{fork: k.fork, number: k.number}] {
			delete(p.posted, k)
		}
	}
	return nil
}

// reportDraft tells the owner only fixed text, the fork, the draft number and a shape-checked upstream URL.
func (p *poller) reportDraft(ctx context.Context, cfg forkConfig, k prKey, res draftResult) {
	d := res.decision
	name := fmt.Sprintf("the draft %s#%d", cfg.Fork, k.number)
	p.logOnce(k, fmt.Sprintf("draft\x00%s\x00%d\x00%s\x00%v", res.outcome, d.approval, res.reason, res.closed), "contribution-approver: draft %s#%d: %s %s (closed: %v)", cfg.Fork, k.number, res.outcome, res.reason, res.closed)
	done := fmt.Sprintf("draft\x00done\x00%d", d.approval)
	switch res.outcome {
	case draftPublished:
		text := "Filed " + name + " upstream as an issue"
		if d.comment {
			text = "Posted " + name + " upstream as a comment"
		}
		p.tellOnce(ctx, k, done, withLink(text, ownDraftURL(cfg, res.url)))
	case draftRecorded:
		p.tellOnce(ctx, k, done, fmt.Sprintf("The draft %s#%d already has a publish record, so it was not published again.", cfg.Fork, k.number))
	case draftWaiting:
		p.tellOnce(ctx, k, fmt.Sprintf("draft\x00wait\x00%d", d.approval), "Not publishing "+name+" yet: "+res.reason+". Upstream cannot be searched for an earlier copy right now, so the approval label stays until it can.")
	case draftRefused:
		note := "The approval label was removed; add it again to publish."
		if !res.unlabeled {
			note = "The approval label could not be removed yet; the approver keeps trying."
		}
		p.tellOnce(ctx, k, fmt.Sprintf("draft\x00%d\x00%s", d.approval, res.reason), "Not publishing "+name+": "+res.reason+". "+note)
	}
}
