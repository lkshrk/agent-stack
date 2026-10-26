package main

import (
	"crypto/hmac"
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"
)

var testSecret = []byte("whsec_test")

func sign(secret, body []byte) string {
	m := hmac.New(sha256.New, secret)
	m.Write(body)
	return hex.EncodeToString(m.Sum(nil))
}

func TestVerifySignatureRawBody(t *testing.T) {
	body := []byte("{ \"webhookTimestamp\" :  1,\n  \"type\":\"AgentSessionEvent\",   \"action\": \"created\" }\n")
	sig := sign(testSecret, body)
	if !verifySignature(testSecret, body, sig) {
		t.Fatal("valid signature over raw bytes rejected")
	}
	flipped := append([]byte(nil), body...)
	flipped[3] ^= 0x01
	if verifySignature(testSecret, flipped, sig) {
		t.Fatal("flipped byte accepted")
	}
	if verifySignature(testSecret, body, "not-hex!") {
		t.Fatal("non-hex header accepted")
	}
	if verifySignature(testSecret, body, "") {
		t.Fatal("empty header accepted")
	}
}

func TestFreshRejectsOlderThan60s(t *testing.T) {
	now := time.UnixMilli(1_700_000_000_000)
	cases := []struct {
		name string
		ts   time.Time
		want bool
	}{
		{"59s old", now.Add(-59 * time.Second), true},
		{"61s old", now.Add(-61 * time.Second), false},
		{"5s future", now.Add(5 * time.Second), true},
	}
	for _, c := range cases {
		if got := fresh(c.ts.UnixMilli(), now, 60*time.Second); got != c.want {
			t.Errorf("%s: got %v want %v", c.name, got, c.want)
		}
	}
}

func TestDeduperConcurrent(t *testing.T) {
	d := newDeduper(7 * time.Hour)
	now := time.Now()
	var wins atomic.Int32
	var wg sync.WaitGroup
	for i := 0; i < 100; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			if d.firstSeen("same", now) {
				wins.Add(1)
			}
		}()
	}
	wg.Wait()
	if wins.Load() != 1 {
		t.Fatalf("firstSeen true %d times, want 1", wins.Load())
	}
}

func TestDeduperExpires(t *testing.T) {
	d := newDeduper(7 * time.Hour)
	now := time.Now()
	if !d.firstSeen("a", now) {
		t.Fatal("first call should be true")
	}
	if d.firstSeen("a", now.Add(7*time.Hour-time.Second)) {
		t.Fatal("within ttl should be false")
	}
	if !d.firstSeen("a", now.Add(7*time.Hour+time.Second)) {
		t.Fatal("after ttl should be true again")
	}
}

func TestIngressStatusCodes(t *testing.T) {
	now := time.UnixMilli(1_700_000_000_000)
	bodyAt := func(ts time.Time) string {
		return fmt.Sprintf(`{ "type":"AgentSessionEvent",  "action" : "created", "webhookTimestamp": %d,"data":{"id":"x"} }`, ts.UnixMilli())
	}
	good := bodyAt(now.Add(-time.Second))
	good2 := strings.Replace(good, `"x"`, `"y"`, 1)
	stale := bodyAt(now.Add(-2 * time.Minute))

	type req struct {
		body, sig, delivery string
	}
	signed := func(body, delivery string) req { return req{body, sign(testSecret, []byte(body)), delivery} }

	cases := []struct {
		name      string
		queueCap  int
		reqs      []req
		wantCodes []int
		wantQueue int
	}{
		{"bad signature", 4, []req{{good, sign([]byte("other"), []byte(good)), "d1"}}, []int{401}, 0},
		{"unparsable", 4, []req{signed("not json", "d1")}, []int{400}, 0},
		{"stale", 4, []req{signed(stale, "d1")}, []int{400}, 0},
		{"no delivery header", 4, []req{signed(good, "")}, []int{400}, 0},
		{"duplicate", 4, []req{signed(good, "d1"), signed(good, "d1")}, []int{200, 200}, 1},
		{"queue full", 1, []req{signed(good, "d1"), signed(good2, "d2")}, []int{200, 503}, 1},
		{"good", 4, []req{signed(good, "d1")}, []int{200}, 1},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			in := &ingress{
				secret: testSecret,
				dedupe: newDeduper(7 * time.Hour),
				queue:  make(chan event, c.queueCap),
				now:    func() time.Time { return now },
			}
			for i, r := range c.reqs {
				hr := httptest.NewRequest(http.MethodPost, "/webhooks/linear", strings.NewReader(r.body))
				hr.Header.Set("Linear-Signature", r.sig)
				if r.delivery != "" {
					hr.Header.Set("Linear-Delivery", r.delivery)
				}
				w := httptest.NewRecorder()
				in.ServeHTTP(w, hr)
				if w.Code != c.wantCodes[i] {
					t.Errorf("request %d: code %d want %d", i, w.Code, c.wantCodes[i])
				}
			}
			if len(in.queue) != c.wantQueue {
				t.Errorf("queue length %d want %d", len(in.queue), c.wantQueue)
			}
		})
	}
}

func TestIngressRetryAfterFullQueueIsNotDeduped(t *testing.T) {
	now := time.UnixMilli(1_700_000_000_000)
	in := &ingress{
		secret: testSecret,
		dedupe: newDeduper(7 * time.Hour),
		queue:  make(chan event, 1),
		now:    func() time.Time { return now },
	}
	body := fmt.Sprintf(`{"type":"t","webhookTimestamp":%d}`, now.UnixMilli())
	send := func(delivery string) int {
		body := body
		if delivery == "d2" {
			body = fmt.Sprintf(`{"type":"u","webhookTimestamp":%d}`, now.UnixMilli())
		}
		hr := httptest.NewRequest(http.MethodPost, "/webhooks/linear", strings.NewReader(body))
		hr.Header.Set("Linear-Signature", sign(testSecret, []byte(body)))
		hr.Header.Set("Linear-Delivery", delivery)
		w := httptest.NewRecorder()
		in.ServeHTTP(w, hr)
		return w.Code
	}
	send("d1")
	if c := send("d2"); c != 503 {
		t.Fatalf("got %d want 503", c)
	}
	<-in.queue
	if c := send("d2"); c != 200 || len(in.queue) != 1 {
		t.Fatalf("retry: code %d queue %d, want 200 and 1", c, len(in.queue))
	}
}

func TestIngressConcurrentIdenticalDeliveriesQueueOnce(t *testing.T) {
	now := time.UnixMilli(1_700_000_000_000)
	in := &ingress{
		secret: testSecret,
		dedupe: newDeduper(7 * time.Hour),
		queue:  make(chan event, 64),
		now:    func() time.Time { return now },
	}
	body := fmt.Sprintf(`{"type":"t","webhookTimestamp":%d}`, now.UnixMilli())
	sig := sign(testSecret, []byte(body))
	var wg sync.WaitGroup
	for i := 0; i < 50; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			hr := httptest.NewRequest(http.MethodPost, "/webhooks/linear", strings.NewReader(body))
			hr.Header.Set("Linear-Signature", sig)
			hr.Header.Set("Linear-Delivery", "same")
			in.ServeHTTP(httptest.NewRecorder(), hr)
		}()
	}
	wg.Wait()
	if len(in.queue) != 1 {
		t.Fatalf("queued %d, want 1", len(in.queue))
	}
}

func TestNewServerTimeouts(t *testing.T) {
	s := newServer(":0", http.NewServeMux())
	if s.ReadHeaderTimeout != 10*time.Second || s.ReadTimeout != 5*time.Second ||
		s.WriteTimeout != 10*time.Second || s.IdleTimeout != 60*time.Second {
		t.Fatalf("unexpected timeouts: %+v", s)
	}
}

func TestServerCutsOffStalledBody(t *testing.T) {
	now := time.Now()
	in := &ingress{secret: testSecret, dedupe: newDeduper(time.Hour), queue: make(chan event, 1), now: func() time.Time { return now }}
	cfg := newServer("", in)
	cfg.ReadTimeout = 200 * time.Millisecond
	ts := httptest.NewUnstartedServer(in)
	ts.Config = cfg
	ts.Start()
	defer ts.Close()

	conn, err := net.Dial("tcp", ts.Listener.Addr().String())
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	fmt.Fprint(conn, "POST /webhooks/linear HTTP/1.1\r\nHost: x\r\nContent-Length: 100\r\n\r\n{")
	_ = conn.SetReadDeadline(time.Now().Add(3 * time.Second))
	start := time.Now()
	_, _ = io.ReadAll(conn)
	if time.Since(start) > 2*time.Second {
		t.Fatal("stalled body was not cut off")
	}
	if len(in.queue) != 0 {
		t.Fatal("stalled request was queued")
	}
}

type errReader struct{}

func (errReader) Read([]byte) (int, error) { return 0, errors.New("boom") }

func TestIngressReadErrorCodes(t *testing.T) {
	in := &ingress{secret: testSecret, dedupe: newDeduper(time.Hour), queue: make(chan event, 1), now: time.Now}
	big := httptest.NewRequest(http.MethodPost, "/webhooks/linear", strings.NewReader(strings.Repeat("a", maxBodyBytes+1)))
	w := httptest.NewRecorder()
	in.ServeHTTP(w, big)
	if w.Code != http.StatusRequestEntityTooLarge {
		t.Errorf("oversize: %d want 413", w.Code)
	}
	bad := httptest.NewRequest(http.MethodPost, "/webhooks/linear", errReader{})
	w = httptest.NewRecorder()
	in.ServeHTTP(w, bad)
	if w.Code != http.StatusBadRequest {
		t.Errorf("read error: %d want 400", w.Code)
	}
}

func TestIngressReplayWithFreshDeliveryIDIsDuplicate(t *testing.T) {
	now := time.UnixMilli(1_700_000_000_000)
	in := &ingress{secret: testSecret, dedupe: newDeduper(7 * time.Hour), queue: make(chan event, 4), now: func() time.Time { return now }}
	body := fmt.Sprintf(`{"type":"t","webhookTimestamp":%d}`, now.UnixMilli())
	for _, d := range []string{"d1", "d2"} {
		hr := httptest.NewRequest(http.MethodPost, "/webhooks/linear", strings.NewReader(body))
		hr.Header.Set("Linear-Signature", sign(testSecret, []byte(body)))
		hr.Header.Set("Linear-Delivery", d)
		w := httptest.NewRecorder()
		in.ServeHTTP(w, hr)
		if w.Code != 200 {
			t.Fatalf("%s: %d", d, w.Code)
		}
	}
	if len(in.queue) != 1 {
		t.Fatalf("queued %d, want 1", len(in.queue))
	}
}
