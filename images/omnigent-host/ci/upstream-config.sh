#!/usr/bin/env bash
set -euo pipefail

dockerfile=$(dirname "$0")/../Dockerfile
ref=${1:-$(sed -n 's/^ARG OMNIGENT_HOST_IMAGE=//p' "$dockerfile")}
reviewed='{"Cmd":["sleep","infinity"],"WorkingDir":"/root"}'

inspect() {
  docker buildx imagetools inspect "$ref" --format '{{json .Image}}'
}
if ! image=$(inspect); then
  echo "retrying the inspection of $ref in 10 s" >&2
  sleep 10
  image=$(inspect) || {
    echo "FAIL could not inspect $ref"
    exit 1
  }
fi

config=$(jq -c '
  if has("config") then select(.os == "linux" and .architecture == "amd64") | .config
  else .["linux/amd64"].config end // empty' <<<"$image")
if [[ -z $config ]]; then
  echo "FAIL $ref has no linux/amd64 image config"
  exit 1
fi

# Env is compared by test/upstream-env.sh; ArgsEscaped only affects Windows images.
drift=$(jq -r --argjson reviewed "$reviewed" '
  to_entries[]
  | select(.key != "Env" and .key != "ArgsEscaped")
  | select(.value != null and .value != "" and .value != [] and .value != {})
  | select(.value != $reviewed[.key])
  | "  \(.key) = \(.value | tojson)"' <<<"$config")

if [[ -z $drift ]]; then
  echo "ok   the upstream image config sets nothing beyond Env that the flattened image drops"
else
  echo "FAIL $ref sets image config the flattened image drops; restore it in the Dockerfile, then update the reviewed values here:"
  echo "$drift"
  exit 1
fi
