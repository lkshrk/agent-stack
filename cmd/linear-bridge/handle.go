package main

import (
	"context"
	"encoding/json"
	"fmt"
	"log"
	"unicode/utf8"
)

type bridge struct {
	linear    linearAPI
	notify    notifier
	appUserID string
}

func (b *bridge) handle(ctx context.Context, ev event) error {
	if ev.Type == "Issue" && ev.Action == "create" {
		return b.auditIssue(ctx, ev)
	}
	return nil
}

func (b *bridge) auditIssue(ctx context.Context, ev event) error {
	var d struct {
		ID         string `json:"id"`
		Identifier string `json:"identifier"`
		CreatorID  string `json:"creatorId"`
		ParentID   string `json:"parentId"`
	}
	if err := json.Unmarshal(ev.Data, &d); err != nil {
		return fmt.Errorf("issue data: %w", err)
	}
	if b.appUserID == "" || d.CreatorID != b.appUserID || d.ParentID != "" {
		return nil
	}
	msg := "linear-bridge: archived top-level issue " + d.Identifier + " created by the Hermes app"
	archiveErr := b.linear.ArchiveIssue(ctx, d.ID)
	if archiveErr != nil {
		msg = fmt.Sprintf("linear-bridge: failed to archive top-level issue %s created by the Hermes app: %v", d.Identifier, archiveErr)
	}
	if err := b.notify.Notify(ctx, msg); err != nil {
		log.Printf("linear-bridge: notify failed: %v", err)
		if archiveErr == nil {
			return err
		}
	}
	return archiveErr
}

func shortErr(err error) string { return truncate(err.Error(), 200) }

func truncate(s string, n int) string {
	if len(s) <= n {
		return s
	}
	for n > 0 && !utf8.RuneStart(s[n]) {
		n--
	}
	return s[:n]
}
