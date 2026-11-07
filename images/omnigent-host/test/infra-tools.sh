#!/usr/bin/env bash
set -uo pipefail

fail=0
home=$(mktemp -d /var/tmp/home.XXXXXX) || exit 1
trap 'rm -rf "$home"' EXIT

for cmd in "flate --help" "crane version" "kubectl version --client" "flux version --client" "helm version" "yq --version" "rg --version" "gh --version" "/opt/venv/bin/python3 -I -m agent_stack_policies.manifests_cli --help" "/opt/venv/bin/python3 -I -m agent_stack_policies.renovate_land --help"; do
  read -ra argv <<<"$cmd"
  if out=$(cd "$home" && env -i HOME="$home" PATH=/usr/local/bin:/usr/bin:/bin bash -lc '"$@"' _ "${argv[@]}" 2>&1); then
    echo "ok   $cmd runs in a login shell"
  else
    echo "FAIL $cmd fails in a login shell:"
    head -3 <<<"$out"
    fail=1
  fi
done

exit "$fail"
