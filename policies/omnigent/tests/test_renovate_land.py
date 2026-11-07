import base64
import http.server
import io
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.parse
from pathlib import Path

from . import require_yaml

require_yaml()

from agent_stack_policies import renovate_land
from agent_stack_policies.renovate_land import GitHub, Refused, land

POLICIES = Path(__file__).resolve().parents[1]
REPO = "lkshrk/h-cloud"
TOKEN = "ghs_secret-token-value"
HEAD = "a1" * 20
BASE = "b2" * 20
NULL = "0" * 40
BRANCH = "renovate/litellm-1.x"
APP = "kubernetes/apps/ai/litellm/app/helmrelease.yaml"
RELEASE = """\
apiVersion: helm.toolkit.fluxcd.io/v2
kind: HelmRelease
metadata:
  name: litellm
spec:
  interval: 1h
  chartRef:
    kind: OCIRepository
    name: litellm
  values:
    image:
      tag: 1.0.0
"""
ROLE = "apiVersion: rbac.authorization.k8s.io/v1\nkind: ClusterRole\nmetadata:\n  name: x\nrules: []\n"


def pull(**over):
    body = {
        "number": 7,
        "state": "open",
        "draft": False,
        "merged": False,
        "mergeable": True,
        "user": {"login": "renovate-master[bot]"},
        "head": {"ref": BRANCH, "sha": HEAD, "repo": {"full_name": REPO}},
        "base": {"ref": "main", "sha": BASE, "repo": {"full_name": REPO}},
    }
    for key, value in over.items():
        if key in ("head", "base"):
            body[key] = {**body[key], **value}
        else:
            body[key] = value
    return body


def push(before, after, actor="renovate-master[bot]", kind="push"):
    return {"ref": f"refs/heads/{BRANCH}", "timestamp": "2026-11-07T10:00:00Z", "actor": {"login": actor} if actor else None,
            "before": before, "after": after, "activity_type": kind}


def content(text):
    data = text.encode()
    return {"type": "file", "size": len(data), "encoding": "base64", "content": base64.b64encode(data).decode()}


class Fake:
    def __init__(self, routes=None):
        self.routes = {
            f"GET /repos/{REPO}/pulls/7": pull(),
            f"GET /repos/{REPO}/commits/{HEAD}/check-runs?per_page=100": {
                "total_count": 2,
                "check_runs": [{"name": "flate", "status": "completed", "conclusion": "success"},
                               {"name": "label", "status": "completed", "conclusion": "skipped"}],
            },
            f"GET /repos/{REPO}/commits/{HEAD}/status?per_page=100": {"state": "pending", "total_count": 0, "statuses": []},
            f"GET /repos/{REPO}/activity?ref=refs%2Fheads%2Frenovate%2Flitellm-1.x&direction=desc&per_page=100": [
                push("r1", HEAD), push(NULL, "r1", kind="branch_creation"),
            ],
            f"PUT /repos/{REPO}/pulls/7/merge": {"sha": "c3" * 20, "merged": True, "message": "Pull Request successfully merged"},
        }
        self.routes.update(routes or {})
        self.calls = []

    def __call__(self, request, timeout=None):
        url = urllib.parse.urlsplit(request.full_url)
        key = f"{request.get_method()} {url.path}" + (f"?{url.query}" if url.query else "")
        body = json.loads(request.data) if request.data else None
        self.calls.append((key, body, request.get_header("Authorization")))
        if key not in self.routes:
            raise urllib.error.HTTPError(request.full_url, 404, "Not Found", {}, io.BytesIO(b'{"message":"Not Found"}'))
        value = self.routes[key]
        if isinstance(value, tuple):
            status, value = value
            raise urllib.error.HTTPError(request.full_url, status, "error", {}, io.BytesIO(json.dumps(value).encode()))
        if isinstance(value, list) and value and callable(value[0]):
            value = value.pop(0)()
        return Response(value)

    def merges(self):
        return [body for key, body, _ in self.calls if key.startswith("PUT ")]


class Response:
    status = 200

    def __init__(self, body):
        self._raw = json.dumps(body).encode()
        self.headers = {}

    def read(self):
        return self._raw

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class LandTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.token_file = Path(self._tmp.name) / "hcloud"
        self.token_file.write_text(TOKEN + "\n")

    def tearDown(self):
        self._tmp.cleanup()

    def land(self, fake, number=7):
        github = GitHub(str(self.token_file), opener=fake, sleep=lambda _: None)
        return land(github, REPO, number)

    def refused(self, fake, text):
        with self.assertRaises(Refused) as caught:
            self.land(fake)
        self.assertIn(text, str(caught.exception))
        self.assertEqual(fake.merges(), [])
        self.assertNotIn(TOKEN, str(caught.exception))
        return str(caught.exception)

    def test_green_renovate_pr_is_squash_merged_at_the_verified_head(self):
        fake = Fake()
        out = self.land(fake)
        self.assertEqual(out, {"merged": True, "pr": f"https://github.com/{REPO}/pull/7", "head_sha": HEAD, "sha": "c3" * 20})
        self.assertEqual(fake.merges(), [{"sha": HEAD, "merge_method": "squash"}])
        self.assertTrue(all(auth == f"Bearer {TOKEN}" for _, _, auth in fake.calls))

    def test_pull_request_shape_is_verified(self):
        cases = {
            "closed": pull(state="closed"),
            "draft": pull(draft=True),
            "renovate-master[bot]": pull(user={"login": "lkshrk"}),
            "base main": pull(base={"ref": "dev"}),
            "base repository": pull(base={"repo": {"full_name": "lkshrk/other"}}),
            "head repository": pull(head={"repo": {"full_name": "someone/h-cloud"}}),
            "head repository ": pull(head={"repo": None}),
            "renovate/": pull(head={"ref": "feat/x"}),
            "merge conflicts": pull(mergeable=False),
        }
        for text, body in cases.items():
            with self.subTest(text):
                self.refused(Fake({f"GET /repos/{REPO}/pulls/7": body}), text.strip())

    def test_unknown_mergeability_is_asked_again_then_refused(self):
        self.refused(Fake({f"GET /repos/{REPO}/pulls/7": pull(mergeable=None)}), "mergeable")
        answers = [lambda: pull(mergeable=None), lambda: pull()]
        fake = Fake({f"GET /repos/{REPO}/pulls/7": answers})
        self.assertTrue(self.land(fake)["merged"])

    def test_checks_must_all_be_green(self):
        runs = f"GET /repos/{REPO}/commits/{HEAD}/check-runs?per_page=100"
        statuses = f"GET /repos/{REPO}/commits/{HEAD}/status?per_page=100"
        cases = {
            "failure": {runs: {"total_count": 1, "check_runs": [{"name": "flate", "status": "completed", "conclusion": "failure"}]}},
            "in_progress": {runs: {"total_count": 1, "check_runs": [{"name": "flate", "status": "in_progress", "conclusion": None}]}},
            "no checks": {runs: {"total_count": 0, "check_runs": []}},
            "truncated": {runs: {"total_count": 3, "check_runs": [{"name": "flate", "status": "completed", "conclusion": "success"}]}},
            "pending": {statuses: {"state": "pending", "total_count": 1, "statuses": [{"context": "ci", "state": "pending"}]}},
        }
        for text, routes in cases.items():
            with self.subTest(text):
                self.refused(Fake(routes), text)
        fake = Fake({runs: {"total_count": 0, "check_runs": []},
                     statuses: {"state": "success", "total_count": 1, "statuses": [{"context": "ci", "state": "success"}]}})
        self.assertTrue(self.land(fake)["merged"])

    def test_head_history_must_reach_the_branch_creation(self):
        activity = f"GET /repos/{REPO}/activity?ref=refs%2Fheads%2Frenovate%2Flitellm-1.x&direction=desc&per_page=100"
        self.refused(Fake({activity: [push("r1", HEAD)]}), "creation")
        self.refused(Fake({activity: [push("r2", HEAD), push(NULL, "r1", kind="branch_creation")]}), "gap")

    def agent_push(self, files, contents, compare_status="ahead"):
        activity = f"GET /repos/{REPO}/activity?ref=refs%2Fheads%2Frenovate%2Flitellm-1.x&direction=desc&per_page=100"
        routes = {
            activity: [push("r1", HEAD, actor="h-cloud-infra[bot]"), push(NULL, "r1", kind="branch_creation")],
            f"GET /repos/{REPO}/compare/r1...{HEAD}": {"status": compare_status, "files": files},
        }
        for (path, ref), text in contents.items():
            routes[f"GET /repos/{REPO}/contents/{urllib.parse.quote(path)}?ref={ref}"] = content(text)
        return Fake(routes)

    def test_clean_agent_fix_is_merged(self):
        bumped = RELEASE.replace("tag: 1.0.0", "tag: 1.0.1")
        fake = self.agent_push([{"filename": APP}], {(APP, "r1"): RELEASE, (APP, HEAD): bumped})
        self.assertTrue(self.land(fake)["merged"])

    def test_agent_push_outside_app_paths_is_refused(self):
        for path in ("talos/talconfig.yaml", "kubernetes/apps/kube-system/cilium/app/values.yaml",
                     ".github/workflows/x.yaml", "kubernetes/apps/ai/litellm/ks.yaml"):
            with self.subTest(path):
                why = self.refused(self.agent_push([{"filename": path}], {}), path)
                self.assertIn("r1..", why)

    def test_agent_rename_from_a_protected_path_is_refused(self):
        self.refused(self.agent_push([{"filename": APP, "previous_filename": "talos/x.yaml"}], {}), "talos/x.yaml")

    def test_agent_push_content_violation_is_refused(self):
        role = "kubernetes/apps/ai/litellm/app/role.yaml"
        self.refused(self.agent_push([{"filename": role}], {(role, HEAD): ROLE}), "role.yaml")

    def test_agent_branch_creation_is_compared_from_the_pr_base(self):
        activity = f"GET /repos/{REPO}/activity?ref=refs%2Fheads%2Frenovate%2Flitellm-1.x&direction=desc&per_page=100"
        fake = Fake({
            activity: [push(NULL, HEAD, actor="h-cloud-infra[bot]", kind="branch_creation")],
            f"GET /repos/{REPO}/compare/{BASE}...{HEAD}": {"status": "behind", "files": []},
        })
        self.refused(fake, "ahead")

    def test_too_many_agent_files_or_pushes_are_refused(self):
        many = [{"filename": f"kubernetes/apps/ai/litellm/app/{i}.md"} for i in range(51)]
        self.refused(self.agent_push(many, {}), "50")
        activity = f"GET /repos/{REPO}/activity?ref=refs%2Fheads%2Frenovate%2Flitellm-1.x&direction=desc&per_page=100"
        chain = [push(f"r{i}", f"r{i + 1}" if i < 20 else HEAD, actor="x[bot]") for i in range(20, 0, -1)]
        self.refused(Fake({activity: [*chain, push(NULL, "r1", actor="x[bot]", kind="branch_creation")]}), "20")

    def test_github_refusing_the_merge_is_reported(self):
        fake = Fake({f"PUT /repos/{REPO}/pulls/7/merge": (409, {"message": "Head branch was modified. Review and try the merge again."})})
        with self.assertRaises(Refused) as caught:
            self.land(fake)
        self.assertIn("409", str(caught.exception))
        self.assertIn("Head branch was modified", str(caught.exception))
        fake = Fake({f"PUT /repos/{REPO}/pulls/7/merge": {"merged": False, "message": "no"}})
        with self.assertRaises(Refused):
            self.land(fake)

    def test_token_file_problems_never_show_the_token(self):
        github = GitHub(str(self.token_file) + ".missing", opener=Fake(), sleep=lambda _: None)
        with self.assertRaises(Refused) as caught:
            land(github, REPO, 7)
        self.assertIn("token", str(caught.exception))
        self.token_file.write_text("")
        with self.assertRaises(Refused):
            self.land(Fake())

    def test_number_must_be_a_positive_integer(self):
        for bad in (0, -1, True, "7", 7.0, None):
            with self.subTest(bad=bad):
                with self.assertRaises(Refused):
                    self.land(Fake(), number=bad)

    def test_tool_name_is_not_a_merge_tool_name(self):
        self.assertEqual(renovate_land.TOOL["name"], "land_renovate_pr")
        self.assertFalse(renovate_land.TOOL["name"].endswith("merge_pull_request"))
        self.assertEqual(renovate_land.TOOL["inputSchema"]["required"], ["number"])
        self.assertEqual(set(renovate_land.TOOL["inputSchema"]["properties"]), {"number"})


class FakeServer(http.server.BaseHTTPRequestHandler):
    fake: Fake

    def _serve(self):
        length = int(self.headers.get("Content-Length") or 0)
        data = self.rfile.read(length) if length else None
        request = urllib.request.Request(f"http://x{self.path}", data=data, method=self.command,
                                         headers={"Authorization": self.headers.get("Authorization", "")})
        try:
            with self.fake(request) as resp:
                status, raw = 200, resp.read()
        except urllib.error.HTTPError as exc:
            status, raw = exc.code, exc.read()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(raw)

    do_GET = do_PUT = _serve

    def log_message(self, *args):
        pass


import urllib.request  # noqa: E402


class McpTest(unittest.TestCase):
    def session(self, fake, *messages, env=None):
        FakeServer.fake = fake
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), FakeServer)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        with tempfile.TemporaryDirectory() as tmp:
            token = Path(tmp) / "hcloud"
            token.write_text(TOKEN)
            environ = {**os.environ, "PYTHONPATH": str(POLICIES), "GH_TOKEN_FILE": str(token), **(env or {})}
            stdin = "".join(json.dumps(m) + "\n" for m in messages)
            try:
                proc = subprocess.run(
                    [sys.executable, "-m", "agent_stack_policies.renovate_land", "--repo", REPO,
                     "--api", f"http://127.0.0.1:{server.server_port}"],
                    input=stdin, capture_output=True, text=True, env=environ, timeout=60, check=True,
                )
            finally:
                server.shutdown()
        self.assertNotIn(TOKEN, proc.stdout + proc.stderr)
        return {r.get("id"): r for r in map(json.loads, proc.stdout.splitlines())}

    INIT = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}}

    def call(self, arguments, rid=2):
        return {"jsonrpc": "2.0", "id": rid, "method": "tools/call", "params": {"name": "land_renovate_pr", "arguments": arguments}}

    def test_list_and_land(self):
        fake = Fake()
        out = self.session(fake, self.INIT, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}, self.call({"number": 7}, 3))
        (tool,) = out[2]["result"]["tools"]
        self.assertEqual(tool["name"], "land_renovate_pr")
        result = out[3]["result"]
        self.assertFalse(result["isError"])
        self.assertEqual(result["structuredContent"]["sha"], "c3" * 20)
        self.assertEqual(fake.merges(), [{"sha": HEAD, "merge_method": "squash"}])

    def test_refusal_is_a_tool_error(self):
        fake = Fake({f"GET /repos/{REPO}/pulls/7": pull(user={"login": "lkshrk"})})
        result = self.session(fake, self.INIT, self.call({"number": 7}))[2]["result"]
        self.assertTrue(result["isError"])
        self.assertIn("renovate-master[bot]", result["content"][0]["text"])
        self.assertEqual(fake.merges(), [])

    def test_agent_cannot_pick_the_repository(self):
        fake = Fake()
        result = self.session(fake, self.INIT, self.call({"number": 7, "repo": "lkshrk/agent-stack"}))[2]["result"]
        self.assertTrue(result["isError"])
        self.assertEqual(fake.merges(), [])
