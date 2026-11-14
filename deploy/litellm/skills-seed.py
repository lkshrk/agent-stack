import json
import os
import sys
import time
import urllib.error
import urllib.request

KEY = os.environ["LITELLM_MASTER_KEY"]
BASE = os.environ.get("LITELLM_BASE", "http://litellm-proxy.ai.svc.cluster.local:4000")
HDR = {"Authorization": "Bearer " + KEY, "Content-Type": "application/json"}


def call(method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data, HDR, method=method)
    try:
        return urllib.request.urlopen(req, timeout=30).status, ""
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()[:200]


for _ in range(60):
    try:
        urllib.request.urlopen(BASE + "/health/readiness", timeout=5)
        break
    except Exception:
        time.sleep(5)

skills = json.load(open("/seed/skills-seed.json"))
listed = urllib.request.urlopen(
    urllib.request.Request(BASE + "/claude-code/plugins", headers=HDR), timeout=30
)
existing = {p["name"]: p for p in json.load(listed)["plugins"]}
FIELDS = ("source", "version", "description", "keywords", "domain", "namespace")

rc = 0
for s in skills:
    current = existing.get(s["name"])
    if s.get("retired"):
        if current is not None:
            code, msg = call("DELETE", f"/claude-code/plugins/{s['name']}")
            print(f"delete {s['name']}: {code} {msg}")
            rc = rc or (0 if code < 300 else 1)
        continue
    if current is None:
        code, msg = call("POST", "/claude-code/plugins", s)
        print(f"register {s['name']}: {code} {msg}")
        rc = rc or (0 if code < 300 else 1)
    elif any(current.get(f) != s.get(f) for f in FIELDS):
        # PUT replaces the whole manifest, so it gets the full seed entry.
        spec = {k: v for k, v in s.items() if k != "name"}
        code, msg = call("PUT", f"/claude-code/plugins/{s['name']}", spec)
        print(f"update {s['name']}: {code} {msg}")
        rc = rc or (0 if code < 300 else 1)
    else:
        print(f"register {s['name']}: unchanged, skip")
    code, _ = call("POST", f"/claude-code/plugins/{s['name']}/enable")
    print(f"enable {s['name']}: {code}")

sys.exit(rc)
