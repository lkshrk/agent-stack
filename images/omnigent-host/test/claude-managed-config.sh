#!/usr/bin/env bash
set -euo pipefail

fail=0
home=$(mktemp -d /var/tmp/claude-home.XXXXXX)
clean=$(mktemp -d /var/tmp/claude-clean.XXXXXX)
trap 'rm -rf "$home" "$clean"' EXIT

expect() {
  if [[ $2 == "$3" ]]; then
    echo "ok   $1"
  else
    echo "FAIL $1: expected '$3', got '$2'"
    fail=1
  fi
}

mkdir -p "$home/.claude"
printf '%s\n' '{"hooks":{"UserPromptSubmit":[{"hooks":[{"type":"command","command":"touch /var/tmp/hostile-hook"}]}]}}' >"$home/.claude/settings.json"
printf '%s\n' '{"mcpServers":{"hostile":{"type":"stdio","command":"false"}}}' >"$home/.claude.json"
printf '%s\n' '{"mcpServers":{"flag":{"type":"stdio","command":"false"}}}' >/var/tmp/hostile-mcp.json

HOME=$home /opt/agent/bin/seed-agent-home
HOME=$clean /opt/agent/bin/seed-agent-home

expect "a clean HOME has no seeded Claude settings" "$(test -e "$clean/.claude/settings.json" && echo present || echo absent)" absent
expect "a clean HOME has no seeded global config" "$(test -e "$clean/.claude.json" && echo present || echo absent)" absent
expect "the hostile settings file is not replaced" "$(grep -F 'hostile-hook' "$home/.claude/settings.json" >/dev/null && echo unchanged || echo replaced)" unchanged
expect "the hostile global config is not replaced" "$(grep -F '"hostile"' "$home/.claude.json" >/dev/null && echo unchanged || echo replaced)" unchanged

servers=$(HOME=$home claude mcp list 2>&1 | sed -nE 's/^([a-z0-9-]+):.*/\1/p' | sort | tr '\n' ',')
expect "managed MCP servers exclude hostile HOME config" "$servers" codebase-memory-mcp,litellm-tools,
rm -f /var/tmp/hostile-hook
timeout 10s env HOME=$home ANTHROPIC_API_KEY=invalid ANTHROPIC_BASE_URL=http://127.0.0.1:9 \
  claude --print "managed hook check" >/dev/null 2>&1 || true
expect "managed settings prevent the hostile HOME hook" "$(test -e /var/tmp/hostile-hook && echo executed || echo blocked)" blocked
mkdir -p "$home/.claude/runtime"
printf state >"$home/.claude/runtime/state"
expect "Claude runtime state remains writable" "$(cat "$home/.claude/runtime/state")" state

exit "$fail"
