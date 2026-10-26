#!/usr/bin/env bash
set -uo pipefail

fail=0
dir=$(mktemp -d)
trap 'rm -rf "$dir"' EXIT

mkdir "$dir/pin20" "$dir/pin24"
printf '[tools]\nnode = "20.11.0"\n' >"$dir/pin20/mise.toml"
printf '[tools]\nnode = "24.21.0"\n' >"$dir/pin24/mise.toml"

for tool in codex context-mode pyright bash-language-server typescript-language-server; do
  if (cd "$dir/pin20" && "$tool" --version >/dev/null 2>&1); then
    echo "ok   $tool runs in a project pinning a missing node"
  else
    echo "FAIL $tool fails in a project pinning a missing node"
    fail=1
  fi
done

check_node() {
  local got
  got=$(cd "$1" && node --version 2>&1)
  if [[ $got == "$2" ]]; then
    echo "ok   node in $3 is $got"
  else
    echo "FAIL node in $3: expected $2, got $got"
    fail=1
  fi
}
check_node "$dir/pin24" v24.21.0 "a project pinning 24.21.0"
check_node "$dir" "$(/usr/local/bin/node --version)" "an unpinned dir"

exit "$fail"
