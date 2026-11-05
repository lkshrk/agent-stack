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

type publishOutcome int

const (
	outcomeNothing publishOutcome = iota
	outcomeRetry
	outcomeRefused
	outcomePublished
	outcomeCurrent
	outcomeClosedUpstream
)

func (o publishOutcome) String() string {
	return [...]string{"nothing", "retry", "refused", "published", "current", "closed-upstream"}[o]
}

// publishResult reports one publish attempt; reason holds only fixed text, never the token or PR text.
type publishResult struct {
	outcome     publishOutcome
	decision    publishDecision
	reason      string
	dismissed   bool
	upstream    int
	upstreamURL string
	// alreadyUpstream: a hard refusal came after the approved SHA was already upstream, so nothing was dismissed.
	alreadyUpstream bool
	// upstreamUnknown: a hard refusal waits because the upstream pull requests could not be read.
	upstreamUnknown bool
}

const upstreamUnknownReason = "the upstream pull requests could not be read, so the refusal waits"

const carriedReason = "a closed or merged upstream pull request already carried "

func (r publishResult) carried() bool {
	return r.outcome == outcomeCurrent || (r.outcome == outcomeClosedUpstream && strings.HasPrefix(r.reason, carriedReason))
}

type upstreamPullRequest struct {
	Number   int        `json:"number"`
	State    string     `json:"state"`
	ClosedAt *time.Time `json:"closed_at"`
	User     *struct {
		Login string `json:"login"`
	} `json:"user"`
	Head struct {
		Label string `json:"label"`
		SHA   string `json:"sha"`
		Repo  *struct {
			FullName string `json:"full_name"`
		} `json:"repo"`
	} `json:"head"`
}

// ours rejects third-party pull requests, which anyone may open from the owner's public branch.
func (pr upstreamPullRequest) ours(cfg forkConfig, head string) bool {
	return strings.EqualFold(pr.Head.Label, head) &&
		pr.User != nil && strings.EqualFold(pr.User.Login, cfg.Owner) &&
		pr.Head.Repo != nil && strings.EqualFold(pr.Head.Repo.FullName, cfg.Fork)
}

func (pr upstreamPullRequest) closedAt() (time.Time, bool) {
	if pr.ClosedAt == nil {
		return time.Time{}, false
	}
	return *pr.ClosedAt, true
}

func publishBranch(number int) string { return fmt.Sprintf("upstream/pr-%d", number) }

func (p *poller) publish(ctx context.Context, cfg forkConfig, number int) publishResult {
	d := p.decidePublish(ctx, cfg, number)
	res := publishResult{decision: d, reason: d.reason}
	switch d.verdict {
	case verdictNone:
		return res
	case verdictTransient:
		res.outcome = outcomeRetry
		return res
	case verdictHard:
		if fullSHA.MatchString(d.sha) && d.reviewID != 0 {
			r, ok, err := p.gh.settledUpstream(ctx, cfg, d)
			switch {
			case err != nil:
				res.outcome, res.reason, res.upstreamUnknown = outcomeRetry, upstreamUnknownReason, true
				return res
			case ok && r.carried():
				r.alreadyUpstream = true
				return r
			}
		}
		return p.gh.spend(ctx, cfg, res)
	}
	if err := p.gh.publishUpstream(ctx, cfg, d, &res, true); err != nil {
		res = res.withError(err)
		if res.outcome == outcomeRefused {
			return p.gh.spend(ctx, cfg, res)
		}
	}
	return res
}

func (r publishResult) withError(err error) publishResult {
	d := r.decision.refuse(err)
	r.reason = d.reason
	r.outcome = outcomeRetry
	if d.verdict == verdictHard {
		r.outcome = outcomeRefused
	}
	return r
}

// spend dismisses the deciding approval; a review ID of 0 means the decision found no single review to dismiss.
func (g *githubClient) spend(ctx context.Context, cfg forkConfig, res publishResult) publishResult {
	res.outcome = outcomeRefused
	if res.decision.reviewID == 0 {
		return res
	}
	path := fmt.Sprintf("/repos/%s/pulls/%d/reviews/%d/dismissals", cfg.Fork, res.decision.number, res.decision.reviewID)
	payload := map[string]any{"message": res.reason, "event": "DISMISS"}
	res.dismissed = g.do(ctx, http.MethodPut, path, nil, payload, "application/vnd.github+json", nil) == nil
	return res
}

// settledUpstream reports, without writing, whether d's approval is already Current or blocked by a closed upstream pull request.
// A hard refusal from the lookup (several open pull requests) means "not upstream"; any other error is returned.
func (g *githubClient) settledUpstream(ctx context.Context, cfg forkConfig, d publishDecision) (publishResult, bool, error) {
	res := publishResult{decision: d, outcome: outcomeNothing}
	if err := g.publishUpstream(ctx, cfg, d, &res, false); err != nil {
		var r *refusal
		if errors.As(err, &r) {
			return res, false, nil
		}
		return res, false, err
	}
	return res, res.outcome == outcomeCurrent || res.outcome == outcomeClosedUpstream, nil
}

func (g *githubClient) publishUpstream(ctx context.Context, cfg forkConfig, d publishDecision, res *publishResult, write bool) error {
	branch := publishBranch(d.number)
	head := cfg.Owner + ":" + branch
	pulls, err := g.upstreamPulls(ctx, cfg, head)
	if err != nil {
		return err
	}
	var open []upstreamPullRequest
	var closed *upstreamPullRequest
	var carried *upstreamPullRequest
	var closedAt time.Time
	undated := false
	for i, pr := range pulls {
		if pr.State == "open" {
			open = append(open, pr)
			continue
		}
		if pr.Head.SHA == d.sha && carried == nil {
			carried = &pulls[i]
		}
		at, known := pr.closedAt()
		if !undated && (closed == nil || !known || at.After(closedAt)) {
			closed, closedAt, undated = &pulls[i], at, !known
		}
	}
	if len(open) > 1 {
		return hard("more than one open upstream pull request uses %s", head)
	}
	current, exists, err := g.branchSHA(ctx, cfg.Fork, branch)
	if err != nil {
		return err
	}
	if len(open) == 1 && exists && current == d.sha {
		res.upstream, res.upstreamURL = open[0].Number, upstreamURL(cfg, open[0].Number)
		res.outcome = outcomeCurrent
		return nil
	}
	if carried != nil {
		res.upstream, res.upstreamURL = carried.Number, upstreamURL(cfg, carried.Number)
		res.outcome, res.reason = outcomeClosedUpstream, carriedReason+d.sha
		return nil
	}
	if len(open) == 1 {
		if !write {
			return nil
		}
		res.upstream, res.upstreamURL = open[0].Number, upstreamURL(cfg, open[0].Number)
		if err := g.pointBranch(ctx, cfg.Fork, branch, d.sha, exists); err != nil {
			return err
		}
		text := map[string]any{"title": d.title, "body": d.body}
		if err := g.do(ctx, http.MethodPatch, fmt.Sprintf("/repos/%s/pulls/%d", cfg.Upstream, open[0].Number), nil, text, "application/vnd.github+json", nil); err != nil {
			return err
		}
		res.outcome = outcomePublished
		return nil
	}
	if closed != nil && (undated || !d.reviewedAt.After(closedAt)) {
		res.upstream, res.upstreamURL = closed.Number, upstreamURL(cfg, closed.Number)
		res.outcome, res.reason = outcomeClosedUpstream, "the upstream pull request was closed or merged after the approval"
		return nil
	}
	if !write {
		return nil
	}
	if !exists || current != d.sha {
		if err := g.pointBranch(ctx, cfg.Fork, branch, d.sha, exists); err != nil {
			return err
		}
	}
	var created upstreamPullRequest
	payload := map[string]any{"title": d.title, "body": d.body, "head": head, "base": cfg.DefaultBranch, "maintainer_can_modify": false}
	err = g.do(ctx, http.MethodPost, "/repos/"+cfg.Upstream+"/pulls", nil, payload, "application/vnd.github+json", &created)
	var api *apiError
	if errors.As(err, &api) && api.status == http.StatusUnprocessableEntity {
		if headTaken(api.body) {
			return &refusal{verdictTransient, headTakenReason}
		}
		return hard("GitHub refused to open the upstream pull request")
	}
	if err != nil {
		return err
	}
	res.upstream, res.upstreamURL = created.Number, upstreamURL(cfg, created.Number)
	res.outcome = outcomePublished
	return nil
}

// headTaken matches the 422s that a third party's open pull request or a not yet visible branch cause.
func headTaken(body []byte) bool {
	var res struct {
		Errors []struct {
			Message string `json:"message"`
			Field   string `json:"field"`
			Code    string `json:"code"`
		} `json:"errors"`
	}
	if json.Unmarshal(body, &res) != nil {
		return false
	}
	for _, e := range res.Errors {
		if strings.HasPrefix(e.Message, "A pull request already exists") || (e.Field == "head" && e.Code == "invalid") {
			return true
		}
	}
	return false
}

func upstreamURL(cfg forkConfig, number int) string {
	return fmt.Sprintf("https://github.com/%s/pull/%d", cfg.Upstream, number)
}

func (g *githubClient) upstreamPulls(ctx context.Context, cfg forkConfig, head string) ([]upstreamPullRequest, error) {
	var all []upstreamPullRequest
	for page := 1; ; page++ {
		if page > maxPages {
			return nil, &pageCapError{fmt.Sprintf("more than %d upstream pull requests use %s", maxPages*pageSize, head)}
		}
		var batch []upstreamPullRequest
		q := url.Values{"state": {"all"}, "head": {head}, "per_page": {strconv.Itoa(pageSize)}, "page": {strconv.Itoa(page)}}
		if err := g.get(ctx, "/repos/"+cfg.Upstream+"/pulls", q, &batch); err != nil {
			return nil, err
		}
		for _, pr := range batch {
			if pr.ours(cfg, head) {
				all = append(all, pr)
			}
		}
		if len(batch) < pageSize {
			return all, nil
		}
	}
}

func (g *githubClient) branchSHA(ctx context.Context, fork, branch string) (string, bool, error) {
	var ref struct {
		Ref    string `json:"ref"`
		Object struct {
			SHA string `json:"sha"`
		} `json:"object"`
	}
	err := g.get(ctx, "/repos/"+fork+"/git/ref/heads/"+branch, nil, &ref)
	var api *apiError
	if errors.As(err, &api) && api.status == http.StatusNotFound {
		return "", false, nil
	}
	if err != nil {
		return "", false, err
	}
	if ref.Ref != "refs/heads/"+branch || !fullSHA.MatchString(ref.Object.SHA) {
		return "", false, fmt.Errorf("GitHub GET /repos/%s/git/ref/heads/%s: unexpected ref", fork, branch)
	}
	return ref.Object.SHA, true, nil
}

func (g *githubClient) pointBranch(ctx context.Context, fork, branch, sha string, exists bool) error {
	if exists {
		return g.do(ctx, http.MethodPatch, "/repos/"+fork+"/git/refs/heads/"+branch, nil, map[string]any{"sha": sha, "force": true}, "application/vnd.github+json", nil)
	}
	return g.do(ctx, http.MethodPost, "/repos/"+fork+"/git/refs", nil, map[string]any{"ref": "refs/heads/" + branch, "sha": sha}, "application/vnd.github+json", nil)
}
