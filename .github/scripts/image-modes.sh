#!/usr/bin/env bash
# Prints {"<image>": "build"|"retag"}; an empty CHANGES (matched .github/changes.yml filters) or PREVIOUS builds everything.
set -euo pipefail

semver='^v?[0-9]+\.[0-9]+\.[0-9]+$'
inspect=${INSPECT:-docker buildx imagetools inspect}
modes='{}'
for image in omnigent-host omnigent-server agent-tools hermes hermes-webui; do
  mode=build
  changed=$(jq -r --arg image "$image" 'if type == "array" then (index($image) != null | tostring) else error end' \
    <<<"${CHANGES:-}" 2>/dev/null) || changed=unknown
  # The server bakes the runner tag of its own release into its config, so every release rebuilds it.
  if [ "$image" != omnigent-server ] && [ "$changed" = false ] && [ -n "${PREVIOUS:-}" ] \
    && [[ ${VERSION:-} =~ $semver && $PREVIOUS =~ $semver ]]; then
    if $inspect "$REPOSITORY/$image:${PREVIOUS#v}" >/dev/null 2>&1; then
      mode=retag
    else
      echo "::warning::$REPOSITORY/$image:${PREVIOUS#v} not found; building $image" >&2
    fi
  fi
  modes=$(jq -c --arg image "$image" --arg mode "$mode" '.[$image] = $mode' <<<"$modes")
done
echo "$modes"
