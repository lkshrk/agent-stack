import argparse
import base64
import binascii
import http.client
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

from .infra_paths import MAX_PUSH_FILES, MAX_UNTRUSTED_PUSHES, RENOVATE, RENOVATE_PREFIX, agent_path_allowed

PROTOCOL_VERSION = "2025-06-18"
DEFAULT_TOKEN_FILE = "/agent-secrets/github/hcloud"
DEFAULT_API = "https://api.github.com"
DEFAULT_BASE = "main"
MERGE_METHOD = "squash"
TIMEOUT_SECONDS = 30
MAX_PAGES = 20
MERGEABLE_TRIES = 4
MERGEABLE_WAIT_SECONDS = 3
COMPARE_FILE_CAP = 300
MAX_CONTENT_BYTES = 1_000_000
GREEN_CONCLUSIONS = frozenset({"success", "neutral", "skipped"})
REWRITE_BASE_STATUSES = ("ahead", "diverged")
NULL_SHA = "0" * 40
NEXT_LINK = re.compile(r'<([^>]*)>\s*;\s*rel="next"')
_SHA = re.compile(r"[0-9a-f]{40}")

TOOL = {
    "name": "land_renovate_pr",
    "description": (
        "Squash-merge one open Renovate PR of the configured repository after verifying it: author "
        f"{RENOVATE}, base main, head {RENOVATE_PREFIX}* in the same repository, mergeable, every check run "
        "and status on the head green, and every push to the head by anyone but Renovate limited to app "
        "paths and passing the manifest checker. Merges exactly the verified head SHA and returns the merge SHA."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {"number": {"type": "integer", "minimum": 1, "description": "Pull request number."}},
        "required": ["number"],
        "additionalProperties": False,
    },
    "outputSchema": {
        "type": "object",
        "properties": {
            "merged": {"type": "boolean"},
            "pr": {"type": "string"},
            "head_sha": {"type": "string"},
            "sha": {"type": "string"},
        },
        "required": ["merged", "pr", "head_sha", "sha"],
    },
}


class Refused(Exception):
    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


class GitHub:
    def __init__(self, token_file: str, api: str = DEFAULT_API, opener=urllib.request.urlopen, sleep=time.sleep):
        self._token_file = token_file
        self._api = api.rstrip("/")
        self._opener = opener
        self.sleep = sleep

    def _token(self) -> str:
        try:
            with open(self._token_file, encoding="utf-8") as fh:
                token = fh.read().strip()
        except (OSError, UnicodeDecodeError) as exc:
            raise Refused(f"GitHub token file unreadable ({type(exc).__name__})") from None
        if not token:
            raise Refused("GitHub token file is empty")
        return token

    def request(self, method: str, path: str, body: dict | None = None) -> tuple[object, str]:
        url = path if path.startswith(self._api + "/") else f"{self._api}{path}"
        request = urllib.request.Request(
            url,
            data=json.dumps(body).encode() if body is not None else None,
            method=method,
            headers={
                "Authorization": f"Bearer {self._token()}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "Content-Type": "application/json",
            },
        )
        try:
            with self._opener(request, timeout=TIMEOUT_SECONDS) as resp:
                raw = resp.read()
                link = resp.headers.get("Link", "") if getattr(resp, "headers", None) else ""
        except urllib.error.HTTPError as exc:
            try:
                detail = json.loads(exc.read() or b"{}").get("message", "")
            except (ValueError, AttributeError):
                detail = ""
            finally:
                exc.close()
            raise Refused(f"GitHub {method} {urllib.parse.urlsplit(url).path}: HTTP {exc.code} {str(detail)[:300]}".strip(), exc.code) from None
        except (OSError, http.client.HTTPException) as exc:
            raise Refused(f"GitHub {method} {urllib.parse.urlsplit(url).path}: {type(exc).__name__}") from None
        try:
            return json.loads(raw), link or ""
        except (ValueError, UnicodeDecodeError):
            raise Refused(f"GitHub {method} {urllib.parse.urlsplit(url).path}: response is not JSON") from None

    def get(self, path: str):
        return self.request("GET", path)[0]

    def pages(self, path: str, key: str | None = None):
        url = path
        for _ in range(MAX_PAGES):
            body, link = self.request("GET", url)
            yield body.get(key) if key and isinstance(body, dict) else body, body
            match = NEXT_LINK.search(link)
            if not match:
                return
            url = match.group(1)
            if not url.startswith(self._api + "/"):
                raise Refused("GitHub pagination link leaves the API host")
        raise Refused(f"too many pages for {path}")


def _login(value) -> str:
    return value.get("login", "") if isinstance(value, dict) and isinstance(value.get("login"), str) else ""


def _repo_name(side: dict) -> str:
    repo = side.get("repo")
    return repo.get("full_name", "") if isinstance(repo, dict) and isinstance(repo.get("full_name"), str) else ""


def _pull(github: GitHub, repo: str, number: int) -> dict:
    attempt = 1
    while True:
        body = github.get(f"/repos/{repo}/pulls/{number}")
        if not isinstance(body, dict) or not isinstance(body.get("head"), dict) or not isinstance(body.get("base"), dict):
            raise Refused("pull request response has the wrong shape")
        if body.get("mergeable") is not None or attempt >= MERGEABLE_TRIES:
            return body
        # GitHub computes mergeability in the background after a push.
        github.sleep(MERGEABLE_WAIT_SECONDS)
        attempt += 1


def _verify_pull(body: dict, repo: str, base: str) -> str:
    head, target = body["head"], body["base"]
    checks = (
        (body.get("state") == "open" and body.get("merged") is not True, f"PR is {body.get('state')}, not open"),
        (body.get("draft") is not True, "PR is a draft"),
        (_login(body.get("user")).lower() == RENOVATE, f"PR author is {_login(body.get('user')) or 'unknown'}, not {RENOVATE}"),
        (target.get("ref") == base, f"PR base is {target.get('ref')}, not base {base}"),
        (_repo_name(target).lower() == repo.lower(), f"PR base repository is {_repo_name(target) or 'unknown'}, not {repo}"),
        (_repo_name(head).lower() == repo.lower(), f"PR head repository is {_repo_name(head) or 'missing'}, not {repo}"),
        (isinstance(head.get("ref"), str) and head["ref"].startswith(RENOVATE_PREFIX),
         f"PR head {head.get('ref')} is not a {RENOVATE_PREFIX}* branch"),
        (body.get("mergeable") is not None, "GitHub has not computed whether the PR is mergeable yet; try again shortly"),
        (body.get("mergeable") is not False, "PR has merge conflicts (not mergeable)"),
        (isinstance(head.get("sha"), str) and _SHA.fullmatch(head["sha"]), "PR head SHA is missing"),
        (isinstance(target.get("sha"), str) and _SHA.fullmatch(target["sha"]), "PR base SHA is missing"),
    )
    why = next((why for ok, why in checks if not ok), None)
    if why:
        raise Refused(why)
    return head["sha"]


def _verify_checks(github: GitHub, repo: str, sha: str) -> None:
    runs, total = [], 0
    for page, body in github.pages(f"/repos/{repo}/commits/{sha}/check-runs?per_page=100", key="check_runs"):
        count = body.get("total_count") if isinstance(body, dict) else None
        if not isinstance(page, list) or not isinstance(count, int):
            raise Refused("check runs response has the wrong shape")
        runs.extend(page)
        total = count
    if len(runs) < total:
        raise Refused(f"check runs list is truncated ({len(runs)} of {total})")
    status = github.get(f"/repos/{repo}/commits/{sha}/status?per_page=100")
    if not isinstance(status, dict) or not isinstance(status.get("statuses"), list) or not isinstance(status.get("total_count"), int):
        raise Refused("commit status response has the wrong shape")
    statuses = status["statuses"]
    if len(statuses) < status["total_count"]:
        raise Refused(f"commit statuses list is truncated ({len(statuses)} of {status['total_count']})")
    if not runs and not statuses:
        raise Refused(f"no checks reported on head {sha}; wait for CI")
    for run in runs:
        if not isinstance(run, dict):
            raise Refused("check run has the wrong shape")
        if run.get("status") != "completed":
            raise Refused(f"check {run.get('name')} is {run.get('status')}; wait for it")
        if run.get("conclusion") not in GREEN_CONCLUSIONS:
            raise Refused(f"check {run.get('name')} concluded {run.get('conclusion')}")
    for entry in statuses:
        if not isinstance(entry, dict) or entry.get("state") != "success":
            name = entry.get("context") if isinstance(entry, dict) else "?"
            raise Refused(f"status {name} is {entry.get('state') if isinstance(entry, dict) else '?'}")


def _activity(item) -> dict:
    if not isinstance(item, dict) or not all(isinstance(item.get(k), str) for k in ("ref", "before", "after")):
        raise Refused("activity entry has the wrong shape")
    return {"ref": item["ref"], "before": item["before"], "after": item["after"], "actor": _login(item.get("actor")),
            "type": item.get("activity_type") if isinstance(item.get("activity_type"), str) else ""}


def _head_pushes(github: GitHub, repo: str, branch: str, sha: str) -> list[dict]:
    ref = f"refs/heads/{branch}"
    query = urllib.parse.urlencode({"ref": ref, "direction": "desc", "per_page": 100})
    out: list[dict] = []
    for page, _ in github.pages(f"/repos/{repo}/activity?{query}"):
        if not isinstance(page, list):
            raise Refused("activity response is not a list")
        for item in page:
            entry = _activity(item)
            if entry["ref"] != ref or (not out and entry["after"] != sha):
                continue
            out.append(entry)
            if entry["before"] == NULL_SHA:
                pushes = out[::-1]
                if not all(a["after"] == b["before"] for a, b in zip(pushes, pushes[1:])):
                    raise Refused(f"head branch history of {branch} has a gap")
                return pushes
    raise Refused(f"head branch history of {branch} does not reach its creation")


def _compare(github: GitHub, repo: str, base: str, head: str) -> tuple[str, list[tuple[str, str]]]:
    spec = f"{urllib.parse.quote(base, safe='')}...{urllib.parse.quote(head, safe='')}"
    body = github.get(f"/repos/{repo}/compare/{spec}")
    if not isinstance(body, dict) or not isinstance(body.get("files"), list) or not isinstance(body.get("status"), str):
        raise Refused("compare response has the wrong shape")
    if len(body["files"]) >= COMPARE_FILE_CAP:
        raise Refused("compare file list may be truncated")
    files = []
    for entry in body["files"]:
        if not isinstance(entry, dict) or not isinstance(entry.get("filename"), str):
            raise Refused("compare file entry has the wrong shape")
        previous = entry.get("previous_filename")
        files.append((entry["filename"], previous if isinstance(previous, str) else ""))
    return body["status"], files


def _content(github: GitHub, repo: str, path: str, ref: str) -> bytes | None:
    if ref == NULL_SHA:
        return None
    query = urllib.parse.urlencode({"ref": ref})
    try:
        body = github.get(f"/repos/{repo}/contents/{urllib.parse.quote(path)}?{query}")
    except Refused as exc:
        if exc.status == 404:
            return None
        raise
    if not isinstance(body, dict) or body.get("type") != "file":
        raise Refused(f"{path} at {ref} is not a file")
    size = body.get("size")
    if not isinstance(size, int) or size > MAX_CONTENT_BYTES or body.get("encoding") != "base64":
        raise Refused(f"{path} at {ref} is larger than 1 MB")
    try:
        data = base64.b64decode(body.get("content") or "", validate=False)
    except (binascii.Error, TypeError, ValueError):
        raise Refused(f"{path} at {ref}: content is not base64") from None
    if len(data) != size:
        raise Refused(f"{path} at {ref}: content does not match its size")
    return data


def _verify_untrusted(github: GitHub, repo: str, pushes: list[dict], base_sha: str) -> None:
    from .manifests import check_change

    untrusted = [p for p in pushes if p["actor"].lower() != RENOVATE]
    if len(untrusted) > MAX_UNTRUSTED_PUSHES:
        raise Refused(f"more than {MAX_UNTRUSTED_PUSHES} pushes by others than {RENOVATE}")
    for push in untrusted:
        rewritten = push["before"] == NULL_SHA or push["type"] == "force_push"
        base = base_sha if rewritten else push["before"]
        where = f"push {push['before']}..{push['after']} by {push['actor'] or 'unknown'}"
        status, files = _compare(github, repo, base, push["after"])
        if rewritten and status not in REWRITE_BASE_STATUSES:
            raise Refused(f"{where} is not ahead of the PR base ({status})")
        if len(files) > MAX_PUSH_FILES:
            raise Refused(f"{where} changes more than {MAX_PUSH_FILES} files")
        for filename, previous in files:
            for path in (filename, previous):
                if path and not agent_path_allowed(path):
                    raise Refused(f"{where} changes {path} outside the allowed app paths")
        for filename, previous in files:
            before = _content(github, repo, previous or filename, base)
            after = _content(github, repo, filename, push["after"])
            found = check_change(filename, before, after, previous=previous or None)
            if found:
                raise Refused(f"{where}: " + "; ".join(found[:3]))


def land(github: GitHub, repo: str, number: object, base: str = DEFAULT_BASE) -> dict:
    if not isinstance(number, int) or isinstance(number, bool) or number < 1:
        raise Refused("number must be a positive integer")
    body = _pull(github, repo, number)
    head_sha = _verify_pull(body, repo, base)
    _verify_checks(github, repo, head_sha)
    pushes = _head_pushes(github, repo, body["head"]["ref"], head_sha)
    _verify_untrusted(github, repo, pushes, body["base"]["sha"])
    merged, _ = github.request("PUT", f"/repos/{repo}/pulls/{number}/merge", {"sha": head_sha, "merge_method": MERGE_METHOD})
    if not isinstance(merged, dict) or merged.get("merged") is not True or not isinstance(merged.get("sha"), str):
        message = merged.get("message") if isinstance(merged, dict) else None
        raise Refused(f"GitHub did not merge the PR: {str(message)[:300]}")
    return {"merged": True, "pr": f"https://github.com/{repo}/pull/{number}", "head_sha": head_sha, "sha": merged["sha"]}


class RpcError(Exception):
    def __init__(self, code: int, message: str):
        super().__init__(message)
        self.code = code


def _call(params: dict, github: GitHub, repo: str) -> dict:
    if params.get("name") != TOOL["name"]:
        raise RpcError(-32602, f"unknown tool {params.get('name')!r}")
    args = params.get("arguments")
    args = args if isinstance(args, dict) else {}
    try:
        if set(args) != {"number"}:
            raise Refused("pass only number; the repository is fixed by the bundle")
        out = land(github, repo, args["number"])
    except Refused as exc:
        return {"content": [{"type": "text", "text": f"not merged: {exc}"}], "isError": True}
    text = f"merged {out['pr']} at head {out['head_sha']} as {out['sha']}"
    return {"content": [{"type": "text", "text": text}], "structuredContent": out, "isError": False}


def respond(line: str, github: GitHub, repo: str) -> dict | None:
    try:
        message = json.loads(line)
    except (ValueError, RecursionError):
        return {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "parse error"}}
    if not isinstance(message, dict) or "method" not in message or "id" not in message:
        return None
    method = message["method"]
    raw_params = message.get("params")
    params = raw_params if isinstance(raw_params, dict) else {}
    try:
        if method == "initialize":
            version = params.get("protocolVersion")
            result = {
                "protocolVersion": version if isinstance(version, str) else PROTOCOL_VERSION,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": "agent-stack-renovate-land", "version": "1"},
            }
        elif method == "ping":
            result = {}
        elif method == "tools/list":
            result = {"tools": [TOOL]}
        elif method == "tools/call":
            result = _call(params, github, repo)
        else:
            raise RpcError(-32601, f"method {method!r} not found")
    except RpcError as exc:
        return {"jsonrpc": "2.0", "id": message["id"], "error": {"code": exc.code, "message": str(exc)}}
    except Exception as exc:
        return {"jsonrpc": "2.0", "id": message["id"], "error": {"code": -32603, "message": f"internal error: {type(exc).__name__}"}}
    return {"jsonrpc": "2.0", "id": message["id"], "result": result}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python3 -m agent_stack_policies.renovate_land")
    parser.add_argument("--repo", required=True)
    parser.add_argument("--api", default=DEFAULT_API)
    args = parser.parse_args(argv)
    github = GitHub(os.environ.get("GH_TOKEN_FILE") or DEFAULT_TOKEN_FILE, api=args.api)
    for line in sys.stdin:
        if not line.strip():
            continue
        reply = respond(line, github, args.repo)
        if reply is not None:
            sys.stdout.write(json.dumps(reply) + "\n")
            sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
