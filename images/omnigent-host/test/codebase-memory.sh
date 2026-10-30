#!/usr/bin/env bash
set -uo pipefail

home=$(mktemp -d)
trap 'rm -rf "$home"' EXIT
chmod 0777 "$home"

init='{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"test","version":"0"}}}'
out=$(HOME=$home timeout 30 codebase-memory-mcp <<<"$init" 2>&1)
if [[ $out == *'"serverInfo":{"name":"codebase-memory-mcp"'* ]]; then
  echo "ok   codebase-memory-mcp starts with a world-writable HOME"
else
  echo "FAIL codebase-memory-mcp with a world-writable HOME: ${out:0:200}"
  exit 1
fi
