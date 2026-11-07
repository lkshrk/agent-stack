package main

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"strings"
)

type notifier interface {
	Notify(ctx context.Context, text string) error
}

type signalNotifier struct {
	base, number, recipient string
	http                    *http.Client
}

func (s *signalNotifier) Notify(ctx context.Context, text string) error {
	payload, err := json.Marshal(map[string]any{
		"number":     s.number,
		"recipients": []string{s.recipient},
		"message":    text,
	})
	if err != nil {
		return err
	}
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, strings.TrimRight(s.base, "/")+"/v2/send", bytes.NewReader(payload))
	if err != nil {
		return err
	}
	req.Header.Set("Content-Type", "application/json")
	resp, err := s.http.Do(req)
	if err != nil {
		return err
	}
	defer func() { _ = resp.Body.Close() }()
	_, _ = io.Copy(io.Discard, io.LimitReader(resp.Body, maxRespBytes))
	if resp.StatusCode/100 != 2 {
		return fmt.Errorf("signal send returned %d", resp.StatusCode)
	}
	return nil
}
