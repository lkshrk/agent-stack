package main

import (
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"net/url"
	"regexp"
	"strconv"
	"strings"
	"sync"
	"testing"
	"time"
)

type review struct {
	number int
	body   map[string]any
}

type fakeCall struct {
	Method, Path string
	Query        url.Values
	Body         map[string]any
	IfNoneMatch  string
	Status       int
}

type fakeGitHub struct {
	mu       sync.Mutex
	t        *testing.T
	srv      *httptest.Server
	clock    *clock
	login    string
	repos    map[string]*fakeRepo
	fork     *fakeRepo
	upstream *fakeRepo
	prs      map[int]*fakePR
	runs     map[string][]fakeRun
	parents  map[string][]string
	reviews  []review
	failPost bool
	failures map[string]int
	gqlError string
	requests []string
	calls    []fakeCall
	billed   int
	nextID   int64
	routes   []fakeRoute
}

type fakeRoute struct {
	method string
	re     *regexp.Regexp
	handle func(*fakeReq)
}

type fakeReq struct {
	g     *fakeGitHub
	w     http.ResponseWriter
	r     *http.Request
	call  *fakeCall
	m     map[string]string
	body  map[string]any
	query url.Values
}

func newFakeGitHub(t *testing.T) *fakeGitHub {
	g := &fakeGitHub{
		t: t, login: "lkshrk", clock: &clock{t: time.Date(2026, 11, 4, 12, 0, 0, 0, time.UTC)},
		repos: map[string]*fakeRepo{}, parents: map[string][]string{}, failures: map[string]int{}, nextID: 1000,
	}
	g.upstream = newFakeRepo(testUpstream, "", "main", shaUpMain)
	g.fork = newFakeRepo(testFork, testUpstream, "main", shaMain)
	g.repos[testUpstream], g.repos[testFork] = g.upstream, g.fork
	g.prs, g.runs = g.fork.pulls, g.fork.runs
	g.routes = g.routeTable()
	g.srv = httptest.NewServer(http.HandlerFunc(g.serve))
	t.Cleanup(g.srv.Close)
	return g
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

func (g *fakeGitHub) fail(method, path string, status int) {
	g.mu.Lock()
	defer g.mu.Unlock()
	if status == 0 {
		delete(g.failures, method+" "+path)
		return
	}
	g.failures[method+" "+path] = status
}

func (g *fakeGitHub) failGraphQL(message string) {
	g.mu.Lock()
	defer g.mu.Unlock()
	g.gqlError = message
}

func (g *fakeGitHub) allCalls() []fakeCall {
	g.mu.Lock()
	defer g.mu.Unlock()
	return append([]fakeCall(nil), g.calls...)
}

func (g *fakeGitHub) writes() []fakeCall {
	var out []fakeCall
	for _, c := range g.allCalls() {
		if c.Method != http.MethodGet && c.Path != "/graphql" {
			out = append(out, c)
		}
	}
	return out
}

func (g *fakeGitHub) callCount(method, path string) int {
	n := 0
	for _, c := range g.allCalls() {
		if c.Method == method && c.Path == path {
			n++
		}
	}
	return n
}

func (g *fakeGitHub) billedRequests() int {
	g.mu.Lock()
	defer g.mu.Unlock()
	return g.billed
}

func (g *fakeGitHub) serve(w http.ResponseWriter, r *http.Request) {
	g.mu.Lock()
	defer g.mu.Unlock()
	g.requests = append(g.requests, r.Method+" "+r.URL.Path)
	g.calls = append(g.calls, fakeCall{Method: r.Method, Path: r.URL.Path, Query: r.URL.Query(), IfNoneMatch: r.Header.Get("If-None-Match")})
	c := &fakeReq{g: g, w: w, r: r, call: &g.calls[len(g.calls)-1], query: r.URL.Query()}
	if r.Header.Get("Authorization") != "Bearer "+testPAT {
		c.error(http.StatusUnauthorized, "Bad credentials")
		return
	}
	if r.Body != nil && (r.Method == http.MethodPost || r.Method == http.MethodPatch || r.Method == http.MethodPut) {
		if err := json.NewDecoder(r.Body).Decode(&c.body); err != nil {
			c.error(http.StatusBadRequest, "Problems parsing JSON")
			return
		}
		c.call.Body = c.body
	}
	if status, ok := g.failures[r.Method+" "+r.URL.Path]; ok {
		c.error(status, "injected failure")
		return
	}
	for _, rt := range g.routes {
		if rt.method != r.Method {
			continue
		}
		m := rt.re.FindStringSubmatch(r.URL.Path)
		if m == nil {
			continue
		}
		c.m = map[string]string{}
		for i, name := range rt.re.SubexpNames() {
			if name != "" {
				c.m[name] = m[i]
			}
		}
		rt.handle(c)
		return
	}
	g.t.Errorf("unexpected GitHub call %s %s", r.Method, r.URL)
	c.error(http.StatusNotFound, "Not Found")
}

func (c *fakeReq) repo() *fakeRepo {
	r, ok := c.g.repos[strings.ToLower(c.m["repo"])]
	if !ok {
		c.error(http.StatusNotFound, "Not Found")
		return nil
	}
	return r
}

func (c *fakeReq) num(key string) int {
	n, _ := strconv.Atoi(c.m[key])
	return n
}

func (c *fakeReq) str(key string) (string, bool) {
	v, ok := c.body[key].(string)
	return v, ok
}

func (c *fakeReq) error(status int, message string) {
	c.send(status, map[string]any{"message": message, "status": strconv.Itoa(status)})
}

func (c *fakeReq) invalid(message string) {
	c.send(http.StatusUnprocessableEntity, map[string]any{"message": "Validation Failed", "errors": []any{map[string]any{"message": message, "code": "custom"}}, "status": "422"})
}

func (c *fakeReq) send(status int, v any) {
	c.call.Status = status
	if status == http.StatusNoContent {
		c.g.billed++
		c.w.WriteHeader(status)
		return
	}
	raw, _ := json.Marshal(v)
	c.w.Header().Set("Content-Type", "application/json; charset=utf-8")
	if c.r.Method == http.MethodGet && status == http.StatusOK {
		sum := sha256.Sum256(raw)
		etag := `W/"` + hex.EncodeToString(sum[:]) + `"`
		c.w.Header().Set("ETag", etag)
		if matchesETag(c.r.Header.Get("If-None-Match"), etag) {
			c.call.Status = http.StatusNotModified
			c.w.WriteHeader(http.StatusNotModified)
			return
		}
	}
	c.g.billed++
	c.w.WriteHeader(status)
	_, _ = c.w.Write(raw)
}

func matchesETag(header, etag string) bool {
	weak := func(s string) string { return strings.TrimPrefix(strings.TrimSpace(s), "W/") }
	for _, candidate := range strings.Split(header, ",") {
		if strings.TrimSpace(candidate) == "*" || weak(candidate) == weak(etag) {
			return true
		}
	}
	return false
}

func (c *fakeReq) page(items []any) []any {
	per, err := strconv.Atoi(c.query.Get("per_page"))
	if err != nil || per <= 0 {
		per = 30
	}
	if per > 100 {
		per = 100
	}
	page, err := strconv.Atoi(c.query.Get("page"))
	if err != nil || page <= 0 {
		page = 1
	}
	last := (len(items) + per - 1) / per
	if last == 0 {
		last = 1
	}
	link := func(p int, rel string) string {
		q := c.r.URL.Query()
		q.Set("page", strconv.Itoa(p))
		q.Set("per_page", strconv.Itoa(per))
		return fmt.Sprintf(`<%s%s?%s>; rel="%s"`, c.g.srv.URL, c.r.URL.Path, q.Encode(), rel)
	}
	var links []string
	if page > 1 {
		links = append(links, link(page-1, "prev"))
	}
	if page < last {
		links = append(links, link(page+1, "next"), link(last, "last"))
	}
	if page > 1 {
		links = append(links, link(1, "first"))
	}
	if len(links) > 0 {
		c.w.Header().Set("Link", strings.Join(links, ", "))
	}
	start := (page - 1) * per
	if start >= len(items) {
		return []any{}
	}
	end := start + per
	if end > len(items) {
		end = len(items)
	}
	return items[start:end]
}

func (c *fakeReq) list(items []any) { c.send(http.StatusOK, c.page(items)) }

func decodeJSON(t *testing.T, raw []byte, out any) {
	t.Helper()
	if err := json.NewDecoder(bytes.NewReader(raw)).Decode(out); err != nil {
		t.Fatalf("decode %q: %v", raw, err)
	}
}
