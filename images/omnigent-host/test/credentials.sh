#!/usr/bin/env bash
set -uo pipefail

bin=${AGENT_BIN:-/opt/agent/bin}
fail=0
dir=$(mktemp -d)
trap 'rm -rf "$dir"' EXIT
printf code-token >"$dir/code"
printf knowledge-token >"$dir/knowledge"
printf omnigent-token >"$dir/omnigent"
export GH_TOKEN_FILE=$dir/code KNOWLEDGE_TOKEN_FILE=$dir/knowledge OMNIGENT_TOKEN_FILE=$dir/omnigent
unset GH_TOKEN GIT_TOKEN

expect() {
  if [[ $2 == "$3" ]]; then
    echo "ok   $1"
  else
    echo "FAIL $1: expected '$3', got '$2'"
    fail=1
  fi
}

password() {
  printf 'protocol=https\nhost=github.com\npath=%s\n\n' "$1" | "$bin/git-credential-agent" get | sed -n 's/^password=//p'
}

expect "git: other repositories use the code token" "$(password lkshrk/agent-stack.git)" code-token
expect "git: knowledge uses its token" "$(password lkshrk/knowledge.git)" knowledge-token
expect "git: the omnigent fork uses its token" "$(password lkshrk/omnigent.git)" omnigent-token
expect "git: the omnigent fork without .git uses its token" "$(password lkshrk/omnigent)" omnigent-token
expect "git: the upstream uses the code token" "$(password omnigent-ai/omnigent.git)" code-token
expect "git: a lookalike uses the code token" "$(password lkshrk/omnigent-other.git)" code-token
mv "$dir/omnigent" "$dir/omnigent.off"
expect "git: a missing fork token never falls back" "$(GIT_TOKEN=fallback password lkshrk/omnigent.git)" ""
mv "$dir/omnigent.off" "$dir/omnigent"

if [[ $(id -u) == 0 && -x /usr/local/bin/gh ]]; then
  mv /usr/local/bin/gh /usr/local/bin/gh.real
  # shellcheck disable=SC2016
  printf '#!/bin/sh\nprintf %%s "$GH_TOKEN"\n' >/usr/local/bin/gh
  chmod 755 /usr/local/bin/gh
  expect "gh: other repositories use the code token" "$("$bin/gh" pr list)" code-token
  expect "gh: --repo fork uses the fork token" "$("$bin/gh" pr create --repo lkshrk/omnigent --title t)" omnigent-token
  expect "gh: --repo=fork uses the fork token" "$("$bin/gh" pr view 1 --repo=lkshrk/omnigent)" omnigent-token
  expect "gh: -R fork uses the fork token" "$("$bin/gh" issue create -R lkshrk/omnigent --title t)" omnigent-token
  expect "gh: the upstream uses the code token" "$("$bin/gh" pr view 1 --repo omnigent-ai/omnigent)" code-token
  expect "gh: a fork-named body uses the code token" "$("$bin/gh" pr create --body lkshrk/omnigent --repo lkshrk/agent-stack)" code-token
  expect "gh: an explicit GH_TOKEN wins" "$(GH_TOKEN=preset "$bin/gh" pr list --repo lkshrk/omnigent)" preset
  mv /usr/local/bin/gh.real /usr/local/bin/gh
else
  echo "skip gh wrapper checks (need root to stand in for /usr/local/bin/gh)"
fi

exit "$fail"
