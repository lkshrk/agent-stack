#!/usr/bin/env bash
set -uo pipefail

if [[ ! -x /root ]]; then
  echo "SKIP the removed-CLI check needs root to search /root"
  exit 0
fi

fail=0
scan() {
  find / -xdev \( -path /proc -o -path /sys \) -prune -o "$@" -print
}

kiro=$(scan -name 'kiro-cli*')
if [[ -z $kiro ]]; then
  echo "ok   kiro-cli is not installed"
else
  echo "FAIL kiro-cli is installed:"
  echo "$kiro"
  fail=1
fi

for pkg in @anthropic-ai/claude-code @openai/codex; do
  copies=$(scan -path "*/node_modules/$pkg/package.json")
  if [[ $copies == "/usr/local/lib/node_modules/$pkg/package.json" ]]; then
    echo "ok   $pkg is installed once, under /usr/local/lib/node_modules"
  else
    echo "FAIL $pkg should be installed once, under /usr/local/lib/node_modules; found:"
    echo "${copies:-nothing}"
    fail=1
  fi
done

for tool in claude codex; do
  found=$(scan -name "$tool" ! -type d ! -path '*/node_modules/*' ! -path '*/site-packages/*')
  if [[ $found == "/usr/local/bin/$tool" ]]; then
    echo "ok   the only $tool executable is /usr/local/bin/$tool"
  else
    echo "FAIL the only $tool executable should be /usr/local/bin/$tool; found:"
    echo "${found:-nothing}"
    fail=1
  fi
done

exit "$fail"
