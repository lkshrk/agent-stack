// Command token-minter exchanges an Authentik machine identity for short-lived
// GitHub tokens through octo-sts and writes them into a Secret in every listed namespace.
package main

import (
	"bytes"
	"context"
	"crypto/tls"
	"crypto/x509"
	"encoding/json"
	"fmt"
	"io"
	"log"
	"net/http"
	"net/url"
	"os"
	"sort"
	"strings"
	"time"
)

const saDir = "/var/run/secrets/kubernetes.io/serviceaccount"

func main() {
	if err := run(context.Background(), envFunc(os.Getenv), saDir); err != nil {
		log.Fatalf("token-minter: %v", err)
	}
}

type envFunc func(string) string

func (e envFunc) require(name string) (string, error) {
	v := strings.TrimSpace(e(name))
	if v == "" {
		return "", fmt.Errorf("%s is not set", name)
	}
	return v, nil
}

func run(ctx context.Context, env envFunc, sa string) error {
	cfg := map[string]string{}
	for _, name := range []string{
		"AUTHENTIK_TOKEN_URL", "AUTHENTIK_CLIENT_ID", "AUTHENTIK_APP_PASSWORD",
		"OCTO_STS_URL", "OCTO_STS_IDENTITY", "TOKENS", "SECRET_NAME", "SECRET_NAMESPACES",
	} {
		v, err := env.require(name)
		if err != nil {
			return err
		}
		cfg[name] = v
	}

	client := &http.Client{Timeout: 30 * time.Second}

	bearer, err := authentikToken(ctx, client, cfg)
	if err != nil {
		return err
	}

	// TOKENS="code=routivo,knowledge=lkshrk": Secret key -> GitHub owner whose trust policy applies.
	tokens := map[string]string{}
	for _, pair := range strings.Split(cfg["TOKENS"], ",") {
		key, owner, ok := strings.Cut(strings.TrimSpace(pair), "=")
		if !ok || key == "" || owner == "" {
			continue
		}
		tok, err := octoSTSToken(ctx, client, cfg, bearer, strings.TrimSpace(owner))
		if err != nil {
			return err
		}
		tokens[strings.TrimSpace(key)] = tok
	}
	if len(tokens) == 0 {
		return fmt.Errorf("TOKENS contains no key=owner pairs")
	}

	kube, err := kubeClient(sa)
	if err != nil {
		return err
	}
	var namespaces []string
	for _, ns := range strings.Split(cfg["SECRET_NAMESPACES"], ",") {
		if ns = strings.TrimSpace(ns); ns != "" {
			namespaces = append(namespaces, ns)
		}
	}
	for _, ns := range namespaces {
		if err := applySecret(ctx, kube, env("KUBERNETES_SERVICE_HOST"), env("KUBERNETES_SERVICE_PORT"), ns, cfg["SECRET_NAME"], tokens); err != nil {
			return err
		}
	}

	keys := make([]string, 0, len(tokens))
	for k := range tokens {
		keys = append(keys, k)
	}
	sort.Strings(keys)
	log.Printf("token-minter: refreshed %s in %s", strings.Join(keys, ", "), strings.Join(namespaces, ", "))
	return nil
}

func authentikToken(ctx context.Context, c *http.Client, cfg map[string]string) (string, error) {
	form := url.Values{
		"grant_type": {"client_credentials"},
		"client_id":  {cfg["AUTHENTIK_CLIENT_ID"]},
		"username":   {cfg["OCTO_STS_IDENTITY"]},
		"password":   {cfg["AUTHENTIK_APP_PASSWORD"]},
		"scope":      {"openid profile"},
	}
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, cfg["AUTHENTIK_TOKEN_URL"], strings.NewReader(form.Encode()))
	if err != nil {
		return "", err
	}
	req.Header.Set("Content-Type", "application/x-www-form-urlencoded")
	var body struct {
		AccessToken string `json:"access_token"`
	}
	if err := doJSON(c, req, &body); err != nil {
		return "", fmt.Errorf("authentik: %w", err)
	}
	if body.AccessToken == "" {
		return "", fmt.Errorf("authentik response has no access_token")
	}
	return body.AccessToken, nil
}

func octoSTSToken(ctx context.Context, c *http.Client, cfg map[string]string, bearer, owner string) (string, error) {
	q := url.Values{"scope": {owner}, "identity": {cfg["OCTO_STS_IDENTITY"]}}
	u := strings.TrimRight(cfg["OCTO_STS_URL"], "/") + "/sts/exchange?" + q.Encode()
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, u, nil)
	if err != nil {
		return "", err
	}
	req.Header.Set("Authorization", "Bearer "+bearer)
	var body struct {
		Token string `json:"token"`
	}
	if err := doJSON(c, req, &body); err != nil {
		return "", fmt.Errorf("octo-sts %s: %w", owner, err)
	}
	if body.Token == "" {
		return "", fmt.Errorf("octo-sts returned no token for %s", owner)
	}
	return body.Token, nil
}

type kube struct {
	client *http.Client
	token  string
}

func kubeClient(sa string) (*kube, error) {
	tok, err := os.ReadFile(sa + "/token")
	if err != nil {
		return nil, err
	}
	ca, err := os.ReadFile(sa + "/ca.crt")
	if err != nil {
		return nil, err
	}
	pool := x509.NewCertPool()
	if !pool.AppendCertsFromPEM(ca) {
		return nil, fmt.Errorf("no certificates in %s/ca.crt", sa)
	}
	return &kube{
		client: &http.Client{Timeout: 30 * time.Second, Transport: &http.Transport{TLSClientConfig: &tls.Config{RootCAs: pool}}},
		token:  strings.TrimSpace(string(tok)),
	}, nil
}

func applySecret(ctx context.Context, k *kube, host, port, namespace, name string, data map[string]string) error {
	if port == "" {
		port = "443"
	}
	body, err := json.Marshal(map[string]any{
		"apiVersion": "v1",
		"kind":       "Secret",
		"metadata":   map[string]string{"name": name, "namespace": namespace},
		"type":       "Opaque",
		"stringData": data,
	})
	if err != nil {
		return err
	}
	u := fmt.Sprintf("https://%s/api/v1/namespaces/%s/secrets/%s?fieldManager=token-minter&force=true",
		joinHostPort(host, port), url.PathEscape(namespace), url.PathEscape(name))
	req, err := http.NewRequestWithContext(ctx, http.MethodPatch, u, bytes.NewReader(body))
	if err != nil {
		return err
	}
	req.Header.Set("Authorization", "Bearer "+k.token)
	req.Header.Set("Content-Type", "application/apply-patch+yaml")
	if err := doJSON(k.client, req, nil); err != nil {
		return fmt.Errorf("apply secret %s/%s: %w", namespace, name, err)
	}
	return nil
}

func joinHostPort(host, port string) string {
	if strings.Contains(host, ":") && !strings.HasPrefix(host, "[") {
		return "[" + host + "]:" + port
	}
	return host + ":" + port
}

func doJSON(c *http.Client, req *http.Request, out any) error {
	res, err := c.Do(req)
	if err != nil {
		return fmt.Errorf("request to %s failed", req.URL.Host)
	}
	defer func() { _ = res.Body.Close() }()
	raw, _ := io.ReadAll(io.LimitReader(res.Body, 1<<20))
	if res.StatusCode >= 300 {
		return fmt.Errorf("%s %s returned HTTP %d: %.300s", req.Method, req.URL.Host, res.StatusCode, raw)
	}
	if out == nil {
		return nil
	}
	return json.Unmarshal(raw, out)
}
