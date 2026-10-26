package main

import (
	"crypto/hmac"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"sync"
	"time"
)

const (
	maxBodyBytes = 1 << 20
	maxEventAge  = 60 * time.Second
	dedupeTTL    = 7 * time.Hour
)

type event struct {
	Type   string `json:"type"`
	Action string `json:"action"`
	Actor  *struct {
		ID string `json:"id"`
	} `json:"actor"`
	WebhookTimestamp int64           `json:"webhookTimestamp"`
	Data             json.RawMessage `json:"data"`
	AgentSession     json.RawMessage `json:"agentSession"`
}

func verifySignature(secret, body []byte, header string) bool {
	got, err := hex.DecodeString(header)
	if err != nil {
		return false
	}
	mac := hmac.New(sha256.New, secret)
	mac.Write(body)
	return hmac.Equal(got, mac.Sum(nil))
}

func fresh(tsMillis int64, now time.Time, maxAge time.Duration) bool {
	return now.Sub(time.UnixMilli(tsMillis)) <= maxAge
}

type deduper struct {
	mu   sync.Mutex
	ttl  time.Duration
	seen map[string]time.Time
}

func newDeduper(ttl time.Duration) *deduper {
	return &deduper{ttl: ttl, seen: map[string]time.Time{}}
}

func (d *deduper) firstSeen(id string, now time.Time) bool {
	d.mu.Lock()
	defer d.mu.Unlock()
	for k, exp := range d.seen {
		if !now.Before(exp) {
			delete(d.seen, k)
		}
	}
	if _, ok := d.seen[id]; ok {
		return false
	}
	d.seen[id] = now.Add(d.ttl)
	return true
}

func (d *deduper) forget(id string) {
	d.mu.Lock()
	defer d.mu.Unlock()
	delete(d.seen, id)
}

type ingress struct {
	secret []byte
	dedupe *deduper
	queue  chan event
	now    func() time.Time
}

func (in *ingress) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	body, err := io.ReadAll(http.MaxBytesReader(w, r.Body, maxBodyBytes))
	if err != nil {
		var tooBig *http.MaxBytesError
		if errors.As(err, &tooBig) {
			http.Error(w, "body too large", http.StatusRequestEntityTooLarge)
			return
		}
		http.Error(w, "unreadable body", http.StatusBadRequest)
		return
	}
	if !verifySignature(in.secret, body, r.Header.Get("Linear-Signature")) {
		http.Error(w, "bad signature", http.StatusUnauthorized)
		return
	}
	var ev event
	if err := json.Unmarshal(body, &ev); err != nil {
		http.Error(w, "bad body", http.StatusBadRequest)
		return
	}
	now := in.now()
	if !fresh(ev.WebhookTimestamp, now, maxEventAge) {
		http.Error(w, "stale event", http.StatusBadRequest)
		return
	}
	id := r.Header.Get("Linear-Delivery")
	if id == "" {
		http.Error(w, "missing delivery id", http.StatusBadRequest)
		return
	}
	sigKey := "sig:" + r.Header.Get("Linear-Signature")
	deliveryKey := "delivery:" + id
	if !in.dedupe.firstSeen(deliveryKey, now) || !in.dedupe.firstSeen(sigKey, now) {
		w.WriteHeader(http.StatusOK)
		return
	}
	select {
	case in.queue <- ev:
		w.WriteHeader(http.StatusOK)
	default:
		// unmark so Linear's retry is not swallowed as a duplicate
		in.dedupe.forget(deliveryKey)
		in.dedupe.forget(sigKey)
		http.Error(w, "queue full", http.StatusServiceUnavailable)
	}
}
