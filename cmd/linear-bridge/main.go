// Command linear-bridge receives signed Linear webhooks and acts on them.
package main

import (
	"context"
	"log"
	"net/http"
	"os"
	"os/signal"
	"syscall"
	"time"
)

const (
	queueSize     = 256
	eventTimeout  = 9 * time.Second
	shutdownGrace = 25 * time.Second
	tokenURL      = "https://api.linear.app/oauth/token"
	graphQLURL    = "https://api.linear.app/graphql"
)

func main() {
	secret := mustEnv("LINEAR_WEBHOOK_SECRET")
	httpClient := &http.Client{Timeout: callTimeout}
	b := &bridge{
		linear: &graphQL{
			endpoint: graphQLURL,
			http:     httpClient,
			tokens: &tokenSource{
				tokenURL:     tokenURL,
				clientID:     mustEnv("LINEAR_APP_CLIENT_ID"),
				clientSecret: mustEnv("LINEAR_APP_CLIENT_SECRET"),
				scopes:       mustEnv("LINEAR_APP_SCOPES"),
				http:         httpClient,
			},
		},
		notify: &signalNotifier{
			base:      mustEnv("SIGNAL_REST_URL"),
			number:    mustEnv("SIGNAL_NUMBER"),
			recipient: mustEnv("SIGNAL_RECIPIENT"),
			http:      httpClient,
		},
		kanban: &gateClient{
			base:  mustEnv("KANBAN_GATE_URL"),
			token: mustEnv("KANBAN_GATE_TOKEN"),
			http:  &http.Client{Timeout: stopTimeout},
		},
		ownerID:   mustEnv("LINEAR_OWNER_ID"),
		appUserID: mustEnv("LINEAR_APP_USER_ID"),
	}
	listen := os.Getenv("LISTEN_ADDR")
	if listen == "" {
		listen = ":8080"
	}
	in := &ingress{
		secret: []byte(secret),
		dedupe: newDeduper(dedupeTTL),
		queue:  make(chan event, queueSize),
		now:    time.Now,
	}
	mux := http.NewServeMux()
	mux.Handle("POST /webhooks/linear", in)
	mux.HandleFunc("GET /healthz", func(w http.ResponseWriter, _ *http.Request) {
		w.WriteHeader(http.StatusOK)
	})
	srv := newServer(listen, mux)

	workerDone := make(chan struct{})
	abort := make(chan struct{})
	go func() {
		defer close(workerDone)
		work(in.queue, abort, b)
	}()

	stop := make(chan os.Signal, 1)
	signal.Notify(stop, syscall.SIGTERM, syscall.SIGINT)
	serveErr := make(chan error, 1)
	go func() { serveErr <- srv.ListenAndServe() }()
	log.Printf("linear-bridge: listening on %s", listen)

	select {
	case err := <-serveErr:
		log.Fatal(err)
	case <-stop:
	}
	ctx, cancel := context.WithTimeout(context.Background(), shutdownGrace)
	defer cancel()
	if err := srv.Shutdown(ctx); err != nil {
		log.Printf("linear-bridge: shutdown: %v", err)
		close(abort)
	} else {
		close(in.queue)
	}
	select {
	case <-workerDone:
	case <-ctx.Done():
		log.Print("linear-bridge: shutdown grace expired with events still queued")
	}
}

func work(queue <-chan event, abort <-chan struct{}, b *bridge) {
	for {
		var ev event
		select {
		case <-abort:
			return
		default:
		}
		select {
		case <-abort:
			return
		case e, ok := <-queue:
			if !ok {
				return
			}
			ev = e
		}
		ctx, cancel := context.WithTimeout(context.Background(), eventTimeout)
		if err := b.handle(ctx, ev); err != nil {
			log.Printf("linear-bridge: handle %s/%s: %v", ev.Type, ev.Action, err)
		}
		cancel()
	}
}

func mustEnv(name string) string {
	v := os.Getenv(name)
	if v == "" {
		log.Fatalf("linear-bridge: %s is not set", name)
	}
	return v
}

func newServer(addr string, h http.Handler) *http.Server {
	return &http.Server{
		Addr:              addr,
		Handler:           h,
		ReadHeaderTimeout: 10 * time.Second,
		ReadTimeout:       5 * time.Second,
		WriteTimeout:      10 * time.Second,
		IdleTimeout:       60 * time.Second,
	}
}
