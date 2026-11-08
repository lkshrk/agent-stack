#!/usr/bin/env bash
set -uo pipefail

bin=${AGENT_BIN:-/opt/agent/bin}
registry=/opt/agent-stack/public-contributions.yaml
policies=/opt/agent-stack/policies/agent_stack_policies
python=/opt/venv/bin/python3
fail=0
dir=$(mktemp -d)
trap 'rm -rf "$dir"' EXIT
printf code-token >"$dir/code"
printf knowledge-token >"$dir/knowledge"
printf forks-token >"$dir/forks"
printf omnigent-token >"$dir/omnigent"
export GH_TOKEN_FILE=$dir/code KNOWLEDGE_TOKEN_FILE=$dir/knowledge FORKS_TOKEN_FILE=$dir/forks OMNIGENT_TOKEN_FILE=$dir/omnigent
unset GH_TOKEN GIT_TOKEN

break_registry() {
  case $1 in
    invalid) printf 'version: invalid\n' >"$registry" ;;
    non-utf8) printf 'version: 1\nrepositories: \xff\xfe\n' >"$registry" ;;
    nested) /opt/venv/bin/python3 -I -c 'import sys; open(sys.argv[1], "w").write("version: 1\nrepositories: " + "[" * 5000 + "]" * 5000 + "\n")' "$registry" ;;
    no-module) mv "$policies" "$policies.off" ;;
  esac
}

restore_registry() {
  cp -p "$dir/registry.orig" "$registry"
  if [[ -e $policies.off ]]; then mv "$policies.off" "$policies"; fi
}

stub_python() {
  mv "$python" "$python.real"
  printf '#!/bin/sh\nexit 1\n' >"$python"
  chmod 755 "$python"
}

restore_python() {
  mv -f "$python.real" "$python"
}

expect() {
  if [[ $2 == "$3" ]]; then
    echo "ok   $1"
  else
    echo "FAIL $1: expected '$3', got '$2'"
    fail=1
  fi
}

password() {
  printf 'protocol=https\nhost=github.com\npath=%s\n\n' "$1" | "$bin/git-credential-agent" get 2>/dev/null | sed -n 's/^password=//p'
}

# shellcheck disable=SC2016
expect "profile exports the forks token file" "$(env -u FORKS_TOKEN_FILE HOME= sh -c '. /etc/profile.d/zz-agent-home.sh; printf %s "$FORKS_TOKEN_FILE"')" /agent-secrets/github/forks
# shellcheck disable=SC2016
expect "profile no longer exports the omnigent token file" "$(env -u OMNIGENT_TOKEN_FILE HOME= sh -c '. /etc/profile.d/zz-agent-home.sh; printf %s "${OMNIGENT_TOKEN_FILE:-unset}"')" unset
expect "git: other repositories use the code token" "$(password lkshrk/agent-stack.git)" code-token
expect "git: knowledge uses its token" "$(password lkshrk/knowledge.git)" knowledge-token
expect "git: a registered fork uses the forks token" "$(password lkshrk/omnigent.git)" forks-token
expect "git: a registered fork without .git uses the forks token" "$(password lkshrk/omnigent)" forks-token
expect "git: LFS on a registered fork uses the forks token" "$(password lkshrk/omnigent.git/info/lfs)" forks-token
expect "git: LFS on another repository uses the code token" "$(password lkshrk/agent-stack.git/info/lfs)" code-token
expect "git: the upstream uses the code token" "$(password omnigent-ai/omnigent.git)" code-token
expect "git: a lookalike uses the code token" "$(password lkshrk/omnigent-other.git)" code-token
mv "$dir/forks" "$dir/forks.off"
expect "git: a fork without the forks token never falls back" "$(GIT_TOKEN=fallback password lkshrk/omnigent.git)" ""
mv "$dir/forks.off" "$dir/forks"

if [[ $(id -u) == 0 && -w ${registry%/*} ]]; then
  cp -p "$registry" "$dir/registry.orig"
  /opt/venv/bin/python3 -I - "$registry" <<'PY'
import sys
import yaml

path = sys.argv[1]
with open(path) as handle:
    data = yaml.safe_load(handle)
widget = dict(data["repositories"]["omnigent"])
widget.update(upstream="https://github.com/example-org/widget.git", fork="lkshrk/widget", checkout="widget")
data["repositories"] = {"widget": widget}
with open(path, "w") as handle:
    yaml.safe_dump(data, handle, sort_keys=False)
PY
  expect "git: a fork added to the registry uses the forks token" "$(password lkshrk/widget.git)" forks-token
  expect "git: a fork removed from the registry uses the code token" "$(password lkshrk/omnigent.git)" code-token
  restore_registry
  for state in invalid non-utf8 nested no-module; do
    break_registry "$state"
    expect "git: a $state registry hands out no token" "$(GIT_TOKEN=fallback password lkshrk/omnigent.git)" ""
    printf 'protocol=https\nhost=github.com\npath=lkshrk/omnigent.git\n\n' | GIT_TOKEN=fallback "$bin/git-credential-agent" get >/dev/null 2>&1
    expect "git: a $state registry fails the helper" "$?" 1
    rm -f "$dir/code"
    expect "git: a $state registry never hands out GIT_TOKEN" "$(GIT_TOKEN=fallback password lkshrk/omnigent.git)" ""
    printf code-token >"$dir/code"
    "$bin/contribution-fork" lkshrk/omnigent 2>/dev/null
    expect "contribution-fork exits 2 when the registry is $state" "$?" 2
    restore_registry
  done
  stub_python
  expect "git: a fork check that exits 1 hands out no token" "$(GIT_TOKEN=fallback password lkshrk/omnigent.git)" ""
  printf 'protocol=https\nhost=github.com\npath=lkshrk/omnigent.git\n\n' | "$bin/git-credential-agent" get >/dev/null 2>&1
  expect "git: a fork check that exits 1 fails the helper" "$?" 1
  restore_python
else
  echo "skip registry checks (need root to rewrite $registry)"
fi

if [[ $(id -u) == 0 && -x /usr/local/bin/gh ]]; then
  mv /usr/local/bin/gh /usr/local/bin/gh.real
  # shellcheck disable=SC2016
  printf '#!/bin/sh\nprintf %%s "$GH_TOKEN"\n' >/usr/local/bin/gh
  chmod 755 /usr/local/bin/gh
  expect "gh: other repositories use the code token" "$("$bin/gh" pr list)" code-token
  expect "gh: --repo fork uses the forks token" "$("$bin/gh" pr create --repo lkshrk/omnigent --title t)" forks-token
  expect "gh: --repo=fork uses the forks token" "$("$bin/gh" pr view 1 --repo=lkshrk/omnigent)" forks-token
  expect "gh: -R fork uses the forks token" "$("$bin/gh" issue create -R lkshrk/omnigent --title t)" forks-token
  expect "gh: a fork URL uses the forks token" "$("$bin/gh" pr list --repo https://github.com/lkshrk/omnigent.git)" forks-token
  expect "gh: a HOST/OWNER/REPO fork uses the forks token" "$("$bin/gh" pr list --repo github.com/lkshrk/omnigent)" forks-token
  expect "gh: a lookalike uses the code token" "$("$bin/gh" pr list --repo lkshrk/omnigent-other)" code-token
  expect "gh: the upstream uses the code token" "$("$bin/gh" pr view 1 --repo omnigent-ai/omnigent)" code-token
  expect "gh: a fork-named body uses the code token" "$("$bin/gh" pr create --body lkshrk/omnigent --repo lkshrk/agent-stack)" code-token
  expect "gh: an explicit GH_TOKEN wins" "$(GH_TOKEN=preset "$bin/gh" pr list --repo lkshrk/omnigent)" preset
  mv "$dir/forks" "$dir/forks.off"
  expect "gh: a fork without the forks token never falls back" "$("$bin/gh" pr list --repo lkshrk/omnigent)" ""
  mv "$dir/forks.off" "$dir/forks"
  if [[ -w ${registry%/*} ]]; then
    for state in invalid non-utf8 nested no-module; do
      break_registry "$state"
      expect "gh: a $state registry stops a --repo call" "$("$bin/gh" pr list --repo lkshrk/omnigent 2>/dev/null; echo "exit=$?")" exit=1
      expect "gh: a $state registry leaves calls without --repo alone" "$("$bin/gh" pr list)" code-token
      restore_registry
    done
    stub_python
    expect "gh: a fork check that exits 1 stops a --repo call" "$("$bin/gh" pr list --repo lkshrk/omnigent 2>/dev/null; echo "exit=$?")" exit=1
    restore_python
  fi
  if [[ ! -e /agent-secrets ]]; then
    install -d /agent-secrets/github
    printf hcloud-token >/agent-secrets/github/hcloud
    printf mounted-knowledge-token >/agent-secrets/github/knowledge
    expect "gh: with only hcloud mounted and no GH_TOKEN_FILE, gh uses hcloud" "$(env -u GH_TOKEN_FILE "$bin/gh" pr list -R lkshrk/h-cloud)" hcloud-token
    expect "git: with only hcloud mounted and no GH_TOKEN_FILE, git uses hcloud" "$(unset GH_TOKEN_FILE; GIT_TOKEN=stale password lkshrk/h-cloud.git)" hcloud-token
    expect "git: with only hcloud mounted, knowledge keeps its token" "$(unset GH_TOKEN_FILE KNOWLEDGE_TOKEN_FILE; password lkshrk/knowledge.git)" mounted-knowledge-token
    # shellcheck disable=SC2016
    expect "profile: with only hcloud mounted, GH_TOKEN_FILE is hcloud" "$(env -u GH_TOKEN_FILE HOME= sh -c '. /etc/profile.d/zz-agent-home.sh; printf %s "$GH_TOKEN_FILE"')" /agent-secrets/github/hcloud
    printf review-token >/agent-secrets/github/review
    expect "gh: hcloud wins over review" "$(env -u GH_TOKEN_FILE "$bin/gh" pr list)" hcloud-token
    rm /agent-secrets/github/hcloud
    expect "gh: with only review mounted, gh uses review" "$(env -u GH_TOKEN_FILE "$bin/gh" pr list)" review-token
    printf mounted-code-token >/agent-secrets/github/code
    printf hcloud-token >/agent-secrets/github/hcloud
    expect "gh: with code mounted, gh uses code" "$(env -u GH_TOKEN_FILE "$bin/gh" pr list)" mounted-code-token
    expect "git: with code mounted, git uses code" "$(unset GH_TOKEN_FILE; password lkshrk/agent-stack.git)" mounted-code-token
    # shellcheck disable=SC2016
    expect "profile: with code mounted, GH_TOKEN_FILE is code" "$(env -u GH_TOKEN_FILE HOME= sh -c '. /etc/profile.d/zz-agent-home.sh; printf %s "$GH_TOKEN_FILE"')" /agent-secrets/github/code
    expect "gh: an explicit GH_TOKEN_FILE wins over the mounted keys" "$("$bin/gh" pr list)" code-token
    # shellcheck disable=SC2016
    expect "profile: an explicit GH_TOKEN_FILE wins" "$(env GH_TOKEN_FILE=/x HOME= sh -c '. /etc/profile.d/zz-agent-home.sh; printf %s "$GH_TOKEN_FILE"')" /x
    rm -rf /agent-secrets
  else
    echo "skip mounted-key checks (/agent-secrets exists)"
  fi
  mv /usr/local/bin/gh.real /usr/local/bin/gh
else
  echo "skip gh wrapper checks (need root to stand in for /usr/local/bin/gh)"
fi

exit "$fail"
