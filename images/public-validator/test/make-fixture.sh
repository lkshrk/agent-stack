#!/usr/bin/env bash
set -euo pipefail

out=$1
repo=$(mktemp -d)
export GIT_CONFIG_NOSYSTEM=1 GIT_CONFIG_GLOBAL=/dev/null HOME=$repo
export GIT_AUTHOR_NAME=fixture GIT_AUTHOR_EMAIL=fixture@example.invalid GIT_AUTHOR_DATE=2026-01-01T00:00:00Z
export GIT_COMMITTER_NAME=fixture GIT_COMMITTER_EMAIL=fixture@example.invalid GIT_COMMITTER_DATE=2026-01-01T00:00:00Z

git -C "$repo" init -q -b main
mkdir -p "$repo/web/src/nested" "$repo/docs"
printf '{"name":"root","packageManager":"pnpm@11.15.1"}\n' >"$repo/package.json"
printf '{"name":"web"}\n' >"$repo/web/package.json"
printf 'export const a = 1;\n' >"$repo/web/src/a.ts"
printf 'nested\n' >"$repo/web/src/nested/b.txt"
printf '#!/bin/sh\nexit 0\n' >"$repo/web/run.sh"
chmod 0755 "$repo/web/run.sh"
ln -s ../package.json "$repo/web/root-package.json"
printf 'docs\n' >"$repo/docs/readme.md"
# export-subst and export-ignore would make git archive differ from the commit tree.
printf '* -export-subst -export-ignore\n' >"$repo/.git/info/attributes"
git -C "$repo" add -A
git -C "$repo" commit -q -m first
first=$(git -C "$repo" rev-parse HEAD)
printf 'export const a = 2;\n' >"$repo/web/src/a.ts"
git -C "$repo" commit -q -am second
second=$(git -C "$repo" rev-parse HEAD)

mkdir -p "$out/good" "$out/stale" "$out/extra" "$out/escape"
git -C "$repo" archive --format=tar -o "$out/good/source.tar" "$second"
git -C "$repo" cat-file commit "$second" >"$out/good/commit"
echo "$second" >"$out/good/head"

git -C "$repo" archive --format=tar -o "$out/stale/source.tar" "$first"
git -C "$repo" cat-file commit "$second" >"$out/stale/commit"
echo "$second" >"$out/stale/head"

cp "$out/good/source.tar" "$out/extra/source.tar"
cp "$out/good/commit" "$out/good/head" "$out/extra/"
extra=$(mktemp -d)
printf 'planted\n' >"$extra/planted.txt"
tar -rf "$out/extra/source.tar" -C "$extra" planted.txt

cp "$out/good/commit" "$out/good/head" "$out/escape/"
escape=$(mktemp -d)
ln -s /etc "$escape/web"
printf 'x\n' >"$escape/x"
tar -cf "$out/escape/source.tar" -C "$escape" web x
tar -rf "$out/escape/source.tar" --transform 's,^x$,web/x,' -C "$escape" x
