#!/usr/bin/env bash
set -uo pipefail

fail=0

expect() {
  if [[ $2 == "$3" ]]; then
    echo "ok   $1"
  else
    echo "FAIL $1: expected '$3', got '$2'"
    fail=1
  fi
}

expect "helper is on PATH" "$(command -v agent-stack-contribute 2>/dev/null || true)" /opt/agent/bin/agent-stack-contribute
expect "registry is root-owned and readable" "$(stat -c '%U:%a' /opt/agent-stack/public-contributions.yaml)" root:644
expect "registry rejects runner writes" "$({ : >>/opt/agent-stack/public-contributions.yaml; } 2>/dev/null && echo writable || echo protected)" protected
expect "list emits only registered names" "$(agent-stack-contribute list)" '["omnigent"]'
workdir=$(mktemp -d)
printf 'version: invalid\n' >"$workdir/public-contributions.yaml"
expect "list ignores current-directory registry" "$(cd "$workdir" && agent-stack-contribute list)" '["omnigent"]'
rm -rf "$workdir"
agent-stack-contribute publish >/dev/null 2>&1
expect "unknown command fails" "$?" 2
agent-stack-contribute prepare unknown >/dev/null 2>&1
expect "unknown repository fails" "$?" 2

exit "$fail"
