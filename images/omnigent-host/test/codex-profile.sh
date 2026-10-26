#!/usr/bin/env bash
set -uo pipefail

fail=0
export HOME
HOME=$(mktemp -d /var/tmp/home.XXXXXX) || exit 1
work=$(mktemp -d /var/tmp/work.XXXXXX) || exit 1
trap 'rm -rf "$HOME" "$work"' EXIT

/opt/agent/bin/seed-agent-home 2>/dev/null
if [[ -r $HOME/.codex/security-review.config.toml ]]; then
  echo "ok   seed-agent-home installs the security-review Codex profile"
else
  echo "FAIL seed-agent-home did not install the security-review Codex profile"
  exit 1
fi

sandbox() {
  codex sandbox -p security-review -P security-review -C "$work" -- sh -c "$1" 2>&1
}

if ! err=$(sandbox true); then
  echo "SKIP the Codex Linux sandbox cannot start here: $(tail -1 <<<"$err")"
  exit "$fail"
fi

check() {
  if [[ $2 == "$3" ]]; then
    echo "ok   $1"
  else
    echo "FAIL $1: expected '$3', got '$2'"
    fail=1
  fi
}

check "the workspace is read-only" "$(sandbox "touch $work/x >/dev/null 2>&1; echo \$?")" 1
check "HOME is read-only" "$(sandbox "touch $HOME/x >/dev/null 2>&1; echo \$?")" 1
check "/tmp is writable" "$(sandbox 'f=$(mktemp /tmp/probe.XXXXXX) && rm "$f" && echo ok')" ok
check "tool caches point into /tmp" "$(sandbox 'echo $UV_CACHE_DIR')" /tmp/cache/uv
check "the network is reachable" "$(sandbox 'curl -sS -o /dev/null -w %{http_code} https://pypi.org/simple/')" 200

exit "$fail"
