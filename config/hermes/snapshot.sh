#!/usr/bin/env bash
# Nightly audit trail of what Hermes learned: commits skills/ and memories/ into a local repo on the PVC.
set -euo pipefail

home=${HERMES_HOME:-/opt/data}
repo="$home/.learning-history"

snapshot() {
  mkdir -p "$repo"
  [ -d "$repo/.git" ] || git -C "$repo" init -q -b main
  for dir in skills memories; do
    rm -rf "${repo:?}/$dir"
    if [ -d "$home/$dir" ]; then cp -a "$home/$dir" "$repo/$dir"; else mkdir -p "$repo/$dir"; fi
  done
  git -C "$repo" add -A
  git -C "$repo" diff --cached --quiet || git -C "$repo" commit -q -m "snapshot $(date -u +%Y-%m-%dT%H:%MZ)"
}

while true; do
  snapshot || echo "snapshot failed" >&2
  sleep "${SNAPSHOT_INTERVAL_SECONDS:-86400}"
done
