import re
from collections.abc import Callable

from ._event import Decision, Event, allow, tool_call
from ._event import deny as _deny
from ._shell import OpaqueCommand, group_token, resolve, shell_command, shell_shape_problem, substituted, walk
from .infra_paths import RENOVATE_PREFIX
from .paths import SHELL_TOOLS
from .tools import gh_api_mutates

_GIT_GLOBAL_VALUES = frozenset(
    {"-C", "-c", "--config-env", "--git-dir", "--work-tree", "--namespace", "--attr-source", "--shallow-file"}
)
_REMOTE_OPTIONS = frozenset({"-v", "--verbose"})
_REMOTE_READS = frozenset({"show", "get-url"})
_BRANCH_PART = r"[A-Za-z0-9][A-Za-z0-9._-]*"
_GROUPING = frozenset({"(", ")", "{", "}", "$"})
_GH_READS = {
    "pr": frozenset({"view", "diff", "checks", "list", "status", "comment", "close"}),
    "issue": frozenset({"view", "list", "status"}),
    "release": frozenset({"view", "list"}),
    "run": frozenset({"view", "list", "watch"}),
    "repo": frozenset({"view"}),
    "auth": frozenset({"status"}),
}
_GH_ANY = frozenset({"search", "status"})
_SHOW_TOKEN = frozenset({"-t", "--show-token"})

PUSH_FORM = "only `git push origin HEAD:{prefix}<branch>` is allowed: no options, tags, deletes or other branches"
GH_FORM = (
    "gh allows only pr view|diff|checks|list|status|comment|close (without --delete-branch), issue view|list|status, "
    "release view|list, run view|list|watch, repo view, search, status, auth status and read-only gh api; "
    "merge with the land_renovate_pr tool"
)
REMOTE_FORM = "git remote may only list or show remotes"


def _renovate_branch(prefix: str) -> re.Pattern:
    return re.compile(re.escape(prefix) + _BRANCH_PART + r"(?:/" + _BRANCH_PART + r")*")


def _git_subcommand(rest: list[str]) -> int:
    i = 0
    while i < len(rest) and rest[i].startswith("-"):
        i += 2 if rest[i] in _GIT_GLOBAL_VALUES else 1
    return i


def _check_git(rest: list[str], assignments: list[str], wrappers: list[str], branch: re.Pattern, prefix: str) -> str | None:
    index = _git_subcommand(rest)
    if index >= len(rest):
        return None
    sub, args = rest[index], rest[index + 1 :]
    form = PUSH_FORM.format(prefix=prefix)
    if sub == "push":
        if index or wrappers or any(a.startswith("GIT_") for a in assignments):
            return f"git push runs directly, without global options, wrappers or GIT_* variables; {form}"
        if len(args) != 2 or args[0] != "origin" or not args[1].startswith("HEAD:"):
            return form
        if not branch.fullmatch(args[1].removeprefix("HEAD:")) or ".." in args[1] or args[1].endswith(".lock"):
            return form
        return None
    if sub == "remote":
        return _check_remote(args)
    return None


def _check_remote(args: list[str]) -> str | None:
    for arg in args:
        if not arg.startswith("-"):
            return None if arg in _REMOTE_READS else REMOTE_FORM
        if arg not in _REMOTE_OPTIONS:
            return REMOTE_FORM
    return None


def _deletes_branch(args: list[str]) -> bool:
    return any(
        a == "--delete-branch" or a.startswith("--delete-branch=") or (a.startswith("-") and not a.startswith("--") and "d" in a)
        for a in args
    )


def _check_gh(rest: list[str]) -> str | None:
    if not rest:
        return GH_FORM
    group = rest[0]
    if group in _GH_ANY:
        return None
    if group == "api":
        return GH_FORM if gh_api_mutates(rest[1:]) else None
    allowed = _GH_READS.get(group)
    if not allowed or len(rest) < 2 or rest[1] not in allowed:
        return GH_FORM
    if group == "pr" and rest[1] == "close" and _deletes_branch(rest[2:]):
        return GH_FORM
    if group == "auth" and any(a in _SHOW_TOKEN or a.startswith("--show-token=") for a in rest[2:]):
        return GH_FORM
    return None


def _segments(tokens: list[str]) -> list[list[str]]:
    segments, current = [], []
    for token in tokens:
        if token in _GROUPING or group_token(token):
            segments.append(current)
            current = []
        else:
            current.append(token)
    segments.append(current)
    return [s for s in segments if s]


def infra_vcs(*, branch_prefix: str = RENOVATE_PREFIX) -> Callable[[Event], Decision | None]:
    branch = _renovate_branch(branch_prefix)

    def check_statement(tokens: list[str]) -> str | None:
        resolved = resolve(tokens)
        if not resolved.argv:
            return None
        command, rest = resolved.argv[0].rsplit("/", 1)[-1], resolved.argv[1:]
        if command not in ("git", "gh"):
            return None
        if command == "git" and "push" in rest and any(substituted(t) or "$" in t for t in rest):
            return f"git push with expansions is not allowed; {PUSH_FORM.format(prefix=branch_prefix)}"
        if command == "git":
            return _check_git(rest, resolved.assignments, resolved.wrappers, branch, branch_prefix)
        return _check_gh(rest)

    def policy(event: Event) -> Decision | None:
        arguments = tool_call(event, SHELL_TOOLS)
        if arguments is None:
            return None
        if arguments.get("_unparseable"):
            return _deny("unparseable tool arguments")
        command = shell_command(arguments)
        if command is None:
            return _deny("shell command is not a string")
        try:
            statements = walk(command)
        except OpaqueCommand as error:
            return _deny(str(error))
        except ValueError:
            return _deny("unparseable shell command; simplify it")
        reason = shell_shape_problem(statements)
        if reason:
            return _deny(reason)
        for tokens in statements:
            for segment in _segments(tokens):
                reason = check_statement(segment)
                if reason:
                    return _deny(reason)
        return allow()

    return policy
