package main

import (
	"context"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

func newGate(t *testing.T, h http.HandlerFunc) *gateClient {
	t.Helper()
	srv := httptest.NewServer(h)
	t.Cleanup(srv.Close)
	return &gateClient{base: srv.URL, token: "gate-secret", http: srv.Client()}
}

func TestGateClientSendsBearer(t *testing.T) {
	var gotAuth, gotPath, gotMethod string
	var gotBody map[string]string
	g := newGate(t, func(w http.ResponseWriter, r *http.Request) {
		gotAuth, gotPath, gotMethod = r.Header.Get("Authorization"), r.URL.Path, r.Method
		raw, _ := io.ReadAll(r.Body)
		_ = json.Unmarshal(raw, &gotBody)
		_, _ = w.Write([]byte(`{"id":"t1","status":"blocked","run_id":0,"link":{"issue_id":"i1","identifier":"ROU-1","session_id":"s1","root_issue_id":"i1"}}`))
	})
	card, err := g.CreateRootCard(context.Background(), RootCardRequest{
		IssueID: "i1", Identifier: "ROU-1", Title: "T", Description: "D", SessionID: "s1",
	})
	if err != nil {
		t.Fatal(err)
	}
	if gotAuth != "Bearer gate-secret" || gotPath != "/v1/cards" || gotMethod != http.MethodPost {
		t.Fatalf("auth=%q path=%q method=%q", gotAuth, gotPath, gotMethod)
	}
	if gotBody["issue_id"] != "i1" || gotBody["identifier"] != "ROU-1" || gotBody["title"] != "T" ||
		gotBody["description"] != "D" || gotBody["session_id"] != "s1" {
		t.Fatalf("body = %v", gotBody)
	}
	if card.ID != "t1" || card.Link == nil || card.Link.RootIssueID != "i1" {
		t.Fatalf("card = %+v", card)
	}
}

func TestGateClientNon2xxIsError(t *testing.T) {
	g := newGate(t, func(w http.ResponseWriter, _ *http.Request) {
		http.Error(w, "nope", http.StatusInternalServerError)
	})
	ctx := context.Background()
	if _, err := g.CreateRootCard(ctx, RootCardRequest{}); err == nil {
		t.Fatal("create: want error")
	}
	if _, err := g.Card(ctx, "t1"); err == nil {
		t.Fatal("card: want error")
	}
	if err := g.StopCard(ctx, "t1", "r"); err == nil {
		t.Fatal("stop: want error")
	}
	if _, err := g.Events(ctx, 0, 10); err == nil {
		t.Fatal("events: want error")
	}
	if _, err := g.Cursor(ctx, "c"); err == nil {
		t.Fatal("cursor: want error")
	}
	if err := g.SetCursor(ctx, "c", 1); err == nil {
		t.Fatal("setcursor: want error")
	}
}

func TestGateClientCardByIssueForeign404IsError(t *testing.T) {
	g := newGate(t, func(w http.ResponseWriter, _ *http.Request) {
		http.Error(w, "404 page not found", http.StatusNotFound)
	})
	if _, ok, err := g.CardByIssue(context.Background(), "i1"); err == nil || ok {
		t.Fatalf("ok=%v err=%v", ok, err)
	}
}

func TestGateClientCardByIssueNotFound(t *testing.T) {
	g := newGate(t, func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Query().Get("linear_issue") != "i1" {
			t.Errorf("query = %q", r.URL.RawQuery)
		}
		w.WriteHeader(http.StatusNotFound)
		_, _ = w.Write([]byte(`{"error": "unknown card"}`))
	})
	_, ok, err := g.CardByIssue(context.Background(), "i1")
	if err != nil || ok {
		t.Fatalf("ok=%v err=%v", ok, err)
	}
}

func TestGateClientStopReportsPartialFailure(t *testing.T) {
	g := newGate(t, func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/v1/cards/t1/stop" {
			t.Errorf("path = %q", r.URL.Path)
		}
		_, _ = w.Write([]byte(`{"stopped":["t1"],"interrupt_errors":["s1: boom"]}`))
	})
	if err := g.StopCard(context.Background(), "t1", "owner: un-delegated"); err == nil {
		t.Fatal("want error for interrupt_errors")
	}
}

func TestGateClientEventsAndCursors(t *testing.T) {
	var putBody map[string]int64
	g := newGate(t, func(w http.ResponseWriter, r *http.Request) {
		switch {
		case r.URL.Path == "/v1/events":
			if r.URL.Query().Get("after") != "5" || r.URL.Query().Get("limit") != "20" {
				t.Errorf("query = %q", r.URL.RawQuery)
			}
			_, _ = w.Write([]byte(`{"events":[{"id":6,"card_id":"t1","kind":"completed","created_at":99}]}`))
		case r.Method == http.MethodPut && r.URL.Path == "/v1/cursors/bridge":
			raw, _ := io.ReadAll(r.Body)
			_ = json.Unmarshal(raw, &putBody)
			_, _ = w.Write([]byte(`{"value":7}`))
		case r.URL.Path == "/v1/cursors/bridge":
			_, _ = w.Write([]byte(`{"value":7}`))
		default:
			t.Errorf("unexpected %s %s", r.Method, r.URL.Path)
		}
	})
	ctx := context.Background()
	evs, err := g.Events(ctx, 5, 20)
	if err != nil || len(evs) != 1 || evs[0] != (CardEvent{ID: 6, CardID: "t1", Kind: "completed", CreatedAt: 99}) {
		t.Fatalf("evs=%v err=%v", evs, err)
	}
	if v, err := g.Cursor(ctx, "bridge"); err != nil || v != 7 {
		t.Fatalf("v=%d err=%v", v, err)
	}
	if err := g.SetCursor(ctx, "bridge", 7); err != nil || putBody["value"] != 7 {
		t.Fatalf("put=%v err=%v", putBody, err)
	}
}

func TestGateClientStopErrorCarriesInterruptDetails(t *testing.T) {
	g := newGate(t, func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusBadGateway)
		_, _ = w.Write([]byte(`{"error":"interrupt failed","interrupt_errors":["sess-9: boom"]}`))
	})
	err := g.StopCard(context.Background(), "t1", "owner: un-delegated")
	if err == nil || !strings.Contains(err.Error(), "sess-9: boom") {
		t.Fatalf("err = %v", err)
	}
	g = newGate(t, func(w http.ResponseWriter, r *http.Request) {
		_, _ = w.Write([]byte(`{"stopped":["t1"],"interrupt_errors":["sess-9: boom"]}`))
	})
	if err := g.StopCard(context.Background(), "t1", "x"); err == nil || !strings.Contains(err.Error(), "sess-9: boom") {
		t.Fatalf("err = %v", err)
	}
}

func TestGateHTTPTimeoutOutlastsStopBudget(t *testing.T) {
	if c := newGateHTTPClient(); c.Timeout <= stopTimeout {
		t.Fatalf("timeout %v <= stop budget %v", c.Timeout, stopTimeout)
	}
}
