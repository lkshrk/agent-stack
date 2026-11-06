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
registered=$(/opt/venv/bin/python3 -I -c 'import json, yaml; print(json.dumps(list(yaml.safe_load(open("/opt/agent-stack/public-contributions.yaml"))["repositories"]), separators=(",", ":")))')
expect "registry lists omnigent" "$([[ $registered == *'"omnigent"'* ]] && echo yes || echo no)" yes
expect "list emits only registered names" "$(agent-stack-contribute list)" "$registered"
workdir=$(mktemp -d)
printf 'version: invalid\n' >"$workdir/public-contributions.yaml"
expect "list ignores current-directory registry" "$(cd "$workdir" && agent-stack-contribute list)" "$registered"
rm -rf "$workdir"
agent-stack-contribute publish >/dev/null 2>&1
expect "unknown command fails" "$?" 2
agent-stack-contribute prepare unknown >/dev/null 2>&1
expect "unknown repository fails" "$?" 2
for removed in "preflight omnigent" "validate omnigent web"; do
  # shellcheck disable=SC2086
  agent-stack-contribute $removed >/dev/null 2>&1
  expect "$removed is no longer a subcommand" "$?" 2
done

tree=/home/omnigent/work/omnigent
if [[ -e $tree ]]; then
  echo "FAIL $tree already exists"
  exit 1
fi
mkdir -p "$tree"
: >"$tree/notes.txt"
agent-stack-contribute prepare omnigent >/dev/null 2>&1
expect "prepare refuses a path that is not a checkout" "$?" 1
rm -rf "$tree"
git init -q -b main "$tree"
git -C "$tree" remote add origin https://github.com/omnigent-ai/omnigent.git
out=$(agent-stack-contribute prepare omnigent)
expect "prepare reuses a checkout of the upstream" "$(jq -r .state <<<"$out")" existing
expect "prepare adds the fork remote" "$(git -C "$tree" remote get-url fork)" https://github.com/lkshrk/omnigent.git
git -C "$tree" remote set-url fork https://github.com/someone/omnigent.git
agent-stack-contribute prepare omnigent >/dev/null 2>&1
expect "prepare refuses a foreign fork remote" "$?" 1
rm -rf "$tree"

exit "$fail"
