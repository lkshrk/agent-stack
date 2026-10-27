#!/usr/bin/env bash
set -uo pipefail

fail=0
export HOME
HOME=$(mktemp -d /var/tmp/home.XXXXXX) || exit 1
work=$(mktemp -d /var/tmp/work.XXXXXX) || exit 1
trap 'rm -rf "$HOME" "$work"' EXIT

expect() {
  if [[ $2 == "$3" ]]; then
    echo "ok   $1"
  else
    echo "FAIL $1: expected '$3', got '$2'"
    fail=1
  fi
}

expect "safe.bareRepository is explicit" "$(git config --includes --system safe.bareRepository)" explicit
expect "core.fsmonitor is off" "$(git config --includes --system core.fsmonitor)" false
expect "protocol.file.allow is user" "$(git config --includes --system protocol.file.allow)" user

git init -q "$work/src"
echo a >"$work/src/file"
git -C "$work/src" add file && git -C "$work/src" commit -qm a
echo b >"$work/src/file"
git -C "$work/src" commit -qam b

mkdir "$work/checkout"
git clone -q --bare "$work/src" "$work/checkout/planted"
printf '#!/bin/sh\ntouch %s/pwned\n' "$work" >"$work/ext.sh"
chmod +x "$work/ext.sh"
git --git-dir="$work/checkout/planted" config diff.external "$work/ext.sh"

(cd "$work/checkout" && git -C planted diff HEAD~1 HEAD) >/dev/null 2>&1
(cd "$work/checkout/planted" && git diff HEAD~1 HEAD) >/dev/null 2>&1
if [[ -e $work/pwned ]]; then
  echo "FAIL git ran diff.external from a planted bare repository"
  fail=1
else
  echo "ok   a planted bare repository is refused and its diff.external does not run"
fi

expect "an explicit --git-dir still reads a bare repository" \
  "$(git --git-dir="$work/checkout/planted" log -1 --format=%s HEAD~1)" a
expect "a normal repository still diffs" "$(git -C "$work/src" diff HEAD~1 HEAD --stat | tail -1 | tr -s ' ')" \
  " 1 file changed, 1 insertion(+), 1 deletion(-)"
git -C "$work/src" worktree add -q "$work/wt" -b feat/x 2>/dev/null
expect "git worktree add still works" "$(git -C "$work/wt" rev-parse --abbrev-ref HEAD)" feat/x
git clone -q "$work/src" "$work/clone" 2>/dev/null
expect "a local clone still works" "$(git -C "$work/clone" log -1 --format=%s)" b

exit "$fail"
