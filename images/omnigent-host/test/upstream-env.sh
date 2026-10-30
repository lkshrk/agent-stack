#!/usr/bin/env bash
set -uo pipefail

env_file=/src/upstream.env
if [[ ! -r $env_file ]] || ! grep -q '^PATH=' "$env_file"; then
  echo "FAIL $env_file is unreadable or has no PATH line"
  exit 1
fi

fail=0
while IFS= read -r line; do
  key=${line%%=*} want=${line#*=}
  case $key in
    # GPG_KEY only verified the Python source in the python image's own build.
    HOME | HOSTNAME | PWD | SHLVL | _ | GPG_KEY) continue ;;
    # BuildKit sets these in each RUN (tracing, proxy build args); they are not part of the image.
    TRACEPARENT | TRACESTATE | OTEL_TRACES_EXPORTER | OTEL_EXPORTER_OTLP_TRACES_ENDPOINT | OTEL_EXPORTER_OTLP_TRACES_PROTOCOL | \
      HTTP_PROXY | http_proxy | HTTPS_PROXY | https_proxy | FTP_PROXY | ftp_proxy | NO_PROXY | no_proxy | ALL_PROXY | all_proxy)
      continue
      ;;
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
done <"$env_file"

((fail)) || echo "ok   the image keeps the upstream base's environment"
exit "$fail"
