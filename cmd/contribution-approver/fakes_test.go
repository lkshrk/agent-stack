package main

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"log"
	"net/http"
	"net/http/httptest"
	"os"
	"strconv"
	"strings"
	"sync"
	"testing"
	"time"
)

const (
	testPAT    = "fake-owner-pat-for-tests"
	testShared = "shared-token-0123456789abcdef0123456789"
	testBot    = "+4915550000"
	testGroup  = "group.R3JvdXBJbnRlcm5hbElkMDEyMzQ1Njc4OWFiY2RlZg=="
	testFork   = "lkshrk/omnigent"
	shaA       = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
	shaB       = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
	forksJSON  = `[{"upstream":"omnigent-ai/omnigent","fork":"lkshrk/omnigent","owner":"lkshrk","default_branch":"main","title_format":"conventional-scoped","ci_workflow":"contribution-ci.yml"}]`
)

type fakePR struct {
	number          int
	title, headRef  string
	body            string
	headRepo, base  string
	sha             string
	labels          []string
	additions, dels int
	files           int
	changes         []map[string]any
	compareStatus   string
}

type fakeRun struct{ status, conclusion, path string }

type review struct {
	number int
	body   map[string]any
}

type fakeGitHub struct {
	mu        sync.Mutex
	t         *testing.T
	login     string
	parent    string
	repoOwner string
	prs       map[int]*fakePR
	runs      map[string][]fakeRun
	reviews   []review
	existing  map[int][]map[string]any
	failPost  bool
	requests  []string
	srv       *httptest.Server
}

func newFakeGitHub(t *testing.T) *fakeGitHub {
	g := &fakeGitHub{t: t, login: "lkshrk", parent: "omnigent-ai/omnigent", repoOwner: "lkshrk", prs: map[int]*fakePR{}, runs: map[string][]fakeRun{}, existing: map[int][]map[string]any{}}
	g.srv = httptest.NewServer(http.HandlerFunc(g.serve))
	t.Cleanup(g.srv.Close)
	return g
}

func (g *fakeGitHub) readyPR(number int, sha string) *fakePR {
	g.mu.Lock()
	defer g.mu.Unlock()
	pr := &fakePR{
		number: number, title: "feat(web): add the thing", body: "Adds the thing.\n\nTested with the web suite.", headRef: "feature-" + strconv.Itoa(number),
		headRepo: testFork, base: "main", sha: sha, labels: []string{"upstream-ready"}, additions: 40, dels: 2, files: 3,
		changes: []map[string]any{{"filename": "web/src/thing.ts"}}, compareStatus: "ahead",
	}
	g.prs[number] = pr
	g.runs[sha] = []fakeRun{{"completed", "success", ".github/workflows/contribution-ci.yml"}}
	return pr
}

func (g *fakeGitHub) update(f func()) {
	g.mu.Lock()
	defer g.mu.Unlock()
	f()
}

func (g *fakeGitHub) reviewCalls() []review {
	g.mu.Lock()
	defer g.mu.Unlock()
	return append([]review(nil), g.reviews...)
}

func (g *fakeGitHub) prJSON(pr *fakePR) map[string]any {
	labels := []any{}
	for _, l := range pr.labels {
		labels = append(labels, map[string]any{"name": l})
	}
	return map[string]any{
		"number": pr.number, "state": "open", "title": pr.title, "body": pr.body, "labels": labels,
		"html_url":  fmt.Sprintf("https://github.com/%s/pull/%d", testFork, pr.number),
		"head":      map[string]any{"ref": pr.headRef, "sha": pr.sha, "repo": map[string]any{"full_name": pr.headRepo}},
		"base":      map[string]any{"ref": pr.base, "repo": map[string]any{"full_name": testFork}},
		"additions": pr.additions, "deletions": pr.dels, "changed_files": pr.files,
	}
}

func (g *fakeGitHub) serve(w http.ResponseWriter, r *http.Request) {
	g.mu.Lock()
	defer g.mu.Unlock()
	g.requests = append(g.requests, r.Method+" "+r.URL.Path)
	if r.Header.Get("Authorization") != "Bearer "+testPAT {
		http.Error(w, "bad credentials", http.StatusUnauthorized)
		return
	}
	path := r.URL.Path
	write := func(v any) { _ = json.NewEncoder(w).Encode(v) }
	switch {
	case r.Method == http.MethodGet && path == "/user":
		write(map[string]any{"login": g.login})
	case r.Method == http.MethodGet && path == "/repos/"+testFork:
		write(map[string]any{"full_name": testFork, "owner": map[string]any{"login": g.repoOwner}, "parent": map[string]any{"full_name": g.parent}})
	case r.Method == http.MethodGet && path == "/repos/"+testFork+"/pulls":
		q := r.URL.Query()
		if q.Get("state") != "open" || q.Get("base") != "main" {
			g.t.Errorf("pulls query = %v", q)
		}
		list := []any{}
		if q.Get("page") == "1" {
			for n := 1; n <= 100; n++ {
				if pr, ok := g.prs[n]; ok {
					list = append(list, g.prJSON(pr))
				}
			}
		}
		write(list)
	case r.Method == http.MethodGet && strings.HasPrefix(path, "/repos/"+testFork+"/pulls/") && strings.HasSuffix(path, "/reviews"):
		n, _ := strconv.Atoi(strings.TrimSuffix(strings.TrimPrefix(path, "/repos/"+testFork+"/pulls/"), "/reviews"))
		list := []any{}
		if r.URL.Query().Get("page") == "1" {
			for _, rv := range g.existing[n] {
				list = append(list, rv)
			}
		}
		write(list)
	case r.Method == http.MethodGet && strings.HasPrefix(path, "/repos/"+testFork+"/pulls/"):
		n, _ := strconv.Atoi(strings.TrimPrefix(path, "/repos/"+testFork+"/pulls/"))
		pr, ok := g.prs[n]
		if !ok {
			http.NotFound(w, r)
			return
		}
		write(g.prJSON(pr))
	case r.Method == http.MethodGet && strings.HasPrefix(path, "/repos/"+testFork+"/compare/omnigent-ai:main..."):
		sha := strings.TrimPrefix(path, "/repos/"+testFork+"/compare/omnigent-ai:main...")
		for _, pr := range g.prs {
			if pr.sha == sha {
				write(map[string]any{"status": pr.compareStatus, "ahead_by": 1, "files": pr.changes})
				return
			}
		}
		http.NotFound(w, r)
	case r.Method == http.MethodGet && path == "/repos/"+testFork+"/actions/workflows/contribution-ci.yml/runs":
		sha := r.URL.Query().Get("head_sha")
		runs := []any{}
		for _, run := range g.runs[sha] {
			runs = append(runs, map[string]any{"head_sha": sha, "path": run.path, "status": run.status, "conclusion": run.conclusion})
		}
		write(map[string]any{"total_count": len(runs), "workflow_runs": runs})
	case r.Method == http.MethodPost && strings.HasPrefix(path, "/repos/"+testFork+"/pulls/") && strings.HasSuffix(path, "/reviews"):
		n, _ := strconv.Atoi(strings.TrimSuffix(strings.TrimPrefix(path, "/repos/"+testFork+"/pulls/"), "/reviews"))
		var body map[string]any
		_ = json.NewDecoder(r.Body).Decode(&body)
		g.reviews = append(g.reviews, review{number: n, body: body})
		if g.failPost {
			http.Error(w, "unprocessable", http.StatusUnprocessableEntity)
			return
		}
		state := map[any]string{"APPROVE": "APPROVED", "REQUEST_CHANGES": "CHANGES_REQUESTED"}[body["event"]]
		g.existing[n] = append(g.existing[n], map[string]any{"user": map[string]any{"login": g.login}, "state": state, "commit_id": body["commit_id"]})
		write(map[string]any{"id": 1})
	default:
		g.t.Errorf("unexpected GitHub call %s %s", r.Method, r.URL)
		http.NotFound(w, r)
	}
}

type fakeSignal struct {
	mu   sync.Mutex
	sent []map[string]any
	fail bool
	srv  *httptest.Server
}

func newFakeSignal(t *testing.T) *fakeSignal {
	s := &fakeSignal{}
	s.srv = httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		s.mu.Lock()
		defer s.mu.Unlock()
		var body map[string]any
		_ = json.NewDecoder(r.Body).Decode(&body)
		if r.Method != http.MethodPost || r.URL.Path != "/v2/send" {
			t.Errorf("unexpected Signal call %s %s", r.Method, r.URL.Path)
		}
		if s.fail {
			http.Error(w, "down", http.StatusServiceUnavailable)
			return
		}
		s.sent = append(s.sent, body)
		w.WriteHeader(http.StatusCreated)
		_, _ = io.WriteString(w, `[{"timestamp":"1"}]`)
	}))
	t.Cleanup(s.srv.Close)
	return s
}

func (s *fakeSignal) messages() []string {
	s.mu.Lock()
	defer s.mu.Unlock()
	var out []string
	for _, b := range s.sent {
		out = append(out, b["message"].(string))
	}
	return out
}

type clock struct {
	mu sync.Mutex
	t  time.Time
}

func (c *clock) now() time.Time {
	c.mu.Lock()
	defer c.mu.Unlock()
	return c.t
}

func (c *clock) advance(d time.Duration) {
	c.mu.Lock()
	defer c.mu.Unlock()
	c.t = c.t.Add(d)
}

type harness struct {
	gh     *fakeGitHub
	signal *fakeSignal
	clock  *clock
	codes  *codeStore
	poller *poller
	api    http.Handler
	logs   func() string
}

func newHarness(t *testing.T) *harness {
	var buf bytes.Buffer
	var mu sync.Mutex
	log.SetOutput(writerFunc(func(p []byte) (int, error) { mu.Lock(); defer mu.Unlock(); return buf.Write(p) }))
	t.Cleanup(func() { log.SetOutput(os.Stderr) })

	h := &harness{gh: newFakeGitHub(t), signal: newFakeSignal(t), clock: &clock{t: time.Date(2026, 11, 4, 12, 0, 0, 0, time.UTC)}}
	h.logs = func() string { mu.Lock(); defer mu.Unlock(); return buf.String() }
	gh := &githubClient{base: h.gh.srv.URL, token: testPAT, http: h.gh.srv.Client()}
	notify := &signalNotifier{base: h.signal.srv.URL, number: testBot, recipient: testGroup, http: h.signal.srv.Client()}
	h.codes = newCodeStore(codeTTL, h.clock.now)
	forks, err := loadForks([]byte(forksJSON))
	if err != nil {
		t.Fatal(err)
	}
	h.poller = &poller{gh: gh, forks: forks, codes: h.codes, notify: notify}
	h.api = (&decider{token: testShared, codes: h.codes, gh: gh, forks: forks, notify: notify}).routes()
	t.Cleanup(func() {
		out := h.logs()
		for _, secret := range []string{testPAT, testShared, testBot, testGroup, "R3JvdXBJbnRlcm5hbElk"} {
			if strings.Contains(out, secret) {
				t.Errorf("log contains a secret value: %q", out)
			}
		}
	})
	return h
}

type writerFunc func([]byte) (int, error)

func (f writerFunc) Write(p []byte) (int, error) { return f(p) }
