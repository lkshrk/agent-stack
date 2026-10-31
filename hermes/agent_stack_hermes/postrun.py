import datetime
import http.client
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass

TIMEOUT_SECONDS = 30
TRANSPORT_STATUS = 0
BAD_BODY_STATUS = 502
MAX_PAGES = 100
COMPARE_FILE_CAP = 300
PR_URL = re.compile(r"^https://github\.com/([A-Za-z0-9-]+)/([A-Za-z0-9._-]+)/pull/(\d+)/?$")
REF_PREFIXES = ("heads", "tags")
NEXT_LINK = re.compile(r'<([^>]*)>\s*;\s*rel="next"')

RefMap = dict[str, str]


class GitHubError(Exception):
    def __init__(self, message: str, status: int = BAD_BODY_STATUS):
        super().__init__(f"github: {message}")
        self.status = status


@dataclass(frozen=True)
class PullHead:
    head_ref: str
    head_repo: str
    base_ref: str
    base_repo: str
    default_branch: str


@dataclass(frozen=True)
class Activity:
    ref: str
    timestamp: float
    actor: str


def agent_actors(value: str | None) -> frozenset[str]:
    return frozenset(login.strip().lower() for login in (value or "").split(",") if login.strip())


def _str(value, what: str) -> str:
    if not isinstance(value, str) or not value:
        raise GitHubError(f"{what} missing or not a string")
    return value


class GitHubReader:
    def __init__(
        self, token_file: str, api: str = "https://api.github.com", opener=urllib.request.urlopen, agents=frozenset()
    ):
        self._token_file = token_file
        self.agents = frozenset(agents)
        self._api = api.rstrip("/")
        self._opener = opener

    def _token(self) -> str:
        try:
            with open(self._token_file, encoding="utf-8") as fh:
                token = fh.read().strip()
        except (OSError, UnicodeDecodeError) as exc:
            raise GitHubError(f"token file unreadable: {exc}", TRANSPORT_STATUS) from None
        if not token:
            raise GitHubError("token file is empty", TRANSPORT_STATUS)
        return token

    def _get(self, url: str) -> tuple[object, str]:
        request = urllib.request.Request(
            url,
            headers={
                "Authorization": f"Bearer {self._token()}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
        try:
            with self._opener(request, timeout=TIMEOUT_SECONDS) as resp:
                status = resp.status
                raw = resp.read()
                link = resp.headers.get("Link", "") if getattr(resp, "headers", None) else ""
        except urllib.error.HTTPError as exc:
            try:
                detail = exc.read().decode("utf-8", "replace")[:500]
            except Exception:
                detail = ""
            finally:
                exc.close()
            raise GitHubError(f"HTTP {exc.code} {detail}".strip(), exc.code) from None
        except (OSError, http.client.HTTPException, AttributeError) as exc:
            raise GitHubError(f"{type(exc).__name__}: {exc}", TRANSPORT_STATUS) from None
        if not 200 <= status < 300:
            raise GitHubError(f"HTTP {status}", status)
        try:
            return json.loads(raw), link or ""
        except (ValueError, UnicodeDecodeError):
            raise GitHubError("response body is not JSON") from None

    def refs(self, repo: str) -> RefMap:
        out: RefMap = {}
        for kind in REF_PREFIXES:
            out.update(self._refs_under(repo, kind))
        return out

    def _pages(self, url: str, what: str):
        for _ in range(MAX_PAGES):
            body, link = self._get(url)
            if not isinstance(body, list):
                raise GitHubError(f"{what} response is not a list")
            yield body
            match = NEXT_LINK.search(link)
            if not match:
                return
            url = match.group(1)
            if not url.startswith(self._api + "/"):
                raise GitHubError("pagination link leaves the API host")
        raise GitHubError(f"too many {what} pages")

    def _refs_under(self, repo: str, kind: str) -> RefMap:
        out: RefMap = {}
        for body in self._pages(f"{self._api}/repos/{repo}/git/matching-refs/{kind}?per_page=100", "refs"):
            for item in body:
                if not isinstance(item, dict) or not isinstance(item.get("object"), dict):
                    raise GitHubError("ref entry has the wrong shape")
                name = _str(item.get("ref"), "ref name")
                sha = _str(item["object"].get("sha"), "ref sha")
                if name.startswith(f"refs/{kind}/"):
                    out[name] = sha
        return out

    def activity(self, repo: str, ref: str, since: float) -> list[Activity]:
        query = urllib.parse.urlencode({"ref": ref, "direction": "desc", "per_page": 100})
        out: list[Activity] = []
        for body in self._pages(f"{self._api}/repos/{repo}/activity?{query}", "activity"):
            for item in body:
                entry = _activity(item)
                if entry.timestamp < since:
                    return out
                if entry.ref == ref:
                    out.append(entry)
        return out

    def release_author(self, repo: str, tag: str) -> str | None:
        try:
            body, _ = self._get(f"{self._api}/repos/{repo}/releases/tags/{urllib.parse.quote(tag, safe='')}")
        except GitHubError as exc:
            if exc.status == 404:
                return None
            raise
        if not isinstance(body, dict):
            raise GitHubError("release response has the wrong shape")
        author = body.get("author")
        if author is None:
            return ""
        if not isinstance(author, dict):
            raise GitHubError("release author has the wrong shape")
        return _str(author.get("login"), "release author login")

    def default_branch(self, repo: str) -> str:
        body, _ = self._get(f"{self._api}/repos/{repo}")
        if not isinstance(body, dict):
            raise GitHubError("repo response has the wrong shape")
        return _str(body.get("default_branch"), "default branch")

    def pull(self, pr_url: str) -> PullHead:
        match = PR_URL.match(pr_url or "")
        if not match:
            raise GitHubError(f"unparseable PR URL: {pr_url!r}")
        owner, name, number = match.groups()
        if name in (".", ".."):
            raise GitHubError(f"unparseable PR URL: {pr_url!r}")
        body, _ = self._get(f"{self._api}/repos/{owner}/{name}/pulls/{number}")
        if not isinstance(body, dict) or not isinstance(body.get("head"), dict) or not isinstance(body.get("base"), dict):
            raise GitHubError("pull response has the wrong shape")
        head, base = body["head"], body["base"]
        head_repo = head.get("repo")
        if head_repo is None:
            repo_name = ""
        elif isinstance(head_repo, dict):
            repo_name = _str(head_repo.get("full_name"), "head repo name")
        else:
            raise GitHubError("pull head repo has the wrong shape")
        base_repo = base.get("repo")
        if not isinstance(base_repo, dict):
            raise GitHubError("pull base repo missing")
        return PullHead(
            _str(head.get("ref"), "head ref"),
            repo_name,
            _str(base.get("ref"), "base ref"),
            _str(base_repo.get("full_name"), "base repo name"),
            _str(base_repo.get("default_branch"), "default branch"),
        )

    def changed_files(self, repo: str, base: str, head: str) -> list[str]:
        spec = f"{urllib.parse.quote(base, safe='')}...{urllib.parse.quote(head, safe='')}"
        body, _ = self._get(f"{self._api}/repos/{repo}/compare/{spec}")
        if not isinstance(body, dict) or not isinstance(body.get("files"), list):
            raise GitHubError("compare response has the wrong shape")
        files = body["files"]
        if len(files) >= COMPARE_FILE_CAP:
            raise GitHubError("compare file list may be truncated")
        out: list[str] = []
        for entry in files:
            if not isinstance(entry, dict):
                raise GitHubError("compare file entry has the wrong shape")
            out.append(_str(entry.get("filename"), "filename"))
            previous = entry.get("previous_filename")
            if previous is not None:
                out.append(_str(previous, "previous filename"))
        return out


def _activity(item) -> Activity:
    if not isinstance(item, dict):
        raise GitHubError("activity entry has the wrong shape")
    actor = item.get("actor")
    if actor is None:
        login = ""
    elif isinstance(actor, dict):
        login = _str(actor.get("login"), "activity actor login")
    else:
        raise GitHubError("activity actor has the wrong shape")
    stamp = _str(item.get("timestamp"), "activity timestamp")
    try:
        when = datetime.datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    except ValueError:
        raise GitHubError("activity timestamp unparseable") from None
    if when.tzinfo is None:
        raise GitHubError("activity timestamp has no zone")
    return Activity(_str(item.get("ref"), "activity ref"), when.timestamp(), login)


def _moved_by(ref: str, actors: list[str], agents: frozenset[str]) -> str | None:
    if any(a.lower() in agents for a in actors if a):
        return f"ref moved by agent: {ref}"
    if not actors or not all(actors):
        return f"ref moved (unattributed): {ref}"
    return None


def _actors(ref: str, after: RefMap, activity, release) -> list[str]:
    if not ref.startswith("refs/tags/"):
        return [e.actor for e in activity(ref)]
    author = release(ref.removeprefix("refs/tags/")) if ref in after else None
    return [] if author is None else [author]


def violations(
    bundle: str,
    repo: str,
    before: RefMap,
    after: RefMap,
    pull: PullHead | None,
    files: list[str],
    activity: Callable[[str], list[Activity]],
    release: Callable[[str], str | None],
    agents: frozenset[str],
    branches: tuple[str, ...] = (),
) -> list[str]:
    from agent_stack_policies import vcs

    agents = frozenset(a.lower() for a in agents if a)
    heads = set(branches) | ({pull.base_ref, pull.default_branch} if pull else set())
    protected = {"refs/heads/" + name for name in heads}
    found = []
    for ref in sorted(set(before) | set(after)):
        if before.get(ref) != after.get(ref) and (ref.startswith("refs/tags/") or ref in protected):
            reason = _moved_by(ref, _actors(ref, after, activity, release), agents) if agents else f"ref moved: {ref}"
            if reason:
                found.append(reason)
    if pull and pull.head_ref in heads:
        found.append(f"PR head is a protected branch: {pull.head_ref}")
    if pull and pull.head_repo.lower() != repo.lower():
        found.append(f"PR head is not in {repo}")
    if pull and pull.base_repo.lower() != repo.lower():
        found.append(f"PR is not in {repo}")
    if bundle == "documenter":
        found.extend(f"non-doc file: {path}" for path in files if not vcs.is_doc_path(path))
    return found
