package main

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"net/url"
	"strconv"
	"strings"
	"sync"
)

const pollMemory = 256

type poll struct {
	question string
	options  []string
}

// pollStore remembers polls created through the shim, because a vote carries only option indexes.
type pollStore struct {
	mu    sync.Mutex
	byTS  map[int64]poll
	order []int64
}

func (ps *pollStore) put(ts int64, p poll) {
	ps.mu.Lock()
	defer ps.mu.Unlock()
	if ps.byTS == nil {
		ps.byTS = map[int64]poll{}
	}
	if _, ok := ps.byTS[ts]; !ok {
		ps.order = append(ps.order, ts)
	}
	ps.byTS[ts] = p
	for len(ps.order) > pollMemory {
		delete(ps.byTS, ps.order[0])
		ps.order = ps.order[1:]
	}
}

func (ps *pollStore) get(ts int64) (poll, bool) {
	ps.mu.Lock()
	defer ps.mu.Unlock()
	p, ok := ps.byTS[ts]
	return p, ok
}

func (s *shim) pollCreate(ctx context.Context, p map[string]any) (any, error) {
	account, err := s.accountFor(ctx, p)
	if err != nil {
		return nil, err
	}
	recipient, err := singleTarget(p)
	if err != nil {
		return nil, err
	}
	question := strings.TrimSpace(str(p["question"]))
	var options []string
	for _, key := range []string{"options", "option"} {
		if list, ok := p[key].([]any); ok {
			for _, o := range list {
				if o := strings.TrimSpace(str(o)); o != "" {
					options = append(options, o)
				}
			}
		}
	}
	if question == "" || len(options) < 2 {
		return nil, fmt.Errorf("a poll needs a question and at least two options")
	}
	multi, _ := p["allowMultiple"].(bool)
	body := map[string]any{"recipient": recipient, "question": question, "answers": options, "allow_multiple_selections": multi}
	raw, err := s.doJSON(ctx, http.MethodPost, "/v1/polls/"+url.PathEscape(account), body)
	if err != nil {
		return nil, err
	}
	var resp struct {
		Timestamp string `json:"timestamp"`
	}
	if err := json.Unmarshal(raw, &resp); err != nil {
		return nil, fmt.Errorf("decode poll response: %w", err)
	}
	ts, err := strconv.ParseInt(resp.Timestamp, 10, 64)
	if err != nil {
		return nil, fmt.Errorf("decode poll timestamp %q: %w", resp.Timestamp, err)
	}
	s.polls.put(ts, poll{question: question, options: options})
	return map[string]any{"timestamp": ts}, nil
}

func (s *shim) pollTerminate(ctx context.Context, p map[string]any) (any, error) {
	account, err := s.accountFor(ctx, p)
	if err != nil {
		return nil, err
	}
	recipient, err := singleTarget(p)
	if err != nil {
		return nil, err
	}
	ts, ok := num(p["pollTimestamp"])
	if !ok {
		if v, err := strconv.ParseInt(str(p["pollTimestamp"]), 10, 64); err == nil {
			ts, ok = v, true
		}
	}
	if !ok {
		return nil, fmt.Errorf("pollTimestamp missing")
	}
	body := map[string]any{"recipient": recipient, "poll_timestamp": strconv.FormatInt(ts, 10)}
	if _, err := s.doJSON(ctx, http.MethodDelete, "/v1/polls/"+url.PathEscape(account), body); err != nil {
		return nil, err
	}
	return map[string]any{}, nil
}

// accountFor falls back to the only registered account, so callers without SIGNAL_ACCOUNT still work.
func (s *shim) accountFor(ctx context.Context, p map[string]any) (string, error) {
	if a := str(p["account"]); a != "" {
		return a, nil
	}
	raw, err := s.do(ctx, http.MethodGet, "/v1/accounts", nil)
	if err != nil {
		return "", err
	}
	var accounts []string
	if err := json.Unmarshal(raw, &accounts); err != nil {
		return "", fmt.Errorf("decode accounts: %w", err)
	}
	if len(accounts) != 1 {
		return "", fmt.Errorf("account missing and %d accounts are registered", len(accounts))
	}
	return accounts[0], nil
}

func singleTarget(p map[string]any) (string, error) {
	t := targets(p)
	if len(t) != 1 {
		return "", fmt.Errorf("a poll needs exactly one recipient or groupId, got %d", len(t))
	}
	return t[0], nil
}

// withPollVoteText gives a poll vote the text message Hermes reads, since the adapter only reads dataMessage.message.
func (s *shim) withPollVoteText(data []byte) []byte {
	var msg map[string]any
	if err := json.Unmarshal(data, &msg); err != nil {
		return data
	}
	env, _ := msg["envelope"].(map[string]any)
	dm, _ := env["dataMessage"].(map[string]any)
	vote, _ := dm["pollVote"].(map[string]any)
	if vote == nil || str(dm["message"]) != "" {
		return data
	}
	ts, _ := num(vote["targetSentTimestamp"])
	var indexes []int64
	if list, ok := vote["optionIndexes"].([]any); ok {
		for _, v := range list {
			if i, ok := num(v); ok {
				indexes = append(indexes, i)
			}
		}
	}
	dm["message"] = voteText(s.polls.get, ts, indexes)
	out, err := json.Marshal(msg)
	if err != nil {
		return data
	}
	return out
}

func voteText(lookup func(int64) (poll, bool), ts int64, indexes []int64) string {
	p, known := lookup(ts)
	ref := fmt.Sprintf("(poll %d)", ts)
	if len(indexes) == 0 {
		if known {
			return fmt.Sprintf("🗳️ Poll %q: vote withdrawn %s", p.question, ref)
		}
		return "🗳️ Poll vote withdrawn " + ref
	}
	var picked []string
	for _, i := range indexes {
		if known && i >= 0 && int(i) < len(p.options) {
			picked = append(picked, p.options[i])
		} else {
			picked = append(picked, fmt.Sprintf("option %d", i+1))
		}
	}
	if known {
		return fmt.Sprintf("🗳️ Poll %q: %s %s", p.question, strings.Join(picked, ", "), ref)
	}
	return fmt.Sprintf("🗳️ Poll vote: %s %s", strings.Join(picked, ", "), ref)
}
