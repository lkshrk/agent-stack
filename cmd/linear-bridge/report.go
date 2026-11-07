package main

import (
	"fmt"
	"regexp"
	"strings"
)

const (
	runnerAssignee  = "omnigent-runner"
	reportItemChars = 160
	reportReason    = 600
	reportMaxItems  = 15
)

var (
	reportKinds = map[string]bool{"completed": true, "blocked": true, "gave_up": true, "block_loop_detected": true}
	pullNumber  = regexp.MustCompile(`/pull/(\d+)$`)
)

func runnerReport(c Card) (string, bool) {
	if c.Link != nil || c.Assignee != runnerAssignee {
		return "", false
	}
	bundle, where := "runner", "runner"
	if c.Run != nil && c.Run.Bundle != "" {
		bundle, where = c.Run.Bundle, c.Run.Bundle
		if c.Run.Repo != "" {
			where += " on " + c.Run.Repo
		}
	}
	switch {
	case c.Status == "done":
		result, _ := c.Metadata["result"].(map[string]any)
		if bundle == "infra" && result != nil {
			return infraReport(where, result, c.Metadata["post_run"]), true
		}
		return genericReport(where, c), true
	case liveBlocked(c):
		marker := "🟡"
		if strings.HasPrefix(c.BlockReason, postRunPrefix) {
			marker = "🔴"
		}
		reason := c.BlockReason
		if reason == "" {
			reason = "no reason given"
		}
		if c.Outcome == "gave_up" {
			reason = "gave up: " + reason
		}
		return fmt.Sprintf("%s %s blocked (card %s)\n%s", marker, where, c.ID, clip(reason, reportReason)), true
	}
	return "", false
}

func genericReport(where string, c Card) string {
	result, _ := c.Metadata["result"].(map[string]any)
	status := metaString(result, "status")
	if status == "" {
		status = "done"
	}
	lines := []string{fmt.Sprintf("%s %s %s (card %s)", doneMarker(status), where, status, c.ID)}
	if c.Summary != "" {
		lines = append(lines, clip(c.Summary, reportItemChars))
	}
	if gaps, _ := c.Metadata["gaps"].([]any); len(gaps) > 0 {
		lines = append(lines, fmt.Sprintf("%d gaps", len(gaps)))
	}
	if pr := metaString(c.Metadata, "pr_url"); pr != "" {
		lines = append(lines, pr)
	}
	return strings.Join(lines, "\n")
}

func doneMarker(status string) string {
	if status == "DONE" || status == "done" {
		return "✅"
	}
	return "🟡"
}

func infraReport(where string, result map[string]any, postRun any) string {
	lists := map[string][]map[string]any{}
	for _, key := range []string{"merged", "skipped", "closed", "failed"} {
		items, _ := result[key].([]any)
		for _, it := range items {
			if m, ok := it.(map[string]any); ok {
				lists[key] = append(lists[key], m)
			}
		}
	}
	status := metaString(result, "status")
	lines := []string{fmt.Sprintf("%s %s %s: %d merged, %d skipped, %d closed, %d failed", doneMarker(status), where,
		strings.ReplaceAll(strings.ToLower(status), "_", " "), len(lists["merged"]), len(lists["skipped"]), len(lists["closed"]), len(lists["failed"]))}
	section := func(title string, entries []string) {
		if len(entries) == 0 {
			return
		}
		lines = append(lines, title+":")
		for i, e := range entries {
			if i == reportMaxItems {
				lines = append(lines, fmt.Sprintf("• … %d more", len(entries)-reportMaxItems))
				break
			}
			lines = append(lines, "• "+e)
		}
	}
	pr := func(key string) []string {
		var out []string
		for _, e := range lists[key] {
			text := clip(metaString(e, "summary"), reportItemChars)
			if rca := metaString(e, "rca"); rca != "" {
				text += "; RCA: " + clip(rca, reportItemChars)
			}
			out = append(out, strings.TrimSpace(prLabel(e)+": "+text+" "+metaString(e, "pr")))
		}
		return out
	}
	post, _ := postRun.(map[string]any)
	section("Merged", pr("merged"))
	section("Agent changes", agentChanges(post))
	section("Skipped, for you", pr("skipped"))
	section("Closed", pr("closed"))
	section("Failed", pr("failed"))
	var notes []string
	items, _ := post["notes"].([]any)
	for _, n := range items {
		notes = append(notes, clip(fmt.Sprint(n), reportItemChars))
	}
	section("Post-run notes", notes)
	if vault, _ := result["vault"].(map[string]any); vault != nil {
		if sha := metaString(vault, "commit"); len(sha) >= 7 {
			lines = append(lines, "Vault: "+sha[:7])
		}
	}
	return strings.Join(lines, "\n")
}

func agentChanges(post map[string]any) []string {
	var out []string
	landed, _ := post["landed"].([]any)
	for _, it := range landed {
		l, _ := it.(map[string]any)
		pushes, _ := l["agent_pushes"].([]any)
		if len(pushes) == 0 {
			continue
		}
		var files []string
		seen := map[string]bool{}
		for _, p := range pushes {
			pm, _ := p.(map[string]any)
			fs, _ := pm["files"].([]any)
			for _, f := range fs {
				if s := fmt.Sprint(f); !seen[s] {
					seen[s] = true
					files = append(files, s)
				}
			}
		}
		shown := files
		if len(shown) > 3 {
			shown = append(shown[:3:3], fmt.Sprintf("+%d more", len(files)-3))
		}
		plural := "es"
		if len(pushes) == 1 {
			plural = ""
		}
		out = append(out, fmt.Sprintf("%s: %d push%s, %s", prNumber(metaString(l, "pr")), len(pushes), plural, strings.Join(shown, ", ")))
	}
	return out
}

func prLabel(e map[string]any) string {
	label := prNumber(metaString(e, "pr"))
	if v := metaString(e, "version"); v != "" {
		label += " " + v
	}
	return label
}

func prNumber(url string) string {
	if m := pullNumber.FindStringSubmatch(url); m != nil {
		return "#" + m[1]
	}
	return url
}

func clip(s string, n int) string {
	s = strings.Join(strings.Fields(s), " ")
	if len(s) <= n {
		return s
	}
	return truncate(s, n-len("…")) + "…"
}
