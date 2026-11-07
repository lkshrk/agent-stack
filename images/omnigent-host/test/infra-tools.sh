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

project=$(mktemp -d /var/tmp/project.XXXXXX) || exit 1
trap 'rm -rf "$home" "$project"' EXIT
cat >"$project/.mise.toml" <<'TOML'
[tools]
kubectl = "1.20.0"
flux2 = "2.0.0"
helm = "3.0.0"
crane = "0.1.0"
"github:home-operations/flate" = "v0.0.1"
TOML
for bin in kubectl flux helm flate crane; do
  # shellcheck disable=SC2016
  if out=$(cd "$project" && env -i HOME="$home" PATH=/usr/local/bin:/usr/bin:/bin bash -lc 'command -v "$1" && "$1" --help >/dev/null' _ "$bin" 2>&1) \
    && [[ $(head -1 <<<"$out") == "/opt/agent/pinned/$bin" ]]; then
    echo "ok   $bin is the image's pin inside a project with its own .mise.toml"
  else
    echo "FAIL $bin follows a project .mise.toml or fails there:"
    head -3 <<<"$out"
    fail=1
  fi
done

exit "$fail"
