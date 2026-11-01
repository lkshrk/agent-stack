#!/usr/bin/env bash
set -uo pipefail

launcher=/opt/agent-stack/bin/run-validation
registry=/opt/agent-stack/public-contributions.yaml
source_dir=/var/run/agent-stack/source
key=/var/run/agent-stack/attestation/signing-key
token=/var/run/agent-stack/github/token
logs=/var/log/validator-test
fixture=/fixture
scratch=$(mktemp -d)
fail=0

check() {
  local name=$1
  shift
  if "$@"; then echo "ok   $name"; else echo "FAIL $name"; fail=1; return 1; fi
}

py() { /usr/bin/python3 -I - "$@"; }

check "pnpm matches the Omnigent packageManager pin" test "$(pnpm --version)" = 11.15.1
check "node is major 22" test "$(node -p 'process.versions.node.split(".")[0]')" = 22
check "no node user or home" bash -c '! id node 2>/dev/null && [ ! -e /home/node ]'
check "sandbox user is 10001 without a home" bash -c '[ "$(id -u validator)" = 10001 ] && [ ! -e "$(getent passwd validator | cut -d: -f6)" ]'
check "no git or system git config" bash -c '! command -v git && [ ! -e /etc/gitconfig ] && [ ! -e /root/.gitconfig ]'
check "launcher and policies are root-owned and not writable by others" \
  bash -c '[ "$(stat -c %U:%a '"$launcher"')" = root:755 ] && [ -z "$(find /opt/agent-stack ! -user root -o -perm /022)" ]'
check "launcher never invokes a shell" bash -c '! grep -nE "shell=True|\"sh\"|/bin/sh|bash|os\.system|popen\(" '"$launcher"

rm /usr/local/bin/pnpm
install -m 0755 /src/test/fake-pnpm /usr/local/bin/pnpm

install -d -m 0755 "$source_dir" "$(dirname "$key")" "$(dirname "$token")"
py "$key" "$scratch/public.pem" <<'EOF'
import sys
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
key = Ed25519PrivateKey.generate()
open(sys.argv[1], "wb").write(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
open(sys.argv[2], "wb").write(key.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo))
EOF
chmod 0400 "$key"
head -c 24 /dev/urandom | od -An -tx1 | tr -d ' \n' >"$token"
chown root:10001 "$token"
chmod 0440 "$token"

registry_hash=$(py <<'EOF'
import sys
from pathlib import Path
sys.path.insert(0, "/opt/agent-stack/policies")
from agent_stack_policies.public_contributions import Registry
print(Registry.load(Path("/opt/agent-stack/public-contributions.yaml")).revision)
EOF
)

use_source() { install -m 0444 "$fixture/$1/source.tar" "$fixture/$1/commit" "$source_dir/"; }

run() {
  rm -rf "$logs"
  install -d -m 0777 "$logs"
  [ -n "${FAIL_ON:-}" ] && printf '%s\n' "$FAIL_ON" >"$logs/fail-on"
  env -i PATH=/usr/local/bin:/usr/bin:/bin HOME=/root GITHUB_TOKEN=leaked-writer GH_TOKEN=leaked-writer \
    OMNIGENT_API_KEY=leaked KUBECONFIG=/root/.kube/config \
    "$launcher" "$@" >"$scratch/out" 2>"$scratch/err"
  echo $? >"$scratch/rc"
}

rc() { test "$(cat "$scratch/rc")" = "$1"; }
no_calls() { [ ! -s "$logs/calls.jsonl" ]; }
no_attestation() { [ ! -s "$scratch/out" ]; }

head_sha=$(cat "$fixture/good/head")
use_source good
run omnigent web --registry-hash "$registry_hash" --source-head-sha "$head_sha"
check "passing run exits 0" rc 0 || cat "$scratch/err"
check "passing attestation, argv, sandbox and cleanup" py "$scratch" "$registry_hash" "$head_sha" "$token" <<'EOF'
import base64, json, os, sys
from pathlib import Path
from cryptography.hazmat.primitives import serialization
scratch, registry_hash, head, token_path = sys.argv[1:]
lines = Path(scratch, "out").read_text().splitlines()
assert len(lines) == 1, lines
attestation = json.loads(lines[0])
assert set(attestation) == {"registry_hash", "repo", "profile", "source_head_sha", "validator_image_digest", "commands", "status", "finished_at", "signature"}, attestation
assert attestation["registry_hash"] == registry_hash
assert (attestation["repo"], attestation["profile"], attestation["source_head_sha"]) == ("omnigent", "web", head)
assert attestation["validator_image_digest"] == "sha256:4f12f332b748445e41f8e53d1c728357baf66a5d6c1a43cfa93688957c43af7e"
assert attestation["status"] == "passed"
assert attestation["finished_at"].endswith("Z")
expected = [
    ("install", ".", ["pnpm", "install", "--frozen-lockfile", "--filter", "web", "--ignore-scripts"]),
    ("lint", "web", ["pnpm", "run", "lint"]),
    ("typecheck", "web", ["pnpm", "run", "type-check"]),
    ("test", "web", ["pnpm", "test"]),
    ("build", "web", ["pnpm", "run", "build"]),
]
assert [(c["name"], c["cwd"], c["argv"]) for c in attestation["commands"]] == expected, attestation["commands"]
assert all(c["status"] == "passed" and c["exit_code"] == 0 for c in attestation["commands"])
public = serialization.load_pem_public_key(Path(scratch, "public.pem").read_bytes())
signature = base64.b64decode(attestation.pop("signature"))
public.verify(signature, json.dumps(attestation, sort_keys=True, separators=(",", ":")).encode())

calls = [json.loads(line) for line in Path("/var/log/validator-test/calls.jsonl").read_text().splitlines()]
assert [call["argv"][1:] for call in calls] == [argv[1:] for _, _, argv in expected], calls
src = calls[0]["cwd"]
assert src.startswith("/work/") and src.endswith("/src"), src
assert [call["cwd"] for call in calls] == [src] + [f"{src}/web"] * 4
token = Path(token_path).read_text()
allowed = {"PATH", "HOME", "TMPDIR", "LANG", "CI", "NPM_CONFIG_REGISTRY", "AGENT_STACK_GITHUB_TOKEN_FILE"}
for call in calls:
    assert call["uid"] == call["euid"] == 10001 and call["gid"] == 10001, call
    assert 0 not in call["groups"], call["groups"]
    assert call["parent_cmdline"] == ["/usr/bin/python3", "-I", "/opt/agent-stack/bin/run-validation"], call["parent_cmdline"]
    env = call["env"]
    assert set(env) - {"PWD", "OLDPWD", "SHLVL", "_"} <= allowed, sorted(env)
    assert env["AGENT_STACK_GITHUB_TOKEN_FILE"] == token_path
    assert not any(token in value or "leaked" in value for value in env.values()), "credential in environment"
    assert env["HOME"].startswith("/work/") and env["PATH"] == "/usr/local/bin:/usr/bin:/bin"
    assert call["token_readable"] and not call["signing_key_readable"]
    assert call["existing"] == [], call["existing"]
    assert not call["root_listable"]
root = Path(src)
assert (root / "web/src/a.ts").read_text() == "export const a = 2;\n"
assert os.access(root / "web/run.sh", os.X_OK) and not os.access(root / "web/src/a.ts", os.X_OK)
assert os.readlink(root / "web/root-package.json") == "../package.json"
assert root.stat().st_uid == 10001 and (root / "web/src/a.ts").stat().st_uid == 10001
leftover = [p for p in os.listdir("/proc") if p.isdigit() and os.stat(f"/proc/{p}").st_uid == 10001]
assert not leftover, leftover
EOF
check "command output stays off stdout" grep -q 'fake attestation line' "$scratch/err"

FAIL_ON="run type-check" run omnigent web --registry-hash "$registry_hash" --source-head-sha "$head_sha"
check "failing command exits 1" rc 1
check "failing command is attested and later commands are skipped" py "$scratch" <<'EOF'
import base64, json, sys
from pathlib import Path
from cryptography.hazmat.primitives import serialization
attestation = json.loads(Path(sys.argv[1], "out").read_text())
assert attestation["status"] == "failed"
assert [(c["name"], c["status"], c["exit_code"]) for c in attestation["commands"]] == [
    ("install", "passed", 0), ("lint", "passed", 0), ("typecheck", "failed", 3), ("test", "skipped", None), ("build", "skipped", None)
], attestation["commands"]
public = serialization.load_pem_public_key(Path(sys.argv[1], "public.pem").read_bytes())
signature = base64.b64decode(attestation.pop("signature"))
public.verify(signature, json.dumps(attestation, sort_keys=True, separators=(",", ":")).encode())
calls = Path("/var/log/validator-test/calls.jsonl").read_text().splitlines()
assert len(calls) == 3, calls
EOF

rejected() {
  local name=$1
  shift
  run "$@"
  check "$name is rejected before any command" bash -c "[ \"\$(cat $scratch/rc)\" = 2 ] && [ ! -s $scratch/out ] && [ ! -s $logs/calls.jsonl ]"
}

rejected "unknown profile" omnigent nope --registry-hash "$registry_hash" --source-head-sha "$head_sha"
rejected "unknown repository" nope web --registry-hash "$registry_hash" --source-head-sha "$head_sha"
rejected "wrong registry hash" omnigent web --registry-hash "$(printf '0%.0s' {1..64})" --source-head-sha "$head_sha"
rejected "wrong source head" omnigent web --registry-hash "$registry_hash" --source-head-sha "$(printf 'a%.0s' {1..40})"
rejected "missing arguments" omnigent web --registry-hash "$registry_hash"

use_source stale
rejected "archive of another commit" omnigent web --registry-hash "$registry_hash" --source-head-sha "$head_sha"
use_source extra
rejected "archive with a planted file" omnigent web --registry-hash "$registry_hash" --source-head-sha "$head_sha"
use_source escape
rejected "archive writing through a symlink" omnigent web --registry-hash "$registry_hash" --source-head-sha "$head_sha"
check "symlink escape wrote nothing outside the workspace" test ! -e /etc/x
use_source good

chmod 0664 "$registry"
rejected "group-writable registry" omnigent web --registry-hash "$registry_hash" --source-head-sha "$head_sha"
chmod 0644 "$registry"

chmod 0444 "$key"
rejected "signing key readable by the sandbox" omnigent web --registry-hash "$registry_hash" --source-head-sha "$head_sha"
chmod 0400 "$key"

install -d /var/run/secrets/kubernetes.io/serviceaccount
echo not-a-token >/var/run/secrets/kubernetes.io/serviceaccount/token
rejected "mounted Kubernetes token" omnigent web --registry-hash "$registry_hash" --source-head-sha "$head_sha"
rm -rf /var/run/secrets

run omnigent web --registry-hash "$registry_hash" --source-head-sha "$head_sha"
check "clean setup still passes after the rejections" rc 0

exit "$fail"
