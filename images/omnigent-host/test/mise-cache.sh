#!/usr/bin/env bash
set -uo pipefail

if ((EUID != 0)); then
  echo "SKIP the mise cache check needs root to age the baked cache entries"
  exit 0
fi

fail=0
home=$(mktemp -d)
chmod 1777 "$home"
trap 'rm -rf "$home"' EXIT

# rewrite-timestamp on export leaves cache entries no newer than their installs, which mise treats as stale.
for dir in /opt/mise/cache "${MISE_CACHE_DIR:-}"; do
  [[ -n $dir && -d $dir ]] && find "$dir" -type f -exec touch -h -d 2000-01-01 {} +
done

for cmd in "go version" "gopls version" "gitleaks version" "pyright --version" "uv --version"; do
  read -ra argv <<<"$cmd"
  if ! err=$(cd "$home" && HOME=$home setpriv --reuid=1000660000 --regid=1000660000 --clear-groups "${argv[@]}" 2>&1 >/dev/null); then
    echo "FAIL $cmd exits non-zero as uid 1000660000:"
    head -5 <<<"$err"
    fail=1
  elif grep -q 'mise WARN' <<<"$err"; then
    echo "FAIL $cmd prints $(grep -c 'mise WARN' <<<"$err") mise warnings as uid 1000660000:"
    grep -m3 'mise WARN' <<<"$err"
    fail=1
  else
    echo "ok   $cmd prints no mise warnings as uid 1000660000 with stale baked cache entries"
  fi
done

exit "$fail"
