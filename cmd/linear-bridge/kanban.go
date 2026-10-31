package main

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"strconv"
	"strings"
	"time"
)

const cardTimeout = 3 * time.Second

type CardLink struct {
	IssueID     string `json:"issue_id"`
	Identifier  string `json:"identifier"`
	SessionID   string `json:"session_id"`
	RootIssueID string `json:"root_issue_id"`
}

type Card struct {
	ID          string         `json:"id"`
	Status      string         `json:"status"`
	Assignee    string         `json:"assignee"`
	RunID       int64          `json:"run_id"`
	Outcome     string         `json:"outcome"`
	BlockKind   string         `json:"block_kind"`
	BlockReason string         `json:"block_reason"`
	Summary     string         `json:"summary"`
	Link        *CardLink      `json:"link"`
	Metadata    map[string]any `json:"metadata"`
}

type CardEvent struct {
	ID        int64  `json:"id"`
	CardID    string `json:"card_id"`
	Kind      string `json:"kind"`
	CreatedAt int64  `json:"created_at"`
}

type RootCardRequest struct {
	IssueID     string `json:"issue_id"`
	Identifier  string `json:"identifier"`
	Title       string `json:"title"`
	Description string `json:"description"`
	SessionID   string `json:"session_id"`
}

type kanbanAPI interface {
	CreateRootCard(ctx context.Context, r RootCardRequest) (Card, error)
	CardByIssue(ctx context.Context, issueID string) (Card, bool, error)
	Card(ctx context.Context, id string) (Card, error)
	StopCard(ctx context.Context, id, reason string) error
	Events(ctx context.Context, after int64, limit int) ([]CardEvent, error)
	Cursor(ctx context.Context, name string) (int64, error)
	SetCursor(ctx context.Context, name string, v int64) error
}

type gateClient struct {
	base, token string
	http        *http.Client
}

func (g *gateClient) do(ctx context.Context, method, path string, in, out any) (int, error) {
	var body io.Reader
	if in != nil {
		raw, err := json.Marshal(in)
		if err != nil {
			return 0, err
		}
		body = bytes.NewReader(raw)
	}
	req, err := http.NewRequestWithContext(ctx, method, strings.TrimRight(g.base, "/")+path, body)
	if err != nil {
		return 0, err
	}
	req.Header.Set("Authorization", "Bearer "+g.token)
	if in != nil {
		req.Header.Set("Content-Type", "application/json")
	}
	resp, err := g.http.Do(req)
	if err != nil {
		return 0, err
	}
	defer func() { _ = resp.Body.Close() }()
	raw, err := io.ReadAll(io.LimitReader(resp.Body, maxRespBytes))
	if err != nil {
		return resp.StatusCode, err
	}
	if resp.StatusCode < 200 || resp.StatusCode > 299 {
		return resp.StatusCode, fmt.Errorf("kanban gate %s %s returned %d", method, path, resp.StatusCode)
	}
	if out != nil {
		if err := json.Unmarshal(raw, out); err != nil {
			return resp.StatusCode, fmt.Errorf("kanban gate %s %s: %w", method, path, err)
		}
	}
	return resp.StatusCode, nil
}

func (g *gateClient) CreateRootCard(ctx context.Context, r RootCardRequest) (Card, error) {
	var c Card
	_, err := g.do(ctx, http.MethodPost, "/v1/cards", r, &c)
	return c, err
}

func (g *gateClient) CardByIssue(ctx context.Context, issueID string) (Card, bool, error) {
	var c Card
	status, err := g.do(ctx, http.MethodGet, "/v1/cards?linear_issue="+url.QueryEscape(issueID), nil, &c)
	if status == http.StatusNotFound {
		return Card{}, false, nil
	}
	return c, err == nil, err
}

func (g *gateClient) Card(ctx context.Context, id string) (Card, error) {
	var c Card
	_, err := g.do(ctx, http.MethodGet, "/v1/cards/"+url.PathEscape(id), nil, &c)
	return c, err
}

func (g *gateClient) StopCard(ctx context.Context, id, reason string) error {
	var out struct {
		InterruptErrors []string `json:"interrupt_errors"`
		ArchiveErrors   []string `json:"archive_errors"`
	}
	_, err := g.do(ctx, http.MethodPost, "/v1/cards/"+url.PathEscape(id)+"/stop", map[string]string{"reason": reason}, &out)
	if err != nil {
		return err
	}
	if n := len(out.InterruptErrors) + len(out.ArchiveErrors); n > 0 {
		return fmt.Errorf("stop card %s: %d interrupt/archive errors", id, n)
	}
	return nil
}

func (g *gateClient) Events(ctx context.Context, after int64, limit int) ([]CardEvent, error) {
	var out struct {
		Events []CardEvent `json:"events"`
	}
	path := "/v1/events?after=" + strconv.FormatInt(after, 10) + "&limit=" + strconv.Itoa(limit)
	if _, err := g.do(ctx, http.MethodGet, path, nil, &out); err != nil {
		return nil, err
	}
	return out.Events, nil
}

func (g *gateClient) Cursor(ctx context.Context, name string) (int64, error) {
	var out struct {
		Value int64 `json:"value"`
	}
	_, err := g.do(ctx, http.MethodGet, "/v1/cursors/"+url.PathEscape(name), nil, &out)
	return out.Value, err
}

func (g *gateClient) SetCursor(ctx context.Context, name string, v int64) error {
	_, err := g.do(ctx, http.MethodPut, "/v1/cursors/"+url.PathEscape(name), map[string]int64{"value": v}, nil)
	return err
}
