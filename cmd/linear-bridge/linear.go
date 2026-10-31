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
	Issue(ctx context.Context, id string) (IssueInfo, error)
	SetState(ctx context.Context, issueID, stateID string) error
	Activity(ctx context.Context, sessionID string, a Activity) error
	Comment(ctx context.Context, issueID, body string) error
	LinkCard(ctx context.Context, issueID, cardID string) error
}

type WorkflowState struct {
	ID   string `json:"id"`
	Name string `json:"name"`
	Type string `json:"type"`
}

type IssueInfo struct {
	ID, Identifier, Title, Description, ParentID, DelegateID, TeamID, StateID string
	States                                                                    []WorkflowState
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
	archiveMutation  = `mutation($id:String!){ issueArchive(id:$id){ success } }`
	issueQuery       = `query($id:String!){ issue(id:$id){ id identifier title description parent{ id } delegate{ id } state{ id } team{ id states{ nodes{ id name type } } } } }`
	stateMutation    = `mutation($id:String!,$s:String!){ issueUpdate(id:$id, input:{stateId:$s}){ success } }`
	activityMutation = `mutation($s:String!,$content:JSONObject!){ agentActivityCreate(input:{agentSessionId:$s, content:$content}){ success } }`
	commentMutation  = `mutation($id:String!,$b:String!){ commentCreate(input:{issueId:$id, body:$b}){ success } }`
	linkMutation     = `mutation($id:String!,$t:String!,$u:String!){ attachmentCreate(input:{issueId:$id, title:$t, url:$u}){ success } }`
	thoughtMutation  = `mutation($s:String!,$b:String!){ agentActivityCreate(input:{agentSessionId:$s, content:{type: "thought", body:$b}}){ success } }`
)

func (g *graphQL) ArchiveIssue(ctx context.Context, issueID string) error {
	return g.run(ctx, archiveMutation, map[string]any{"id": issueID}, "issueArchive")
}

func (g *graphQL) Thought(ctx context.Context, sessionID, body string) error {
	return g.run(ctx, thoughtMutation, map[string]any{"s": sessionID, "b": body}, "agentActivityCreate")
}

func (g *graphQL) SetState(ctx context.Context, issueID, stateID string) error {
	return g.run(ctx, stateMutation, map[string]any{"id": issueID, "s": stateID}, "issueUpdate")
}

func (g *graphQL) Activity(ctx context.Context, sessionID string, a Activity) error {
	content := map[string]string{"type": a.Type, "body": a.Body}
	return g.run(ctx, activityMutation, map[string]any{"s": sessionID, "content": content}, "agentActivityCreate")
}

func (g *graphQL) Comment(ctx context.Context, issueID, body string) error {
	return g.run(ctx, commentMutation, map[string]any{"id": issueID, "b": body}, "commentCreate")
}

func (g *graphQL) LinkCard(ctx context.Context, issueID, cardID string) error {
	vars := map[string]any{"id": issueID, "t": "Kanban card " + cardID, "u": "kanban://card/" + cardID}
	return g.run(ctx, linkMutation, vars, "attachmentCreate")
}

func (g *graphQL) Issue(ctx context.Context, id string) (IssueInfo, error) {
	raw, err := g.exec(ctx, issueQuery, map[string]any{"id": id})
	if err != nil {
		return IssueInfo{}, err
	}
	var out struct {
		Data struct {
			Issue *struct {
				ID          string  `json:"id"`
				Identifier  string  `json:"identifier"`
				Title       string  `json:"title"`
				Description *string `json:"description"`
				Parent      *struct {
					ID string `json:"id"`
				} `json:"parent"`
				Delegate *struct {
					ID string `json:"id"`
				} `json:"delegate"`
				State *struct {
					ID string `json:"id"`
				} `json:"state"`
				Team struct {
					ID     string `json:"id"`
					States struct {
						Nodes []WorkflowState `json:"nodes"`
					} `json:"states"`
				} `json:"team"`
			} `json:"issue"`
		} `json:"data"`
		Errors []struct{ Message string } `json:"errors"`
	}
	if err := json.Unmarshal(raw, &out); err != nil {
		return IssueInfo{}, err
	}
	if len(out.Errors) > 0 {
		return IssueInfo{}, fmt.Errorf("linear graphql error: %s", out.Errors[0].Message)
	}
	i := out.Data.Issue
	if i == nil {
		return IssueInfo{}, errors.New("linear issue not found")
	}
	info := IssueInfo{ID: i.ID, Identifier: i.Identifier, Title: i.Title, TeamID: i.Team.ID, States: i.Team.States.Nodes}
	if i.Description != nil {
		info.Description = *i.Description
	}
	if i.Parent != nil {
		info.ParentID = i.Parent.ID
	}
	if i.State != nil {
		info.StateID = i.State.ID
	}
	if i.Delegate != nil {
		info.DelegateID = i.Delegate.ID
	}
	return info, nil
}

func (g *graphQL) run(ctx context.Context, query string, vars map[string]any, field string) error {
	raw, err := g.exec(ctx, query, vars)
	if err != nil {
		return err
	}
	return checkResult(raw, field)
}

func (g *graphQL) exec(ctx context.Context, query string, vars map[string]any) ([]byte, error) {
	ctx, cancel := context.WithTimeout(ctx, callTimeout)
	defer cancel()
	payload, err := json.Marshal(map[string]any{"query": query, "variables": vars})
	if err != nil {
		return nil, err
	}
	for attempt := 0; ; attempt++ {
		token, err := g.tokens.Token(ctx)
		if err != nil {
			return nil, err
		}
		status, raw, err := g.post(ctx, token, payload)
		if err != nil {
			return nil, err
		}
		if status == http.StatusUnauthorized && attempt == 0 {
			g.tokens.Invalidate()
			continue
		}
		if status != http.StatusOK {
			return nil, fmt.Errorf("linear graphql returned %d", status)
		}
		return raw, nil
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
