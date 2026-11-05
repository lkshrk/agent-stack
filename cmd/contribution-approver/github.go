package main

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"regexp"
	"strconv"
	"strings"
)

const (
	readyLabel      = "upstream-ready"
	maxCompareFiles = 300
	pageSize        = 100
	maxPages        = 10
	maxRespBytes    = 4 << 20
)

var (
	protectedPrefixes = []string{".github/", "publish/"}
	titleFormats      = map[string]*regexp.Regexp{"conventional-scoped": regexp.MustCompile(`^[a-z]+\([^()\s]+\)!?: \S.*$`)}
)

var (
	fullSHA    = regexp.MustCompile(`^[0-9a-f]{40}$`)
	slug       = regexp.MustCompile(`^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$`)
	branchName = regexp.MustCompile(`^[A-Za-z0-9_.-]+(/[A-Za-z0-9_.-]+)*$`)
)

type githubClient struct {
	base, token string
	http        *http.Client
}

type forkConfig struct {
	Upstream      string `json:"upstream"`
	Fork          string `json:"fork"`
	Owner         string `json:"owner"`
	DefaultBranch string `json:"default_branch"`
	TitleFormat   string `json:"title_format"`
	CIWorkflow    string `json:"ci_workflow"`
}

type gitRef struct {
	Ref  string `json:"ref"`
	SHA  string `json:"sha"`
	Repo *struct {
		FullName string `json:"full_name"`
	} `json:"repo"`
}

func (r gitRef) repo() string {
	if r.Repo == nil {
		return ""
	}
	return r.Repo.FullName
}

type pullRequest struct {
	Number       int    `json:"number"`
	State        string `json:"state"`
	Title        string `json:"title"`
	Body         string `json:"body"`
	HTMLURL      string `json:"html_url"`
	Head         gitRef `json:"head"`
	Base         gitRef `json:"base"`
	Additions    int    `json:"additions"`
	Deletions    int    `json:"deletions"`
	ChangedFiles int    `json:"changed_files"`
	Labels       []struct {
		Name string `json:"name"`
	} `json:"labels"`
}

func (p pullRequest) hasLabel(name string) bool {
	for _, l := range p.Labels {
		if l.Name == name {
			return true
		}
	}
	return false
}

type apiError struct {
	method, path string
	status       int
}

func (e *apiError) Error() string {
	return fmt.Sprintf("GitHub %s %s returned HTTP %d", e.method, e.path, e.status)
}

func (g *githubClient) do(ctx context.Context, method, path string, query url.Values, body any, accept string, out any) error {
	var rd io.Reader
	if body != nil {
		raw, err := json.Marshal(body)
		if err != nil {
			return err
		}
		rd = bytes.NewReader(raw)
	}
	u := strings.TrimRight(g.base, "/") + path
	if len(query) > 0 {
		u += "?" + query.Encode()
	}
	req, err := http.NewRequestWithContext(ctx, method, u, rd)
	if err != nil {
		return err
	}
	req.Header.Set("Authorization", "Bearer "+g.token)
	req.Header.Set("Accept", accept)
	req.Header.Set("X-GitHub-Api-Version", "2022-11-28")
	if body != nil {
		req.Header.Set("Content-Type", "application/json")
	}
	res, err := g.http.Do(req)
	if err != nil {
		return fmt.Errorf("GitHub %s %s: request failed", method, path)
	}
	defer func() { _ = res.Body.Close() }()
	raw, err := io.ReadAll(io.LimitReader(res.Body, maxRespBytes))
	if err != nil {
		return fmt.Errorf("GitHub %s %s: read failed", method, path)
	}
	if res.StatusCode/100 != 2 {
		return &apiError{method: method, path: path, status: res.StatusCode}
	}
	if b, ok := out.(*[]byte); ok {
		*b = raw
		return nil
	}
	if out == nil {
		return nil
	}
	if err := json.Unmarshal(raw, out); err != nil {
		return fmt.Errorf("GitHub %s %s: undecodable response", method, path)
	}
	return nil
}

func (g *githubClient) get(ctx context.Context, path string, query url.Values, out any) error {
	return g.do(ctx, http.MethodGet, path, query, nil, "application/vnd.github+json", out)
}

func (g *githubClient) login(ctx context.Context) (string, error) {
	var user struct {
		Login string `json:"login"`
	}
	if err := g.get(ctx, "/user", nil, &user); err != nil {
		return "", err
	}
	return user.Login, nil
}

func (g *githubClient) verifyFork(ctx context.Context, cfg forkConfig) error {
	var repo struct {
		Owner struct {
			Login string `json:"login"`
		} `json:"owner"`
		Parent *struct {
			FullName string `json:"full_name"`
		} `json:"parent"`
	}
	if err := g.get(ctx, "/repos/"+cfg.Fork, nil, &repo); err != nil {
		return err
	}
	if repo.Owner.Login != cfg.Owner {
		return fmt.Errorf("%s is owned by %s, not %s", cfg.Fork, repo.Owner.Login, cfg.Owner)
	}
	if repo.Parent == nil || repo.Parent.FullName != cfg.Upstream {
		return fmt.Errorf("%s is not a fork of %s", cfg.Fork, cfg.Upstream)
	}
	return nil
}

func (g *githubClient) openPulls(ctx context.Context, cfg forkConfig) ([]pullRequest, error) {
	var all []pullRequest
	for page := 1; page <= maxPages; page++ {
		var batch []pullRequest
		q := url.Values{"state": {"open"}, "base": {cfg.DefaultBranch}, "per_page": {strconv.Itoa(pageSize)}, "page": {strconv.Itoa(page)}}
		if err := g.get(ctx, "/repos/"+cfg.Fork+"/pulls", q, &batch); err != nil {
			return nil, err
		}
		all = append(all, batch...)
		if len(batch) < pageSize {
			return all, nil
		}
	}
	return nil, fmt.Errorf("%s has more than %d open pull requests", cfg.Fork, maxPages*pageSize)
}

func (g *githubClient) pull(ctx context.Context, fork string, number int) (pullRequest, error) {
	var pr pullRequest
	err := g.get(ctx, fmt.Sprintf("/repos/%s/pulls/%d", fork, number), nil, &pr)
	return pr, err
}

// ciGreen must stay in step with _require_green_ci in templates/contribution-fork/publish/gate.py.
func (g *githubClient) ciGreen(ctx context.Context, cfg forkConfig, sha string) (bool, int, error) {
	var res struct {
		TotalCount int `json:"total_count"`
		Runs       []struct {
			HeadSHA    string `json:"head_sha"`
			Path       string `json:"path"`
			Status     string `json:"status"`
			Conclusion string `json:"conclusion"`
		} `json:"workflow_runs"`
	}
	q := url.Values{"head_sha": {sha}, "per_page": {strconv.Itoa(pageSize)}}
	if err := g.get(ctx, "/repos/"+cfg.Fork+"/actions/workflows/"+url.PathEscape(cfg.CIWorkflow)+"/runs", q, &res); err != nil {
		return false, 0, err
	}
	if res.TotalCount != len(res.Runs) {
		return false, 0, fmt.Errorf("%s has more CI runs for %s than one page", cfg.Fork, sha)
	}
	runs := 0
	for _, r := range res.Runs {
		if r.HeadSHA != sha || r.Path != ".github/workflows/"+cfg.CIWorkflow {
			continue
		}
		runs++
		if r.Status != "completed" || r.Conclusion != "success" {
			return false, runs, nil
		}
	}
	return runs > 0, runs, nil
}

func (g *githubClient) ownerReviewed(ctx context.Context, fork string, number int, owner, sha string) (bool, error) {
	for page := 1; page <= maxPages; page++ {
		var batch []struct {
			State    string `json:"state"`
			CommitID string `json:"commit_id"`
			User     *struct {
				Login string `json:"login"`
			} `json:"user"`
		}
		q := url.Values{"per_page": {strconv.Itoa(pageSize)}, "page": {strconv.Itoa(page)}}
		if err := g.get(ctx, fmt.Sprintf("/repos/%s/pulls/%d/reviews", fork, number), q, &batch); err != nil {
			return false, err
		}
		for _, r := range batch {
			if r.User != nil && r.User.Login == owner && r.CommitID == sha && (r.State == "APPROVED" || r.State == "CHANGES_REQUESTED") {
				return true, nil
			}
		}
		if len(batch) < pageSize {
			return false, nil
		}
	}
	return false, fmt.Errorf("%s#%d has more than %d reviews", fork, number, maxPages*pageSize)
}

func (g *githubClient) review(ctx context.Context, fork string, number int, sha, event, body string) error {
	payload := map[string]any{"commit_id": sha, "event": event, "body": body}
	return g.do(ctx, http.MethodPost, fmt.Sprintf("/repos/%s/pulls/%d/reviews", fork, number), nil, payload, "application/vnd.github+json", nil)
}

type compareFile struct {
	Filename         string `json:"filename"`
	PreviousFilename string `json:"previous_filename"`
	Status           string `json:"status"`
	SHA              string `json:"sha"`
}

// compare must stay in step with _require_clean_diff in templates/contribution-fork/publish/gate.py.
func (g *githubClient) compare(ctx context.Context, cfg forkConfig, sha string) (string, error) {
	var res struct {
		Status  string         `json:"status"`
		AheadBy int            `json:"ahead_by"`
		Files   *[]compareFile `json:"files"`
	}
	upstreamOwner := strings.SplitN(cfg.Upstream, "/", 2)[0]
	if err := g.get(ctx, "/repos/"+cfg.Fork+"/compare/"+upstreamOwner+":"+cfg.DefaultBranch+"..."+sha, nil, &res); err != nil {
		return "", err
	}
	if (res.Status != "ahead" && res.Status != "diverged") || res.AheadBy <= 0 {
		return fmt.Sprintf("it is not ahead of the upstream %s", cfg.DefaultBranch), nil
	}
	if res.Files == nil || len(*res.Files) == 0 || len(*res.Files) >= maxCompareFiles {
		return "the upstream diff is empty or too large to inspect", nil
	}
	for _, f := range *res.Files {
		for _, name := range []string{f.Filename, f.PreviousFilename} {
			for _, prefix := range protectedPrefixes {
				if strings.HasPrefix(name, prefix) {
					return "the upstream diff changes " + name + "; .github/ and publish/ never go upstream", nil
				}
			}
		}
	}
	return "", nil
}
