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

const heldPromise = "a new code follows once the approver can read the pull request's reviews again; nothing was submitted."

var codeFormat = regexp.MustCompile(`^[0-9]{8}$`)

var reviewEvents = map[string]struct{ event, body, done string }{
	"approve": {"APPROVE", "Approved over Signal.", "Approved %s#%d at %s. Publishing upstream follows; the result comes here."},
	"reject":  {"REQUEST_CHANGES", "Changes requested over Signal.", "Requested changes on %s#%d at %s."},
}

type decider struct {
	token  string
	codes  *codeStore
	gh     *githubClient
	forks  []forkConfig
	notify notifier
	kick   func()
	links  *upstreamLinks
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
	p, status := d.codes.take(req.Code)
	switch status {
	case codeUnknown:
		log.Printf("contribution-approver: %s with an unknown or used code", req.Action)
		d.tell(ctx, "That code is unknown or already used. Nothing was submitted.")
		http.Error(w, "unknown or used code", http.StatusNotFound)
		return
	case codeExpired:
		log.Printf("contribution-approver: %s with an expired code for %s#%d", req.Action, p.pr.fork, p.pr.number)
		text := fmt.Sprintf("That code expired. Nothing was submitted; a new code for %s#%d follows within a minute if it is still ready.", p.pr.fork, p.pr.number)
		if d.codes.held(p.pr, p.sha) {
			text = fmt.Sprintf("That code for %s#%d expired; %s", p.pr.fork, p.pr.number, heldPromise)
		}
		d.tell(ctx, text)
		http.Error(w, "expired code", http.StatusGone)
		return
	case codeSuperseded:
		log.Printf("contribution-approver: %s with a superseded code for %s#%d", req.Action, p.pr.fork, p.pr.number)
		d.tell(ctx, fmt.Sprintf("That announcement is outdated: %s#%d has a newer announcement. Nothing was submitted.", p.pr.fork, p.pr.number))
		http.Error(w, "superseded code", http.StatusGone)
		return
	case codeExpiredStale:
		log.Printf("contribution-approver: %s with an expired code for a changed head of %s#%d", req.Action, p.pr.fork, p.pr.number)
		d.tell(ctx, fmt.Sprintf("That code expired and %s#%d has changed or been decided since. Nothing was submitted; the normal poll announces any new ready head.", p.pr.fork, p.pr.number))
		http.Error(w, "expired code for a changed head", http.StatusGone)
		return
	}
	if changed, err := d.changedSinceAnnouncement(ctx, p); err != nil || changed {
		d.codes.forget(p.pr)
		if err != nil {
			log.Printf("contribution-approver: %s %s#%d: re-reading the pull request failed: %v", req.Action, p.pr.fork, p.pr.number, err)
			d.tell(ctx, fmt.Sprintf("Could not re-check %s#%d on GitHub. Nothing was submitted; it will be announced again with a new code.", p.pr.fork, p.pr.number))
			http.Error(w, "pull request check failed", http.StatusBadGateway)
			return
		}
		log.Printf("contribution-approver: %s %s#%d refused: the pull request changed since the announcement", req.Action, p.pr.fork, p.pr.number)
		d.tell(ctx, fmt.Sprintf("%s#%d changed since the announcement (head, title, body or %s label). Nothing was submitted; if it is still ready, a new code follows within a minute.", p.pr.fork, p.pr.number, readyLabel))
		http.Error(w, "pull request changed", http.StatusConflict)
		return
	}
	if err := d.verifyFork(ctx, p.pr.fork); err != nil {
		d.codes.forget(p.pr)
		log.Printf("contribution-approver: %s %s#%d refused: %v", req.Action, p.pr.fork, p.pr.number, err)
		d.tell(ctx, fmt.Sprintf("Could not verify the fork %s against the registry. Nothing was submitted; it will be announced again with a new code once it checks out.", p.pr.fork))
		http.Error(w, "fork check failed", http.StatusBadGateway)
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
	done := fmt.Sprintf(review.done, p.pr.fork, p.pr.number, p.sha)
	if link := d.knownUpstream(p.pr); link != "" && req.Action == "approve" {
		done = fmt.Sprintf("Approved %s#%d at %s. Publishing upstream follows; the last known upstream pull request is %s. The result comes here.", p.pr.fork, p.pr.number, p.sha, link)
	}
	d.tell(ctx, done)
	if d.kick != nil {
		d.kick()
	}
	w.Header().Set("Content-Type", "application/json")
	_, _ = fmt.Fprintf(w, `{"result":%q}`, req.Action)
}

func (d *decider) knownUpstream(k prKey) string {
	if d.links == nil {
		return ""
	}
	return d.links.get(k)
}

func (d *decider) tell(ctx context.Context, text string) {
	if err := d.notify.Notify(ctx, text); err != nil {
		log.Printf("contribution-approver: confirmation not sent: %v", err)
	}
}

func (d *decider) changedSinceAnnouncement(ctx context.Context, p pending) (bool, error) {
	pr, err := d.gh.pull(ctx, p.pr.fork, p.pr.number)
	if err != nil {
		return false, err
	}
	a, ok := d.codes.announcementOf(p.pr, p.sha)
	return !ok || pr.State != "open" || pr.Head.SHA != p.sha || !pr.hasLabel(readyLabel) || contentDigest(pr.Title, pr.Body) != a.digest, nil
}

func (d *decider) verifyFork(ctx context.Context, fork string) error {
	for _, cfg := range d.forks {
		if cfg.Fork == fork {
			return d.gh.verifyFork(ctx, cfg)
		}
	}
	return fmt.Errorf("%s is not in the registry", fork)
}
