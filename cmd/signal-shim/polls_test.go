package main

import (
	"bufio"
	"context"
	"encoding/base64"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"
)

func TestPollCreateForwardsAndRemembersThePoll(t *testing.T) {
	f := newFakeREST(t)
	s := &shim{upstream: f.srv.URL, http: f.srv.Client()}

	resp := callRPC(t, s.routes(), "sendPollCreate", map[string]any{"account": "+491", "groupId": "internal-id", "question": "Approve plan: polls?", "options": []any{"✅ Approve", "✏️ Changes", "❌ Reject"}})

	if resp["error"] != nil {
		t.Fatalf("unexpected error: %v", resp["error"])
	}
	if ts := resp["result"].(map[string]any)["timestamp"].(float64); int64(ts) != 1769271479 {
		t.Fatalf("timestamp = %v", ts)
	}
	got := f.last()
	wantGroup := "group." + base64.StdEncoding.EncodeToString([]byte("internal-id"))
	if got.method != http.MethodPost || got.path != "/v1/polls/+491" || got.body["recipient"] != wantGroup || got.body["question"] != "Approve plan: polls?" || len(got.body["answers"].([]any)) != 3 || got.body["allow_multiple_selections"] != false {
		t.Fatalf("poll forwarded as %s %s %v", got.method, got.path, got.body)
	}
	if p, ok := s.polls.get(1769271479); !ok || p.options[2] != "❌ Reject" {
		t.Fatalf("poll not remembered: %v %v", p, ok)
	}
}

func TestPollCreateFallsBackToTheOnlyAccount(t *testing.T) {
	f := newFakeREST(t)
	h := (&shim{upstream: f.srv.URL, http: f.srv.Client()}).routes()

	resp := callRPC(t, h, "sendPollCreate", map[string]any{"recipient": []any{"+492"}, "question": "Start now?", "options": []any{"Yes", "No"}})

	if resp["error"] != nil {
		t.Fatalf("unexpected error: %v", resp["error"])
	}
	if got := f.last(); got.path != "/v1/polls/+491" || got.body["recipient"] != "+492" {
		t.Fatalf("poll forwarded as %s %v", got.path, got.body)
	}
}

func TestPollCreateRejectsIncompletePolls(t *testing.T) {
	f := newFakeREST(t)
	h := (&shim{upstream: f.srv.URL, http: f.srv.Client()}).routes()

	for _, p := range []map[string]any{
		{"account": "+491", "groupId": "g", "question": "Only one?", "options": []any{"Yes"}},
		{"account": "+491", "groupId": "g", "question": " ", "options": []any{"Yes", "No"}},
		{"account": "+491", "question": "Nobody?", "options": []any{"Yes", "No"}},
	} {
		if resp := callRPC(t, h, "sendPollCreate", p); resp["error"] == nil {
			t.Fatalf("params %v accepted", p)
		}
	}
}

func TestPollTerminateForwardsTheTimestamp(t *testing.T) {
	f := newFakeREST(t)
	h := (&shim{upstream: f.srv.URL, http: f.srv.Client()}).routes()

	resp := callRPC(t, h, "sendPollTerminate", map[string]any{"account": "+491", "groupId": "g", "pollTimestamp": 1769271479})

	if resp["error"] != nil {
		t.Fatalf("unexpected error: %v", resp["error"])
	}
	if got := f.last(); got.method != http.MethodDelete || got.path != "/v1/polls/+491" || got.body["poll_timestamp"] != "1769271479" {
		t.Fatalf("close forwarded as %s %s %v", got.method, got.path, got.body)
	}
}

func TestVoteText(t *testing.T) {
	known := func(ts int64) (poll, bool) {
		return poll{question: "Approve plan: polls?", options: []string{"✅ Approve", "✏️ Changes", "❌ Reject"}}, ts == 7
	}
	cases := []struct {
		ts      int64
		indexes []int64
		want    string
	}{
		{7, []int64{0}, `🗳️ Poll "Approve plan: polls?": ✅ Approve (poll 7)`},
		{7, []int64{1, 2}, `🗳️ Poll "Approve plan: polls?": ✏️ Changes, ❌ Reject (poll 7)`},
		{7, []int64{9}, `🗳️ Poll "Approve plan: polls?": option 10 (poll 7)`},
		{7, nil, `🗳️ Poll "Approve plan: polls?": vote withdrawn (poll 7)`},
		{8, []int64{1}, `🗳️ Poll vote: option 2 (poll 8)`},
		{8, nil, `🗳️ Poll vote withdrawn (poll 8)`},
	}
	for _, c := range cases {
		if got := voteText(known, c.ts, c.indexes); got != c.want {
			t.Errorf("voteText(%d, %v) = %q, want %q", c.ts, c.indexes, got, c.want)
		}
	}
}

func TestEventsGiveAPollVoteItsText(t *testing.T) {
	f := newFakeREST(t)
	s := &shim{upstream: f.srv.URL, http: f.srv.Client()}
	s.polls.put(1769271479, poll{question: "Start now?", options: []string{"✅ Start", "⏸️ Later", "❌ Cancel"}})
	srv := httptest.NewServer(s.routes())
	defer srv.Close()

	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	req, _ := http.NewRequestWithContext(ctx, http.MethodGet, srv.URL+"/api/v1/events?account=%2B491", nil)
	res, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	defer func() { _ = res.Body.Close() }()

	f.push <- `{"envelope": {"source": "+492", "dataMessage": {"timestamp": 1769271500, "groupInfo": {"groupId": "g"}, "pollVote": {"authorNumber": "+491", "targetSentTimestamp": 1769271479, "optionIndexes": [1], "voteCount": 1}}}, "account": "+491"}`

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
		dm := ev["envelope"].(map[string]any)["dataMessage"].(map[string]any)
		if dm["message"] != `🗳️ Poll "Start now?": ⏸️ Later (poll 1769271479)` || dm["groupInfo"] == nil || dm["pollVote"] == nil {
			t.Fatalf("vote relayed as %v", dm)
		}
		return
	}
	if err := sc.Err(); err != nil {
		t.Fatal(err)
	}
	t.Fatal("no data event received")
}

func TestEventsLeaveOtherMessagesUnchanged(t *testing.T) {
	s := &shim{}
	for _, in := range []string{
		`{"envelope":{"source":"+492","dataMessage":{"message":"ping"}},"account":"+491"}`,
		`{"envelope":{"source":"+492","typingMessage":{"action":"STARTED"}},"account":"+491"}`,
		`not json`,
	} {
		if out := string(s.withPollVoteText([]byte(in))); out != in {
			t.Errorf("withPollVoteText(%s) = %s", in, out)
		}
	}
}
