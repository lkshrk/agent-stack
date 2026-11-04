package main

import (
	"context"
	"crypto/sha256"
	"crypto/subtle"
	"encoding/json"
	"fmt"
	"io"
	"log"
	"net/http"
	"regexp"
	"strings"
	"time"
)

const reviewTimeout = 20 * time.Second

var codeFormat = regexp.MustCompile(`^[0-9]{8}$`)

var reviewEvents = map[string]struct{ event, body, done string }{
	"approve": {"APPROVE", "Approved over Signal.", "Approved %s#%d at %s; the fork's publish workflow takes it from here."},
	"reject":  {"REQUEST_CHANGES", "Changes requested over Signal.", "Requested changes on %s#%d at %s."},
}

// decider turns the owner's Signal decision, forwarded by signal-shim, into the owner's review on the announced head.
type decider struct {
	token  string
	codes  *codeStore
	gh     *githubClient
	notify notifier
}

func (d *decider) routes() http.Handler {
	mux := http.NewServeMux()
	mux.HandleFunc("POST /v1/decisions", d.decide)
	mux.HandleFunc("GET /healthz", func(w http.ResponseWriter, _ *http.Request) { w.WriteHeader(http.StatusOK) })
	return mux
}

func tokenMatches(got, want string) bool {
	g, w := sha256.Sum256([]byte(got)), sha256.Sum256([]byte(want))
	return subtle.ConstantTimeCompare(g[:], w[:]) == 1
}

func (d *decider) decide(w http.ResponseWriter, r *http.Request) {
	got, ok := strings.CutPrefix(r.Header.Get("Authorization"), "Bearer ")
	if !ok || !tokenMatches(got, d.token) {
		http.Error(w, "unauthorized", http.StatusUnauthorized)
		return
	}
	var req struct {
		Action string `json:"action"`
		Code   string `json:"code"`
	}
	dec := json.NewDecoder(io.LimitReader(r.Body, 1024))
	dec.DisallowUnknownFields()
	if err := dec.Decode(&req); err != nil {
		http.Error(w, "malformed decision", http.StatusBadRequest)
		return
	}
	if _, trailing := dec.Token(); trailing != io.EOF {
		http.Error(w, "malformed decision", http.StatusBadRequest)
		return
	}
	review, known := reviewEvents[req.Action]
	if !known || !codeFormat.MatchString(req.Code) {
		http.Error(w, "malformed decision", http.StatusBadRequest)
		return
	}

	ctx, cancel := context.WithTimeout(context.Background(), reviewTimeout)
	defer cancel()
	p, ok := d.codes.take(req.Code)
	if !ok {
		log.Printf("contribution-approver: %s with an unknown, used or expired code", req.Action)
		d.tell(ctx, "That code is unknown, already used or expired. Nothing was submitted.")
		http.Error(w, "unknown, used or expired code", http.StatusNotFound)
		return
	}
	if err := d.gh.review(ctx, p.pr.fork, p.pr.number, p.sha, review.event, review.body); err != nil {
		d.codes.forget(p.pr)
		log.Printf("contribution-approver: %s %s#%d at %s failed: %v", req.Action, p.pr.fork, p.pr.number, p.sha, err)
		d.tell(ctx, fmt.Sprintf("GitHub refused the review of %s#%d; it will be announced again with a new code.", p.pr.fork, p.pr.number))
		http.Error(w, "review failed", http.StatusBadGateway)
		return
	}
	log.Printf("contribution-approver: %s %s#%d at %s submitted", req.Action, p.pr.fork, p.pr.number, p.sha)
	d.tell(ctx, fmt.Sprintf(review.done, p.pr.fork, p.pr.number, p.sha))
	w.Header().Set("Content-Type", "application/json")
	_, _ = fmt.Fprintf(w, `{"result":%q}`, req.Action)
}

func (d *decider) tell(ctx context.Context, text string) {
	if err := d.notify.Notify(ctx, text); err != nil {
		log.Printf("contribution-approver: confirmation not sent: %v", err)
	}
}
