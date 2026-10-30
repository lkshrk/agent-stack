#!/usr/bin/env bash
set -uo pipefail

fail=0
while IFS= read -r line; do
  key=${line%%=*} want=${line#*=}
  case $key in
    # GPG_KEY only verified the Python source in the python image's own build.
    HOME | HOSTNAME | PWD | SHLVL | _ | GPG_KEY) continue ;;
    PATH)
      IFS=: read -ra dirs <<<"$want"
      for dir in "${dirs[@]}"; do
        if [[ :$PATH: != *":$dir:"* ]]; then
          echo "FAIL PATH lacks the upstream entry $dir"
          fail=1
        fi
      done
      continue
      ;;
  esac
  if [[ ! -v $key ]]; then
    echo "FAIL the upstream variable $key is not set"
    fail=1
  elif [[ ${!key} != "$want" ]]; then
    echo "FAIL $key: expected '$want', got '${!key}'"
    fail=1
  fi
done </src/upstream.env

((fail)) || echo "ok   the image keeps the upstream base's environment"
exit "$fail"
