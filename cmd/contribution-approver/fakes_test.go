package main

import (
	"bytes"
	"encoding/json"
	"io"
	"log"
	"net/http"
	"net/http/httptest"
	"os"
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

type fakeSignal struct {
	mu   sync.Mutex
	sent []map[string]any
	fail bool
	// onSend runs once, outside the lock, after the next accepted message is recorded.
	onSend func(message string)
	srv    *httptest.Server
}

func newFakeSignal(t *testing.T) *fakeSignal {
	s := &fakeSignal{}
	s.srv = httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		s.mu.Lock()
		var body map[string]any
		_ = json.NewDecoder(r.Body).Decode(&body)
		if r.Method != http.MethodPost || r.URL.Path != "/v2/send" {
			t.Errorf("unexpected Signal call %s %s", r.Method, r.URL.Path)
		}
		if s.fail {
			s.mu.Unlock()
			http.Error(w, "down", http.StatusServiceUnavailable)
			return
		}
		s.sent = append(s.sent, body)
		hook := s.onSend
		s.onSend = nil
		s.mu.Unlock()
		if hook != nil {
			msg, _ := body["message"].(string)
			hook(msg)
		}
		w.WriteHeader(http.StatusCreated)
		_, _ = io.WriteString(w, `[{"timestamp":"1"}]`)
	}))
	t.Cleanup(s.srv.Close)
	return s
}

func (s *fakeSignal) whenSent(hook func(message string)) {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.onSend = hook
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

	h := &harness{gh: newFakeGitHub(t), signal: newFakeSignal(t)}
	h.clock = h.gh.clock
	h.logs = func() string { mu.Lock(); defer mu.Unlock(); return buf.String() }
	gh := &githubClient{base: h.gh.srv.URL, token: testPAT, http: h.gh.srv.Client()}
	notify := &signalNotifier{base: h.signal.srv.URL, number: testBot, recipient: testGroup, http: h.signal.srv.Client()}
	h.codes = newCodeStore(codeTTL, h.clock.now)
	forks, err := loadForks([]byte(forksJSON))
	if err != nil {
		t.Fatal(err)
	}
	h.poller = newPoller(gh, forks, h.codes, notify)
	h.api = (&decider{token: testShared, codes: h.codes, gh: gh, forks: forks, notify: notify, kick: h.poller.wake, links: h.poller.links}).routes()
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
