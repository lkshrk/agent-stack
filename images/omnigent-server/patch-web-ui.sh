#!/bin/sh
set -eu

assets=${1:?usage: patch-web-ui.sh <web-ui assets dir>}
owner='function ([A-Za-z_$]+)\(([a-z]),([a-z])\)\{let ([a-z])=\2\.owner\?\?null;return \4===null\|\|\4===\3\}'

files=$(grep -lE "$owner" "$assets"/*.js || true)
[ -n "$files" ] && [ "$(printf '%s\n' "$files" | wc -l)" -eq 1 ] \
  || { echo "sidebar owner helper: want exactly one matching file, got: ${files:-none}" >&2; exit 1; }

hits=$(grep -oE "$owner" "$files" | wc -l)
[ "$hits" -eq 1 ] || { echo "sidebar owner helper: want exactly one match in $files, got $hits" >&2; exit 1; }

sed -i -E 's/'"$owner"'/function \1(\2,\3){let \4=\2.owner??null;return \4===null||\4===\3||\2.permission_level!=null\&\&\2.permission_level>=4}/' "$files"

patched=$(grep -oE 'permission_level!=null&&[a-z]\.permission_level>=4\}' "$files" | wc -l)
[ "$patched" -eq 1 ] && ! grep -qE "$owner" "$files" \
  || { echo "sidebar owner helper: patch did not apply cleanly to $files" >&2; exit 1; }
