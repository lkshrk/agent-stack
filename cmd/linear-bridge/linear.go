package main

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"strings"
	"sync"
	"time"
)

const (
	callTimeout   = 5 * time.Second
	tokenSkew     = time.Hour
	maxRespBytes  = 1 << 20
	defaultExpiry = 24 * time.Hour
)

type linearAPI interface {
	ArchiveIssue(ctx context.Context, issueID string) error
	Thought(ctx context.Context, sessionID, body string) error
}

type tokenSource struct {
	tokenURL, clientID, clientSecret, scopes string
	http                                     *http.Client
	mu                                       sync.Mutex
	token                                    string
	expiry                                   time.Time
}

func (t *tokenSource) Token(ctx context.Context) (string, error) {
	t.mu.Lock()
	defer t.mu.Unlock()
	if t.token != "" && time.Now().Before(t.expiry.Add(-tokenSkew)) {
		return t.token, nil
	}
	form := "grant_type=client_credentials&scope=" + t.scopes
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, t.tokenURL, strings.NewReader(form))
	if err != nil {
		return "", err
	}
	req.SetBasicAuth(t.clientID, t.clientSecret)
	req.Header.Set("Content-Type", "application/x-www-form-urlencoded")
	resp, err := t.http.Do(req)
	if err != nil {
		return "", err
	}
	defer func() { _ = resp.Body.Close() }()
	raw, err := io.ReadAll(io.LimitReader(resp.Body, maxRespBytes))
	if err != nil {
		return "", err
	}
	if resp.StatusCode != http.StatusOK {
		return "", fmt.Errorf("token endpoint returned %d", resp.StatusCode)
	}
	var out struct {
		AccessToken string `json:"access_token"`
		ExpiresIn   int64  `json:"expires_in"`
	}
	if err := json.Unmarshal(raw, &out); err != nil {
		return "", err
	}
	if out.AccessToken == "" {
		return "", errors.New("token endpoint returned no access_token")
	}
	ttl := time.Duration(out.ExpiresIn) * time.Second
	if ttl <= 0 {
		ttl = defaultExpiry
	}
	t.token = out.AccessToken
	t.expiry = time.Now().Add(ttl)
	return t.token, nil
}

func (t *tokenSource) Invalidate() {
	t.mu.Lock()
	defer t.mu.Unlock()
	t.token = ""
	t.expiry = time.Time{}
}

type graphQL struct {
	endpoint string
	tokens   *tokenSource
	http     *http.Client
}

const (
	archiveMutation = `mutation($id:String!){ issueArchive(id:$id){ success } }`
	thoughtMutation = `mutation($s:String!,$b:String!){ agentActivityCreate(input:{agentSessionId:$s, content:{type: "thought", body:$b}}){ success } }`
)

func (g *graphQL) ArchiveIssue(ctx context.Context, issueID string) error {
	return g.run(ctx, archiveMutation, map[string]any{"id": issueID}, "issueArchive")
}

func (g *graphQL) Thought(ctx context.Context, sessionID, body string) error {
	return g.run(ctx, thoughtMutation, map[string]any{"s": sessionID, "b": body}, "agentActivityCreate")
}

func (g *graphQL) run(ctx context.Context, query string, vars map[string]any, field string) error {
	ctx, cancel := context.WithTimeout(ctx, callTimeout)
	defer cancel()
	payload, err := json.Marshal(map[string]any{"query": query, "variables": vars})
	if err != nil {
		return err
	}
	for attempt := 0; ; attempt++ {
		token, err := g.tokens.Token(ctx)
		if err != nil {
			return err
		}
		status, raw, err := g.post(ctx, token, payload)
		if err != nil {
			return err
		}
		if status == http.StatusUnauthorized && attempt == 0 {
			g.tokens.Invalidate()
			continue
		}
		if status != http.StatusOK {
			return fmt.Errorf("linear graphql returned %d", status)
		}
		return checkResult(raw, field)
	}
}

func (g *graphQL) post(ctx context.Context, token string, payload []byte) (int, []byte, error) {
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, g.endpoint, bytes.NewReader(payload))
	if err != nil {
		return 0, nil, err
	}
	req.Header.Set("Authorization", "Bearer "+token)
	req.Header.Set("Content-Type", "application/json")
	resp, err := g.http.Do(req)
	if err != nil {
		return 0, nil, err
	}
	defer func() { _ = resp.Body.Close() }()
	raw, err := io.ReadAll(io.LimitReader(resp.Body, maxRespBytes))
	return resp.StatusCode, raw, err
}

func checkResult(raw []byte, field string) error {
	var out struct {
		Data   map[string]struct{ Success bool } `json:"data"`
		Errors []struct{ Message string }        `json:"errors"`
	}
	if err := json.Unmarshal(raw, &out); err != nil {
		return err
	}
	if len(out.Errors) > 0 {
		return fmt.Errorf("linear graphql error: %s", out.Errors[0].Message)
	}
	if !out.Data[field].Success {
		return fmt.Errorf("linear %s returned success=false", field)
	}
	return nil
}
