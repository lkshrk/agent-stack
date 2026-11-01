#!/usr/bin/env bash
set -uo pipefail

export HOME
HOME=$(mktemp -d /var/tmp/home.XXXXXX) || exit 1
trap 'rm -rf "$HOME"' EXIT

LITELLM_BASE_URL=http://gateway.invalid /opt/agent/bin/seed-agent-home 2>/dev/null
profile=$HOME/.codex/security-review.config.toml
if [[ ! -r $profile ]]; then
  echo "FAIL seed-agent-home did not install the security-review Codex profile"
  exit 1
fi
echo "ok   seed-agent-home installs the security-review Codex profile"

if grep -qx 'sandbox_mode = "danger-full-access"' "$profile" && grep -qx 'approval_policy = "never"' "$profile"; then
  echo "ok   the profile turns the Codex sandbox off and never asks"
else
  echo "FAIL the profile must set sandbox_mode danger-full-access and approval_policy never"
  exit 1
fi

for f in "$HOME/.codex/config.toml" "$profile"; do
  if grep -qx 'shell_snapshot = false' "$f"; then
    echo "ok   shell snapshots are off in ${f##*/}"
  else
    echo "FAIL ${f##*/} must set shell_snapshot = false"
    exit 1
  fi
done
