package main

import (
	"context"
	"encoding/json"
	"encoding/pem"
	"io"
	"net"
	"net/http"
	"net/http/httptest"
	"net/url"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"testing"
)

func TestRunMintsTokensIntoEveryNamespace(t *testing.T) {
	var mu sync.Mutex
	var applied []string
	var appliedData map[string]any

	auth := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		_ = r.ParseForm()
		if r.Form.Get("grant_type") != "client_credentials" || r.Form.Get("username") != "agent-stack-minter" {
			http.Error(w, "bad form", http.StatusBadRequest)
			return
		}
		_, _ = w.Write([]byte(`{"access_token":"bearer"}`))
	}))
	defer auth.Close()

	sts := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Header.Get("Authorization") != "Bearer bearer" {
			http.Error(w, "no bearer", http.StatusUnauthorized)
			return
		}
		_ = json.NewEncoder(w).Encode(map[string]string{"token": "ghs_" + r.URL.Query().Get("scope")})
	}))
	defer sts.Close()

	api := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodPatch || r.Header.Get("Content-Type") != "application/apply-patch+yaml" {
			http.Error(w, "unexpected request", http.StatusBadRequest)
			return
		}
		raw, _ := io.ReadAll(r.Body)
		var obj map[string]any
		_ = json.Unmarshal(raw, &obj)
		mu.Lock()
		applied = append(applied, r.URL.Path)
		appliedData = obj["stringData"].(map[string]any)
		mu.Unlock()
		_, _ = w.Write([]byte(`{}`))
	}))
	defer api.Close()

	sa := t.TempDir()
	if err := os.WriteFile(filepath.Join(sa, "token"), []byte("sa-token"), 0o600); err != nil {
		t.Fatal(err)
	}
	cert := pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: api.Certificate().Raw})
	if err := os.WriteFile(filepath.Join(sa, "ca.crt"), cert, 0o600); err != nil {
		t.Fatal(err)
	}
	u, _ := url.Parse(api.URL)
	host, port, _ := net.SplitHostPort(u.Host)

	env := map[string]string{
		"AUTHENTIK_TOKEN_URL":     auth.URL,
		"AUTHENTIK_CLIENT_ID":     "octo-sts",
		"AUTHENTIK_APP_PASSWORD":  "pw",
		"OCTO_STS_URL":            sts.URL + "/",
		"OCTO_STS_IDENTITY":       "agent-stack-minter",
		"TOKENS":                  "code=routivo, knowledge=lkshrk",
		"SECRET_NAME":             "agent-github-tokens",
		"SECRET_NAMESPACES":       "omnigent-sandboxes,ai",
		"KUBERNETES_SERVICE_HOST": host,
		"KUBERNETES_SERVICE_PORT": port,
	}
	if err := run(context.Background(), func(k string) string { return env[k] }, sa); err != nil {
		t.Fatal(err)
	}

	want := []string{
		"/api/v1/namespaces/omnigent-sandboxes/secrets/agent-github-tokens",
		"/api/v1/namespaces/ai/secrets/agent-github-tokens",
	}
	if strings.Join(applied, ",") != strings.Join(want, ",") {
		t.Fatalf("applied %v, want %v", applied, want)
	}
	if appliedData["code"] != "ghs_routivo" || appliedData["knowledge"] != "ghs_lkshrk" {
		t.Fatalf("secret data = %v", appliedData)
	}
}

func TestRunAlsoWritesEnvSecretKeys(t *testing.T) {
	var mu sync.Mutex
	writes := map[string]map[string]any{}

	auth := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		_, _ = w.Write([]byte(`{"access_token":"bearer"}`))
	}))
	defer auth.Close()
	sts := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		_ = json.NewEncoder(w).Encode(map[string]string{"token": "ghs_" + r.URL.Query().Get("scope")})
	}))
	defer sts.Close()
	api := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		raw, _ := io.ReadAll(r.Body)
		var obj map[string]any
		_ = json.Unmarshal(raw, &obj)
		mu.Lock()
		writes[r.URL.Path] = obj["stringData"].(map[string]any)
		mu.Unlock()
		_, _ = w.Write([]byte(`{}`))
	}))
	defer api.Close()

	sa := t.TempDir()
	_ = os.WriteFile(filepath.Join(sa, "token"), []byte("sa-token"), 0o600)
	_ = os.WriteFile(filepath.Join(sa, "ca.crt"), pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: api.Certificate().Raw}), 0o600)
	u, _ := url.Parse(api.URL)
	host, port, _ := net.SplitHostPort(u.Host)

	env := map[string]string{
		"AUTHENTIK_TOKEN_URL":     auth.URL,
		"AUTHENTIK_CLIENT_ID":     "octo-sts",
		"AUTHENTIK_APP_PASSWORD":  "pw",
		"OCTO_STS_URL":            sts.URL + "/",
		"OCTO_STS_IDENTITY":       "agent-stack-minter",
		"TOKENS":                  "code=routivo,knowledge=lkshrk",
		"SECRET_NAME":             "agent-github-tokens",
		"SECRET_NAMESPACES":       "omnigent-sandboxes",
		"ENV_SECRETS":             "omnigent-sandboxes/omnigent-runner-env:GIT_TOKEN=code",
		"KUBERNETES_SERVICE_HOST": host,
		"KUBERNETES_SERVICE_PORT": port,
	}
	if err := run(context.Background(), func(k string) string { return env[k] }, sa); err != nil {
		t.Fatal(err)
	}

	got := writes["/api/v1/namespaces/omnigent-sandboxes/secrets/omnigent-runner-env"]
	if len(got) != 1 || got["GIT_TOKEN"] != "ghs_routivo" {
		t.Fatalf("env secret data = %v, want only GIT_TOKEN=ghs_routivo", got)
	}
}

func TestRunRejectsEnvSecretForUnknownToken(t *testing.T) {
	env := map[string]string{"ENV_SECRETS": "ns/name:GIT_TOKEN=missing"}
	if _, err := parseEnvSecrets(env["ENV_SECRETS"], map[string]string{"code": "x"}); err == nil {
		t.Fatal("expected an error for a token key TOKENS does not mint")
	}
}

func TestRunFailsWithoutRequiredEnv(t *testing.T) {
	if err := run(context.Background(), func(string) string { return "" }, t.TempDir()); err == nil {
		t.Fatal("expected an error when configuration is missing")
	}
}

func TestParseTokens(t *testing.T) {
	got, err := parseTokens(" code=routivo, knowledge=lkshrk ,review=routivo@security-review", "agent-stack-minter")
	if err != nil {
		t.Fatal(err)
	}
	want := []tokenSpec{
		{key: "code", owner: "routivo", identity: "agent-stack-minter"},
		{key: "knowledge", owner: "lkshrk", identity: "agent-stack-minter"},
		{key: "review", owner: "routivo", identity: "security-review"},
	}
	if len(got) != len(want) {
		t.Fatalf("got %v, want %v", got, want)
	}
	for i := range want {
		if got[i] != want[i] {
			t.Fatalf("entry %d = %+v, want %+v", i, got[i], want[i])
		}
	}
}

func TestParseTokensRejectsMalformedEntries(t *testing.T) {
	for _, raw := range []string{
		"code",
		"=routivo",
		"code=",
		"code=routivo@",
		"code=@review",
		"code=routivo@a@b",
		"code=routivo,,knowledge=lkshrk",
		"code=routivo,code=lkshrk",
		"code=rou tivo",
		"",
	} {
		if _, err := parseTokens(raw, "agent-stack-minter"); err == nil {
			t.Errorf("parseTokens(%q) succeeded, want an error", raw)
		}
	}
}

func TestRunSendsEachTokensIdentity(t *testing.T) {
	var mu sync.Mutex
	identities := map[string]string{}
	var data map[string]any

	auth := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		_, _ = w.Write([]byte(`{"access_token":"bearer"}`))
	}))
	defer auth.Close()
	sts := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		q := r.URL.Query()
		mu.Lock()
		identities[q.Get("scope")+"@"+q.Get("identity")] = q.Get("identity")
		mu.Unlock()
		_ = json.NewEncoder(w).Encode(map[string]string{"token": "ghs_" + q.Get("scope") + "_" + q.Get("identity")})
	}))
	defer sts.Close()
	api := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		raw, _ := io.ReadAll(r.Body)
		var obj map[string]any
		_ = json.Unmarshal(raw, &obj)
		mu.Lock()
		data = obj["stringData"].(map[string]any)
		mu.Unlock()
		_, _ = w.Write([]byte(`{}`))
	}))
	defer api.Close()

	sa := t.TempDir()
	if err := os.WriteFile(filepath.Join(sa, "token"), []byte("sa-token"), 0o600); err != nil {
		t.Fatal(err)
	}
	cert := pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: api.Certificate().Raw})
	if err := os.WriteFile(filepath.Join(sa, "ca.crt"), cert, 0o600); err != nil {
		t.Fatal(err)
	}
	u, _ := url.Parse(api.URL)
	host, port, _ := net.SplitHostPort(u.Host)
	env := map[string]string{
		"AUTHENTIK_TOKEN_URL":     auth.URL,
		"AUTHENTIK_CLIENT_ID":     "octo-sts",
		"AUTHENTIK_APP_PASSWORD":  "pw",
		"OCTO_STS_URL":            sts.URL,
		"OCTO_STS_IDENTITY":       "agent-stack-minter",
		"TOKENS":                  "code=routivo,review=routivo@security-review",
		"SECRET_NAME":             "agent-github-tokens",
		"SECRET_NAMESPACES":       "omnigent-sandboxes",
		"KUBERNETES_SERVICE_HOST": host,
		"KUBERNETES_SERVICE_PORT": port,
	}
	if err := run(context.Background(), func(k string) string { return env[k] }, sa); err != nil {
		t.Fatal(err)
	}
	if identities["routivo@agent-stack-minter"] == "" || identities["routivo@security-review"] == "" || len(identities) != 2 {
		t.Fatalf("octo-sts identities = %v", identities)
	}
	if data["code"] != "ghs_routivo_agent-stack-minter" || data["review"] != "ghs_routivo_security-review" {
		t.Fatalf("secret data = %v", data)
	}
}

func TestRunFailsOnMalformedTokens(t *testing.T) {
	env := map[string]string{
		"AUTHENTIK_TOKEN_URL": "http://127.0.0.1:1", "AUTHENTIK_CLIENT_ID": "c", "AUTHENTIK_APP_PASSWORD": "p",
		"OCTO_STS_URL": "http://127.0.0.1:1", "OCTO_STS_IDENTITY": "i", "TOKENS": "code=routivo,broken",
		"SECRET_NAME": "s", "SECRET_NAMESPACES": "n",
	}
	err := run(context.Background(), func(k string) string { return env[k] }, t.TempDir())
	if err == nil || !strings.Contains(err.Error(), "broken") {
		t.Fatalf("run error = %v, want one naming the malformed entry", err)
	}
}
