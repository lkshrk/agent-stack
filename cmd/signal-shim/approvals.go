package main

import (
	"bytes"
	"context"
	"encoding/base64"
	"encoding/json"
	"fmt"
	"io"
	"log"
	"net/http"
	"regexp"
	"strings"
	"time"
)

const (
	minApproverTokenLen = 32
	approverTimeout     = 30 * time.Second
)

var (
	decisionText = regexp.MustCompile(`^(approve|reject) ([0-9]{8})$`)
	e164         = regexp.MustCompile(`^\+[1-9][0-9]{6,14}$`)

	envelopeKeys = map[string]bool{
		"source": true, "sourceNumber": true, "sourceUuid": true, "sourceName": true, "sourceDevice": true,
		"timestamp": true, "serverReceivedTimestamp": true, "serverDeliveredTimestamp": true, "dataMessage": true,
	}
	dataMessageKeys = map[string]bool{"timestamp": true, "message": true, "expiresInSeconds": true, "groupInfo": true}
)

type decision struct {
	Action             string `json:"action"`
	Code               string `json:"code"`
	account, recipient string
}

// The approver tells the owner the outcome itself for these answers.
var approverAnswers = map[int]bool{http.StatusOK: true, http.StatusNotFound: true, http.StatusGone: true, http.StatusConflict: true, http.StatusBadGateway: true}

type approvalRoute struct {
	owner, url, token string
	http              *http.Client
}

func approvalRouteFromEnv(getenv func(string) string) (*approvalRoute, error) {
	url, token, owner := strings.TrimRight(getenv("APPROVER_URL"), "/"), getenv("APPROVER_TOKEN"), getenv("OWNER_NUMBER")
	if url == "" && token == "" && owner == "" {
		return nil, nil
	}
	switch {
	case url == "" || token == "" || owner == "":
		return nil, fmt.Errorf("APPROVER_URL, APPROVER_TOKEN and OWNER_NUMBER must be set together")
	case len(token) < minApproverTokenLen:
		return nil, fmt.Errorf("APPROVER_TOKEN must be at least %d characters", minApproverTokenLen)
	case !e164.MatchString(owner):
		log.Printf("signal-shim: OWNER_NUMBER is not exactly one E.164 number; contribution approvals disabled, messages still reach Hermes")
		return nil, nil
	}
	return &approvalRoute{owner: owner, url: url, token: token, http: &http.Client{Timeout: approverTimeout}}, nil
}

func (a *approvalRoute) match(data []byte) (decision, bool) {
	var msg map[string]any
	if err := json.Unmarshal(data, &msg); err != nil {
		return decision{}, false
	}
	for k, v := range msg {
		if k != "envelope" && k != "account" && !empty(v) {
			return decision{}, false
		}
	}
	env, _ := msg["envelope"].(map[string]any)
	if env == nil || !onlyKnown(env, envelopeKeys) {
		return decision{}, false
	}
	source := str(env["source"])
	if v, ok := env["sourceNumber"]; ok {
		source = str(v)
	}
	if source != a.owner || source == str(msg["account"]) {
		return decision{}, false
	}
	dm, _ := env["dataMessage"].(map[string]any)
	if dm == nil || !onlyKnown(dm, dataMessageKeys) {
		return decision{}, false
	}
	text, ok := dm["message"].(string)
	if !ok {
		return decision{}, false
	}
	m := decisionText.FindStringSubmatch(text)
	if m == nil {
		return decision{}, false
	}
	d := decision{Action: m[1], Code: m[2], account: str(msg["account"]), recipient: a.owner}
	if g, _ := dm["groupInfo"].(map[string]any); g != nil {
		if id := str(g["groupId"]); id != "" {
			d.recipient = "group." + base64.StdEncoding.EncodeToString([]byte(id))
		}
	}
	return d, true
}

func onlyKnown(obj map[string]any, known map[string]bool) bool {
	for k, v := range obj {
		if !known[k] && !empty(v) {
			return false
		}
	}
	return true
}

func empty(v any) bool {
	switch x := v.(type) {
	case nil:
		return true
	case bool:
		return !x
	case string:
		return x == ""
	case float64:
		return x == 0
	case []any:
		return len(x) == 0
	case map[string]any:
		return len(x) == 0
	}
	return false
}

func (a *approvalRoute) post(ctx context.Context, d decision) (int, error) {
	body, _ := json.Marshal(d)
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, a.url+"/v1/decisions", bytes.NewReader(body))
	if err != nil {
		return 0, err
	}
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Authorization", "Bearer "+a.token)
	res, err := a.http.Do(req)
	if err != nil {
		return 0, fmt.Errorf("approver unreachable")
	}
	defer func() { _ = res.Body.Close() }()
	_, _ = io.Copy(io.Discard, io.LimitReader(res.Body, 4096))
	return res.StatusCode, nil
}

// forwardDecision never falls back to Hermes; when the approver can't answer, the owner gets a note instead.
func (s *shim) forwardDecision(d decision) {
	ctx, cancel := context.WithTimeout(context.Background(), approverTimeout+10*time.Second)
	defer cancel()
	status, err := s.approvals.post(ctx, d)
	switch {
	case err != nil:
		log.Printf("signal-shim: owner %s decision not forwarded: %v", d.Action, err)
	case approverAnswers[status]:
		log.Printf("signal-shim: owner %s decision forwarded, approver answered HTTP %d", d.Action, status)
		return
	default:
		log.Printf("signal-shim: owner %s decision refused by the approver with HTTP %d", d.Action, status)
	}
	account, err := s.accountFor(ctx, map[string]any{"account": d.account})
	if err != nil {
		log.Printf("signal-shim: owner note about the undelivered decision has no sending account")
		return
	}
	note := map[string]any{"number": account, "recipients": []string{d.recipient}, "message": "Your " + d.action() + " was not delivered to the contribution approver. Nothing was submitted; try again or review on GitHub."}
	if _, err := s.doJSON(ctx, http.MethodPost, "/v2/send", note); err != nil {
		log.Printf("signal-shim: owner note about the undelivered decision failed")
	}
}

func (d decision) action() string { return d.Action + " " + d.Code }
