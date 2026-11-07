import datetime
import fnmatch
import functools
import http.client
import json
import re
import time
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
PROTECTED_PATHS = (
    "kubernetes/apps/security/**",
    "kubernetes/apps/omnigent-sandboxes/**",
    "kubernetes/apps/ai/hermes/**",
    "kubernetes/apps/ai/contribution-approver/**",
    "kubernetes/flux/**",
    ".github/**",
    "talos/**",
    "**/*.sops.yaml",
)
MONTHLY_TAG = re.compile(r"\d{4}\.\d{1,2}\.\d+")
REACHABLE_STATUSES = ("ahead", "identical")
PULLS_RETRY_SECONDS = 5
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
    merged: bool = False


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
    number: int = 0
    head_sha: str = ""
    base_sha: str = ""


@dataclass(frozen=True)
class TagObject:
    tagger: str
    commit: str


@dataclass(frozen=True)
class InfraReads:
    merged_pulls: Callable[[str], list[MergedPull]]
    head_pushes: Callable[[str, str], list[Activity] | None]
    compare_files: Callable[[str, str], list[str]]
    tag: Callable[[str], TagObject | None]
    reachable: Callable[[str], bool]


def protected_path(path: str) -> bool:
    for pattern in PROTECTED_PATHS:
        if pattern.endswith("/**"):
            if path.startswith(pattern[:-2]):
                return True
        elif fnmatch.fnmatchcase(path, pattern.removeprefix("**/")):
            return True
    return False


def agent_actors(value: str | None) -> frozenset[str]:
    return frozenset(login.strip().lower() for login in (value or "").split(",") if login.strip())


def token_files(value: str | None) -> dict[str, str]:
    out: dict[str, str] = {}
    for index, item in enumerate((value or "").split(","), 1):
        if not item.strip():
            continue
        owner, sep, path = (part.strip() for part in item.partition("="))
        if not sep or not _OWNER.fullmatch(owner) or not path:
            raise ValueError(f"entry {index} is malformed: want owner=path")
        if owner.lower() in out:
            raise ValueError(f"entry {index} repeats an owner")
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
        sleep=time.sleep,
    ):
        self._sleep = sleep
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

    def _pages(self, url: str, what: str, owner: str, key: str | None = None):
        for _ in range(MAX_PAGES):
            body, link = self._get(url, owner)
            if key is not None:
                body = body.get(key) if isinstance(body, dict) else None
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
            bool(_optional_str(body.get("merged_at"), "merged_at")),
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
        url = f"{self._api}/repos/{repo}/commits/{urllib.parse.quote(sha, safe='')}/pulls?per_page=100"
        listed = [item for body in self._pages(url, "commit pulls", _owner(repo)) for item in body]
        if not listed:
            # GitHub links a merge commit to its PR with a short delay.
            self._sleep(PULLS_RETRY_SECONDS)
            listed = [item for body in self._pages(url, "commit pulls", _owner(repo)) for item in body]
        for item in listed:
            if not isinstance(item, dict) or not _is_int(item.get("number")):
                raise GitHubError("commit pull entry has the wrong shape")
        if len(listed) != 1:
            return [_merged_pull(item) for item in listed]
        detail, _ = self._get(f"{self._api}/repos/{repo}/pulls/{listed[0]['number']}", _owner(repo))
        return [_merged_pull(detail)]

    def head_pushes(self, repo: str, head_ref: str, head_sha: str) -> list[Activity] | None:
        ref = f"refs/heads/{head_ref}"
        query = urllib.parse.urlencode({"ref": ref, "direction": "desc", "per_page": 100})
        url = f"{self._api}/repos/{repo}/activity?{query}"
        out: list[Activity] = []
        for _ in range(MAX_PAGES):
            body, link = self._get(url, _owner(repo))
            if not isinstance(body, list):
                raise GitHubError("activity response is not a list")
            for item in body:
                entry = _activity(item)
                if entry.ref != ref or (not out and entry.after != head_sha):
                    continue
                out.append(entry)
                if entry.before == NULL_SHA:
                    return out[::-1]
            match = NEXT_LINK.search(link)
            if not match:
                return None
            url = match.group(1)
            if not url.startswith(self._api + "/"):
                raise GitHubError("pagination link leaves the API host")
        return None

    def tag(self, repo: str, sha: str) -> TagObject | None:
        try:
            body, _ = self._get(f"{self._api}/repos/{repo}/git/tags/{urllib.parse.quote(sha, safe='')}", _owner(repo))
        except GitHubError as exc:
            if exc.status == 404:
                return None
            raise
        if not isinstance(body, dict) or not isinstance(body.get("object"), dict):
            raise GitHubError("tag response has the wrong shape")
        tagger = body.get("tagger")
        if tagger is not None and not isinstance(tagger, dict):
            raise GitHubError("tagger has the wrong shape")
        name = _str(tagger.get("name"), "tagger name") if tagger else ""
        target = body["object"]
        commit = _str(target.get("sha"), "tag object sha") if target.get("type") == "commit" else ""
        return TagObject(name, commit)

    def reachable(self, repo: str, commit: str, branch: str) -> bool:
        spec = f"{urllib.parse.quote(commit, safe='')}...{urllib.parse.quote(branch, safe='')}"
        body, _ = self._get(f"{self._api}/repos/{repo}/compare/{spec}?per_page=1", _owner(repo))
        if not isinstance(body, dict):
            raise GitHubError("compare response has the wrong shape")
        return _str(body.get("status"), "compare status") in REACHABLE_STATUSES


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
    if not _is_int(body.get("number")):
        raise GitHubError("pull number missing or not an integer")
    return MergedPull(
        bool(_optional_str(body.get("merged_at"), "merged_at")),
        _optional_str(body.get("merge_commit_sha"), "merge commit sha"),
        _str(base.get("ref"), "base ref"),
        _str(base["repo"].get("full_name"), "base repo name"),
        _str(head.get("ref"), "head ref"),
        _str(head_repo.get("full_name"), "head repo name") if head_repo else "",
        _login(body["user"], "pull author"),
        _login(body.get("merged_by"), "merged_by"),
        body["number"],
        _str(head.get("sha"), "head sha"),
        _str(base.get("sha"), "base sha"),
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


def _by_agent(entries: list[Activity], agents: frozenset[str]) -> bool:
    return any(e.actor.lower() in agents for e in entries if e.actor)


Landed = Callable[[Activity], "tuple[str | None, dict | None]"]


def _branch(
    ref: str, old: str, new: str, entries: list[Activity], agents: frozenset[str], landed: Landed | None,
    observed: list[dict],
) -> str | None:
    seen: list[dict] = []
    for e in entries:
        if not (e.actor and e.actor.lower() in agents):
            continue
        if not landed:
            return f"ref moved by agent: {ref}"
        why, record = landed(e)
        if why:
            return f"ref moved by agent: {ref} ({why})"
        seen.append(record or {})
    reason = _attributed(ref, old, new, entries)
    if not reason:
        observed.extend(seen)
    return reason


def _renovate_merge(
    entry: Activity, reads: InfraReads, repo: str, default: str, agents: frozenset[str]
) -> tuple[str | None, dict | None]:
    if entry.activity_type != "pr_merge":
        return f"{entry.activity_type or 'untyped'} activity, not a PR merge", None
    pulls = reads.merged_pulls(entry.after)
    if len(pulls) != 1:
        return f"{len(pulls)} PRs for {entry.after}, want exactly one", None
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
    why = next((why for ok, why in checks if not ok), None)
    if why:
        return why, None
    url = f"https://github.com/{repo}/pull/{pull.number}"
    pushes = reads.head_pushes(pull.head_ref, pull.head_sha)
    if pushes is None:
        return f"{url}: head branch history of {pull.head_ref} not traceable to its creation", None
    agent_pushes = []
    for change in pushes:
        if change.actor and change.actor.lower() not in agents:
            continue
        rewritten = change.before == NULL_SHA or change.activity_type == "force_push"
        files = reads.compare_files(pull.base_sha if rewritten else change.before, change.after)
        bad = next((path for path in files if protected_path(path)), None)
        if bad:
            return f"{url}: agent push {change.before}..{change.after} touches protected path {bad}", None
        agent_pushes.append({"before": change.before, "after": change.after, "files": files})
    return None, {"pr": url, "merge_commit_sha": entry.after, "agent_pushes": agent_pushes}


def _monthly_tag(
    ref: str, sha: str, activity: Callable[[str], list[Activity]], reads: InfraReads, agents: frozenset[str]
) -> str | None:
    entries = activity(ref)
    if entries:
        if _by_agent(entries, agents):
            return f"ref moved by agent: {ref}"
        return _attributed(ref, NULL_SHA, sha, entries)
    if not MONTHLY_TAG.fullmatch(ref.removeprefix("refs/tags/")):
        return f"ref moved (unattributed): {ref}"
    tag = reads.tag(sha)
    if tag is None or not tag.tagger or not tag.commit:
        return f"ref moved (unattributed): {ref}"
    if tag.tagger.lower() in agents:
        return f"ref moved by agent: {ref}"
    return None if reads.reachable(tag.commit) else f"ref moved (unattributed): {ref}"


def _tag(
    ref: str, sha: str | None, release: Callable[[str], str | None], agents: frozenset[str],
    fallback: Callable[[str, str], str | None] | None,
) -> str | None:
    if sha is None:
        return f"ref moved (unattributed): {ref}"
    author = release(ref.removeprefix("refs/tags/"))
    if not author and fallback:
        return fallback(ref, sha)
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
    infra: InfraReads | None = None,
    default: str | None = None,
    pulls: Mapping[str, PullHead] | None = None,
    observed: list[dict] | None = None,
) -> list[str]:
    from agent_stack_policies import vcs

    pulls = pulls or {}
    observed = [] if observed is None else observed
    listed = [pull, *pulls.values()] if pull else list(pulls.values())
    heads = set(branches) | {name for p in listed for name in (p.base_ref, p.default_branch)}
    protected = {"refs/heads/" + name for name in heads}
    landed: Landed | None = None
    tag_fallback = None
    if bundle == "infra" and infra and default:
        landed = functools.partial(_renovate_merge, reads=infra, repo=repo, default=default, agents=agents)
        tag_fallback = functools.partial(_monthly_tag, activity=activity, reads=infra, agents=agents)
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
            reason = _tag(ref, after.get(ref), release, agents, tag_fallback)
        else:
            on_default = ref == f"refs/heads/{default}"
            reason = _branch(ref, old, new, activity(ref), agents, landed if on_default else None, observed)
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
