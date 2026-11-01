#!/bin/sh
set -eu

assets=/build/omnigent/server/static/web-ui/assets
patched='\(([a-z]),([a-z])\)\{let ([a-z])=\1\.owner\?\?null;return \3===null\|\|\3===\2\|\|\1\.permission_level!=null&&\1\.permission_level>=4\}'
original='\{let ([a-z])=[a-z]\.owner\?\?null;return \1===null\|\|\1===[a-z]\}'

files=$(grep -lE "$patched" "$assets"/*.js || true)
[ -n "$files" ] && [ "$(printf '%s\n' "$files" | wc -l)" -eq 1 ] \
  || { echo "patched sidebar owner helper not found in exactly one file of $assets" >&2; exit 1; }
[ "$(grep -oE "$patched" "$files" | wc -l)" -eq 1 ] \
  || { echo "patched sidebar owner helper occurs more than once in $files" >&2; exit 1; }
! grep -qE "$original" "$assets"/*.js \
  || { echo "unpatched sidebar owner helper still present" >&2; exit 1; }
echo "sidebar owner helper patched in $files"
