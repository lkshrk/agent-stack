package main

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"testing"
	"time"
)

type tokenServer struct {
	*httptest.Server
	mu    sync.Mutex
	calls int
	body  string
	auth  string
}

func newTokenServer(t *testing.T) *tokenServer {
	ts := &tokenServer{}
	ts.Server = httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		b, _ := io.ReadAll(r.Body)
		ts.mu.Lock()
		ts.calls++
		ts.body = string(b)
		ts.auth = r.Header.Get("Authorization")
		n := ts.calls
		ts.mu.Unlock()
		_, _ = fmt.Fprintf(w, `{"access_token":"tok%d","token_type":"Bearer","expires_in":2592000,"scope":"x"}`, n)
	}))
	t.Cleanup(ts.Close)
	return ts
}

func newSource(url string) *tokenSource {
	return &tokenSource{
		tokenURL: url, clientID: "cid", clientSecret: "sec",
		scopes: "read,write,app:assignable,app:mentionable", http: http.DefaultClient,
	}
}

func TestTokenSourceCachesAndSendsExactScopes(t *testing.T) {
	ts := newTokenServer(t)
	src := newSource(ts.URL)
	for range 2 {
		tok, err := src.Token(context.Background())
		if err != nil || tok != "tok1" {
			t.Fatalf("tok=%q err=%v", tok, err)
		}
	}
	if ts.calls != 1 {
		t.Fatalf("calls = %d", ts.calls)
	}
	if ts.body != "grant_type=client_credentials&scope=read,write,app:assignable,app:mentionable" {
		t.Fatalf("body = %q", ts.body)
	}
	if u, p, ok := (&http.Request{Header: http.Header{"Authorization": {ts.auth}}}).BasicAuth(); !ok || u != "cid" || p != "sec" {
		t.Fatalf("auth = %q", ts.auth)
	}
}

func TestTokenSourceRefetchesNearExpiry(t *testing.T) {
	ts := newTokenServer(t)
	src := newSource(ts.URL)
	if _, err := src.Token(context.Background()); err != nil {
		t.Fatal(err)
	}
	src.mu.Lock()
	src.expiry = time.Now().Add(30 * time.Minute)
	src.mu.Unlock()
	tok, err := src.Token(context.Background())
	if err != nil || tok != "tok2" {
		t.Fatalf("tok=%q err=%v", tok, err)
	}
}

func TestTokenSourceErrorStatus(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.WriteHeader(http.StatusBadRequest)
	}))
	defer srv.Close()
	if _, err := newSource(srv.URL).Token(context.Background()); err == nil {
		t.Fatal("want error")
	}
}

type gqlCall struct {
	auth string
	req  struct {
		Query     string         `json:"query"`
		Variables map[string]any `json:"variables"`
	}
}

func gqlServer(t *testing.T, respond func(n int, w http.ResponseWriter)) (*httptest.Server, *[]gqlCall) {
	var mu sync.Mutex
	var calls []gqlCall
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		var c gqlCall
		c.auth = r.Header.Get("Authorization")
		_ = json.NewDecoder(r.Body).Decode(&c.req)
		mu.Lock()
		calls = append(calls, c)
		n := len(calls)
		mu.Unlock()
		respond(n, w)
	}))
	t.Cleanup(srv.Close)
	return srv, &calls
}

func TestGraphQLArchiveSendsMutation(t *testing.T) {
	ts := newTokenServer(t)
	srv, calls := gqlServer(t, func(_ int, w http.ResponseWriter) {
		_, _ = fmt.Fprint(w, `{"data":{"issueArchive":{"success":true}}}`)
	})
	g := &graphQL{endpoint: srv.URL, tokens: newSource(ts.URL), http: http.DefaultClient}
	if err := g.ArchiveIssue(context.Background(), "issue-1"); err != nil {
		t.Fatal(err)
	}
	c := (*calls)[0]
	if c.auth != "Bearer tok1" || !strings.Contains(c.req.Query, "issueArchive") || c.req.Variables["id"] != "issue-1" {
		t.Fatalf("call = %+v", c)
	}
}

func TestGraphQLThoughtSendsMutation(t *testing.T) {
	ts := newTokenServer(t)
	srv, calls := gqlServer(t, func(_ int, w http.ResponseWriter) {
		_, _ = fmt.Fprint(w, `{"data":{"agentActivityCreate":{"success":true}}}`)
	})
	g := &graphQL{endpoint: srv.URL, tokens: newSource(ts.URL), http: http.DefaultClient}
	if err := g.Thought(context.Background(), "sess-1", "hello"); err != nil {
		t.Fatal(err)
	}
	c := (*calls)[0]
	if !strings.Contains(c.req.Query, "agentActivityCreate") || !strings.Contains(c.req.Query, `type: "thought"`) || c.req.Variables["s"] != "sess-1" || c.req.Variables["b"] != "hello" {
		t.Fatalf("call = %+v", c)
	}
}

func TestGraphQLSuccessFalseIsError(t *testing.T) {
	ts := newTokenServer(t)
	srv, _ := gqlServer(t, func(_ int, w http.ResponseWriter) {
		_, _ = fmt.Fprint(w, `{"data":{"issueArchive":{"success":false}}}`)
	})
	g := &graphQL{endpoint: srv.URL, tokens: newSource(ts.URL), http: http.DefaultClient}
	if err := g.ArchiveIssue(context.Background(), "x"); err == nil {
		t.Fatal("want error")
	}
}

func TestGraphQLErrorsIsError(t *testing.T) {
	ts := newTokenServer(t)
	srv, _ := gqlServer(t, func(_ int, w http.ResponseWriter) {
		_, _ = fmt.Fprint(w, `{"errors":[{"message":"nope"}]}`)
	})
	g := &graphQL{endpoint: srv.URL, tokens: newSource(ts.URL), http: http.DefaultClient}
	if err := g.Thought(context.Background(), "s", "b"); err == nil || !strings.Contains(err.Error(), "nope") {
		t.Fatalf("err = %v", err)
	}
}

func TestGraphQLRetriesOnceOn401(t *testing.T) {
	ts := newTokenServer(t)
	srv, calls := gqlServer(t, func(n int, w http.ResponseWriter) {
		if n == 1 {
			w.WriteHeader(http.StatusUnauthorized)
			return
		}
		_, _ = fmt.Fprint(w, `{"data":{"issueArchive":{"success":true}}}`)
	})
	g := &graphQL{endpoint: srv.URL, tokens: newSource(ts.URL), http: http.DefaultClient}
	if err := g.ArchiveIssue(context.Background(), "x"); err != nil {
		t.Fatal(err)
	}
	if len(*calls) != 2 || (*calls)[0].auth != "Bearer tok1" || (*calls)[1].auth != "Bearer tok2" {
		t.Fatalf("calls = %+v", *calls)
	}
}

func TestGraphQLGivesUpAfterSecond401(t *testing.T) {
	ts := newTokenServer(t)
	srv, calls := gqlServer(t, func(_ int, w http.ResponseWriter) {
		w.WriteHeader(http.StatusUnauthorized)
	})
	g := &graphQL{endpoint: srv.URL, tokens: newSource(ts.URL), http: http.DefaultClient}
	if err := g.ArchiveIssue(context.Background(), "x"); err == nil {
		t.Fatal("want error")
	}
	if len(*calls) != 2 {
		t.Fatalf("calls = %d", len(*calls))
	}
}

func TestSignalNotifierPostsV2Send(t *testing.T) {
	var path string
	var got map[string]any
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		path = r.URL.Path
		_ = json.NewDecoder(r.Body).Decode(&got)
		w.WriteHeader(http.StatusCreated)
	}))
	defer srv.Close()
	n := &signalNotifier{base: srv.URL + "/", number: "+491", recipient: "+492", http: http.DefaultClient}
	if err := n.Notify(context.Background(), "hi"); err != nil {
		t.Fatal(err)
	}
	rec := got["recipients"].([]any)
	if path != "/v2/send" || got["number"] != "+491" || got["message"] != "hi" || len(rec) != 1 || rec[0] != "+492" {
		t.Fatalf("path=%q body=%v", path, got)
	}
}

func TestSignalNotifierNon2xxIsError(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.WriteHeader(http.StatusInternalServerError)
	}))
	defer srv.Close()
	n := &signalNotifier{base: srv.URL, http: http.DefaultClient}
	if err := n.Notify(context.Background(), "x"); err == nil {
		t.Fatal("want error")
	}
}

func TestGraphQLIssueQuery(t *testing.T) {
	var query string
	var vars map[string]any
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		var req struct {
			Query     string         `json:"query"`
			Variables map[string]any `json:"variables"`
		}
		raw, _ := io.ReadAll(r.Body)
		_ = json.Unmarshal(raw, &req)
		query, vars = req.Query, req.Variables
		_, _ = w.Write([]byte(`{"data":{"issue":{"id":"i1","identifier":"ROU-1","title":"T","description":null,
			"parent":{"id":"p1"},"delegate":{"id":"app1"},
			"team":{"id":"tm1","states":{"nodes":[{"id":"s1","name":"Todo","type":"unstarted"}]}}}}}`))
	}))
	defer srv.Close()
	ts := newTokenServer(t)
	g := &graphQL{endpoint: srv.URL, tokens: newSource(ts.URL), http: http.DefaultClient}
	info, err := g.Issue(context.Background(), "i1")
	if err != nil {
		t.Fatal(err)
	}
	for _, frag := range []string{"parent{ id }", "team{ id states{ nodes{ id name type } } }", "delegate{ id }"} {
		if !strings.Contains(query, frag) {
			t.Fatalf("query missing %q: %s", frag, query)
		}
	}
	if vars["id"] != "i1" {
		t.Fatalf("vars = %v", vars)
	}
	want := IssueInfo{
		ID: "i1", Identifier: "ROU-1", Title: "T", ParentID: "p1", DelegateID: "app1", TeamID: "tm1",
		States: []WorkflowState{{ID: "s1", Name: "Todo", Type: "unstarted"}},
	}
	if info.ID != want.ID || info.ParentID != want.ParentID || info.DelegateID != want.DelegateID ||
		info.TeamID != want.TeamID || info.Description != "" || len(info.States) != 1 || info.States[0] != want.States[0] {
		t.Fatalf("info = %+v", info)
	}
}

func TestGraphQLIssueNotFound(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		_, _ = w.Write([]byte(`{"data":{"issue":null}}`))
	}))
	defer srv.Close()
	ts := newTokenServer(t)
	g := &graphQL{endpoint: srv.URL, tokens: newSource(ts.URL), http: http.DefaultClient}
	if _, err := g.Issue(context.Background(), "i1"); err == nil {
		t.Fatal("want error")
	}
}

func TestGraphQLActivityTypes(t *testing.T) {
	ts := newTokenServer(t)
	srv, calls := gqlServer(t, func(_ int, w http.ResponseWriter) {
		_, _ = fmt.Fprint(w, `{"data":{"agentActivityCreate":{"success":true}}}`)
	})
	g := &graphQL{endpoint: srv.URL, tokens: newSource(ts.URL), http: http.DefaultClient}
	for _, typ := range []string{"thought", "elicitation", "response", "error"} {
		if err := g.Activity(context.Background(), "sess-1", Activity{typ, "b-" + typ}); err != nil {
			t.Fatal(err)
		}
	}
	for i, typ := range []string{"thought", "elicitation", "response", "error"} {
		c := (*calls)[i]
		content, _ := c.req.Variables["content"].(map[string]any)
		if c.req.Variables["s"] != "sess-1" || content["type"] != typ || content["body"] != "b-"+typ {
			t.Fatalf("call %d = %+v", i, c.req.Variables)
		}
	}
}

func TestGraphQLProjectionMutations(t *testing.T) {
	ts := newTokenServer(t)
	srv, calls := gqlServer(t, func(_ int, w http.ResponseWriter) {
		_, _ = fmt.Fprint(w, `{"data":{"issueUpdate":{"success":true},"commentCreate":{"success":true},"attachmentCreate":{"success":true}}}`)
	})
	g := &graphQL{endpoint: srv.URL, tokens: newSource(ts.URL), http: http.DefaultClient}
	ctx := context.Background()
	for _, err := range []error{g.SetState(ctx, "i", "s"), g.Comment(ctx, "i", "b"), g.LinkCard(ctx, "i", "c1")} {
		if err != nil {
			t.Fatal(err)
		}
	}
	for i, want := range []string{"issueUpdate", "commentCreate", "attachmentCreate"} {
		if !strings.Contains((*calls)[i].req.Query, want) {
			t.Fatalf("call %d query = %s", i, (*calls)[i].req.Query)
		}
	}
	if (*calls)[2].req.Variables["t"] != "Kanban card c1" {
		t.Fatalf("vars = %v", (*calls)[2].req.Variables)
	}
}
