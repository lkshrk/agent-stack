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
	"sort"
	"strings"
	"time"
)

const (
	minApproverTokenLen = 32
	approverTimeout     = 30 * time.Second
)

var (
	decisionText     = regexp.MustCompile(`^(?i:(approve|reject))\s+([0-9]{8})$`)
	bareDecisionText = regexp.MustCompile(`^(?i:(approve|reject))$`)
	announcementCode = regexp.MustCompile(`(?m)^Reply "approve ([0-9]{8})" to publish this commit upstream or "reject ([0-9]{8})"`)
	e164             = regexp.MustCompile(`^\+[1-9][0-9]{6,14}$`)
	decisionShaped   = regexp.MustCompile(`^(?i:approve|reject)(\s|$)`)
	uuidRe           = regexp.MustCompile(`^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$`)

	envelopeKeys = map[string]bool{
		"source": true, "sourceNumber": true, "sourceUuid": true, "sourceName": true, "sourceDevice": true,
		"timestamp": true, "serverReceivedTimestamp": true, "serverDeliveredTimestamp": true, "dataMessage": true,
	}
	dataMessageKeys = map[string]bool{"timestamp": true, "message": true, "expiresInSeconds": true, "groupInfo": true, "quote": true, "hasProfileKey": true}
)

type decision struct {
	Action             string `json:"action"`
	Code               string `json:"code"`
	account, recipient string
}

// The approver tells the owner the outcome itself for these answers.
var approverAnswers = map[int]bool{http.StatusOK: true, http.StatusNotFound: true, http.StatusGone: true, http.StatusConflict: true, http.StatusBadGateway: true}

type approvalRoute struct {
	owner, ownerUUID, shimUUID string
	url, token                 string
	http                       *http.Client
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
	r := &approvalRoute{owner: owner, url: url, token: token, http: &http.Client{Timeout: approverTimeout}}
	for key, field := range map[string]*string{"OWNER_UUID": &r.ownerUUID, "SHIM_ACCOUNT_UUID": &r.shimUUID} {
		v := getenv(key)
		if v != "" && !uuidRe.MatchString(v) {
			log.Printf("signal-shim: %s is not exactly one UUID; contribution approvals disabled, messages still reach Hermes", key)
			return nil, nil
		}
		*field = v
	}
	return r, nil
}

func (a *approvalRoute) match(data []byte) (decision, bool) {
	d, reason, text := a.classify(data)
	if reason == "" {
		return d, true
	}
	if decisionShaped.MatchString(strings.TrimSpace(text)) {
		log.Printf("signal-shim: approve/reject message not routed as a decision: %s", reason)
	}
	return decision{}, false
}

func (a *approvalRoute) classify(data []byte) (d decision, reason, text string) {
	var msg map[string]any
	if err := json.Unmarshal(data, &msg); err != nil {
		return d, "not JSON", ""
	}
	env, _ := msg["envelope"].(map[string]any)
	dm, _ := env["dataMessage"].(map[string]any)
	text, _ = dm["message"].(string)
	for _, k := range sortedKeys(msg) {
		if k != "envelope" && k != "account" && !empty(msg[k]) {
			return d, "unknown message key " + k, text
		}
	}
	if env == nil {
		return d, "no envelope", text
	}
	if k := unknownKey(env, envelopeKeys); k != "" {
		return d, "unknown envelope key " + k, text
	}
	account := str(msg["account"])
	number, uuid := str(env["sourceNumber"]), str(env["sourceUuid"])
	if source := str(env["source"]); number == "" && e164.MatchString(source) {
		number = source
	} else if uuid == "" && uuidRe.MatchString(source) {
		uuid = source
	}
	if (number != "" && number == account) || sameUUID(uuid, a.shimUUID) {
		return d, "message is from this account", text
	}
	if (number == "" || number != a.owner) && !sameUUID(uuid, a.ownerUUID) {
		return d, fmt.Sprintf("sender is not the owner (number present: %t, uuid present: %t)", number != "", uuid != ""), text
	}
	if dm == nil {
		return d, "no dataMessage", text
	}
	if k := unknownKey(dm, dataMessageKeys); k != "" {
		return d, "unknown dataMessage key " + k, text
	}
	if _, ok := dm["message"].(string); !ok {
		return d, "message is not text", text
	}
	action, code, reason := a.decisionIn(strings.TrimSpace(text), dm["quote"], account)
	if reason != "" {
		return d, reason, text
	}
	d = decision{Action: action, Code: code, account: account, recipient: a.owner}
	if g, _ := dm["groupInfo"].(map[string]any); g != nil {
		if id := str(g["groupId"]); id != "" {
			d.recipient = "group." + base64.StdEncoding.EncodeToString([]byte(id))
		}
	}
	return d, "", text
}

func (a *approvalRoute) decisionIn(text string, quote any, account string) (action, code, reason string) {
	if m := decisionText.FindStringSubmatch(text); m != nil {
		return strings.ToLower(m[1]), m[2], ""
	}
	m := bareDecisionText.FindStringSubmatch(text)
	if m == nil {
		return "", "", "text does not match"
	}
	q, _ := quote.(map[string]any)
	switch {
	case q == nil:
		return "", "", "bare reply without a quote"
	case account == "" && a.shimUUID == "":
		return "", "", "no account"
	case !a.quotedByThisAccount(q, account):
		return "", "", "quote is not by this account"
	}
	quoted, _ := q["text"].(string)
	codes := announcementCode.FindAllStringSubmatch(quoted, -1)
	if len(codes) != 1 || codes[0][1] != codes[0][2] {
		return "", "", "quote is not an announcement by this account"
	}
	return strings.ToLower(m[1]), codes[0][1], ""
}

func (a *approvalRoute) quotedByThisAccount(q map[string]any, account string) bool {
	author := str(q["authorNumber"])
	if author == "" {
		author = str(q["author"])
	}
	if account != "" && author == account {
		return true
	}
	for _, k := range []string{"authorUuid", "authorAci", "authorServiceId", "author"} {
		if sameUUID(str(q[k]), a.shimUUID) {
			return true
		}
	}
	return false
}

func sameUUID(got, want string) bool { return want != "" && strings.EqualFold(got, want) }

func sortedKeys(obj map[string]any) []string {
	keys := make([]string, 0, len(obj))
	for k := range obj {
		keys = append(keys, k)
	}
	sort.Strings(keys)
	return keys
}

func unknownKey(obj map[string]any, known map[string]bool) string {
	for _, k := range sortedKeys(obj) {
		if !known[k] && !empty(obj[k]) {
			return k
		}
	}
	return ""
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
