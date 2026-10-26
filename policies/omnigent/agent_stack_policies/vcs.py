import os
import re
from collections.abc import Callable

from ._event import Decision, Event, allow, tool_call
from ._event import deny as _deny
from ._shell import shell_command, substituted, walk
from .paths import SHELL_TOOLS, glob_match

_ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
_VCS = frozenset({"git", "gh"})
_WRAPPERS = frozenset(
    {"xargs", "find", "env", "command", "sudo", "doas", "exec", "nohup", "time", "nice", "timeout", "stdbuf"}
)
_GIT_READ = frozenset({"status", "diff", "log", "show", "rev-parse", "ls-files", "fetch", "merge-base"})
_GIT_UNSAFE_FLAGS = ("--output", "--upload-pack", "--receive-pack", "--exec", "--ext-diff")
_BRANCH_LIST_FLAGS = frozenset(
    {"--show-current", "-a", "--all", "-r", "--remotes", "-l", "--list", "-v", "-vv", "--verbose"}
)
_GH_PR_READ = frozenset({"view", "diff", "checks", "list"})
_GH_API_WRITE_FLAGS = ("-f", "-F", "--field", "--raw-field", "--input")
_PROTECTED = frozenset({"main", "master"})
_GROUPING = frozenset({"(", ")", "{", "}", "$"})
_KEYWORDS = frozenset({"!", "if", "then", "elif", "else", "do", "while", "until"})
_DOC_NAME = re.compile(r"^(README|CHANGELOG)|.\.")

ADD_FORM = "only `git add -- <doc paths>` naming doc files is allowed"
COMMIT_FORM = "only `git commit -m <message>` is allowed"
PUSH_FORM = "only `git push origin HEAD:<branch>` to a non-protected branch is allowed"
GIT_FORM = (
    "git allows only status, diff, log, show, rev-parse, ls-files, branch (list), remote -v, "
    "fetch, merge-base, `git add -- <doc paths>`, `git commit -m <message>` and "
    "`git push origin HEAD:<branch>`"
)
GH_FORM = "gh allows only `gh pr view|diff|checks|list` and read-only `gh api` (GET, no -f/-F/--input)"


def _base(token: str) -> str:
    return os.path.basename(token)


def _check_add(args: list[str], is_doc: Callable[[str], bool]) -> str | None:
    if args[:1] == ["--"]:
        args = args[1:]
    if not args or any(a.startswith("-") for a in args):
        return ADD_FORM
    for path in args:
        if any(ch in path for ch in "*?[") or path.startswith(":") or path.endswith("/"):
            return f"{ADD_FORM} (got {path})"
        if not _DOC_NAME.search(_base(path)) or not is_doc(path):
            return f"{ADD_FORM} (got {path})"
    return None


def _check_commit(args: list[str]) -> str | None:
    i = 0
    messages = 0
    while i < len(args):
        arg = args[i]
        if arg in ("-m", "--message") and i + 1 < len(args):
            i += 2
        elif arg.startswith("--message=") or (arg.startswith("-m") and len(arg) > 2):
            i += 1
        elif arg in ("-q", "--quiet"):
            i += 1
            continue
        else:
            return COMMIT_FORM
        messages += 1
    return None if messages else COMMIT_FORM


def _check_push(args: list[str]) -> str | None:
    if len(args) != 2 or args[0] != "origin" or not args[1].startswith("HEAD:"):
        return PUSH_FORM
    branch = args[1].removeprefix("HEAD:").removeprefix("refs/heads/")
    if not branch or ":" in branch or branch in _PROTECTED:
        return PUSH_FORM
    return None


def _check_git(rest: list[str], is_doc: Callable[[str], bool]) -> str | None:
    if not rest:
        return None
    sub, args = rest[0], rest[1:]
    if sub.startswith("-"):
        return f"git global options are not allowed; {GIT_FORM}"
    if any(a.startswith(_GIT_UNSAFE_FLAGS) for a in args):
        return f"git {sub} with an output or exec option is not allowed; {GIT_FORM}"
    if sub in _GIT_READ:
        return None
    if sub == "branch":
        return None if all(a in _BRANCH_LIST_FLAGS for a in args) else f"git branch may only list; {GIT_FORM}"
    if sub == "remote":
        return None if args in ([], ["-v"]) else f"git remote may only list; {GIT_FORM}"
    if sub == "add":
        return _check_add(args, is_doc)
    if sub == "commit":
        return _check_commit(args)
    if sub == "push":
        return _check_push(args)
    return f"git {sub} is not allowed; {GIT_FORM}"


def _check_gh_api(args: list[str]) -> str | None:
    for i, arg in enumerate(args):
        if arg.startswith(_GH_API_WRITE_FLAGS):
            return GH_FORM
        method = None
        if arg in ("-X", "--method"):
            method = args[i + 1] if i + 1 < len(args) else ""
        elif arg.startswith("--method="):
            method = arg.split("=", 1)[1]
        elif arg.startswith("-X"):
            method = arg[2:]
        if method is not None and method.upper() != "GET":
            return GH_FORM
    return None


def _check_gh(rest: list[str]) -> str | None:
    if rest[:1] == ["pr"] and len(rest) > 1 and rest[1] in _GH_PR_READ:
        return None
    if rest[:1] == ["api"]:
        return _check_gh_api(rest[1:])
    return GH_FORM


def _segments(tokens: list[str]) -> list[list[str]]:
    """Split at subshell, group and `$(` tokens, which `_shell.parse` leaves inside a statement."""
    segments, current = [], []
    for token in tokens:
        if token in _GROUPING:
            segments.append(current)
            current = []
        else:
            current.append(token)
    segments.append(current)
    return [s for s in segments if s]


def _check_statement(tokens: list[str], is_doc: Callable[[str], bool]) -> str | None:
    i = 0
    while i < len(tokens) and tokens[i] in _KEYWORDS:
        i += 1
    while i < len(tokens) and _ASSIGNMENT.match(tokens[i]):
        if tokens[i].startswith("GIT_"):
            return "GIT_* environment overrides are not allowed"
        i += 1
    if i == len(tokens):
        return None
    command, rest = _base(tokens[i]), tokens[i + 1 :]
    if command in _WRAPPERS and any(_base(t) in _VCS for t in rest):
        return f"git and gh may not run through {command}; run them directly"
    if command not in _VCS:
        return None
    if any(substituted(t) or set(t) <= set("<>&") for t in rest):
        return f"{command} with command substitution or redirection is not allowed"
    return _check_git(rest, is_doc) if command == "git" else _check_gh(rest)


def docs_vcs(
    *,
    allow_paths: list[str],
    deny: list[str] | None = None,
    exempt: list[str] | None = None,
) -> Callable[[Event], Decision | None]:
    allowed = list(allow_paths)
    denied = list(deny or [])
    exempted = list(exempt or [])

    def is_doc(path: str) -> bool:
        if ".." in path.split("/"):
            return False
        if any(glob_match(path, p) for p in denied) and not any(glob_match(path, p) for p in exempted):
            return False
        return any(glob_match(path, p) for p in allowed)

    def policy(event: Event) -> Decision | None:
        arguments = tool_call(event, SHELL_TOOLS)
        if arguments is None:
            return None
        if arguments.get("_unparseable"):
            return _deny("unparseable tool arguments")
        command = shell_command(arguments)
        try:
            statements = walk(command)
        except ValueError:
            return _deny("unparseable shell command; simplify it")
        for tokens in statements:
            if any(substituted(t) and re.search(r"\b(git|gh)\b", t) for t in tokens):
                return _deny("git or gh inside command substitution is not allowed")
            for segment in _segments(tokens):
                reason = _check_statement(segment, is_doc)
                if reason:
                    return _deny(reason)
        return allow()

    return policy
