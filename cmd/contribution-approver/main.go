// Command contribution-approver turns the owner's Signal replies into reviews on contribution fork pull requests.
package main

import (
	"context"
	"log"
	"net/http"
	"os"
	"os/signal"
	"strings"
	"syscall"
	"time"
)

const (
	codeTTL        = 30 * time.Minute
	pollInterval   = 60 * time.Second
	pollTimeout    = 45 * time.Second
	minSharedToken = 32
	defaultGitHub  = "https://api.github.com"
)

func main() {
	shared := mustEnv("APPROVER_TOKEN")
	if len(shared) < minSharedToken {
		log.Fatalf("contribution-approver: APPROVER_TOKEN must be at least %d characters", minSharedToken)
	}
	forks, err := loadForks(embeddedForks)
	if err != nil {
		log.Fatalf("contribution-approver: %v", err)
	}
	base := os.Getenv("GITHUB_API_URL")
	if base == "" {
		base = defaultGitHub
	}
	httpClient := &http.Client{Timeout: 15 * time.Second}
	gh := &githubClient{base: base, token: mustEnv("GITHUB_TOKEN"), http: httpClient}
	notify := &signalNotifier{
		base:      mustEnv("SIGNAL_REST_URL"),
		number:    mustEnv("SIGNAL_NUMBER"),
		recipient: mustEnv("SIGNAL_RECIPIENT"),
		http:      httpClient,
	}
	codes := newCodeStore(codeTTL, time.Now)
	p := newPoller(gh, forks, codes, notify)
	d := &decider{token: shared, codes: codes, gh: gh, forks: forks, notify: notify}

	listen := os.Getenv("LISTEN_ADDR")
	if listen == "" {
		listen = ":8080"
	}
	srv := &http.Server{
		Addr:              listen,
		Handler:           d.routes(),
		ReadHeaderTimeout: 5 * time.Second,
		ReadTimeout:       5 * time.Second,
		WriteTimeout:      reviewTimeout + 10*time.Second,
		IdleTimeout:       60 * time.Second,
	}

	ctx, stop := signal.NotifyContext(context.Background(), syscall.SIGTERM, syscall.SIGINT)
	defer stop()
	go func() {
		for {
			pctx, cancel := context.WithTimeout(ctx, pollTimeout)
			p.poll(pctx)
			cancel()
			select {
			case <-ctx.Done():
				return
			case <-time.After(pollInterval):
			}
		}
	}()

	serveErr := make(chan error, 1)
	go func() { serveErr <- srv.ListenAndServe() }()
	names := make([]string, len(forks))
	for i, f := range forks {
		names[i] = f.Fork
	}
	log.Printf("contribution-approver: listening on %s, watching %s", listen, strings.Join(names, ", "))
	select {
	case err := <-serveErr:
		log.Fatal(err)
	case <-ctx.Done():
	}
	sctx, cancel := context.WithTimeout(context.Background(), reviewTimeout+5*time.Second)
	defer cancel()
	if err := srv.Shutdown(sctx); err != nil {
		log.Printf("contribution-approver: shutdown: %v", err)
	}
}

func mustEnv(name string) string {
	v := os.Getenv(name)
	if v == "" {
		log.Fatalf("contribution-approver: %s is not set", name)
	}
	return v
}
