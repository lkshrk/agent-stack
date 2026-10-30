#!/usr/bin/env bash
set -uo pipefail

fail=0
dir=$(mktemp -d)
trap 'rm -rf "$dir"' EXIT

mkdir "$dir/pin20"
printf '[tools]\nnode = "20.11.0"\n' >"$dir/pin20/mise.toml"

for tool in codex pyright bash-language-server typescript-language-server context-mode; do
  if (cd "$dir/pin20" && "$tool" --version >/dev/null 2>&1); then
    echo "ok   $tool runs in a project pinning a missing node"
  else
    echo "FAIL $tool fails in a project pinning a missing node"
    fail=1
  fi
done

want=$(/usr/local/bin/node --version)
got=$(cd "$dir/pin20" && node --version 2>&1)
if [[ $got == "$want" ]]; then
  echo "ok   node in a project pinning a missing node falls back to $got"
else
  echo "FAIL node in a project pinning a missing node: expected $want, got $got"
  fail=1
fi

exit "$fail"
