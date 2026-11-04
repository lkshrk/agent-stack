package main

import (
	"bufio"
	"bytes"
	"context"
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
	testOwner    = "+4915550001"
	testAccount  = "+491"
	testToken    = "fake-shared-token-padded-to-32-chars"
	testGroupRaw = "R3JvdXBJbnRlcm5hbElkMDEyMzQ1Njc4OWFiY2RlZg=="
)

func envelope(source string, dm map[string]any, extra map[string]any) string {
	env := map[string]any{"source": source, "sourceNumber": source, "sourceUuid": "uuid-1", "sourceDevice": 1, "timestamp": 1761245000123}
	if dm != nil {
		env["dataMessage"] = dm
	}
	for k, v := range extra {
		env[k] = v
	}
	raw, _ := json.Marshal(map[string]any{"envelope": env, "account": testAccount})
	return string(raw)
}

func groupText(text string) map[string]any {
	return map[string]any{"timestamp": 1761245000123, "message": text, "expiresInSeconds": 0, "viewOnce": false, "groupInfo": map[string]any{"groupId": testGroupRaw, "type": "DELIVER"}}
}

func with(dm map[string]any, key string, v any) map[string]any {
	dm[key] = v
	return dm
}

func TestApprovalMatchAcceptsOnlyTheOwnersExactDecision(t *testing.T) {
	route := &approvalRoute{owner: testOwner}
	noSourceNumber, _ := json.Marshal(map[string]any{"account": testAccount, "envelope": map[string]any{"source": testOwner, "dataMessage": groupText("approve 12345678")}})
	cases := []struct {
		name, data string
		want       *decision
	}{
		{"owner approve", envelope(testOwner, groupText("approve 12345678"), nil), &decision{Action: "approve", Code: "12345678"}},
		{"owner reject", envelope(testOwner, groupText("reject 87654321"), nil), &decision{Action: "reject", Code: "87654321"}},
		{"owner direct message", envelope(testOwner, map[string]any{"timestamp": 1, "message": "approve 12345678"}, nil), &decision{Action: "approve", Code: "12345678"}},
		{"source without sourceNumber", string(noSourceNumber), &decision{Action: "approve", Code: "12345678"}},
		{"other sender", envelope("+4915559999", groupText("approve 12345678"), nil), nil},
		{"bot echo", envelope(testAccount, groupText("approve 12345678"), nil), nil},
		{"sync message", envelope(testOwner, nil, map[string]any{"syncMessage": map[string]any{"sentMessage": groupText("approve 12345678")}}), nil},
		{"sync next to data", envelope(testOwner, groupText("approve 12345678"), map[string]any{"syncMessage": map[string]any{"readMessages": []any{1}}}), nil},
		{"edit", envelope(testOwner, nil, map[string]any{"editMessage": map[string]any{"targetSentTimestamp": 1, "dataMessage": groupText("approve 12345678")}}), nil},
		{"quote reply", envelope(testOwner, with(groupText("approve 12345678"), "quote", map[string]any{"id": 1, "author": testAccount, "text": "x"}), nil), nil},
		{"reaction", envelope(testOwner, with(groupText(""), "reaction", map[string]any{"emoji": "👍", "targetSentTimestamp": 1}), nil), nil},
		{"attachment", envelope(testOwner, with(groupText("approve 12345678"), "attachments", []any{map[string]any{"id": "a"}}), nil), nil},
		{"mention", envelope(testOwner, with(groupText("approve 12345678"), "mentions", []any{map[string]any{"start": 0}}), nil), nil},
		{"view once", envelope(testOwner, with(groupText("approve 12345678"), "viewOnce", true), nil), nil},
		{"styled", envelope(testOwner, with(groupText("approve 12345678"), "textStyles", []any{map[string]any{"style": "BOLD"}}), nil), nil},
		{"empty fields are fine", envelope(testOwner, with(with(groupText("approve 12345678"), "mentions", []any{}), "quote", nil), nil), &decision{Action: "approve", Code: "12345678"}},
		{"capitalised", envelope(testOwner, groupText("Approve 12345678"), nil), nil},
		{"trailing space", envelope(testOwner, groupText("approve 12345678 "), nil), nil},
		{"trailing newline", envelope(testOwner, groupText("approve 12345678\n"), nil), nil},
		{"double space", envelope(testOwner, groupText("approve  12345678"), nil), nil},
		{"short code", envelope(testOwner, groupText("approve 1234567"), nil), nil},
		{"long code", envelope(testOwner, groupText("approve 123456789"), nil), nil},
		{"prefixed", envelope(testOwner, groupText("please approve 12345678"), nil), nil},
		{"other verb", envelope(testOwner, groupText("merge 12345678"), nil), nil},
		{"receipt", envelope(testOwner, nil, map[string]any{"receiptMessage": map[string]any{"isRead": true}}), nil},
		{"not json", "not json", nil},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			got, ok := route.match([]byte(tc.data))
			if tc.want == nil {
				if ok {
					t.Fatalf("matched %+v, want no match", got)
				}
				return
			}
			if !ok || got != *tc.want {
				t.Fatalf("match = %+v, %v; want %+v", got, ok, *tc.want)
			}
		})
	}
}

func TestApprovalRouteFromEnv(t *testing.T) {
	env := func(m map[string]string) func(string) string { return func(k string) string { return m[k] } }
	if r, err := approvalRouteFromEnv(env(nil)); r != nil || err != nil {
		t.Fatalf("unset = %v, %v; want disabled", r, err)
	}
	good := map[string]string{"APPROVER_URL": "http://approver:8080", "APPROVER_TOKEN": testToken, "OWNER_NUMBER": testOwner}
	r, err := approvalRouteFromEnv(env(good))
	if err != nil || r == nil || r.owner != testOwner || r.url != "http://approver:8080" || r.token != testToken {
		t.Fatalf("configured = %+v, %v", r, err)
	}
	for _, bad := range []map[string]string{
		{"APPROVER_URL": "http://approver:8080"},
		{"APPROVER_URL": "http://approver:8080", "APPROVER_TOKEN": testToken},
		{"APPROVER_URL": "http://approver:8080", "APPROVER_TOKEN": "short", "OWNER_NUMBER": testOwner},
		{"APPROVER_URL": "http://approver:8080", "APPROVER_TOKEN": testToken, "OWNER_NUMBER": "+491,+492"},
		{"APPROVER_URL": "http://approver:8080", "APPROVER_TOKEN": testToken, "OWNER_NUMBER": "4915550001"},
		{"APPROVER_TOKEN": testToken, "OWNER_NUMBER": testOwner},
	} {
		if r, err := approvalRouteFromEnv(env(bad)); err == nil {
			t.Fatalf("%v accepted as %+v", bad, r)
		}
	}
}

type fakeApprover struct {
	mu    sync.Mutex
	calls []map[string]any
	auth  []string
	got   chan struct{}
	srv   *httptest.Server
}

func newFakeApprover(t *testing.T) *fakeApprover {
	a := &fakeApprover{got: make(chan struct{}, 8)}
	a.srv = httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		var body map[string]any
		raw, _ := io.ReadAll(r.Body)
		_ = json.Unmarshal(raw, &body)
		a.mu.Lock()
		a.calls = append(a.calls, map[string]any{"method": r.Method, "path": r.URL.Path, "body": body})
		a.auth = append(a.auth, r.Header.Get("Authorization"))
		a.mu.Unlock()
		_, _ = w.Write([]byte(`{"result":"approved"}`))
		a.got <- struct{}{}
	}))
	t.Cleanup(a.srv.Close)
	return a
}

func captureLog(t *testing.T) func() string {
	var buf bytes.Buffer
	var mu sync.Mutex
	log.SetOutput(writerFunc(func(p []byte) (int, error) { mu.Lock(); defer mu.Unlock(); return buf.Write(p) }))
	t.Cleanup(func() { log.SetOutput(os.Stderr) })
	return func() string { mu.Lock(); defer mu.Unlock(); return buf.String() }
}

func waitForLog(t *testing.T, logs func() string, want string) string {
	t.Helper()
	deadline := time.Now().Add(5 * time.Second)
	for !strings.Contains(logs(), want) {
		if time.Now().After(deadline) {
			t.Fatalf("log never contained %q: %q", want, logs())
		}
		time.Sleep(10 * time.Millisecond)
	}
	return logs()
}

type writerFunc func([]byte) (int, error)

func (f writerFunc) Write(p []byte) (int, error) { return f(p) }

func openEvents(t *testing.T, s *shim) (*bufio.Scanner, func()) {
	t.Helper()
	srv := httptest.NewServer(s.routes())
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	req, _ := http.NewRequestWithContext(ctx, http.MethodGet, srv.URL+"/api/v1/events?account=%2B491", nil)
	res, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	return bufio.NewScanner(res.Body), func() { cancel(); _ = res.Body.Close(); srv.Close() }
}

func nextMessage(t *testing.T, sc *bufio.Scanner) string {
	t.Helper()
	for sc.Scan() {
		line := sc.Text()
		if !strings.HasPrefix(line, "data: ") {
			continue
		}
		var ev map[string]any
		if err := json.Unmarshal([]byte(strings.TrimPrefix(line, "data: ")), &ev); err != nil {
			t.Fatalf("event is not JSON: %q", line)
		}
		env, _ := ev["envelope"].(map[string]any)
		dm, _ := env["dataMessage"].(map[string]any)
		return str(dm["message"])
	}
	t.Fatal("event stream ended")
	return ""
}

func TestEventsRouteOwnerDecisionToApproverNotHermes(t *testing.T) {
	logs := captureLog(t)
	f := newFakeREST(t)
	a := newFakeApprover(t)
	s := &shim{upstream: f.srv.URL, http: f.srv.Client(), approvals: &approvalRoute{owner: testOwner, url: a.srv.URL, token: testToken, http: a.srv.Client()}}
	sc, done := openEvents(t, s)
	defer done()

	f.push <- envelope(testOwner, groupText("approve 12345678"), nil)
	f.push <- envelope(testOwner, with(groupText("reject 12345678"), "quote", map[string]any{"id": 1, "author": testAccount}), nil)
	f.push <- envelope("+4915559999", groupText("approve 12345678"), nil)
	f.push <- envelope(testOwner, groupText("hello hermes"), nil)

	for _, want := range []string{"reject 12345678", "approve 12345678", "hello hermes"} {
		if got := nextMessage(t, sc); got != want {
			t.Fatalf("Hermes received %q, want %q", got, want)
		}
	}
	select {
	case <-a.got:
	case <-time.After(5 * time.Second):
		t.Fatal("approver was not called")
	}
	a.mu.Lock()
	defer a.mu.Unlock()
	if len(a.calls) != 1 {
		t.Fatalf("approver calls = %v, want exactly one", a.calls)
	}
	call := a.calls[0]
	body := call["body"].(map[string]any)
	if call["method"] != http.MethodPost || call["path"] != "/v1/decisions" || body["action"] != "approve" || body["code"] != "12345678" || len(body) != 2 {
		t.Fatalf("approver call = %v", call)
	}
	if a.auth[0] != "Bearer "+testToken {
		t.Fatal("approver call lacks the shared token")
	}
	out := waitForLog(t, logs, "decision forwarded")
	for _, secret := range []string{testOwner, testToken, testGroupRaw} {
		if strings.Contains(out, secret) {
			t.Fatalf("log contains a secret value: %q", out)
		}
	}
}

func TestEventsWithoutApprovalsRelayDecisionsUnchanged(t *testing.T) {
	f := newFakeREST(t)
	sc, done := openEvents(t, &shim{upstream: f.srv.URL, http: f.srv.Client()})
	defer done()

	f.push <- envelope(testOwner, groupText("approve 12345678"), nil)

	if got := nextMessage(t, sc); got != "approve 12345678" {
		t.Fatalf("Hermes received %q", got)
	}
}

func TestForwardFailureIsLoggedWithoutSecrets(t *testing.T) {
	logs := captureLog(t)
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		http.Error(w, "unknown code", http.StatusNotFound)
	}))
	defer srv.Close()
	route := &approvalRoute{owner: testOwner, url: srv.URL, token: testToken, http: srv.Client()}

	route.forward(decision{Action: "approve", Code: "12345678"})

	out := logs()
	if !strings.Contains(out, "404") {
		t.Fatalf("log lacks the approver status: %q", out)
	}
	for _, secret := range []string{testOwner, testToken} {
		if strings.Contains(out, secret) {
			t.Fatalf("log contains a secret value: %q", out)
		}
	}
}
