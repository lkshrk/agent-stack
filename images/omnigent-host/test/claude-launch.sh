#!/usr/bin/env bash
set -uo pipefail

fail=0
export LITELLM_BASE_URL=http://gateway.invalid:4000
home=$(mktemp -d /var/tmp/claude-home.XXXXXX) || exit 1
work=$(mktemp -d /var/tmp/claude-launch.XXXXXX) || exit 1
trap 'rm -rf "$home" "$work"' EXIT

expect() {
  if [[ $2 == "$3" ]]; then
    echo "ok   $1"
  else
    echo "FAIL $1: expected '$3', got '$2'"
    fail=1
  fi
}

HOME=$home /opt/agent/bin/seed-agent-home

expect "the managed rules are root-owned" "$(stat -c '%U:%a' /etc/claude-code/CLAUDE.md 2>&1)" root:644
expect "the managed rules carry the runner rules" "$(head -1 /etc/claude-code/CLAUDE.md 2>&1)" "# Runner rules"
for f in .codex/AGENTS.md .omp/agent/AGENTS.md; do
  rules=$(grep -oE '[~/][^ :]*CLAUDE\.md' "$home/$f" | head -1)
  expect "$f points at runner rules that exist" "$(test -s "${rules/#\~/$home}" && echo exists || echo "missing ${rules:-reference}")" exists
done
expect "the managed settings keep the deny list" \
  "$(jq -r '.permissions.deny | length' /etc/claude-code/managed-settings.json)" 4
expect "the sandbox workspace is a working directory, so file tools never prompt for sibling worktrees" \
  "$(jq -c '.permissions.additionalDirectories' /etc/claude-code/managed-settings.json)" '["/home/omnigent/workspace"]'
expect "the managed settings leave Omnigent's hook settings in force" \
  "$(jq -r '[.allowManagedHooksOnly, .disableAllHooks, .permissions.defaultMode] | map(select(. != null)) | length' /etc/claude-code/managed-settings.json)" 0
expect "a seeded HOME enables the runner plugins" \
  "$(jq -r '.enabledPlugins["runner@agent-stack"]' "$home/.claude/settings.json" 2>&1)" true
expect "a seeded HOME has the gateway MCP servers" \
  "$(jq -r '.mcpServers | keys | join(",")' "$home/.claude.json" 2>&1)" codebase-memory-mcp,litellm-tools
expect "the gateway MCP URL follows LITELLM_BASE_URL" \
  "$(jq -r '.mcpServers["litellm-tools"].url' "$home/.claude.json" 2>&1)" "$LITELLM_BASE_URL/mcp/"

# Mirrors how Omnigent's claude-native bridge launches Claude: its MCP server via --mcp-config, its hooks via --settings.
jq -n --arg dir "$work" '{hooks: {
  SessionStart: [{hooks: [{type: "command", command: "touch \($dir)/session-start"}]}],
  UserPromptSubmit: [{hooks: [{type: "command", command: "touch \($dir)/prompt-submit"}]}]
}}' >"$work/settings.json"
for mode in default auto; do
  rm -f "$work/session-start" "$work/prompt-submit"
  out=$(cd "$work" && timeout 30s env HOME="$home" ANTHROPIC_API_KEY=invalid ANTHROPIC_BASE_URL=http://127.0.0.1:9 \
    claude --print "launch check" --permission-mode "$mode" --output-format stream-json --verbose \
    --mcp-config '{"mcpServers":{"omnigent":{"type":"stdio","command":"true"}}}' \
    --settings "$work/settings.json" 2>&1)
  expect "[$mode] Claude accepts a launcher MCP config" "$(grep -c 'enterprise MCP config' <<<"$out")" 0
  expect "[$mode] the session starts in the requested permission mode" \
    "$(jq -Rr 'fromjson? | select(.type == "system" and .subtype == "init") | .permissionMode' <<<"$out" | head -1)" "$mode"
  expect "[$mode] the launcher's SessionStart hook runs" "$(test -e "$work/session-start" && echo ran || echo blocked)" ran
  expect "[$mode] the launcher's UserPromptSubmit hook runs" "$(test -e "$work/prompt-submit" && echo ran || echo blocked)" ran
done

exit "$fail"
