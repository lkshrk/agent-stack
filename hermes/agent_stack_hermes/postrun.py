import datetime
import functools
import http.client
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass

TIMEOUT_SECONDS = 30
TRANSPORT_STATUS = 0
BAD_BODY_STATUS = 502
MAX_PAGES = 100
COMPARE_FILE_CAP = 300
PR_URL = re.compile(r"^https://github\.com/([A-Za-z0-9-]+)/([A-Za-z0-9._-]+)/pull/(\d+)/?$")
REF_PREFIXES = ("heads", "tags")
NEXT_LINK = re.compile(r'<([^>]*)>\s*;\s*rel="next"')
NULL_SHA = "0" * 40
NO_TOKEN_STATUS = 401
RENOVATE = "renovate-master[bot]"
RENOVATE_PREFIX = "renovate/"
_OWNER = re.compile(r"[A-Za-z0-9-]+")

RefMap = dict[str, str]


class GitHubError(Exception):
    def __init__(self, message: str, status: int | None = None):
        super().__init__(f"github: {message}")
        self.status = BAD_BODY_STATUS if status is None else status
        self.malformed = status is None


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
    before: str
    after: str
    activity_type: str = ""


@dataclass(frozen=True)
class MergedPull:
    merged: bool
    merge_commit_sha: str
    base_ref: str
    base_repo: str
    head_ref: str
    head_repo: str
    author: str
    merged_by: str


def agent_actors(value: str | None) -> frozenset[str]:
    return frozenset(login.strip().lower() for login in (value or "").split(",") if login.strip())


def token_files(value: str | None) -> dict[str, str]:
    out: dict[str, str] = {}
    for item in (value or "").split(","):
        if not item.strip():
            continue
        owner, sep, path = (part.strip() for part in item.partition("="))
        if not sep or not _OWNER.fullmatch(owner) or not path:
            raise ValueError(f"malformed token file entry {item.strip()!r}: want owner=path")
        if owner.lower() in out:
            raise ValueError(f"owner {owner!r} listed twice")
        out[owner.lower()] = path
    return out


def _str(value, what: str) -> str:
    if not isinstance(value, str) or not value:
        raise GitHubError(f"{what} missing or not a string")
    return value


def _owner(repo: str) -> str:
    return repo.partition("/")[0]


class GitHubReader:
    def __init__(
        self,
        token_file: str | None,
        api: str = "https://api.github.com",
        opener=urllib.request.urlopen,
        agents=frozenset(),
        token_files: dict[str, str] | None = None,
    ):
        self._token_file = token_file
        self._token_files = {owner.lower(): path for owner, path in (token_files or {}).items()}
        self.agents = frozenset(agents)
        self._api = api.rstrip("/")
        self._opener = opener

    def _token(self, owner: str) -> str:
        path = self._token_files.get(owner.lower(), self._token_file)
        if not path:
            raise GitHubError(f"no read token configured for owner {owner!r}", NO_TOKEN_STATUS)
        try:
            with open(path, encoding="utf-8") as fh:
                token = fh.read().strip()
        except (OSError, UnicodeDecodeError) as exc:
            raise GitHubError(f"token file unreadable: {exc}", TRANSPORT_STATUS) from None
        if not token:
            raise GitHubError("token file is empty", TRANSPORT_STATUS)
        return token

    def _get(self, url: str, owner: str) -> tuple[object, str]:
        request = urllib.request.Request(
            url,
            headers={
                "Authorization": f"Bearer {self._token(owner)}",
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

    def _pages(self, url: str, what: str, owner: str):
        for _ in range(MAX_PAGES):
            body, link = self._get(url, owner)
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
        for body in self._pages(f"{self._api}/repos/{repo}/git/matching-refs/{kind}?per_page=100", "refs", _owner(repo)):
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
        for body in self._pages(f"{self._api}/repos/{repo}/activity?{query}", "activity", _owner(repo)):
            for item in body:
                entry = _activity(item)
                if entry.timestamp < since:
                    return out[::-1]
                if entry.ref == ref:
                    out.append(entry)
        return out[::-1]

    def release_author(self, repo: str, tag: str) -> str | None:
        try:
            body, _ = self._get(f"{self._api}/repos/{repo}/releases/tags/{urllib.parse.quote(tag, safe='')}", _owner(repo))
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
        body, _ = self._get(f"{self._api}/repos/{repo}", _owner(repo))
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
        body, _ = self._get(f"{self._api}/repos/{owner}/{name}/pulls/{number}", owner)
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
        body, _ = self._get(f"{self._api}/repos/{repo}/compare/{spec}", _owner(repo))
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

    def merged_pulls(self, repo: str, sha: str) -> list[MergedPull]:
        listed: list = []
        url = f"{self._api}/repos/{repo}/commits/{urllib.parse.quote(sha, safe='')}/pulls?per_page=100"
        for body in self._pages(url, "commit pulls", _owner(repo)):
            listed.extend(body)
        for item in listed:
            if not isinstance(item, dict) or not _is_int(item.get("number")):
                raise GitHubError("commit pull entry has the wrong shape")
        if len(listed) != 1:
            return [_merged_pull(item) for item in listed]
        detail, _ = self._get(f"{self._api}/repos/{repo}/pulls/{listed[0]['number']}", _owner(repo))
        return [_merged_pull(detail)]

    def tagger(self, repo: str, sha: str) -> str | None:
        try:
            body, _ = self._get(f"{self._api}/repos/{repo}/git/tags/{urllib.parse.quote(sha, safe='')}", _owner(repo))
        except GitHubError as exc:
            if exc.status == 404:
                return None
            raise
        if not isinstance(body, dict):
            raise GitHubError("tag response has the wrong shape")
        tagger = body.get("tagger")
        if tagger is None:
            return ""
        if not isinstance(tagger, dict):
            raise GitHubError("tagger has the wrong shape")
        return _str(tagger.get("name"), "tagger name")


def _is_int(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _login(value, what: str) -> str:
    if value is None:
        return ""
    if not isinstance(value, dict):
        raise GitHubError(f"{what} has the wrong shape")
    return _str(value.get("login"), f"{what} login")


def _optional_str(value, what: str) -> str:
    if value is None:
        return ""
    return _str(value, what)


def _merged_pull(body) -> MergedPull:
    if not isinstance(body, dict) or not isinstance(body.get("head"), dict) or not isinstance(body.get("base"), dict):
        raise GitHubError("pull response has the wrong shape")
    head, base = body["head"], body["base"]
    head_repo = head.get("repo")
    if head_repo is not None and not isinstance(head_repo, dict):
        raise GitHubError("pull head repo has the wrong shape")
    if not isinstance(base.get("repo"), dict):
        raise GitHubError("pull base repo missing")
    if not isinstance(body.get("user"), dict):
        raise GitHubError("pull author missing")
    return MergedPull(
        bool(_optional_str(body.get("merged_at"), "merged_at")),
        _optional_str(body.get("merge_commit_sha"), "merge commit sha"),
        _str(base.get("ref"), "base ref"),
        _str(base["repo"].get("full_name"), "base repo name"),
        _str(head.get("ref"), "head ref"),
        _str(head_repo.get("full_name"), "head repo name") if head_repo else "",
        _login(body["user"], "pull author"),
        _login(body.get("merged_by"), "merged_by"),
    )


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
    return Activity(
        _str(item.get("ref"), "activity ref"),
        when.timestamp(),
        login,
        _str(item.get("before"), "activity before sha"),
        _str(item.get("after"), "activity after sha"),
        _optional_str(item.get("activity_type"), "activity type"),
    )


def _chained(entries: list[Activity], start: str, end: str) -> bool:
    expected = end
    for entry in reversed(entries):
        if expected == start:
            return True
        if entry.after != expected:
            return False
        expected = entry.before
    return expected == start


def _attributed(ref: str, old: str, new: str, entries: list[Activity]) -> str | None:
    if not all(e.actor for e in entries) or not _chained(entries, old, new):
        return f"ref moved (unattributed): {ref}"
    return None


def _branch(
    ref: str, old: str, new: str, entries: list[Activity], agents: frozenset[str],
    landed: Callable[[Activity], str | None] | None = None,
) -> str | None:
    for e in entries:
        if not (e.actor and e.actor.lower() in agents):
            continue
        if not landed:
            return f"ref moved by agent: {ref}"
        why = landed(e)
        if why:
            return f"ref moved by agent: {ref} ({why})"
    return _attributed(ref, old, new, entries)


def _renovate_merge(
    entry: Activity, merged: Callable[[str], list[MergedPull]], repo: str, default: str, agents: frozenset[str]
) -> str | None:
    if entry.activity_type != "pr_merge":
        return f"{entry.activity_type or 'untyped'} activity, not a PR merge"
    pulls = merged(entry.after)
    if len(pulls) != 1:
        return f"{len(pulls)} PRs for {entry.after}, want exactly one"
    pull = pulls[0]
    checks = (
        (pull.merged, "PR is not merged"),
        (pull.merge_commit_sha == entry.after, "PR merge commit is not the new head"),
        (pull.base_ref == default, f"PR base is not {default}"),
        (pull.base_repo.lower() == repo.lower() and pull.head_repo.lower() == repo.lower(), f"PR is not within {repo}"),
        (pull.head_ref.startswith(RENOVATE_PREFIX), f"PR head is not {RENOVATE_PREFIX}*"),
        (pull.author.lower() == RENOVATE, f"PR author is not {RENOVATE}"),
        (pull.merged_by.lower() in agents, "PR was not merged by an agent"),
    )
    return next((why for ok, why in checks if not ok), None)


def _tag(
    ref: str, sha: str | None, release: Callable[[str], str | None], activity: Callable[[str], list[Activity]],
    tagger: Callable[[str], str | None], agents: frozenset[str],
) -> str | None:
    if sha is None:
        return f"ref moved (unattributed): {ref}"
    author = release(ref.removeprefix("refs/tags/"))
    if not author:
        entries = activity(ref)
        if entries:
            if any(e.actor.lower() in agents for e in entries if e.actor):
                return f"ref moved by agent: {ref}"
            return _attributed(ref, NULL_SHA, sha, entries)
        author = tagger(sha)
    if author and author.lower() in agents:
        return f"ref moved by agent: {ref}"
    return None if author else f"ref moved (unattributed): {ref}"


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
    *,
    tagger: Callable[[str], str | None] = lambda sha: None,
    merged: Callable[[str], list[MergedPull]] | None = None,
    default: str | None = None,
    pulls: Mapping[str, PullHead] | None = None,
) -> list[str]:
    from agent_stack_policies import vcs

    pulls = pulls or {}
    listed = [pull, *pulls.values()] if pull else list(pulls.values())
    heads = set(branches) | {name for p in listed for name in (p.base_ref, p.default_branch)}
    protected = {"refs/heads/" + name for name in heads}
    landed: Callable[[Activity], str | None] | None = None
    if bundle == "infra" and merged and default:
        landed = functools.partial(_renovate_merge, merged=merged, repo=repo, default=default, agents=agents)
    found = []
    for ref in sorted(set(before) | set(after) | protected):
        old, new = before.get(ref, NULL_SHA), after.get(ref, NULL_SHA)
        is_tag = ref.startswith("refs/tags/")
        if is_tag and old != new and ref in before and ref in after:
            reason = f"tag moved: {ref}"
        elif not (is_tag or ref in protected) or (old == new and (is_tag or not agents)):
            continue
        elif not agents:
            reason = f"ref moved: {ref}"
        elif is_tag:
            reason = _tag(ref, after.get(ref), release, activity, tagger, agents)
        else:
            reason = _branch(ref, old, new, activity(ref), agents, landed if ref == f"refs/heads/{default}" else None)
        if reason:
            found.append(reason)
    if pull:
        found.extend(_pull_violations(pull, repo, heads))
    for url, head in pulls.items():
        found.extend(f"{url}: {reason}" for reason in _pull_violations(head, repo, heads))
    if bundle == "documenter":
        found.extend(f"non-doc file: {path}" for path in files if not vcs.is_doc_path(path))
    return found


def _pull_violations(pull: PullHead, repo: str, heads: set[str]) -> list[str]:
    found = []
    if pull.head_ref in heads:
        found.append(f"PR head is a protected branch: {pull.head_ref}")
    if pull.head_repo.lower() != repo.lower():
        found.append(f"PR head is not in {repo}")
    if pull.base_repo.lower() != repo.lower():
        found.append(f"PR is not in {repo}")
    return found
