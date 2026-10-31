#!/usr/bin/env bash
set -uo pipefail

fail=0
startup=(.bashrc .bash_profile .bash_login .bash_logout .profile .zshrc .zshenv .zprofile .zlogin .zlogout .inputrc)
export LITELLM_BASE_URL=http://gateway.invalid:4000
home=$(mktemp -d /var/tmp/home.XXXXXX) || exit 1
trap 'rm -rf "$home"' EXIT

expect() {
  if [[ $2 == "$3" ]]; then
    echo "ok   $1"
  else
    echo "FAIL $1: expected '$3', got '$2'"
    fail=1
  fi
}

seeded() {
  local f
  for f in .claude/settings.json .agents/skills .claude.json .codex/AGENTS.md .codex/config.toml \
    .omp/agent/config.yml .omp/agent/models.yml .omp/agent/mcp.json; do
    [[ -e $1/$f ]] || { echo "missing $f"; return; }
  done
  echo seeded
}

login() { HOME=$1 bash -lc true >/dev/null 2>&1; }
marker() { [[ -e $1/.agent-seeded ]] && echo present || echo absent; }

login "$home"
expect "a login shell seeds a writable HOME" "$(seeded "$home")" seeded
expect "a login shell writes the marker" "$(marker "$home")" present
expect "the system-managed Claude settings are root-owned" "$(stat -c '%U:%a' /etc/claude-code/managed-settings.json)" root:644
expect "the system-managed Claude MCP config is root-owned" "$(stat -c '%U:%a' /etc/claude-code/managed-mcp.json)" root:644
expect "the system-managed Claude settings reject runner writes" "$({ : >>/etc/claude-code/managed-settings.json; } 2>/dev/null && echo writable || echo protected)" protected
expect "the system-managed Claude MCP config rejects runner writes" "$({ : >>/etc/claude-code/managed-mcp.json; } 2>/dev/null && echo writable || echo protected)" protected
expect "the managed Claude MCP config uses only two servers" "$(jq -r '.mcpServers | keys | join(",")' /etc/claude-code/managed-mcp.json)" codebase-memory-mcp,litellm-tools
expect "the managed Claude MCP config uses the h-cloud gateway" "$(jq -r '.mcpServers["litellm-tools"].url' /etc/claude-code/managed-mcp.json)" http://litellm-proxy.ai.svc.cluster.local:4000/mcp/

rm "$home/.agent-seeded"
HOME=$home /opt/agent/bin/seed-agent-home >/dev/null 2>&1
expect "seed-agent-home re-runs over its own seed" "$?" 0
rm -f "$home/.agent-seeded"
login "$home"
expect "a login shell after the marker is deleted writes it again" "$(marker "$home")" present
expect "the re-run leaves a complete seed" "$(seeded "$home")" seeded
for repo in "$home"/.claude/plugins/marketplaces/*/.git; do
  name=${repo%/.git}
  expect "the re-run leaves the ${name##*/} marketplace a valid repository" \
    "$(git --git-dir="$repo" fsck --connectivity-only >/dev/null 2>&1 && echo valid || echo broken)" valid
done

ro=${SEED_RO_HOME:-/home/omnigent}
mounted=1
for f in "${startup[@]}"; do
  if ! [[ -f $ro/$f ]] || { : >>"$ro/$f"; } 2>/dev/null; then
    mounted=0
  fi
done
if ((mounted)); then
  before=$(cd "$ro" && sha256sum "${startup[@]}")
  login "$ro"
  expect "a login shell writes the marker with read-only startup files" "$(marker "$ro")" present
  expect "the rest of HOME is seeded with read-only startup files" "$(seeded "$ro")" seeded
  expect "the read-only startup files are unchanged" "$(cd "$ro" && sha256sum "${startup[@]}")" "$before"
else
  echo "SKIP $ro has no read-only mounts of the ${#startup[@]} shell startup files"
fi

exit "$fail"
