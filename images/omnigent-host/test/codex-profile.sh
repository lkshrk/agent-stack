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
  FOO_TOKEN=t LITELLM_API_KEY=k GH_TOKEN=g GITHUB_TOKEN=g MY_SECRET=s KEEP_ME=kept \
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
sandbox "touch $HOME/x" >/dev/null
check "HOME writes don't reach the real home" "$([[ -e $HOME/x ]] && echo written || echo kept)" kept
check "/tmp is writable" "$(sandbox 'f=$(mktemp /tmp/probe.XXXXXX) && rm "$f" && echo ok')" ok
check "tool caches point into /tmp" "$(sandbox 'echo $UV_CACHE_DIR')" /tmp/cache/uv
echo source >"$work/a.py"
echo dotfile >"$HOME/.gitconfig"
check "the checkout is readable" "$(sandbox "cat $work/a.py")" source
check "home dotfiles are unreadable" "$(sandbox "cat $HOME/.gitconfig >/dev/null 2>&1; echo \$?")" 1
check "tool binaries run" "$(sandbox 'gitleaks version >/dev/null && uv --version >/dev/null && echo ok')" ok
check "secret env vars are dropped" "$(sandbox 'env | grep -E "TOKEN|_KEY|SECRET|LITELLM_|GH_" || echo none')" none
check "other env vars are kept" "$(sandbox 'echo $KEEP_ME')" kept
secret=$(find /agent-secrets -type f -readable 2>/dev/null | head -1)
if [[ -n $secret ]]; then
  check "/agent-secrets is unreadable" "$(sandbox "cat $secret >/dev/null 2>&1; echo \$?")" 1
else
  echo "SKIP no readable file under /agent-secrets to probe"
fi
check "the network is reachable" "$(sandbox 'curl -sS -o /dev/null -w %{http_code} https://pypi.org/simple/')" 200

exit "$fail"
