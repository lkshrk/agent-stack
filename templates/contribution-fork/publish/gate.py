"""Publish an owner-approved fork pull request or issue to the registered upstream, refusing everything else."""

import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

READY_LABEL = "upstream-ready"
ISSUE_LABEL = "upstream-issue"
APPROVED_LABEL = "approved-upstream"
ISSUE_MARKER = "<!-- contribution-fork:upstream-issue -->"
PUBLISH_BRANCH = "upstream/pr-{number}"
PROTECTED_PREFIXES = (".github/", "publish/")
MAX_COMPARE_FILES = 300
PAGE = 100
SHA = re.compile(r"[0-9a-f]{40}")
SLUG = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")
TITLE_FORMATS = {"conventional-scoped": re.compile(r"[a-z]+\([^()\s]+\)!?: \S.*")}
CONFIG_KEYS = {"upstream", "fork", "owner", "default_branch", "title_format", "ci_workflow"}


class Refused(Exception):
    pass


class GitHub:
    def __init__(self, base_url: str, token: str):
        self._base = base_url.rstrip("/")
        self._token = token

    def request(self, method: str, path: str, body: dict | None = None, query: dict | None = None) -> dict:
        result = self._send(method, path, body, query)
        if not isinstance(result, dict):
            raise Refused(f"GitHub API {method} {path} returned no object")
        return result

    def request_list(self, path: str, query: dict) -> list[dict]:
        result = self._send("GET", path, None, query)
        if not isinstance(result, list) or not all(isinstance(item, dict) for item in result):
            raise Refused(f"GitHub API GET {path} returned no list")
        return result

    def exists(self, path: str) -> bool:
        return self._send("GET", path, None, None, missing_ok=True) is not None

    def _send(self, method: str, path: str, body: dict | None, query: dict | None, missing_ok: bool = False) -> Any:
        url = self._base + urllib.parse.quote(path, safe="/:") + ("?" + urllib.parse.urlencode(query) if query else "")
        data = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(url, data=data, method=method)
        request.add_header("Authorization", f"Bearer {self._token}")
        request.add_header("Accept", "application/vnd.github+json")
        request.add_header("X-GitHub-Api-Version", "2022-11-28")
        if data is not None:
            request.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return json.loads(response.read() or b"null")
        except urllib.error.HTTPError as error:
            if missing_ok and error.code == 404:
                return None
            raise Refused(f"GitHub API {method} {path} failed with HTTP {error.code}") from None
        except (urllib.error.URLError, TimeoutError, ValueError) as error:
            raise Refused(f"GitHub API {method} {path} failed: {type(error).__name__}") from None

    def pages(self, path: str, query: dict | None = None):
        page = 1
        while True:
            items = self.request_list(path, {**(query or {}), "per_page": PAGE, "page": page})
            yield from items
            if len(items) < PAGE:
                return
            page += 1


def load_config(path: Path) -> dict:
    try:
        config = json.loads(path.read_text())
    except (OSError, ValueError) as error:
        raise Refused(f"cannot read {path.name}: {type(error).__name__}") from None
    if not isinstance(config, dict) or set(config) != CONFIG_KEYS or not all(isinstance(v, str) and v for v in config.values()):
        raise Refused(f"{path.name} must hold exactly {sorted(CONFIG_KEYS)} as strings")
    if not SLUG.fullmatch(config["upstream"]) or not SLUG.fullmatch(config["fork"]):
        raise Refused(f"{path.name} upstream and fork must be owner/name")
    if config["fork"].split("/")[0] != config["owner"] or config["title_format"] not in TITLE_FORMATS:
        raise Refused(f"{path.name} fork must belong to the owner and title_format must be known")
    return config


def _labels(item: dict) -> set[str]:
    labels = item.get("labels")
    if not isinstance(labels, list):
        return set()
    return {label["name"] for label in labels if isinstance(label, dict) and isinstance(label.get("name"), str)}


def _login(item) -> str | None:
    return (item or {}).get("login") if isinstance(item, dict) else None


def _require(condition: bool, reason: str) -> None:
    if not condition:
        raise Refused(reason)


def publish_pull_request(api: GitHub, config: dict, event: dict) -> str:
    fork, owner = config["fork"], config["owner"]
    review_event, pull_event = event.get("review") or {}, event.get("pull_request") or {}
    number, review_id = pull_event.get("number"), review_event.get("id")
    _require(type(number) is int and type(review_id) is int, "the event names no pull request review")
    _require(_login(event.get("sender")) == owner and _login(review_event.get("user")) == owner, f"only a review by the owner {owner} publishes")
    _require(review_event.get("state") == "approved", "only an approving review publishes")

    review = api.request("GET", f"/repos/{fork}/pulls/{number}/reviews/{review_id}")
    _require(_login(review.get("user")) == owner, f"the review is not by the owner {owner}")
    _require(review.get("state") == "APPROVED", f"the review state is {review.get('state')}, not APPROVED")
    sha = review.get("commit_id")
    if not isinstance(sha, str) or not SHA.fullmatch(sha) or sha != review_event.get("commit_id"):
        raise Refused("the reviewed commit is not one full SHA")

    pull = api.request("GET", f"/repos/{fork}/pulls/{number}")
    _require(pull.get("state") == "open", "the pull request is not open")
    _require(READY_LABEL in _labels(pull), f"the pull request lacks the {READY_LABEL} label")
    base, head = pull.get("base") or {}, pull.get("head") or {}
    _require(
        base.get("ref") == config["default_branch"] and (base.get("repo") or {}).get("full_name") == fork,
        f"the pull request base is not {fork}:{config['default_branch']}",
    )
    head_ref = head.get("ref")
    _require((head.get("repo") or {}).get("full_name") == fork, f"the pull request head is not a branch of {fork}")
    _require(isinstance(head_ref, str) and not head_ref.casefold().startswith("upstream/"), "the pull request head is a publish branch")
    title, body = pull.get("title"), pull.get("body") or ""
    _require(title == pull_event.get("title") and body == (pull_event.get("body") or ""), "the title or body changed after the review")
    _require(isinstance(title, str) and bool(TITLE_FORMATS[config["title_format"]].fullmatch(title)), f"the title does not follow {config['title_format']}")

    _require_green_ci(api, config, sha)
    _require_clean_diff(api, config, sha)

    branch = PUBLISH_BRANCH.format(number=number)
    upstream, head_label = config["upstream"], f"{owner}:{branch}"
    existing = api.request_list(f"/repos/{upstream}/pulls", {"head": head_label, "state": "open", "per_page": PAGE})
    _require(len(existing) <= 1, f"more than one open upstream pull request uses {head_label}")
    if existing:
        published = api.request("PATCH", f"/repos/{upstream}/pulls/{existing[0]['number']}", {"title": title, "body": body})
        _point_branch(api, fork, branch, sha)
    else:
        _point_branch(api, fork, branch, sha)
        published = api.request(
            "POST",
            f"/repos/{upstream}/pulls",
            {"title": title, "body": body, "head": head_label, "base": config["default_branch"], "maintainer_can_modify": False},
        )
    return f"published {sha} as {published.get('html_url')}"


def _point_branch(api: GitHub, fork: str, branch: str, sha: str) -> None:
    if api.exists(f"/repos/{fork}/git/ref/heads/{branch}"):
        api.request("PATCH", f"/repos/{fork}/git/refs/heads/{branch}", {"sha": sha, "force": True})
    else:
        api.request("POST", f"/repos/{fork}/git/refs", {"ref": f"refs/heads/{branch}", "sha": sha})


def _require_green_ci(api: GitHub, config: dict, sha: str) -> None:
    fork, workflow = config["fork"], config["ci_workflow"]
    result = api.request("GET", f"/repos/{fork}/actions/workflows/{workflow}/runs", query={"head_sha": sha, "per_page": PAGE})
    runs = result.get("workflow_runs")
    if not isinstance(runs, list) or not all(isinstance(r, dict) for r in runs) or result.get("total_count") != len(runs):
        raise Refused(f"cannot list the CI runs for {sha}")
    runs = [r for r in runs if r.get("head_sha") == sha and r.get("path") == f".github/workflows/{workflow}"]
    _require(bool(runs), f"no {workflow} CI run exists for {sha}")
    _require(all(r.get("status") == "completed" and r.get("conclusion") == "success" for r in runs), f"CI for {sha} is not green")


def _require_clean_diff(api: GitHub, config: dict, sha: str) -> None:
    upstream_owner = config["upstream"].split("/")[0]
    compare = api.request("GET", f"/repos/{config['fork']}/compare/{upstream_owner}:{config['default_branch']}...{sha}")
    _require(
        compare.get("status") in ("ahead", "diverged") and isinstance(compare.get("ahead_by"), int) and compare["ahead_by"] > 0,
        f"{sha} is not ahead of the upstream {config['default_branch']}",
    )
    files = compare.get("files")
    if not isinstance(files, list) or not 0 < len(files) < MAX_COMPARE_FILES or not all(isinstance(f, dict) for f in files):
        raise Refused("the upstream diff is empty or too large to inspect")
    for change in files:
        for name in (change.get("filename"), change.get("previous_filename")):
            _require(not (name or "").startswith(PROTECTED_PREFIXES), f"the upstream diff changes {name}; .github/ and publish/ never go upstream")


def publish_issue(api: GitHub, config: dict, event: dict) -> str:
    fork, owner = config["fork"], config["owner"]
    issue_event = event.get("issue") or {}
    number = issue_event.get("number")
    _require(type(number) is int, "the event names no issue")
    _require((event.get("label") or {}).get("name") == APPROVED_LABEL, f"only the {APPROVED_LABEL} label publishes an issue")
    _require(_login(event.get("sender")) == owner, f"only the owner {owner} approves an issue")

    issue = api.request("GET", f"/repos/{fork}/issues/{number}")
    _require("pull_request" not in issue and issue.get("state") == "open", "only an open issue is filed upstream")
    _require({ISSUE_LABEL, APPROVED_LABEL} <= _labels(issue), f"the issue needs both {ISSUE_LABEL} and {APPROVED_LABEL}")
    approvals = [
        e for e in api.pages(f"/repos/{fork}/issues/{number}/events")
        if e.get("event") in ("labeled", "unlabeled") and (e.get("label") or {}).get("name") == APPROVED_LABEL
    ]
    _require(bool(approvals) and approvals[-1].get("event") == "labeled" and _login(approvals[-1].get("actor")) == owner, f"the {APPROVED_LABEL} label was not applied by the owner {owner}")
    title, body = issue.get("title"), issue.get("body") or ""
    _require(title == issue_event.get("title") and body == (issue_event.get("body") or ""), "the title or body changed after the approval")

    for comment in api.pages(f"/repos/{fork}/issues/{number}/comments"):
        text = comment.get("body") or ""
        if _login(comment.get("user")) == owner and text.startswith(ISSUE_MARKER):
            return f"already filed upstream as {text.removeprefix(ISSUE_MARKER).strip()}"

    upstream = config["upstream"]
    for candidate in api.pages(f"/repos/{upstream}/issues", {"creator": owner, "state": "open"}):
        if "pull_request" not in candidate and _login(candidate.get("user")) == owner and candidate.get("title") == title and (candidate.get("body") or "") == body:
            url = candidate.get("html_url")
            api.request("POST", f"/repos/{fork}/issues/{number}/comments", {"body": f"{ISSUE_MARKER}\n{url}"})
            return f"already filed upstream as {url}"

    filed = api.request("POST", f"/repos/{upstream}/issues", {"title": title, "body": body})
    url = filed.get("html_url")
    api.request("POST", f"/repos/{fork}/issues/{number}/comments", {"body": f"{ISSUE_MARKER}\n{url}"})
    return f"filed upstream as {url}"


def main(environ: dict, config_path: Path | None = None) -> int:
    try:
        token = environ.get("UPSTREAM_PUBLISH_TOKEN") or ""
        _require(bool(token), "UPSTREAM_PUBLISH_TOKEN is not set")
        config = load_config(config_path or Path(__file__).resolve().parent / "contribution.json")
        _require(environ.get("GITHUB_REPOSITORY") == config["fork"], f"this workflow publishes only from {config['fork']}")
        try:
            event = json.loads(Path(environ.get("GITHUB_EVENT_PATH", "")).read_text())
        except (OSError, ValueError):
            raise Refused("cannot read the event payload") from None
        api = GitHub(environ.get("GITHUB_API_URL") or "https://api.github.com", token)
        name = environ.get("GITHUB_EVENT_NAME")
        if name == "pull_request_review":
            message = publish_pull_request(api, config, event)
        elif name == "issues":
            message = publish_issue(api, config, event)
        else:
            raise Refused(f"unexpected event {name}")
    except Refused as refusal:
        print(f"refused: {refusal}", file=sys.stderr)
        return 1
    except Exception as error:
        print(f"refused: unexpected {type(error).__name__}", file=sys.stderr)
        return 1
    print(message)
    return 0


if __name__ == "__main__":
    sys.exit(main(dict(os.environ)))
