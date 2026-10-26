// Command linear-bridge receives signed Linear webhooks and queues them for processing.
package main

import (
	"log"
	"net/http"
	"os"
	"time"
)

const queueSize = 256

func main() {
	secret := os.Getenv("LINEAR_WEBHOOK_SECRET")
	if secret == "" {
		log.Fatal("linear-bridge: LINEAR_WEBHOOK_SECRET is not set")
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
	srv := &http.Server{Addr: listen, Handler: mux, ReadHeaderTimeout: 10 * time.Second}
	log.Printf("linear-bridge: listening on %s", listen)
	log.Fatal(srv.ListenAndServe())
}
