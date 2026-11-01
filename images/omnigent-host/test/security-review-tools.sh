#!/usr/bin/env bash
set -uo pipefail

fail=0
home=$(mktemp -d /var/tmp/home.XXXXXX) || exit 1
trap 'rm -rf "$home"' EXIT

mapfile -t vars < <(/opt/venv/bin/python3 -I - /src/images/omnigent-host/codex/security-review.config.toml <<'PY'
import sys, tomllib

with open(sys.argv[1], "rb") as f:
    for k, v in tomllib.load(f)["shell_environment_policy"]["set"].items():
        print(f"{k}={v}")
PY
)

# Codex rebuilds the tool environment; only the profile's own variables are guaranteed to reach it.
for cmd in "gitleaks version" "uv --version" "uvx --version" "pnpm --version" "rg --version" "go version" "git --version" "gh --version"; do
  read -ra argv <<<"$cmd"
  if out=$(cd "$home" && env -i PATH="$PATH" HOME="$home" "${vars[@]}" "${argv[@]}" 2>&1); then
    echo "ok   $cmd runs under the security-review profile environment"
  else
    echo "FAIL $cmd fails under the security-review profile environment:"
    head -3 <<<"$out"
    fail=1
  fi
done

if out=$(cd "$home" && env -i HOME="$home" PATH=/usr/local/bin:/usr/bin:/bin bash -lc 'gitleaks version && go version && uv --version' 2>&1); then
  echo "ok   the shims run in a login shell that sources only profile.d"
else
  echo "FAIL the shims fail in a login shell that sources only profile.d:"
  head -3 <<<"$out"
  fail=1
fi

exit "$fail"
