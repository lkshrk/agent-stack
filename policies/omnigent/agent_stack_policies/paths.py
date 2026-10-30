import os
import posixpath
import re
from collections.abc import Callable
from functools import lru_cache

from ._event import Decision, Event, allow, tool_call, tool_name
from ._event import deny as _deny
from ._shell import OpaqueCommand, resolve, shell_command, shell_shape_problem, strip_redirects, walk

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
_EXPANDING_TARGET = re.compile(r"`|\{[^{}]*(?:,|\.\.)[^{}]*\}")
_FUSED_REDIRECT_TARGET = re.compile(r"(?:&>>?|>\||>&)[ \t]*([^\s;&|<>()]+)")
_COPY = frozenset({"cp", "mv", "install", "ln", "rsync"})
_TOUCH = frozenset({"touch", "rm", "rmdir", "truncate", "mkdir"})
_OWNER = frozenset({"chmod", "chown", "chgrp"})
_VALUE_FLAGS = {
    "sed": frozenset({"-e", "-f", "-l", "--expression", "--file", "--line-length"}),
    "cp": frozenset({"-S", "--suffix", "-t", "--target-directory"}),
    "mv": frozenset({"-S", "--suffix", "-t", "--target-directory"}),
    "install": frozenset({"-m", "--mode", "-o", "--owner", "-g", "--group", "-S", "--suffix", "-t", "--target-directory"}),
    "ln": frozenset({"-S", "--suffix", "-t", "--target-directory"}),
    "rsync": frozenset({"-e", "--rsh", "--exclude", "--include", "--filter", "-f", "--chmod"}),
    "touch": frozenset({"-d", "--date", "-t", "-r", "--reference"}),
    "truncate": frozenset({"-s", "--size", "-r", "--reference"}),
    "mkdir": frozenset({"-m", "--mode"}),
    "tee": frozenset(),
}
_OPTIONAL_VALUE_LETTERS = {"sed": "i"}
HOME_READINGS = 64


def _home_readings(path: str, home: str) -> list[str] | None:
    """`path` with `$HOME` and each `${HOME…}` replaced by `home`, once for every `}` that may close the `${HOME…}` (the quotes that would pick one are gone from a token); None above HOME_READINGS readings."""
    states, done = [([], 0)], []
    while states:
        pieces, i = states.pop()
        plain, braced = path.find("$HOME", i), path.find("${HOME", i)
        if plain < 0 and braced < 0:
            done.append("".join(pieces) + path[i:])
            continue
        start = braced if plain < 0 or 0 <= braced < plain else plain
        head = [*pieces, path[i:start], home]
        if start == plain:
            states.append((head, start + 5))
            continue
        closes = [c + 1 for c in range(start + 6, len(path)) if path[c] == "}"]
        states.extend((head, after) for after in closes)
        if not closes:
            done.append("".join(pieces) + path[i:])
        if len(states) + len(done) > HOME_READINGS:
            return None
    return done


def _short_cluster(arg: str, values: frozenset[str], optional: str, following: str | None) -> tuple[dict[str, str], bool]:
    """getopt-style `-abc` / `-tDIR`: the flags it sets and whether it took `following` as a value."""
    found: dict[str, str] = {}
    for k in range(1, len(arg)):
        flag, rest = "-" + arg[k], arg[k + 1 :]
        if flag in values:
            found[flag] = rest if rest else following or ""
            return found, not rest and following is not None
        found[flag] = ""
        if arg[k] in optional:
            found[flag] = rest
            break
    return found, False


def _operands(name: str, args: list[str]) -> tuple[list[str], dict[str, str]]:
    """Non-flag operands and the values of value-taking flags."""
    values = _VALUE_FLAGS.get(name, frozenset())
    optional = _OPTIONAL_VALUE_LETTERS.get(name, "")
    operands, found, i = [], {}, 0
    while i < len(args):
        arg = args[i]
        following = args[i + 1] if i + 1 < len(args) else None
        if arg == "--":
            operands.extend(args[i + 1 :])
            break
        if arg.startswith("--"):
            flag, eq, value = arg.partition("=")
            if flag in values and not eq:
                found[flag] = following or ""
                i += 2
                continue
            found[flag] = value
        elif arg.startswith("-") and arg != "-":
            flags, consumed = _short_cluster(arg, values, optional, following)
            found.update(flags)
            i += 1 + consumed
            continue
        else:
            operands.append(arg)
        i += 1
    return operands, found


def _in_place(arg: str) -> bool:
    return arg.startswith("--in-place") or (arg.startswith("-") and not arg.startswith("--") and "i" in arg[1:].split("=")[0])


def shell_write_targets(tokens: list[str]) -> list[str]:
    """Files a statement plainly writes: redirect targets and the targets of tee, sed -i, cp/mv/install/ln/rsync, touch/rm/rmdir/truncate/mkdir, chmod/chown and dd of=."""
    targets = [tokens[i + 1] for i, t in enumerate(tokens[:-1]) if t in (">", ">>", "<>")]
    argv = resolve(strip_redirects(tokens)).argv
    if not argv:
        return targets
    name, args = os.path.basename(argv[0]), argv[1:]
    operands, flags = _operands(name, args)
    if name == "tee":
        targets += [o for o in operands if o != "-"]
    elif name == "sed" and any(_in_place(a) for a in args if a.startswith("-")):
        scripted = any(f in flags for f in ("-e", "-f", "--expression", "--file"))
        targets += operands if scripted else operands[1:]
    elif name in _COPY:
        directory = flags.get("-t") or flags.get("--target-directory")
        targets += [directory] if directory else operands[-1:] if len(operands) > 1 else []
        if name == "mv":
            targets += operands
    elif name in _TOUCH:
        targets += operands
    elif name in _OWNER:
        targets += operands if "--reference" in flags else operands[1:]
    elif name == "dd":
        targets += [a[3:] for a in args if a.startswith("of=")]
    return targets


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


@lru_cache(maxsize=256)
def _compile_any(patterns: tuple[str, ...]) -> re.Pattern:
    return re.compile("|".join(f"(?:{_compile(_normalise(p)).pattern})" for p in patterns) or "(?!)")


def _match_at_any_depth(path: str, regex: re.Pattern) -> bool:
    path = _normalise(path)
    starts = [0] + [i + 1 for i, ch in enumerate(path) if ch == "/"]
    return any(regex.fullmatch(path, start) for start in starts)


def glob_match(path: str, pattern: str) -> bool:
    return _match_at_any_depth(path, _compile(_normalise(pattern)))


def _matches_any(path: str, patterns: list[str]) -> bool:
    return _match_at_any_depth(path, _compile_any(tuple(patterns)))


def matches_allowed(path: str, patterns: list[str]) -> bool:
    """A relative path matches from the checkout root; an absolute one at any depth, since the event carries no session cwd."""
    if path.startswith("/"):
        return _matches_any(path, patterns)
    return _compile_any(tuple(patterns)).fullmatch(_normalise(path)) is not None


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
    startup_files: list[str] | None = None,
    startup_home: str | None = None,
) -> Callable[[Event], Decision | None]:
    forbidden = forbidden_matcher(deny, exempt)
    has_denied = bool(deny)
    allowed_only = list(allow_only) if allow_only is not None else None
    startup = frozenset(startup_files or [])
    home = startup_home.rstrip("/") if startup_home else None

    startup_targets = frozenset(f"{home}/{name}" for name in startup) if home else frozenset()
    startup_names = frozenset(posixpath.basename(name) for name in startup)
    home_parts = frozenset(home.split("/")) if home else frozenset()
    tilde = re.compile(rf"^~(?:{re.escape(posixpath.basename(home))})?(?=/|$)") if home else None

    def startup_path(path: str) -> bool:
        if not startup_targets or not (any(name in path for name in startup_names) or startup_names & home_parts):
            return False
        readings = _home_readings(tilde.sub(lambda _: home, path), home)
        if readings is None:
            return True
        return any(posixpath.normpath(re.sub(r"/+", "/", reading)) in startup_targets for reading in readings)

    def relative_startup_template(path: str) -> bool:
        return bool(startup) and not path.startswith(("/", "~", "$HOME", "${HOME}")) and posixpath.normpath(path) in startup

    def check_path(path: str) -> Decision | None:
        if startup_path(path):
            return _deny(f"startup file {path} is not writable")
        if escapes(path) and not relative_startup_template(path):
            return _deny(f"path {path} tries to escape with '..'")
        if forbidden(path):
            return _deny(f"forbidden path {path}")
        if allowed_only is not None and not matches_allowed(path, allowed_only):
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

    def scratch(path: str) -> bool:
        return path == "/dev/null" or path == "/tmp" or path.startswith("/tmp/")

    def check_scope(command: str, scope: list[str] | None) -> Decision | None:
        fused = [re.sub(r"['\"]", "", t) for t in _FUSED_REDIRECT_TARGET.findall(command)]
        targets = [t for t in fused if t and not t.isdigit() and t != "-"]
        for literal in (False, True):
            try:
                statements = walk(command, literal_operators=literal)
            except OpaqueCommand as error:
                return _deny(str(error))
            except ValueError:
                return _deny("unparseable shell command while a write scope or startup files are set")
            for tokens in statements:
                targets += shell_write_targets(tokens)
        for raw in targets:
            if startup_path(raw):
                return _deny(f"startup file {raw} is not writable")
            target = posixpath.normpath(raw)
            if scope is not None:
                if escapes(target) or raw.startswith("~") or (target.startswith("/") and not scratch(target)):
                    return _deny(f"shell write to {raw} leaves the checkout; write a relative path or under /tmp")
                if "$" in raw or _EXPANDING_TARGET.search(raw):
                    return _deny(f"shell write to {raw} needs variable, brace or backtick expansion; write the path literally")
                if not scratch(target) and not matches_allowed(target, scope):
                    return _deny(f"shell write to {target} is outside allowed paths")
        return None

    def check_shell(arguments: dict) -> Decision:
        command = shell_command(arguments)
        if command is None:
            return _deny("shell command is not a string")
        try:
            statements = walk(command)
        except OpaqueCommand as error:
            return _deny(str(error))
        except ValueError:
            if has_denied or allowed_only is not None:
                return _deny("unparseable shell command while forbidden or allowed paths are set")
            return allow()
        reason = shell_shape_problem(statements)
        if reason:
            return _deny(reason)
        if allowed_only is not None or startup:
            decision = check_scope(command, list(allowed_only) if allowed_only is not None else None)
            if decision:
                return decision
        for tokens in statements:
            for token in tokens:
                candidate = _FLAG_PREFIX.sub("", token)
                if candidate and startup_path(candidate):
                    return _deny(f"startup file {candidate} is not readable or writable")
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
