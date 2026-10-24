package main

import (
	"bufio"
	"context"
	"encoding/base64"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/coder/websocket"
)

type recorded struct {
	method, path string
	body         map[string]any
}

type fakeREST struct {
	mu    sync.Mutex
	calls []recorded
	srv   *httptest.Server
	push  chan string
}

func newFakeREST(t *testing.T) *fakeREST {
	f := &fakeREST{push: make(chan string, 4)}
	f.srv = httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if strings.HasPrefix(r.URL.Path, "/v1/receive/") {
			c, err := websocket.Accept(w, r, nil)
			if err != nil {
				return
			}
			defer c.CloseNow()
			for msg := range f.push {
				if err := c.Write(r.Context(), websocket.MessageText, []byte(msg)); err != nil {
					return
				}
			}
			return
		}
		var body map[string]any
		raw, _ := io.ReadAll(r.Body)
		_ = json.Unmarshal(raw, &body)
		f.mu.Lock()
		f.calls = append(f.calls, recorded{r.Method, r.URL.Path, body})
		f.mu.Unlock()
		switch {
		case r.URL.Path == "/v1/about":
			_, _ = w.Write([]byte(`{"mode":"json-rpc"}`))
		case r.URL.Path == "/v2/send":
			w.WriteHeader(http.StatusCreated)
			_, _ = w.Write([]byte(`[{"timestamp":"1761245000123"}]`))
		case strings.HasPrefix(r.URL.Path, "/v1/attachments/"):
			_, _ = w.Write([]byte("ATTACHMENT-BYTES"))
		default:
			w.WriteHeader(http.StatusNoContent)
		}
	}))
	t.Cleanup(func() { close(f.push); f.srv.Close() })
	return f
}

func (f *fakeREST) last() recorded {
	f.mu.Lock()
	defer f.mu.Unlock()
	return f.calls[len(f.calls)-1]
}

func callRPC(t *testing.T, h http.Handler, method string, params map[string]any) map[string]any {
	t.Helper()
	body, _ := json.Marshal(map[string]any{"jsonrpc": "2.0", "method": method, "params": params, "id": "x"})
	rec := httptest.NewRecorder()
	h.ServeHTTP(rec, httptest.NewRequest(http.MethodPost, "/api/v1/rpc", strings.NewReader(string(body))))
	var resp map[string]any
	if err := json.Unmarshal(rec.Body.Bytes(), &resp); err != nil {
		t.Fatalf("decode rpc response: %v (%s)", err, rec.Body.String())
	}
	return resp
}

func TestSendToGroupConvertsIDAndTimestamp(t *testing.T) {
	f := newFakeREST(t)
	h := (&shim{upstream: f.srv.URL, http: f.srv.Client()}).routes()

	resp := callRPC(t, h, "send", map[string]any{"account": "+491", "message": "hi", "groupId": "internal-id"})

	if resp["error"] != nil {
		t.Fatalf("unexpected error: %v", resp["error"])
	}
	ts := resp["result"].(map[string]any)["timestamp"].(float64)
	if int64(ts) != 1761245000123 {
		t.Fatalf("timestamp = %v, want numeric 1761245000123", ts)
	}
	got := f.last()
	want := "group." + base64.StdEncoding.EncodeToString([]byte("internal-id"))
	if got.path != "/v2/send" || got.body["recipients"].([]any)[0] != want || got.body["number"] != "+491" {
		t.Fatalf("send forwarded as %s %v, want recipients [%s]", got.path, got.body, want)
	}
}

func TestSendInlinesLocalAttachments(t *testing.T) {
	f := newFakeREST(t)
	h := (&shim{upstream: f.srv.URL, http: f.srv.Client()}).routes()
	path := filepath.Join(t.TempDir(), "note.txt")
	if err := os.WriteFile(path, []byte("hello"), 0o600); err != nil {
		t.Fatal(err)
	}

	callRPC(t, h, "send", map[string]any{"account": "+491", "message": "", "recipient": []any{"+492"}, "attachments": []any{path}})

	att := f.last().body["base64_attachments"].([]any)[0].(string)
	if !strings.HasPrefix(att, "data:text/plain") || !strings.HasSuffix(att, base64.StdEncoding.EncodeToString([]byte("hello"))) {
		t.Fatalf("attachment = %q", att)
	}
}

func TestTypingStopUsesDelete(t *testing.T) {
	f := newFakeREST(t)
	h := (&shim{upstream: f.srv.URL, http: f.srv.Client()}).routes()

	callRPC(t, h, "sendTyping", map[string]any{"account": "+491", "recipient": []any{"+492"}, "stop": true})

	if got := f.last(); got.method != http.MethodDelete || got.path != "/v1/typing-indicator/+491" || got.body["recipient"] != "+492" {
		t.Fatalf("typing forwarded as %s %s %v", got.method, got.path, got.body)
	}
}

func TestGetAttachmentReturnsBase64Data(t *testing.T) {
	f := newFakeREST(t)
	h := (&shim{upstream: f.srv.URL, http: f.srv.Client()}).routes()

	resp := callRPC(t, h, "getAttachment", map[string]any{"account": "+491", "id": "abc.jpg"})

	data := resp["result"].(map[string]any)["data"].(string)
	if raw, _ := base64.StdEncoding.DecodeString(data); string(raw) != "ATTACHMENT-BYTES" {
		t.Fatalf("data decodes to %q", raw)
	}
}

func TestUnknownMethodIsAnRPCError(t *testing.T) {
	f := newFakeREST(t)
	h := (&shim{upstream: f.srv.URL, http: f.srv.Client()}).routes()

	if resp := callRPC(t, h, "updateProfile", map[string]any{}); resp["error"] == nil {
		t.Fatal("expected an error for an unsupported method")
	}
}

func TestEventsRelaysEnvelopesAsSSE(t *testing.T) {
	f := newFakeREST(t)
	srv := httptest.NewServer((&shim{upstream: f.srv.URL, http: f.srv.Client()}).routes())
	defer srv.Close()

	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	req, _ := http.NewRequestWithContext(ctx, http.MethodGet, srv.URL+"/api/v1/events?account=%2B491", nil)
	res, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	defer res.Body.Close()

	f.push <- `{"envelope": {"source": "+492", "dataMessage": {"message": "ping"}}, "account": "+491"}`

	sc := bufio.NewScanner(res.Body)
	for sc.Scan() {
		line := sc.Text()
		if !strings.HasPrefix(line, "data: ") {
			continue
		}
		var ev map[string]any
		if err := json.Unmarshal([]byte(strings.TrimPrefix(line, "data: ")), &ev); err != nil {
			t.Fatalf("event is not JSON: %q", line)
		}
		if ev["envelope"].(map[string]any)["dataMessage"].(map[string]any)["message"] != "ping" {
			t.Fatalf("unexpected event %v", ev)
		}
		return
	}
	t.Fatal("no data event received")
}
