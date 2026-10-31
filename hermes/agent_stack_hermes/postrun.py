import http.client
import json
import re
import urllib.error
import urllib.parse
import urllib.request
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


def _str(value, what: str) -> str:
    if not isinstance(value, str) or not value:
        raise GitHubError(f"{what} missing or not a string")
    return value


class GitHubReader:
    def __init__(self, token_file: str, api: str = "https://api.github.com", opener=urllib.request.urlopen):
        self._token_file = token_file
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

    def _refs_under(self, repo: str, kind: str) -> RefMap:
        url = f"{self._api}/repos/{repo}/git/matching-refs/{kind}?per_page=100"
        out: RefMap = {}
        for _ in range(MAX_PAGES):
            body, link = self._get(url)
            if not isinstance(body, list):
                raise GitHubError("refs response is not a list")
            for item in body:
                if not isinstance(item, dict) or not isinstance(item.get("object"), dict):
                    raise GitHubError("ref entry has the wrong shape")
                name = _str(item.get("ref"), "ref name")
                sha = _str(item["object"].get("sha"), "ref sha")
                if name.startswith(f"refs/{kind}/"):
                    out[name] = sha
            match = NEXT_LINK.search(link)
            if not match:
                return out
            url = match.group(1)
            if not url.startswith(self._api + "/"):
                raise GitHubError("pagination link leaves the API host")
        raise GitHubError("too many ref pages")

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


def violations(
    bundle: str, repo: str, before: RefMap, after: RefMap, pull: PullHead | None, files: list[str]
) -> list[str]:
    from agent_stack_policies import vcs

    protected = bool(pull) and pull.head_ref in (pull.base_ref, pull.default_branch)
    allowed = {"refs/heads/" + pull.head_ref} if pull and not protected else set()
    found = [
        f"ref moved: {ref}"
        for ref in sorted(set(before) | set(after))
        if before.get(ref) != after.get(ref) and ref not in allowed
    ]
    if pull and protected:
        found.append(f"PR head is a protected branch: {pull.head_ref}")
    if pull and pull.head_repo.lower() != repo.lower():
        found.append(f"PR head is not in {repo}")
    if pull and pull.base_repo.lower() != repo.lower():
        found.append(f"PR is not in {repo}")
    if bundle == "documenter":
        found.extend(f"non-doc file: {path}" for path in files if not vcs.is_doc_path(path))
    return found
