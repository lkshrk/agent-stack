import re
from collections.abc import Callable
from functools import lru_cache

from ._event import Decision, Event, allow, tool_call, tool_name
from ._event import deny as _deny
from ._shell import shell_command, shell_shape_problem, walk

PATCH_TOOLS = frozenset({"apply_patch"})
WRITE_TOOLS = frozenset(
    {"Write", "Edit", "MultiEdit", "NotebookEdit", "sys_os_write", "sys_os_edit", "write", "edit"}
) | PATCH_TOOLS
SHELL_TOOLS = frozenset({"sys_os_shell", "Bash", "bash", "Shell", "terminal", "shell"})
PATH_KEYS = ("file_path", "path", "notebook_path")
PATCH_KEYS = ("command", "input", "patch")

_FLAG_PREFIX = re.compile(r"^--[^=\s]+=")
_PATCH_PATH = re.compile(r"^[ \t]*\*\*\* (?:Add File|Update File|Delete File|Move to): *(.*?)\s*$", re.MULTILINE)
_PATCH_BEGIN = "*** Begin Patch"


def _normalise(path: str) -> str:
    path = re.sub(r"/{2,}", "/", path)
    while path.startswith("./"):
        path = path[2:]
    return path


@lru_cache(maxsize=256)
def _compile(pattern: str) -> re.Pattern:
    out = []
    i = 0
    while i < len(pattern):
        if pattern.startswith("/**", i) and i + 3 == len(pattern):
            out.append("(?:/.*)?")
            i += 3
        elif pattern.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif pattern.startswith("**", i):
            out.append(".*")
            i += 2
        elif pattern[i] == "*":
            out.append("[^/]*")
            i += 1
        elif pattern[i] == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(pattern[i]))
            i += 1
    return re.compile("".join(out))


def glob_match(path: str, pattern: str) -> bool:
    path = _normalise(path)
    regex = _compile(_normalise(pattern))
    starts = [0] + [i + 1 for i, ch in enumerate(path) if ch == "/"]
    return any(regex.fullmatch(path, start) for start in starts)


def _matches_any(path: str, patterns: list[str]) -> bool:
    return any(glob_match(path, p) for p in patterns)


def forbidden_matcher(deny: list[str] | None, exempt: list[str] | None) -> Callable[[str], bool]:
    denied = list(deny or [])
    exempted = list(exempt or [])
    return lambda path: _matches_any(path, denied) and not _matches_any(path, exempted)


def escapes(path: str) -> bool:
    return ".." in path.replace("\\", "/").split("/")


def patch_paths(arguments: dict) -> list[str] | None:
    """Every path an apply_patch call touches, or None when the patch can't be read."""
    changes = arguments.get("changes")
    if isinstance(changes, list):
        paths = []
        for change in changes:
            if not isinstance(change, dict) or not isinstance(change.get("path"), str):
                return None
            paths.append(change["path"])
            kind = change.get("kind")
            move = kind.get("move_path") if isinstance(kind, dict) else None
            if isinstance(move, str):
                paths.append(move)
        return paths or None
    text = next((arguments[k] for k in PATCH_KEYS if k in arguments), None)
    if isinstance(text, list):
        text = next((part for part in text if isinstance(part, str) and _PATCH_BEGIN in part), None)
    if not isinstance(text, str):
        return None
    paths = _PATCH_PATH.findall(text)
    if not paths or not all(paths):
        return None
    return paths


def path_guard(
    *,
    deny: list[str] | None = None,
    allow_only: list[str] | None = None,
    exempt: list[str] | None = None,
) -> Callable[[Event], Decision | None]:
    forbidden = forbidden_matcher(deny, exempt)
    has_denied = bool(deny)
    allowed_only = list(allow_only) if allow_only is not None else None

    def check_path(path: str) -> Decision | None:
        if escapes(path):
            return _deny(f"path {path} tries to escape with '..'")
        if forbidden(path):
            return _deny(f"forbidden path {path}")
        if allowed_only is not None and not _matches_any(path, allowed_only):
            return _deny(f"path {path} is outside allowed paths")
        return None

    def check_write(arguments: dict) -> Decision:
        path = next(
            (arguments[k] for k in PATH_KEYS if isinstance(arguments.get(k), str) and arguments[k]),
            None,
        )
        if path is None:
            return _deny("write without a path argument")
        return check_path(path) or allow()

    def check_patch(arguments: dict) -> Decision:
        paths = patch_paths(arguments)
        if paths is None:
            return _deny("apply_patch without a readable patch; name every file in an `*** Add/Update/Delete File:` line")
        for path in paths:
            decision = check_path(path)
            if decision:
                return decision
        return allow()

    def check_shell(arguments: dict) -> Decision:
        command = shell_command(arguments)
        if command is None:
            return _deny("shell command is not a string")
        try:
            statements = walk(command)
        except ValueError:
            if has_denied:
                return _deny("unparseable shell command while forbidden paths are set")
            return allow()
        reason = shell_shape_problem(statements)
        if reason:
            return _deny(reason)
        for tokens in statements:
            for token in tokens:
                candidate = _FLAG_PREFIX.sub("", token)
                if candidate and forbidden(candidate):
                    return _deny(
                        f"forbidden path {candidate} in shell command; use the Read tool for reads"
                    )
        return allow()

    def policy(event: Event) -> Decision | None:
        arguments = tool_call(event, WRITE_TOOLS | SHELL_TOOLS)
        if arguments is None:
            return None
        if arguments.get("_unparseable"):
            return _deny("unparseable tool arguments")
        name = tool_name(event)
        if name in PATCH_TOOLS:
            return check_patch(arguments)
        return check_write(arguments) if name in WRITE_TOOLS else check_shell(arguments)

    return policy
