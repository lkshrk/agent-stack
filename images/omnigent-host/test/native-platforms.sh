#!/usr/bin/env bash
set -uo pipefail

foreign=$(/opt/venv/bin/python3 -I - /usr/local/lib/node_modules /opt/mise/installs/npm-* <<'EOF'
import os, struct, sys

for root in sys.argv[1:]:
    for dirpath, _, files in os.walk(root):
        for name in files:
            path = os.path.join(dirpath, name)
            if os.path.islink(path):
                continue
            try:
                with open(path, "rb") as f:
                    head = f.read(20)
            except OSError:
                continue
            if head[:4] == b"\x7fELF" and len(head) == 20:
                if struct.unpack("<H", head[18:20])[0] != 0x3E:
                    print(path)
            elif head[:4] in (b"\xcf\xfa\xed\xfe", b"\xce\xfa\xed\xfe"):
                print(path)
            elif head[:4] == b"\xca\xfe\xba\xbe" and len(head) >= 8 and struct.unpack(">I", head[4:8])[0] < 45:
                print(path)
            elif head[:2] == b"MZ" and name.endswith((".dll", ".exe", ".node")):
                print(path)
EOF
) || {
  echo "FAIL the native binary scanner exited with an error"
  exit 1
}

if [[ -z $foreign ]]; then
  echo "ok   the npm trees hold only linux-x64 native binaries"
else
  echo "FAIL native binaries for other platforms:"
  head -20 <<<"$foreign"
  exit 1
fi
