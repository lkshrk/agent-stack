import re
from collections.abc import Callable
from functools import lru_cache

from ._event import Decision, Event, allow, tool_call, tool_name
from ._event import deny as _deny
from ._shell import shell_command, walk

WRITE_TOOLS = frozenset(
    {"Write", "Edit", "MultiEdit", "NotebookEdit", "sys_os_write", "sys_os_edit", "write", "edit"}
)
SHELL_TOOLS = frozenset({"sys_os_shell", "Bash", "bash", "Shell", "terminal", "shell"})
PATH_KEYS = ("file_path", "path", "notebook_path")

_FLAG_PREFIX = re.compile(r"^--[^=\s]+=")


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


def path_guard(
    *,
    deny: list[str] | None = None,
    allow_only: list[str] | None = None,
    exempt: list[str] | None = None,
) -> Callable[[Event], Decision | None]:
    denied = list(deny or [])
    allowed_only = list(allow_only) if allow_only is not None else None
    exempted = list(exempt or [])

    def forbidden(path: str) -> bool:
        return _matches_any(path, denied) and not _matches_any(path, exempted)

    def check_write(arguments: dict) -> Decision:
        path = next(
            (arguments[k] for k in PATH_KEYS if isinstance(arguments.get(k), str) and arguments[k]),
            None,
        )
        if path is None:
            return _deny("write without a path argument")
        if ".." in path.replace("\\", "/").split("/"):
            return _deny(f"path {path} tries to escape with '..'")
        if forbidden(path):
            return _deny(f"forbidden path {path}")
        if allowed_only is not None and not _matches_any(path, allowed_only):
            return _deny(f"path {path} is outside allowed paths")
        return allow()

    def check_shell(arguments: dict) -> Decision:
        try:
            statements = walk(shell_command(arguments))
        except ValueError:
            if denied:
                return _deny("unparseable shell command while forbidden paths are set")
            return allow()
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
        return check_write(arguments) if tool_name(event) in WRITE_TOOLS else check_shell(arguments)

    return policy

