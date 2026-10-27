import os
import re
from collections.abc import Callable

from ._event import Decision, Event, allow, tool_call
from ._event import deny as _deny
from ._shell import DEFAULT_PROTECTED, group_token, opaque, push_branch, resolve, shell_command, shell_shape_problem, substituted, walk
from .paths import SHELL_TOOLS, escapes, forbidden_matcher, glob_match

_VCS = frozenset({"git", "gh"})
_GIT_READ = frozenset({"status", "diff", "log", "show", "rev-parse", "ls-files", "fetch", "merge-base"})
_GIT_UNSAFE_FLAGS = ("--output", "--upload-pack", "--receive-pack", "--exec", "--ext-diff")
_BRANCH_LIST_FLAGS = frozenset(
    {"--show-current", "-a", "--all", "-r", "--remotes", "-l", "--list", "-v", "-vv", "--verbose"}
)
_GH_PR_READ = frozenset({"view", "diff", "checks", "list"})
_GH_API_WRITE_FLAGS = ("-f", "-F", "--field", "--raw-field", "--input")
_PROTECTED = frozenset(DEFAULT_PROTECTED)
_GROUPING = frozenset({"(", ")", "{", "}", "$"})
_DOC_EXTS = frozenset(
    {".md", ".mdx", ".markdown", ".rst", ".txt", ".adoc", ".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".drawio"}
)
_DOC_NAMES = frozenset({"README", "CHANGELOG", "LICENSE", "NOTICE", "CONTRIBUTING"})
_PAGER_OFF = frozenset({"--no-pager", "-P"})
_ALLOWED_GIT_ENV = frozenset({"GIT_PAGER=cat"})
_FUSED_REDIRECT = re.compile(r">&|&>")
_STDERR_REDIRECT = re.compile(r"(?<=\s)2>\s*(?:&1|/dev/null)(?=$|[\s;|&)])")

ADD_FORM = "only `git add -- <doc paths>` naming doc files is allowed"
COMMIT_FORM = "only `git commit -m <message>` is allowed"
PUSH_FORM = "only `git push origin HEAD:<branch>` to a non-protected branch (no tags or other refs) is allowed"
GIT_FORM = (
    "git allows only `--no-pager` with read verbs, status, diff, log, show, rev-parse, ls-files, branch (list), remote -v, "
    "fetch, merge-base, `git add -- <doc paths>`, `git commit -m <message>` and "
    "`git push origin HEAD:<branch>`"
)
GH_FORM = "gh allows only `gh pr view|diff|checks|list` and read-only `gh api` (GET, no -f/-F/--input)"


def _base(token: str) -> str:
    return os.path.basename(token)


def _doc_file(name: str) -> bool:
    return name in _DOC_NAMES or os.path.splitext(name)[1].lower() in _DOC_EXTS


def _check_add(args: list[str], is_doc: Callable[[str], bool]) -> str | None:
    if args[:1] == ["--"]:
        args = args[1:]
    if not args or any(a.startswith("-") for a in args):
        return ADD_FORM
    for path in args:
        if any(ch in path for ch in "*?[") or path.startswith(":") or path.endswith("/"):
            return f"{ADD_FORM} (got {path})"
        if not _doc_file(_base(path)) or not is_doc(path):
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
    destination = args[1].removeprefix("HEAD:")
    branch = push_branch(destination)
    if not branch or ":" in destination or branch in _PROTECTED:
        return PUSH_FORM
    return None


def _check_git(rest: list[str], is_doc: Callable[[str], bool]) -> str | None:
    if not rest:
        return None
    pager_off = False
    while rest and rest[0] in _PAGER_OFF:
        pager_off, rest = True, rest[1:]
    if not rest:
        return None
    sub, args = rest[0], rest[1:]
    if pager_off and sub not in _GIT_READ | {"branch", "remote"}:
        return f"git --no-pager is allowed only with read verbs; {GIT_FORM}"
    if sub.startswith("-"):
        return f"git global options are not allowed; {GIT_FORM}"
    if any(a.startswith(_GIT_UNSAFE_FLAGS) for a in args):
        return f"git {sub} with an output or exec option is not allowed; {GIT_FORM}"
    if sub in _GIT_READ:
        return None
    if sub == "branch":
        flags = [a for a in args if a.startswith("-")]
        listing = all(f in _BRANCH_LIST_FLAGS for f in flags) and (
            len(flags) == len(args) or {"--list", "-l"} & set(flags)
        )
        return None if listing else f"git branch may only list; {GIT_FORM}"
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
        if token in _GROUPING or group_token(token):
            segments.append(current)
            current = []
        else:
            current.append(token)
    segments.append(current)
    return [s for s in segments if s]


def _check_statement(tokens: list[str], is_doc: Callable[[str], bool]) -> str | None:
    resolved = resolve(tokens)
    if any(a.startswith("GIT_") and a not in _ALLOWED_GIT_ENV for a in resolved.assignments):
        return "GIT_* environment overrides other than GIT_PAGER=cat are not allowed"
    command, rest = resolved.name, resolved.argv[1:]
    if command not in _VCS:
        return None
    if resolved.wrappers:
        return f"git and gh may not run through {resolved.wrappers[0]}; run them directly"
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
    forbidden = forbidden_matcher(deny, exempt)

    def is_doc(path: str) -> bool:
        if escapes(path) or forbidden(path):
            return False
        return any(glob_match(path, p) for p in allowed)

    def policy(event: Event) -> Decision | None:
        arguments = tool_call(event, SHELL_TOOLS)
        if arguments is None:
            return None
        if arguments.get("_unparseable"):
            return _deny("unparseable tool arguments")
        command = shell_command(arguments)
        if command is None:
            return _deny("shell command is not a string")
        command = _STDERR_REDIRECT.sub("", command)
        if _FUSED_REDIRECT.search(command) and re.search(r"\b(git|gh)\b", command):
            return _deny("redirecting git or gh output is not allowed; only `2>&1` or `2>/dev/null` alone")
        try:
            statements = walk(command)
        except ValueError:
            return _deny("unparseable shell command; simplify it")
        reason = shell_shape_problem(statements)
        if reason:
            return _deny(reason)
        if opaque(command) and re.search(r"\b(git|gh)\b", command):
            return _deny("substring, subscript, transformation or indirect expansions next to git or gh are not allowed")
        for tokens in statements:
            if any(substituted(t) and re.search(r"\b(git|gh)\b", t) for t in tokens):
                return _deny("git or gh inside command substitution is not allowed")
            for segment in _segments(tokens):
                reason = _check_statement(segment, is_doc)
                if reason:
                    return _deny(reason)
        return allow()

    return policy
