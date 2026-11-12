package main

import (
	"context"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"net/http/httptest"
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
	g := newGate(t, func(w http.ResponseWriter, r *http.Request) {
		gotAuth, gotPath, gotMethod = r.Header.Get("Authorization"), r.URL.Path, r.Method
		_, _ = w.Write([]byte(`{"id":"t1","status":"blocked","run_id":0,"link":{"issue_id":"i1","identifier":"ROU-1","session_id":"s1","root_issue_id":"i1"}}`))
	})
	card, err := g.Card(context.Background(), "t1")
	if err != nil {
		t.Fatal(err)
	}
	if gotAuth != "Bearer gate-secret" || gotPath != "/v1/cards/t1" || gotMethod != http.MethodGet {
		t.Fatalf("auth=%q path=%q method=%q", gotAuth, gotPath, gotMethod)
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
	if _, err := g.Card(ctx, "t1"); err == nil {
		t.Fatal("card: want error")
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

func TestGateClientUnknownCardIsPermanent(t *testing.T) {
	g := newGate(t, func(w http.ResponseWriter, _ *http.Request) {
		w.WriteHeader(http.StatusNotFound)
		_, _ = w.Write([]byte(`{"error": "unknown card"}`))
	})
	if _, err := g.Card(context.Background(), "t1"); !errors.Is(err, errUnknownCard) || !isPermanent(err) {
		t.Fatalf("err = %v", err)
	}
}

func TestGateClientForeign404IsNotUnknownCard(t *testing.T) {
	g := newGate(t, func(w http.ResponseWriter, _ *http.Request) {
		http.Error(w, "404 page not found", http.StatusNotFound)
	})
	if _, err := g.Card(context.Background(), "t1"); err == nil || errors.Is(err, errUnknownCard) {
		t.Fatalf("err = %v", err)
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
