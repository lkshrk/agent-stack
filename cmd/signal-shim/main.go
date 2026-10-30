// Command signal-shim serves the subset of signal-cli's HTTP daemon API that the
// Hermes Signal adapter uses and translates it to signal-cli-rest-api in json-rpc mode.
package main

import (
	"bytes"
	"context"
	"encoding/base64"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log"
	"mime"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"time"

	"github.com/coder/websocket"
)

func main() {
	upstream := strings.TrimRight(os.Getenv("SIGNAL_REST_URL"), "/")
	if upstream == "" {
		log.Fatal("signal-shim: SIGNAL_REST_URL is not set")
	}
	listen := os.Getenv("LISTEN_ADDR")
	if listen == "" {
		listen = "127.0.0.1:8090"
	}
	s := &shim{upstream: upstream, http: &http.Client{Timeout: 60 * time.Second}}
	srv := &http.Server{Addr: listen, Handler: s.routes(), ReadHeaderTimeout: 10 * time.Second}
	log.Printf("signal-shim: listening on %s, forwarding to %s", listen, upstream)
	log.Fatal(srv.ListenAndServe())
}

type shim struct {
	upstream string
	http     *http.Client
}

func (s *shim) routes() http.Handler {
	mux := http.NewServeMux()
	mux.HandleFunc("GET /api/v1/check", s.check)
	mux.HandleFunc("GET /api/v1/events", s.events)
	mux.HandleFunc("POST /api/v1/rpc", s.rpc)
	return mux
}

func (s *shim) check(w http.ResponseWriter, r *http.Request) {
	res, err := s.do(r.Context(), http.MethodGet, "/v1/about", nil)
	if err != nil {
		http.Error(w, err.Error(), http.StatusBadGateway)
		return
	}
	w.Header().Set("Content-Type", "application/json")
	_, _ = w.Write([]byte(`{"versions":{"signal-cli":"via signal-cli-rest-api"},"upstream":`))
	_, _ = w.Write(res)
	_, _ = w.Write([]byte(`}`))
}

// events relays the json-rpc receive websocket as the SSE stream signal-cli's daemon serves.
func (s *shim) events(w http.ResponseWriter, r *http.Request) {
	account := r.URL.Query().Get("account")
	if account == "" {
		http.Error(w, "account query parameter required", http.StatusBadRequest)
		return
	}
	flusher, ok := w.(http.Flusher)
	if !ok {
		http.Error(w, "streaming unsupported", http.StatusInternalServerError)
		return
	}

	wsURL := strings.Replace(s.upstream, "http", "ws", 1) + "/v1/receive/" + url.PathEscape(account)
	conn, _, err := websocket.Dial(r.Context(), wsURL, nil)
	if err != nil {
		http.Error(w, "upstream receive: "+err.Error(), http.StatusBadGateway)
		return
	}
	defer func() { _ = conn.CloseNow() }()
	conn.SetReadLimit(64 << 20)

	w.Header().Set("Content-Type", "text/event-stream")
	w.Header().Set("Cache-Control", "no-cache")
	w.WriteHeader(http.StatusOK)
	_, _ = io.WriteString(w, ": connected\n\n")
	flusher.Flush()

	msgs := make(chan []byte)
	errs := make(chan error, 1)
	go func() {
		for {
			_, data, err := conn.Read(r.Context())
			if err != nil {
				errs <- err
				return
			}
			msgs <- data
		}
	}()

	keepalive := time.NewTicker(25 * time.Second)
	defer keepalive.Stop()
	for {
		select {
		case <-r.Context().Done():
			return
		case err := <-errs:
			log.Printf("signal-shim: receive stream ended: %v", err)
			return
		case <-keepalive.C:
			_, _ = io.WriteString(w, ": keepalive\n\n")
			flusher.Flush()
		case data := <-msgs:
			// signal-cli-rest-api relays the daemon's notification params ({"envelope":…,"account":…}) unchanged.
			_, _ = io.WriteString(w, "data: ")
			_, _ = w.Write(compact(data))
			_, _ = io.WriteString(w, "\n\n")
			flusher.Flush()
		}
	}
}

type rpcRequest struct {
	JSONRPC string          `json:"jsonrpc"`
	Method  string          `json:"method"`
	Params  json.RawMessage `json:"params"`
	ID      any             `json:"id"`
}

type rpcError struct {
	Code    int    `json:"code"`
	Message string `json:"message"`
}

func (s *shim) rpc(w http.ResponseWriter, r *http.Request) {
	var req rpcRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		writeRPC(w, nil, nil, &rpcError{Code: -32700, Message: "parse error"})
		return
	}
	var params map[string]any
	if len(req.Params) > 0 {
		if err := json.Unmarshal(req.Params, &params); err != nil {
			writeRPC(w, req.ID, nil, &rpcError{Code: -32602, Message: "invalid params"})
			return
		}
	}

	var (
		result any
		err    error
	)
	switch req.Method {
	case "send":
		result, err = s.send(r.Context(), params)
	case "sendTyping":
		result, err = s.typing(r.Context(), params)
	case "sendReaction":
		result, err = s.reaction(r.Context(), params)
	case "getAttachment":
		result, err = s.attachment(r.Context(), params)
	case "getContact":
		result = map[string]any{}
	case "listContacts":
		result = []any{}
	default:
		writeRPC(w, req.ID, nil, &rpcError{Code: -32601, Message: "method not supported by signal-shim: " + req.Method})
		return
	}
	if err != nil {
		code := -32603
		var rl *rateLimitError
		if errors.As(err, &rl) {
			code = 429
		}
		writeRPC(w, req.ID, nil, &rpcError{Code: code, Message: err.Error()})
		return
	}
	writeRPC(w, req.ID, result, nil)
}

func writeRPC(w http.ResponseWriter, id, result any, rerr *rpcError) {
	w.Header().Set("Content-Type", "application/json")
	resp := map[string]any{"jsonrpc": "2.0", "id": id}
	if rerr != nil {
		resp["error"] = rerr
	} else {
		resp["result"] = result
	}
	_ = json.NewEncoder(w).Encode(resp)
}

func (s *shim) send(ctx context.Context, p map[string]any) (any, error) {
	account := str(p["account"])
	body := map[string]any{
		"number":     account,
		"message":    str(p["message"]),
		"recipients": targets(p),
	}
	if paths, ok := p["attachments"].([]any); ok && len(paths) > 0 {
		encoded := make([]string, 0, len(paths))
		for _, raw := range paths {
			a, err := encodeAttachment(str(raw))
			if err != nil {
				return nil, err
			}
			encoded = append(encoded, a)
		}
		body["base64_attachments"] = encoded
	}
	if msg, ok := styledMessage(str(p["message"]), textStyles(p)); ok {
		body["message"] = msg
		body["text_mode"] = "styled"
	}
	if ts, ok := num(p["quoteTimestamp"]); ok {
		body["quote_timestamp"] = ts
		if a := str(p["quoteAuthor"]); a != "" {
			body["quote_author"] = a
		}
	}

	raw, err := s.doJSON(ctx, http.MethodPost, "/v2/send", body)
	if err != nil {
		return nil, err
	}
	// signal-cli-rest-api answers /v2/send with one entry per request it split the send into.
	var resp []struct {
		Timestamp string `json:"timestamp"`
	}
	if err := json.Unmarshal(raw, &resp); err != nil {
		return nil, fmt.Errorf("decode send response: %w", err)
	}
	if len(resp) == 0 {
		return nil, fmt.Errorf("decode send response: empty")
	}
	ts, _ := strconv.ParseInt(resp[0].Timestamp, 10, 64)
	return map[string]any{"timestamp": ts, "results": []any{map[string]any{"type": "SUCCESS"}}}, nil
}

func (s *shim) typing(ctx context.Context, p map[string]any) (any, error) {
	account := str(p["account"])
	method := http.MethodPut
	if b, _ := p["stop"].(bool); b {
		method = http.MethodDelete
	}
	for _, t := range targets(p) {
		if _, err := s.doJSON(ctx, method, "/v1/typing-indicator/"+url.PathEscape(account), map[string]any{"recipient": t}); err != nil {
			return nil, err
		}
	}
	return map[string]any{}, nil
}

func (s *shim) reaction(ctx context.Context, p map[string]any) (any, error) {
	account := str(p["account"])
	ts, _ := num(p["targetTimestamp"])
	method := http.MethodPost
	if b, _ := p["remove"].(bool); b {
		method = http.MethodDelete
	}
	for _, t := range targets(p) {
		body := map[string]any{"recipient": t, "reaction": str(p["emoji"]), "target_author": str(p["targetAuthor"]), "timestamp": ts}
		if _, err := s.doJSON(ctx, method, "/v1/reactions/"+url.PathEscape(account), body); err != nil {
			return nil, err
		}
	}
	return map[string]any{}, nil
}

func (s *shim) attachment(ctx context.Context, p map[string]any) (any, error) {
	id := str(p["id"])
	if id == "" {
		return nil, fmt.Errorf("attachment id missing")
	}
	data, err := s.do(ctx, http.MethodGet, "/v1/attachments/"+url.PathEscape(id), nil)
	if err != nil {
		return nil, err
	}
	return map[string]any{"data": base64.StdEncoding.EncodeToString(data)}, nil
}

// targets maps signal-cli's groupId (internal id) / recipient list to REST recipients.
func targets(p map[string]any) []string {
	if g := str(p["groupId"]); g != "" {
		return []string{"group." + base64.StdEncoding.EncodeToString([]byte(g))}
	}
	var out []string
	switch v := p["recipient"].(type) {
	case []any:
		for _, r := range v {
			if s := str(r); s != "" {
				out = append(out, s)
			}
		}
	case string:
		if v != "" {
			out = append(out, v)
		}
	}
	return out
}

// textStyles collects signal-cli's textStyle (one range or a list) and textStyles params.
func textStyles(p map[string]any) []string {
	var out []string
	for _, key := range []string{"textStyle", "textStyles"} {
		switch v := p[key].(type) {
		case string:
			if v != "" {
				out = append(out, v)
			}
		case []any:
			for _, s := range v {
				if s := str(s); s != "" {
					out = append(out, s)
				}
			}
		}
	}
	return out
}

func encodeAttachment(path string) (string, error) {
	data, err := os.ReadFile(path)
	if err != nil {
		return "", fmt.Errorf("read attachment %s: %w", filepath.Base(path), err)
	}
	ct := mime.TypeByExtension(filepath.Ext(path))
	if ct == "" {
		ct = http.DetectContentType(data)
	}
	if i := strings.IndexByte(ct, ';'); i >= 0 {
		ct = ct[:i]
	}
	return fmt.Sprintf("data:%s;filename=%s;base64,%s", ct, filepath.Base(path), base64.StdEncoding.EncodeToString(data)), nil
}

type rateLimitError struct{ msg string }

func (e *rateLimitError) Error() string { return e.msg }

func (s *shim) doJSON(ctx context.Context, method, path string, body any) ([]byte, error) {
	raw, err := json.Marshal(body)
	if err != nil {
		return nil, err
	}
	return s.do(ctx, method, path, raw)
}

func (s *shim) do(ctx context.Context, method, path string, body []byte) ([]byte, error) {
	var rd io.Reader
	if body != nil {
		rd = bytes.NewReader(body)
	}
	req, err := http.NewRequestWithContext(ctx, method, s.upstream+path, rd)
	if err != nil {
		return nil, err
	}
	if body != nil {
		req.Header.Set("Content-Type", "application/json")
	}
	res, err := s.http.Do(req)
	if err != nil {
		return nil, fmt.Errorf("%s %s: %w", method, path, err)
	}
	defer func() { _ = res.Body.Close() }()
	out, _ := io.ReadAll(io.LimitReader(res.Body, 128<<20))
	switch {
	case res.StatusCode == http.StatusTooManyRequests || strings.Contains(string(out), "RateLimit"):
		return nil, &rateLimitError{msg: fmt.Sprintf("rate limited: %.200s", out)}
	case res.StatusCode >= 300:
		return nil, fmt.Errorf("%s %s returned HTTP %d: %.300s", method, path, res.StatusCode, out)
	}
	return out, nil
}

func compact(data []byte) []byte {
	var buf bytes.Buffer
	if err := json.Compact(&buf, data); err != nil {
		return data
	}
	return buf.Bytes()
}

func str(v any) string {
	s, _ := v.(string)
	return s
}

func num(v any) (int64, bool) {
	switch n := v.(type) {
	case float64:
		return int64(n), true
	case int64:
		return n, true
	case json.Number:
		i, err := n.Int64()
		return i, err == nil
	}
	return 0, false
}
