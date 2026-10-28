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
_MAX_ALTERNATIVES = 256
_BRACE_WILD = "\0"
_INNER_BRACE = re.compile(r"\{[^{}]*\}")
_HOME_VARIABLE = re.compile(r"\"?\$(?:HOME\b|\{HOME\})\"?")
_DOTGLOB_WORD = re.compile(r"(?:dotglob|globskipdots|globdots|glob_dots|globignore|bashopts)", re.IGNORECASE)
_POSIX_CLASSES = {
    "alpha": "a-zA-Z", "digit": "0-9", "alnum": "a-zA-Z0-9", "upper": "A-Z", "lower": "a-z",
    "xdigit": "0-9A-Fa-f", "space": r" \t\n\r\f\v", "blank": r" \t", "word": "a-zA-Z0-9_",
    "punct": re.escape("!\"#$%&'()*+,-./:;<=>?@[\\]^_`{|}~"), "print": " -~", "graph": "!-~", "cntrl": r"\x00-\x1f\x7f",
}


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


def glob_match(path: str, pattern: str) -> bool:
    path = _normalise(path)
    regex = _compile(_normalise(pattern))
    starts = [0] + [i + 1 for i, ch in enumerate(path) if ch == "/"]
    return any(regex.fullmatch(path, start) for start in starts)


def _matches_any(path: str, patterns: list[str]) -> bool:
    return any(glob_match(path, p) for p in patterns)


def matches_allowed(path: str, patterns: list[str]) -> bool:
    """A relative path matches from the checkout root; an absolute one at any depth, since the event carries no session cwd."""
    if path.startswith("/"):
        return _matches_any(path, patterns)
    path = _normalise(path)
    return any(_compile(_normalise(p)).fullmatch(path) for p in patterns)


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


class _TooManyAlternatives(Exception):
    pass


def _sequence(body: str) -> list[str] | None:
    numbers = re.fullmatch(r"([-+]?\d+)\.\.([-+]?\d+)(?:\.\.([-+]?\d+))?", body)
    letters = re.fullmatch(r"([A-Za-z])\.\.([A-Za-z])(?:\.\.([-+]?\d+))?", body)
    match = numbers or letters
    if match is None:
        return None
    first, last, step = match.group(1), match.group(2), abs(int(match.group(3) or 1)) or 1
    start, end = (int(first), int(last)) if numbers else (ord(first), ord(last))
    if abs(end - start) // step + 1 > _MAX_ALTERNATIVES:
        raise _TooManyAlternatives
    values = range(start, end + (1 if end >= start else -1), step if end >= start else -step)
    if letters:
        return [chr(value) for value in values]
    padded = re.match(r"[-+]?0\d", first) or re.match(r"[-+]?0\d", last)
    width = max(len(first), len(last)) if padded else 0
    return [f"{value:0{width}d}" for value in values]


def _expand_braces(word: str) -> list[str]:
    for start, char in enumerate(word):
        if char != "{":
            continue
        depth, close, commas = 0, None, []
        for i in range(start, len(word)):
            if word[i] == "{":
                depth += 1
            elif word[i] == "}":
                depth -= 1
                if depth == 0:
                    close = i
                    break
            elif word[i] == "," and depth == 1:
                commas.append(i)
        if close is None:
            continue
        if commas:
            bounds = [start, *commas, close]
            parts = [word[a + 1 : b] for a, b in zip(bounds, bounds[1:])]
        else:
            parts = _sequence(word[start + 1 : close])
            if parts is None:
                continue
        prefix, tails = word[:start], _expand_braces(word[close + 1 :])
        results = []
        for part in parts:
            for head in _expand_braces(part):
                for tail in tails:
                    results.append(prefix + head + tail)
                    if len(results) > _MAX_ALTERNATIVES:
                        raise _TooManyAlternatives
        return results
    return [word]


def brace_alternatives(word: str) -> list[str] | None:
    """Bash brace expansion of `word`, or None above the alternative cap."""
    try:
        return _expand_braces(word)
    except _TooManyAlternatives:
        return None


def _bracket(pattern: str, start: int) -> tuple[str, int] | None:
    i = start + 1
    negate = pattern[i : i + 1] in ("!", "^")
    i += negate
    body = []
    if pattern[i : i + 1] == "]":
        body.append(r"\]")
        i += 1
    while i < len(pattern) and pattern[i] != "]":
        if pattern.startswith("[:", i):
            end = pattern.find(":]", i + 2)
            if end != -1 and pattern[i + 2 : end] in _POSIX_CLASSES:
                body.append(_POSIX_CLASSES[pattern[i + 2 : end]])
                i = end + 2
                continue
        char = pattern[i]
        ranged = char == "-" and body and pattern[i + 1 : i + 2] not in ("", "]")
        body.append("-" if ranged else re.escape(char))
        i += 1
    if i >= len(pattern):
        return None
    return "[" + ("^" if negate else "") + "".join(body) + "]", i + 1


def segment_matches(pattern: str, name: str, dotglob: bool = False) -> bool:
    """Bash glob match of one path segment, with dotglob off."""
    if not dotglob and name.startswith(".") and pattern[:1] in ("*", "?", "["):
        return False
    regex, i = [], 0
    while i < len(pattern):
        char = pattern[i]
        if char in ("*", _BRACE_WILD):
            regex.append(".*")
        elif char == "?":
            regex.append(".")
        elif char == "[" and (bracket := _bracket(pattern, i)) is not None:
            regex.append(bracket[0])
            i = bracket[1]
            continue
        else:
            regex.append(re.escape(char))
        i += 1
    return re.fullmatch("".join(regex), name, re.DOTALL) is not None



def _range_parts(body: str) -> list[str]:
    match = re.fullmatch(r"([-+]?\d+)\.\.([-+]?\d+)(?:\.\.([-+]?\d+))?", body)
    if match is None:
        match = re.fullmatch(r"([A-Za-z])\.\.([A-Za-z])(?:\.\.([-+]?\d+))?", body)
    if match is None:
        return [body]
    first, last, step = match.group(1), match.group(2), abs(int(match.group(3) or 1)) or 1
    numeric = first.lstrip("+-").isdigit()
    start, end = (int(first), int(last)) if numeric else (ord(first), ord(last))
    count = abs(end - start) // step + 1
    if count > _MAX_ALTERNATIVES:
        return ["__NUMBER_RANGE__"] if numeric else ["__LETTER_RANGE__"]
    values = range(start, end + (1 if end >= start else -1), step if end >= start else -step)
    if not numeric:
        return [chr(value) for value in values]
    padded = re.match(r"[-+]?0\d", first) or re.match(r"[-+]?0\d", last)
    width = max(len(first), len(last)) if padded else 0
    return [f"{value:0{width}d}" for value in values]


def _glob_regex(word: str, home: str, dotglob: bool, segment_start: bool = True) -> str:
    out, i = [], 0
    while i < len(word):
        if word.startswith("$HOME", i):
            out.append(re.escape(home)); i += 5; segment_start = False; continue
        if word.startswith("${HOME", i):
            close = word.find("}", i + 6)
            if close >= 0:
                out.append(re.escape(home)); i = close + 1; segment_start = False; continue
        if i == 0 and word.startswith("~") and (len(word) == 1 or word[1] == "/" or word.startswith("~" + posixpath.basename(home))):
            end = 1 + len(posixpath.basename(home)) if word.startswith("~" + posixpath.basename(home)) else 1
            out.append(re.escape(home)); i = end; segment_start = False; continue
        if word.startswith("__NUMBER_RANGE__", i):
            out.append(r"-?[0-9]+"); i += len("__NUMBER_RANGE__"); segment_start = False; continue
        if word.startswith("__LETTER_RANGE__", i):
            out.append(r"[A-Za-z]"); i += len("__LETTER_RANGE__"); segment_start = False; continue
        char = word[i]
        if char == "{":
            depth, close, commas = 0, None, []
            for j in range(i, len(word)):
                if word[j] == "{": depth += 1
                elif word[j] == "}":
                    depth -= 1
                    if depth == 0: close = j; break
                elif word[j] == "," and depth == 1: commas.append(j)
            if close is not None:
                body = word[i + 1:close]
                if commas:
                    bounds = [i, *commas, close]
                    parts = [word[x + 1:y] for x, y in zip(bounds, bounds[1:])]
                else:
                    parts = _range_parts(body)
                out.append("(?:" + "|".join(_glob_regex(part, home, dotglob, segment_start) for part in parts) + ")")
                i = close + 1; segment_start = False; continue
        if char in "*?": out.append((r"(?!\.)" if segment_start and not dotglob else "") + ("[^/]*" if char == "*" else "[^/]"))
        elif char == "[" and (bracket := _bracket(word, i)) is not None:
            out.append((r"(?!\.)" if segment_start and not dotglob else "") + bracket[0]); i = bracket[1]; segment_start = False; continue
        elif char == "(":
            end = word.find(")", i + 1)
            out.append("[^/]*")
            if end >= 0: i = end + 1; segment_start = False; continue
        elif char == "/": out.append("/+")
        else: out.append(re.escape(char))
        segment_start = char == "/"; i += 1
    return "".join(out)


def dotglob_enabled(command: str) -> bool:
    for tokens in walk(command):
        resolved = resolve(tokens)
        if resolved.name in {"shopt", "setopt", "unsetopt", "zsh"}:
            return True
        if resolved.name == "set" and any(arg.startswith(("-", "+")) for arg in resolved.argv[1:]):
            return True
        if resolved.name == "bash" and any(arg.startswith(("-O", "+O")) for arg in resolved.argv[1:]):
            return True
        for word in tokens:
            variants = brace_alternatives(word) or [re.sub(r"[{},]", "", word)]
            if any("glob" in re.sub(r"['\\\"]", "", variant).lower() or "bashopts" in variant.lower() for variant in variants):
                return True
    return False

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

    def startup_path(path: str, dotglob: bool = False) -> bool:
        if not startup or not home:
            return False
        if "${HOME" in path and any(name in path for name in startup):
            return True
        if path.startswith("~/{") and any(name in path for name in startup):
            return True
        if not path.startswith(("/", "~", "$HOME", "${HOME}", '"$HOME"', "{")):
            return False
        alternatives = brace_alternatives(path)
        home_marked = path.startswith("~") or "$HOME" in path or "${HOME" in path or "/home/omnigent" in path or ("{" in path and ("home" in path or "omnigent" in path))
        if alternatives is None:
            if home_marked and ("/./" in path or "/../" in path or "/..{" in path or "/.{" in path):
                return True
        elif any("/." in alternative for alternative in alternatives):
            tilde = re.compile(rf"^~(?:{re.escape(posixpath.basename(home))})?(?=/|$)")
            targets = [f"{home}/{name}" for name in startup]
            for alternative in alternatives:
                expanded = _HOME_VARIABLE.sub(lambda _: home, tilde.sub(lambda _: home, alternative))
                if posixpath.normpath(re.sub(r"/+", "/", expanded)) in targets:
                    return True
        pattern = _glob_regex(re.sub(r"/+", "/", path), home, dotglob)
        targets = [f"{home}/{name}" for name in startup]
        if any(re.fullmatch(pattern, target) for target in targets):
            return True
        if alternatives is None:
            return False
        tilde = re.compile(rf"^~(?:{re.escape(posixpath.basename(home))})?(?=/|$)")
        for alternative in alternatives:
            expanded = _HOME_VARIABLE.sub(lambda _: home, tilde.sub(lambda _: home, alternative))
            normal = posixpath.normpath(re.sub(r"/+", "/", expanded))
            if normal in targets:
                return True
        return False

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
        dotglob = dotglob_enabled(command)
        if re.search(r"(?:~(?:omnigent)?|\"?\$HOME\"?|\"?\$\{HOME[^}]*\}\"?|/home/[^/]+)/(?:(?!\$\()[^\s;|])*\(", command):
            return _deny("startup file target with zsh grouping is not readable or writable")
        fused = [re.sub(r"['\"]", "", t) for t in _FUSED_REDIRECT_TARGET.findall(command)]
        targets = [t for t in fused if t and not t.isdigit() and t != "-"]
        for literal in (False, True):
            for tokens in walk(command, literal_operators=literal):
                targets += shell_write_targets(tokens)
        for raw in targets:
            home_capable = raw.startswith(("/", "~", "$HOME", "${HOME}", '"$HOME"', "{")) or "home" in raw or "omnigent" in raw
            if home_capable and (len(raw) > 4096 or sum(raw.count(char) for char in "*?{") > 16):
                return _deny("startup path pattern is too large")
            grouped = "(" in raw and "$(" not in raw and "<(" not in raw and ">(" not in raw
            home_marked = raw.startswith(("~", "$HOME", "${HOME}", '"$HOME"', "/")) or "home" in raw or "omnigent" in raw
            if grouped and home_marked:
                return _deny(f"startup file {raw} is not readable or writable")
            if startup_path(raw, dotglob):
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
        if startup and len(command) > 4096 and re.search(r"(?:~/|\$HOME|\$\{HOME|/home/omnigent)", command):
            return _deny("startup path pattern is too large")
        try:
            statements = walk(command)
        except OpaqueCommand:
            return _deny("a heredoc without its delimiter line hides what runs after it; close it")
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
                if candidate and startup_path(candidate, dotglob_enabled(command)):
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
