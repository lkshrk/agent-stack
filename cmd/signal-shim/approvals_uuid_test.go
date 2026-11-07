package main

import (
	"encoding/base64"
	"encoding/json"
	"strings"
	"testing"
)

const (
	testOwnerUUID = "0f1e2d3c-4b5a-4697-8877-665544332211"
	testBotUUID   = "a1b2c3d4-e5f6-4a7b-8c9d-0e1f2a3b4c5d"
	otherUUID     = "99999999-8888-4777-8666-555555555555"
)

func uuidEnvelope(sourceNumber any, source, sourceUUID string, dm map[string]any) string {
	env := map[string]any{"source": source, "sourceUuid": sourceUUID, "sourceDevice": 1, "timestamp": 1761245000123, "dataMessage": dm}
	if sourceNumber != "absent" {
		env["sourceNumber"] = sourceNumber
	}
	raw, _ := json.Marshal(map[string]any{"envelope": env, "account": testAccount})
	return string(raw)
}

func TestApprovalMatchRecognisesTheOwnerByUUID(t *testing.T) {
	withUUID := &approvalRoute{owner: testOwner, ownerUUID: testOwnerUUID, shimUUID: testBotUUID}
	numberOnly := &approvalRoute{owner: testOwner}
	text := groupText("approve 12345678")
	cases := []struct {
		name   string
		route  *approvalRoute
		data   string
		wantOK bool
	}{
		{"null sourceNumber", withUUID, uuidEnvelope(nil, testOwnerUUID, testOwnerUUID, text), true},
		{"absent sourceNumber", withUUID, uuidEnvelope("absent", testOwnerUUID, testOwnerUUID, text), true},
		{"uuid only in source", withUUID, uuidEnvelope(nil, testOwnerUUID, "", text), true},
		{"uuid only in sourceUuid", withUUID, uuidEnvelope(nil, "", testOwnerUUID, text), true},
		{"upper case uuid", withUUID, uuidEnvelope(nil, strings.ToUpper(testOwnerUUID), strings.ToUpper(testOwnerUUID), text), true},
		{"number still works", withUUID, envelope(testOwner, text, nil), true},
		{"without OWNER_UUID", numberOnly, uuidEnvelope(nil, testOwnerUUID, testOwnerUUID, text), false},
		{"wrong uuid", withUUID, uuidEnvelope(nil, otherUUID, otherUUID, text), false},
		{"empty uuid", withUUID, uuidEnvelope(nil, "", "", text), false},
		{"empty uuid with an empty route uuid", &approvalRoute{owner: testOwner}, uuidEnvelope(nil, "", "", text), false},
		{"other number and uuid, owner uuid in source", withUUID, uuidEnvelope("+4915559999", testOwnerUUID, otherUUID, text), false},
		{"own account number", withUUID, uuidEnvelope(testAccount, testOwnerUUID, testOwnerUUID, text), false},
		{"owner number from the own account uuid", withUUID, uuidEnvelope(testOwner, testOwner, testBotUUID, text), false},
		{"own account uuid", &approvalRoute{owner: testOwner, ownerUUID: testOwnerUUID, shimUUID: testOwnerUUID}, uuidEnvelope(nil, testOwnerUUID, testOwnerUUID, text), false},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			captureLog(t)
			got, ok := tc.route.match([]byte(tc.data))
			if ok != tc.wantOK {
				t.Fatalf("match = %+v, %v; want %v", got, ok, tc.wantOK)
			}
			if ok && (got.Action != "approve" || got.Code != "12345678" || got.recipient != "group."+base64.StdEncoding.EncodeToString([]byte(testGroupRaw))) {
				t.Fatalf("match = %+v", got)
			}
		})
	}
}

func TestBareReplyRecognisesTheBotsAnnouncementByUUID(t *testing.T) {
	withUUID := &approvalRoute{owner: testOwner, ownerUUID: testOwnerUUID, shimUUID: testBotUUID}
	numberOnly := &approvalRoute{owner: testOwner, ownerUUID: testOwnerUUID}
	quote := func(key, value string) map[string]any {
		q := map[string]any{"id": 1, "author": nil, "authorNumber": nil, "text": announcementText("12345678")}
		q[key] = value
		return q
	}
	cases := []struct {
		name   string
		route  *approvalRoute
		quote  map[string]any
		wantOK bool
	}{
		{"authorUuid", withUUID, quote("authorUuid", testBotUUID), true},
		{"authorAci", withUUID, quote("authorAci", testBotUUID), true},
		{"authorServiceId", withUUID, quote("authorServiceId", testBotUUID), true},
		{"author is the uuid", withUUID, quote("author", testBotUUID), true},
		{"upper case", withUUID, quote("authorUuid", strings.ToUpper(testBotUUID)), true},
		{"other uuid", withUUID, quote("authorUuid", otherUUID), false},
		{"empty uuid", withUUID, quote("authorUuid", ""), false},
		{"without SHIM_ACCOUNT_UUID", numberOnly, quote("authorUuid", testBotUUID), false},
		{"number still works", withUUID, announcementQuote(testAccount, announcementText("12345678")), true},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			captureLog(t)
			got, ok := tc.route.match([]byte(uuidEnvelope(nil, testOwnerUUID, testOwnerUUID, with(groupText("approve"), "quote", tc.quote))))
			if ok != tc.wantOK {
				t.Fatalf("match = %+v, %v; want %v", got, ok, tc.wantOK)
			}
			if ok && (got.Action != "approve" || got.Code != "12345678") {
				t.Fatalf("match = %+v", got)
			}
		})
	}
}

func TestApprovalRouteReadsTheAccountUUIDs(t *testing.T) {
	env := func(m map[string]string) func(string) string { return func(k string) string { return m[k] } }
	base := func(extra map[string]string) map[string]string {
		m := map[string]string{"APPROVER_URL": "http://approver:8080", "APPROVER_TOKEN": testToken, "OWNER_NUMBER": testOwner}
		for k, v := range extra {
			m[k] = v
		}
		return m
	}
	r, err := approvalRouteFromEnv(env(base(map[string]string{"OWNER_UUID": strings.ToUpper(testOwnerUUID), "SHIM_ACCOUNT_UUID": testBotUUID})))
	if err != nil || r == nil || !strings.EqualFold(r.ownerUUID, testOwnerUUID) || r.shimUUID != testBotUUID {
		t.Fatalf("configured = %+v, %v", r, err)
	}
	t.Run("valid OWNER_UUID does not rescue an invalid OWNER_NUMBER", func(t *testing.T) {
		logs := captureLog(t)
		r, err := approvalRouteFromEnv(env(base(map[string]string{"OWNER_NUMBER": "owner", "OWNER_UUID": testOwnerUUID})))
		if err != nil || r != nil {
			t.Fatalf("= %+v, %v; want approvals disabled without an error", r, err)
		}
		if out := logs(); !strings.Contains(out, "OWNER_NUMBER is not exactly one E.164 number") || strings.Contains(out, testOwnerUUID) {
			t.Fatalf("log = %q", out)
		}
	})
	for _, key := range []string{"OWNER_UUID", "SHIM_ACCOUNT_UUID"} {
		for _, bad := range []string{"not-a-uuid", testOwnerUUID + "," + testBotUUID, "{" + testOwnerUUID + "}", strings.ReplaceAll(testOwnerUUID, "-", ""), " " + testOwnerUUID, "0f1e2d3c-4b5a-4697-8877-66554433221g"} {
			t.Run(key+" "+bad, func(t *testing.T) {
				logs := captureLog(t)
				r, err := approvalRouteFromEnv(env(base(map[string]string{key: bad})))
				if err != nil || r != nil {
					t.Fatalf("%s %q = %+v, %v; want approvals disabled without an error", key, bad, r, err)
				}
				out := logs()
				if !strings.Contains(out, key+" is not exactly one UUID") || !strings.Contains(out, "approvals disabled") {
					t.Fatalf("log = %q", out)
				}
				if strings.Contains(out, bad) || strings.Contains(out, testOwnerUUID[:8]) {
					t.Fatalf("log leaks the value: %q", out)
				}
			})
		}
	}
}

func TestUnroutedDecisionLooksLogTheReasonOnly(t *testing.T) {
	route := &approvalRoute{owner: testOwner, ownerUUID: testOwnerUUID, shimUUID: testBotUUID}
	otherQuote := map[string]any{"id": 1, "author": "+4915559999", "authorUuid": otherUUID, "text": announcementText("12345678")}
	botNotAnnouncement := map[string]any{"id": 1, "authorUuid": testBotUUID, "text": "Published upstream"}
	noAccount, _ := json.Marshal(map[string]any{"envelope": map[string]any{"sourceNumber": testOwner, "dataMessage": with(groupText("approve"), "quote", map[string]any{"id": 1, "text": announcementText("12345678")})}})
	cases := []struct {
		name, route, data, want string
	}{
		{"other sender", "uuid", uuidEnvelope("+4915559999", otherUUID, otherUUID, groupText("approve 12345678")), "sender is not the owner (number present: true, uuid present: true)"},
		{"hidden other sender", "uuid", uuidEnvelope(nil, "", otherUUID, groupText("Reject 12345678")), "sender is not the owner (number present: false, uuid present: true)"},
		{"own account", "uuid", uuidEnvelope(testAccount, "", "", groupText("approve 12345678")), "message is from this account"},
		{"unknown envelope key", "uuid", envelope(testOwner, groupText("approve 12345678"), map[string]any{"syncMessage": map[string]any{"x": 1}}), "unknown envelope key syncMessage"},
		{"unknown dataMessage key", "uuid", envelope(testOwner, with(groupText("approve 12345678"), "mentions", []any{1}), nil), "unknown dataMessage key mentions"},
		{"text", "uuid", envelope(testOwner, groupText("approve 1234"), nil), "text does not match"},
		{"bare without quote", "uuid", envelope(testOwner, groupText("approve"), nil), "bare reply without a quote"},
		{"quote by someone else", "uuid", envelope(testOwner, with(groupText("approve"), "quote", otherQuote), nil), "quote is not by this account"},
		{"quote not an announcement", "uuid", envelope(testOwner, with(groupText("approve"), "quote", botNotAnnouncement), nil), "quote is not an announcement by this account"},
		{"no account", "number", string(noAccount), "no account"},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			r := route
			if tc.route == "number" {
				r = &approvalRoute{owner: testOwner}
			}
			logs := captureLog(t)
			if d, ok := r.match([]byte(tc.data)); ok {
				t.Fatalf("matched %+v", d)
			}
			out := logs()
			if !strings.Contains(out, "not routed as a decision: "+tc.want+"\n") || strings.Count(out, "\n") != 1 {
				t.Fatalf("log = %q, want reason %q", out, tc.want)
			}
			for _, secret := range []string{testOwner, "+4915559999", testAccount, testOwnerUUID, testBotUUID, otherUUID, "12345678", "1234", "approve ", "Published", testGroupRaw} {
				if strings.Contains(out, secret) {
					t.Fatalf("log contains %q: %q", secret, out)
				}
			}
		})
	}
}

func TestOrdinaryChatAndDecisionsAreNotLogged(t *testing.T) {
	route := &approvalRoute{owner: testOwner, ownerUUID: testOwnerUUID}
	for _, data := range []string{
		envelope(testOwner, groupText("hello hermes"), nil),
		envelope(testOwner, groupText("approved, thanks"), nil),
		envelope(testOwner, groupText("Rejected by CI"), nil),
		envelope(testOwner, groupText("approver is down"), nil),
		envelope("+4915559999", groupText("please approve 12345678"), nil),
		envelope(testOwner, nil, map[string]any{"editMessage": map[string]any{"dataMessage": groupText("approve 12345678")}}),
		envelope(testOwner, groupText("approve 12345678"), nil),
		"not json",
	} {
		logs := captureLog(t)
		route.match([]byte(data))
		if out := logs(); out != "" {
			t.Fatalf("logged %q for %s", out, data)
		}
	}
}
