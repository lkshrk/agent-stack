import os
import re
import shlex
from dataclasses import dataclass, field

_OPERATOR_CHARS = frozenset(";&|")
_PUNCTUATION = frozenset(";&|()<>")
_GROUPING = frozenset({"(", ")", "$"})
SHELLS = frozenset({"sh", "bash", "zsh", "dash", "ksh"})
DEFAULT_PROTECTED = ("main", "master")
_KEYWORDS = frozenset({"!", "if", "then", "elif", "else", "do", "while", "until", "(", "{"})
_ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
_SHELL_VALUE_FLAGS = frozenset({"-o", "+o", "-O", "+O", "--rcfile", "--init-file"})
_ROUGH_STATEMENT = re.compile(r"[;&|\n]+")
_ROUGH_TOKEN = re.compile(r"[^\s'\"`;&|()<>]+")
_BACKTICKS = re.compile(r"`([^`]*)`")

_SUDO_VALUES = {
    "-u", "-g", "-C", "-D", "-h", "-p", "-r", "-t", "-U", "-T", "--user", "--group", "--chdir",
    "--host", "--prompt", "--role", "--type", "--other-user", "--close-from", "--command-timeout",
}
_XARGS_VALUES = {
    "-I", "-n", "-P", "-L", "-s", "-d", "-E", "-a", "--max-args", "--max-procs", "--max-lines",
    "--max-chars", "--delimiter", "--eof", "--arg-file", "--process-slot-var",
}
_PARALLEL_VALUES = {
    "-j", "--jobs", "-S", "--sshlogin", "--joblog", "--delay", "--timeout", "-N", "-n",
    "--max-args", "--colsep", "-d", "--delimiter", "-a", "--arg-file", "-I", "--tmpdir", "--results",
}
# wrapper -> (options that take a value, operands before the wrapped command)
_WRAPPERS: dict[str, tuple[frozenset[str], int]] = {
    "command": (frozenset(), 0),
    "builtin": (frozenset(), 0),
    "exec": (frozenset({"-a"}), 0),
    "nohup": (frozenset(), 0),
    "setsid": (frozenset(), 0),
    "ionice": (frozenset({"-c", "--class", "-n", "--classdata", "-p", "--pid", "-P", "--pgid", "-u", "--uid"}), 0),
    "nice": (frozenset({"-n", "--adjustment"}), 0),
    "timeout": (frozenset({"-s", "--signal", "-k", "--kill-after"}), 1),
    "stdbuf": (frozenset({"-i", "-o", "-e", "--input", "--output", "--error"}), 0),
    "time": (frozenset({"-f", "-o", "--format", "--output"}), 0),
    "sudo": (frozenset(_SUDO_VALUES), 0),
    "doas": (frozenset({"-u", "-C"}), 0),
    "xargs": (frozenset(_XARGS_VALUES), 0),
}
_UV_VALUES = frozenset({
    "--extra", "--no-extra", "--group", "--no-group", "--only-group", "--no-editable-package", "--env-file", "-w",
    "--with", "--with-editable", "--with-requirements", "--package", "--python-platform", "--index",
    "--default-index", "-i", "--index-url", "--extra-index-url", "-f", "--find-links", "--index-strategy",
    "--keyring-provider", "-P", "--upgrade-package", "--upgrade-group", "--resolution", "--prerelease",
    "--prerelease-package", "--fork-strategy", "--exclude-newer", "--exclude-newer-package", "--no-sources-package",
    "--reinstall-package", "--link-mode", "-C", "--config-setting", "--config-settings-package",
    "--no-build-isolation-package", "--no-build-package", "--no-binary-package", "--cache-dir", "--refresh-package",
    "-p", "--python", "--color", "--allow-insecure-host", "--directory", "--project", "--config-file", "--from",
    "-c", "--constraints", "-b", "--build-constraints", "--overrides", "--torch-backend",
})
_MISE_VALUES = frozenset({"-C", "--cd", "-E", "--env", "-j", "--jobs", "--log-level"})
_PYTHON_SPEC = re.compile(r"[@=<>!~\[]")
_LAUNCH_OPTIONS = frozenset({"-p", "--package", "-c", "--call"})
_LAUNCH_SUBCOMMANDS = {"pnpm": {"dlx", "exec"}, "yarn": {"dlx", "exec"}, "npm": {"exec", "x"}, "bun": {"x"}}
_FIND_EXEC = frozenset({"-exec", "-execdir", "-ok", "-okdir"})


@dataclass
class Command:
    """A statement's command after keywords, `VAR=` assignments and launcher wrappers."""

    argv: list[str]
    assignments: list[str] = field(default_factory=list)
    wrappers: list[str] = field(default_factory=list)
    nested: list[str] = field(default_factory=list)
    stdin_exec: bool = False

    @property
    def name(self) -> str:
        return os.path.basename(self.argv[0]) if self.argv else ""


def _lines(command: str) -> list[str]:
    lines, current, quote, escaped = [], [], None, False
    for ch in command:
        if escaped:
            escaped = False
            if ch == "\n":
                current.pop()
                continue
        elif ch == "\\" and quote != "'":
            escaped = True
        elif quote:
            quote = None if ch == quote else quote
        elif ch in "'\"":
            quote = ch
        elif ch == "\n":
            lines.append("".join(current))
            current = []
            continue
        current.append(ch)
    lines.append("".join(current))
    return lines


def parse(command: str) -> list[list[str]]:
    statements: list[list[str]] = []
    for line in _lines(command):
        lexer = shlex.shlex(line, posix=True, punctuation_chars=True)
        lexer.whitespace_split = True
        lexer.commenters = ""
        current: list[str] = []
        for token in lexer:
            chars = set(token)
            if chars <= _PUNCTUATION and chars & _OPERATOR_CHARS:
                if current:
                    statements.append(current)
                current = []
            else:
                current.append(token)
        if current:
            statements.append(current)
    return statements


def _scan(args: list[str], values: frozenset[str]) -> tuple[int, list[tuple[str, str]]]:
    """getopt-style: index of the first operand and the (option, value) pairs for `values`."""
    found = []
    i = 0
    while i < len(args):
        arg = args[i]
        if arg == "--":
            return i + 1, found
        if not arg.startswith("-") or arg == "-":
            return i, found
        following = args[i + 1] if i + 1 < len(args) else ""
        if arg.startswith("--"):
            name, eq, value = arg.partition("=")
            if name in values:
                found.append((name, value if eq else following))
                i += 1 if eq else 2
                continue
            i += 1
            continue
        i += 1
        for k in range(1, len(arg)):
            if "-" + arg[k] in values:
                rest = arg[k + 1 :]
                found.append(("-" + arg[k], rest or following))
                i += 0 if rest else 1
                break
    return i, found


def _command_options(args: list[str], options: frozenset[str]) -> list[str]:
    out = []
    for i, arg in enumerate(args):
        following = args[i + 1] if i + 1 < len(args) else ""
        if arg.startswith("--"):
            name, eq, value = arg.partition("=")
            if name in options:
                out.append(value if eq else following)
        elif arg.startswith("-") and len(arg) > 1:
            for k in range(1, len(arg)):
                if "-" + arg[k] in options:
                    out.append(arg[k + 1 :] or following)
                    break
    return out


def _package(spec: str) -> str:
    if spec.startswith("@"):
        scope, _, rest = spec[1:].partition("/")
        return f"@{scope}/{rest.split('@', 1)[0]}"
    return spec.split("@", 1)[0] or spec


def _launch(args: list[str], nested: list[str]) -> list[str]:
    start, found = _scan(args, _LAUNCH_OPTIONS)
    calls = [value for option, value in found if option in ("-c", "--call")]
    if calls:
        nested.extend(calls)
        return []
    operands = args[start:]
    return [_package(operands[0]), *operands[1:]] if operands else []


def _uv(args: list[str], tool: bool) -> list[str]:
    start, _ = _scan(args, _UV_VALUES)
    operands = args[start:]
    if tool and operands:
        return [_PYTHON_SPEC.split(operands[0], 1)[0], *operands[1:]]
    return operands


def _mise_exec(args: list[str], nested: list[str]) -> list[str]:
    split = args.index("--") if "--" in args else len(args)
    commands = _command_options(args[:split], frozenset({"-c", "--command"}))
    nested.extend(commands)
    return [] if commands else args[split + 1 :]


def _shell_inputs(args: list[str], nested: list[str]) -> bool:
    """Collect `-c` and here-string bodies; True when the shell runs a script from stdin."""
    i = 0
    while i < len(args):
        arg = args[i]
        following = args[i + 1] if i + 1 < len(args) else None
        if arg == "<<<":
            if following is not None:
                nested.append(following)
            return False
        if arg in ("<", "<<"):
            return True
        if arg in (">", ">>"):
            i += 2
            continue
        if arg == "--":
            return i + 1 >= len(args)
        if arg in _SHELL_VALUE_FLAGS:
            i += 2
            continue
        if arg.startswith(("-", "+")) and not arg.startswith("--"):
            if arg.startswith("-") and "c" in arg[1:]:
                if following is not None:
                    nested.append(following)
                return False
            if arg.startswith("-") and "s" in arg[1:]:
                return True
            i += 1
            continue
        if arg.startswith("--"):
            i += 1
            continue
        return False
    return True


def _unwrap(name: str, args: list[str], nested: list[str]) -> list[str] | None:
    """The command a wrapper runs, or None when `name` is not a wrapper."""
    if name in ("npx", "bunx", "pnpx"):
        return _launch(args, nested)
    if name in _LAUNCH_SUBCOMMANDS and args[:1] and args[0] in _LAUNCH_SUBCOMMANDS[name]:
        return _launch(args[1:], nested)
    if name == "uvx":
        return _uv(args, tool=True)
    if name == "uv":
        start, _ = _scan(args, _UV_VALUES)
        rest = args[start:]
        if rest[:1] == ["run"]:
            return _uv(rest[1:], tool=False)
        if rest[:2] == ["tool", "run"]:
            return _uv(rest[2:], tool=True)
        return None
    if name == "mise":
        start, _ = _scan(args, _MISE_VALUES)
        rest = args[start:]
        return _mise_exec(rest[1:], nested) if rest[:1] in (["exec"], ["x"]) else None
    if name == "env":
        start, found = _scan(args, frozenset({"-u", "--unset", "-C", "--chdir", "-S", "--split-string"}))
        operands = args[start:]
        for option, value in found:
            if option in ("-S", "--split-string"):
                try:
                    return shlex.split(value) + operands
                except ValueError:
                    nested.append(value)
        return operands
    if name == "flock":
        start, _ = _scan(args, frozenset({"-w", "--wait", "--timeout", "-E", "--conflict-exit-code"}))
        operands = args[start + 1 :]
        if operands[:1] and operands[0] in ("-c", "--command"):
            nested.extend(operands[1:2])
            return []
        return operands
    if name in ("script", "su"):
        nested.extend(_command_options(args, frozenset({"-c", "--command"})))
        return []
    if name == "watch":
        start, _ = _scan(args, frozenset({"-n", "--interval"}))
        if args[start:]:
            nested.append(" ".join(args[start:]))
        return []
    if name == "find":
        starts = [i for i, a in enumerate(args) if a in _FIND_EXEC]
        if not starts:
            return None
        runs = []
        for i in starts:
            end = next((j for j in range(i + 1, len(args)) if args[j] in (";", "+")), len(args))
            runs.append(args[i + 1 : end])
        nested.extend(shlex.join(run) for run in runs[1:] if run)
        return runs[0]
    if name == "parallel":
        start, _ = _scan(args, frozenset(_PARALLEL_VALUES))
        operands = args[start:]
        if ":::" in operands:
            split = operands.index(":::")
            if split == 0:
                nested.extend(a for a in operands[1:] if not a.startswith(":::"))
                return []
            return operands[:split]
        return operands
    if name not in _WRAPPERS:
        return None
    values, positionals = _WRAPPERS[name]
    start, _ = _scan(args, values)
    return args[start + positionals :]


def resolve(tokens: list[str]) -> Command:
    """The command a statement runs, through keywords, assignments and the wrappers above."""
    argv = list(tokens)
    command = Command(argv=[])
    while True:
        while argv and argv[0] in _KEYWORDS:
            argv = argv[1:]
        while argv and _ASSIGNMENT.match(argv[0]):
            command.assignments.append(argv.pop(0))
        if not argv:
            break
        name = os.path.basename(argv[0])
        if name in SHELLS:
            command.stdin_exec = _shell_inputs(argv[1:], command.nested)
            break
        if name == "eval":
            if argv[1:]:
                command.nested.append(" ".join(argv[1:]))
            break
        unwrapped = _unwrap(name, argv[1:], command.nested)
        if unwrapped is None:
            break
        command.wrappers.append(name)
        if name == "parallel" and not unwrapped and not command.nested:
            command.stdin_exec = True
        argv = unwrapped
    command.argv = argv
    return command


def _nested_command(tokens: list[str]) -> str | None:
    for i, token in enumerate(tokens):
        name = os.path.basename(token)
        if name == "eval" and i + 1 < len(tokens):
            return " ".join(tokens[i + 1 :])
        if name not in SHELLS:
            continue
        for j in range(i + 1, len(tokens) - 1):
            flag = tokens[j]
            if flag.startswith("-") and not flag.startswith("--") and "c" in flag[1:]:
                return tokens[j + 1]
    return None


def substituted(token: str) -> bool:
    return "`" in token or "$(" in token


def _bodies(text: str) -> list[str]:
    bodies = []
    i = 0
    while (start := text.find("$(", i)) >= 0:
        depth, j = 1, start + 2
        while j < len(text) and depth:
            depth += {"(": 1, ")": -1}.get(text[j], 0)
            j += 1
        bodies.append(text[start + 2 : j - 1 if depth == 0 else j])
        i = j
    return bodies + _BACKTICKS.findall(text)


def _rough(text: str) -> list[list[str]]:
    return [tokens for part in _ROUGH_STATEMENT.split(text) if (tokens := _ROUGH_TOKEN.findall(part))]


def _walk_quietly(command: str) -> list[list[str]]:
    try:
        return walk(command)
    except ValueError:
        return _rough(command)


def _groups(tokens: list[str]) -> list[list[str]]:
    if not any(t in _GROUPING for t in tokens):
        return []
    segments, current = [], []
    for token in tokens:
        if token in _GROUPING:
            segments.append(current)
            current = []
        else:
            current.append(token)
    segments.append(current)
    return [s for s in segments if s]


def walk(command: str) -> list[list[str]]:
    """Every statement, segment and nested body; raises ValueError on an unparseable top level or `-c` body, splits an unparseable `$(...)` body roughly."""
    result: list[list[str]] = []
    for tokens in parse(command):
        for unit in [tokens, *_groups(tokens)]:
            result.append(unit)
            nested = resolve(unit).nested
            extra = _nested_command(unit)
            for body in dict.fromkeys([*nested, *([extra] if extra is not None else [])]):
                result.extend(walk(body))
    for body in _bodies(command):
        result.extend(_walk_quietly(body))
    return result


_REF_NAMESPACES = frozenset({"refs", "heads", "tags", "remotes", "notes"})


def push_destination(refspec: str) -> str:
    return refspec.lstrip("+").rsplit(":", 1)[-1]


def push_branch(refspec: str) -> str | None:
    """Branch a push refspec writes to, or None for `heads/x`, tags, notes and other refs git resolves outside a plain branch name."""
    destination = push_destination(refspec).removeprefix("refs/heads/")
    if destination.split("/", 1)[0] in _REF_NAMESPACES:
        return None
    return destination


def shell_command(arguments: dict) -> str | None:
    """The shell command of a tool call ("" when absent), or None for an argument shape no gate can read."""
    command = arguments.get("command", arguments.get("cmd", ""))
    if isinstance(command, str):
        return command
    if isinstance(command, list) and all(isinstance(part, str) for part in command):
        return shlex.join(command)
    return None
