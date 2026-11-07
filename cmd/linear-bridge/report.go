package main

import (
	"fmt"
	"regexp"
	"strings"
	"unicode"
	"unicode/utf8"
)

const (
	runnerAssignee    = "omnigent-runner"
	reportItemChars   = 160
	reportReason      = 600
	reportMaxItems    = 15
	reportMaxChars    = 2000
	maxReportAttempts = 10
)

var (
	reportKinds = map[string]bool{"completed": true, "blocked": true, "gave_up": true, "block_loop_detected": true}
	pullURL     = regexp.MustCompile(`^https://github\.com/[A-Za-z0-9-]+/[A-Za-z0-9._-]+/pull/([1-9][0-9]*)$`)
	commitSHA   = regexp.MustCompile(`^[0-9a-f]{7,40}$`)
	invalidPR   = "(invalid PR)"
)

func runnerReport(c Card) (string, bool) {
	if c.Link != nil || c.Assignee != runnerAssignee {
		return "", false
	}
	bundle, where := "runner", "runner"
	if c.Run != nil && c.Run.Bundle != "" {
		bundle = c.Run.Bundle
		where = clean(bundle, 40)
		if c.Run.Repo != "" {
			where += " on " + clean(c.Run.Repo, 100)
		}
	}
	id := clean(c.ID, 40)
	switch {
	case c.Status == "done":
		result, _ := c.Metadata["result"].(map[string]any)
		if bundle == "infra" && result != nil {
			return capReport(infraReport(where, result, c.Metadata["post_run"])), true
		}
		return capReport(genericReport(where, id, c)), true
	case liveBlocked(c):
		marker := "🟡"
		if strings.HasPrefix(c.BlockReason, postRunPrefix) {
			marker = "🔴"
		}
		reason := clean(c.BlockReason, reportReason)
		if reason == "" {
			reason = "no reason given"
		}
		if c.Outcome == "gave_up" {
			reason = "gave up: " + reason
		}
		return capReport([]string{fmt.Sprintf("%s %s blocked (card %s)", marker, where, id), reason}), true
	}
	return "", false
}

func genericReport(where, id string, c Card) []string {
	result, _ := c.Metadata["result"].(map[string]any)
	status := clean(metaString(result, "status"), 40)
	if status == "" {
		status = "done"
	}
	lines := []string{fmt.Sprintf("%s %s %s (card %s)", doneMarker(status), where, status, id)}
	if s := clean(c.Summary, reportItemChars); s != "" {
		lines = append(lines, s)
	}
	if gaps, _ := c.Metadata["gaps"].([]any); len(gaps) > 0 {
		lines = append(lines, fmt.Sprintf("%d gaps", len(gaps)))
	}
	if url := metaString(c.Metadata, "pr_url"); url != "" {
		lines = append(lines, prLink(url))
	}
	return lines
}

func doneMarker(status string) string {
	if status == "DONE" || status == "done" {
		return "✅"
	}
	return "🟡"
}

func infraReport(where string, result map[string]any, postRun any) []string {
	lists := map[string][]map[string]any{}
	for _, key := range []string{"merged", "skipped", "closed", "failed"} {
		items, _ := result[key].([]any)
		for _, it := range items {
			if m, ok := it.(map[string]any); ok {
				lists[key] = append(lists[key], m)
			}
		}
	}
	status := clean(metaString(result, "status"), 40)
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
			text := clean(metaString(e, "summary"), reportItemChars)
			if rca := clean(metaString(e, "rca"), reportItemChars); rca != "" {
				text += "; RCA: " + rca
			}
			line := prLabel(e) + ": " + text
			if url := metaString(e, "pr"); validPR(url) {
				line += " " + url
			}
			out = append(out, line)
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
		notes = append(notes, clean(anyText(n), reportItemChars))
	}
	section("Post-run notes", notes)
	if vault, _ := result["vault"].(map[string]any); vault != nil {
		if sha, _ := vault["commit"].(string); commitSHA.MatchString(sha) {
			lines = append(lines, "Vault: "+sha[:7])
		}
	}
	return lines
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
				if s := clean(anyText(f), 120); !seen[s] {
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
	if v := clean(metaString(e, "version"), 40); v != "" {
		label += " " + v
	}
	return label
}

func validPR(url string) bool { return pullURL.MatchString(url) }

func prNumber(url string) string {
	if m := pullURL.FindStringSubmatch(url); m != nil {
		return "#" + m[1]
	}
	return invalidPR
}

func prLink(url string) string {
	if validPR(url) {
		return url
	}
	return invalidPR
}

func anyText(v any) string {
	if s, ok := v.(string); ok {
		return s
	}
	return metaString(map[string]any{"v": v}, "v")
}

// clean is the only way a card field reaches a report: one line, no control or format runes, at most n bytes.
func clean(s string, n int) string {
	s = strings.Map(func(r rune) rune {
		switch {
		case unicode.IsSpace(r):
			return ' '
		case unicode.IsControl(r), unicode.Is(unicode.Cf, r), r == utf8.RuneError:
			return -1
		}
		return r
	}, s)
	s = strings.Join(strings.Fields(s), " ")
	if len(s) <= n {
		return s
	}
	return truncate(s, n-len("…")) + "…"
}

func capReport(lines []string) string {
	const tail = len("… 99999 more lines") + 1
	kept, runes := 0, 0
	for _, line := range lines {
		n := utf8.RuneCountInString(line) + 1
		if runes+n > reportMaxChars-tail {
			break
		}
		kept, runes = kept+1, runes+n
	}
	out := strings.Join(lines[:kept], "\n")
	if kept < len(lines) {
		out += fmt.Sprintf("\n… %d more lines", len(lines)-kept)
	}
	return out
}
