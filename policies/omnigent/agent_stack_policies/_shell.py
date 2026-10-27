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
OPAQUE_EXPANSION = re.compile(r"\$\{[^}]*[:\[@!]")
_DECODING_EXPANSION = re.compile(r"\$\{[^}]*@[PE]")
_FD = re.compile(r"^\d+$")
_QUOTED_DOLLAR = re.compile(r"\$['\"]")
_LITERAL_OPERATOR = re.compile(r"(?<![^\s;&|()])(?:'[<>&|;()]+'|\"[<>&|;()]+\"|\\[<>&|;()])(?=$|[\s;&|()])")
LITERAL_OPERATOR = "__agent_stack_literal_operator__"


@dataclass
class Command:
    """A statement's command after keywords, `VAR=` assignments and launcher wrappers."""

    argv: list[str]
    assignments: list[str] = field(default_factory=list)
    wrappers: list[str] = field(default_factory=list)
    nested: list[str] = field(default_factory=list)
    stdin_exec: bool = False
    body_arguments: list[str] = field(default_factory=list)

    @property
    def name(self) -> str:
        return os.path.basename(self.argv[0]) if self.argv else ""


class OpaqueCommand(ValueError):
    """Input whose statements can't be known, such as a heredoc without its delimiter line; every gate denies it."""


_HEREDOC = re.compile(r"<<(-?)[ \t]*(?:'([^']*)'|\"([^\"]*)\"|\\?([^\s;&|<>()'\"]+))")


def _heredocs(line: str) -> list[tuple[str, bool]]:
    """(delimiter, strip leading tabs) for each unquoted `<<WORD`/`<<-WORD` in a line, in order; `<<<` is a here-string."""
    docs, quote, i = [], None, 0
    while i < len(line):
        ch = line[i]
        if quote:
            if ch == "\\" and quote in ('"', "$'"):
                i += 2
                continue
            quote = None if ch == quote[-1] else quote
        elif ch == "$" and line[i + 1 : i + 2] == "'":
            quote = "$'"
            i += 2
            continue
        elif ch == "\\":
            i += 2
            continue
        elif ch in "'\"":
            quote = ch
        elif line.startswith("<<", i) and not line.startswith("<<<", i) and line[i - 1 : i] != "<":
            arithmetic = line.count("((", 0, i) > line.count("))", 0, i)
            match = _HEREDOC.match(line, i)
            if match and not arithmetic:
                docs.append((next(g for g in match.groups()[1:] if g is not None), match.group(1) == "-"))
                i = match.end()
                continue
        i += 1
    return docs


def _skip_heredocs(text: str, pos: int, docs: list[tuple[str, bool]]) -> int:
    """Index after the bodies of `docs`, which start at `pos`; raises OpaqueCommand when a delimiter line is missing."""
    for delimiter, strip in docs:
        while True:
            if pos >= len(text):
                raise OpaqueCommand(f"heredoc without its {delimiter} line")
            end = text.find("\n", pos)
            end = len(text) if end < 0 else end
            line = text[pos:end]
            pos = end + 1
            if (line.lstrip("\t") if strip else line) == delimiter:
                break
    return pos


def _substitution_char(command: str, i: int, current: list[str], frames: list[list]) -> int:
    """One character inside a `$(…)` opened within double quotes, where bash parses a fresh shell context; returns the next index."""
    frame = frames[-1]
    quote, ch = frame[0], command[i]
    if ch == "\\" and quote != "'" and i + 1 < len(command):
        current.append(command[i : i + 2])
        return i + 2
    if quote == '"' and command.startswith("$(", i):
        frames.append([None, 0, len(current) + 1])
        current.append("$(")
        return i + 2
    if quote:
        frame[0] = None if ch == quote[-1] else quote
    elif command.startswith("$'", i):
        frame[0] = "$'"
        current.append("$'")
        return i + 2
    elif command.startswith("$(", i):
        frames.append([None, 0, len(current) + 1])
        current.append("$(")
        return i + 2
    elif ch in "'\"":
        frame[0] = ch
    elif ch == "(":
        frame[1] += 1
    elif ch == ")":
        if frame[1]:
            frame[1] -= 1
        else:
            frames.pop()
    elif ch == "\n":
        current.append(ch)
        docs = _heredocs("".join(current[frame[2] :]))
        frame[2] = len(current)
        return _skip_heredocs(command, i + 1, docs)
    current.append(ch)
    return i + 1


def _lines(command: str) -> list[str]:
    """Logical lines: newlines inside quotes and after a backslash join, `#` comments drop, and heredoc bodies are text, not commands."""
    lines, current, quote, escaped, i = [], [], None, False, 0
    frames: list[list] = []
    parens: list[bool] = []
    closed_substitution = False
    while i < len(command):
        if frames:
            i = _substitution_char(command, i, current, frames)
            continue
        ch = command[i]
        if escaped:
            escaped = False
            if ch == "\n":
                current.pop()
                i += 1
                continue
        elif ch == "\\" and quote != "'":
            escaped = True
        elif quote == '"' and command.startswith("$(", i):
            frames.append([None, 0, len(current) + 1])
            current.append("$(")
            i += 2
            continue
        elif quote:
            quote = None if ch == quote[-1] else quote
        elif ch == "$" and command[i + 1 : i + 2] == "'":
            current.append("$'")
            quote = "$'"
            i += 2
            continue
        elif ch == "#" and (not current or current[-1] in " \t;&|(" or (current[-1] == ")" and not closed_substitution)):
            end = command.find("\n", i)
            i = len(command) if end < 0 else end
            continue
        elif command.startswith("$(", i):
            parens.append(True)
            current.append("$(")
            i += 2
            continue
        elif ch == "(":
            parens.append(False)
        elif ch == ")":
            closed_substitution = parens.pop() if parens else False
        elif ch in "'\"":
            quote = ch
        elif ch == "\n":
            line = "".join(current)
            lines.append(line)
            current = []
            i = _skip_heredocs(command, i + 1, _heredocs(line))
            continue
        current.append(ch)
        i += 1
    line = "".join(current)
    lines.append(line)
    if (quote is None or frames) and _heredocs(line if not frames else "".join(current[frames[-1][2] :])):
        raise OpaqueCommand("heredoc without a body")
    return lines


_ANSI_SIMPLE = {
    "a": "\a", "b": "\b", "e": "\x1b", "E": "\x1b", "f": "\f", "n": "\n", "r": "\r", "t": "\t", "v": "\v",
    "\\": "\\", "'": "'", '"': '"', "?": "?",
}
_ANSI_DIGITS = {"x": (16, 2), "u": (16, 4), "U": (16, 8)}


OPAQUE_WORD = "$__agent_stack_opaque__"


def _ansi_body(text: str, start: int) -> tuple[str, int, bool] | None:
    """(value, index after the closing quote, opaque) for a `$'…'` body by bash's rules, or None when unterminated; a NUL, `\\c` or a raw byte ≥ 0x80 is opaque."""
    out, i, opaque, truncated = [], start, False, False

    def add(char: str) -> None:
        nonlocal opaque, truncated
        if char == "\0":
            opaque = truncated = True
        elif not truncated:
            out.append(char)

    while i < len(text):
        ch = text[i]
        if ch == "'":
            return "".join(out), i + 1, opaque
        if ch != "\\" or i + 1 >= len(text):
            add(ch)
            i += 1
            continue
        esc = text[i + 1]
        if esc in _ANSI_SIMPLE:
            add(_ANSI_SIMPLE[esc])
            i += 2
        elif esc in _ANSI_DIGITS:
            base, width = _ANSI_DIGITS[esc]
            digits = re.match(r"[0-9a-fA-F]{1,%d}" % width, text[i + 2 :])
            if not digits:
                add("\\")
                add(esc)
                i += 2
                continue
            code = int(digits.group(), base)
            if (esc == "x" and code >= 0x80) or code > 0x10FFFF or 0xD800 <= code <= 0xDFFF:
                opaque = True
            else:
                add(chr(code))
            i += 2 + len(digits.group())
        elif esc in "01234567":
            digits = re.match(r"[0-7]{1,3}", text[i + 1 :]).group()
            code = int(digits, 8) & 0xFF
            if code >= 0x80:
                opaque = True
            else:
                add(chr(code))
            i += 1 + len(digits)
        elif esc == "c":
            opaque = True
            following = text[i + 2 : i + 3]
            if following in ("", "'"):
                add("\\")
                add("c")
                i += 2
            else:
                i += 4 if text[i + 2 : i + 4] == "\\\\" else 3
        else:
            add("\\")
            add(esc)
            i += 2
    return None


def has_opaque_word(tokens: list[str]) -> bool:
    return any(OPAQUE_WORD in t for t in tokens)


def decode_ansi(command: str) -> str:
    """Rewrite `$'…'` as the single-quoted decoded value and `$"…"` as `"…"`, so the lexer sees the words bash runs."""
    out, i, quote = [], 0, None
    while i < len(command):
        ch = command[i]
        if quote == "'":
            quote = None if ch == "'" else quote
        elif quote == '"':
            if ch == "\\" and i + 1 < len(command):
                out.append(command[i : i + 2])
                i += 2
                continue
            quote = None if ch == '"' else quote
        elif ch == "\\" and i + 1 < len(command):
            out.append(command[i : i + 2])
            i += 2
            continue
        elif ch == "$" and command[i + 1 : i + 2] == "'":
            decoded = _ansi_body(command, i + 2)
            if decoded is not None:
                value, i, opaque_value = decoded
                out.append("'" + value.replace("'", "'\\''") + "'" + (OPAQUE_WORD if opaque_value else ""))
                continue
        elif ch == "$" and command[i + 1 : i + 2] == '"':
            i += 1
            continue
        elif ch in "'\"":
            quote = ch
        out.append(ch)
        i += 1
    return "".join(out)


def parse(command: str, literal_operators: bool = False) -> list[list[str]]:
    """Statements as token lists; with `literal_operators`, a quoted or escaped operator word becomes LITERAL_OPERATOR."""
    statements: list[list[str]] = []
    for line in [decode_ansi(raw) for raw in _lines(command)]:
        if literal_operators:
            line = _LITERAL_OPERATOR.sub(LITERAL_OPERATOR, line)
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


def strip_redirects(args: list[str]) -> list[str]:
    """Words without redirections and their targets (`2>&1` leaves a bare fd when `parse` splits at `>&`)."""
    out, i = [], 0
    while i < len(args):
        if args[i] and set(args[i]) <= set("<>"):
            i += 2
            continue
        if _FD.match(args[i]) and (i + 1 == len(args) or set(args[i + 1]) <= set("<>")):
            i += 1
            continue
        out.append(args[i])
        i += 1
    return out


def _shell_inputs(args: list[str], nested: list[str], body_arguments: list[str] | None = None) -> bool:
    """Collect `-c` and here-string bodies (and the positional arguments after a `-c` body); True when the shell runs a script from stdin."""
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
                    if body_arguments is not None:
                        body_arguments.extend(strip_redirects(args[i + 2 :]))
                return False
            if arg.startswith("-") and "s" in arg[1:]:
                return True
            i += 2 if arg[-1] in "oO" else 1
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
            command.stdin_exec = _shell_inputs(argv[1:], command.nested, command.body_arguments)
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


def opaque(text: str) -> bool:
    """A `${…}` with `:`, `[`, `@` or `!` after the name: substrings, subscripts, transformations and indirection evaluate or decode values no gate can read."""
    return OPAQUE_EXPANSION.search(text) is not None


def ansi_quoted(text: str) -> bool:
    """`$'…'` or `$"…"` somewhere: `parse` turns `$'\x67it'` into a `$…` token, so a `$`-led name may hide any command."""
    return _QUOTED_DOLLAR.search(text) is not None


def opaque_name(text: str, token: str) -> bool:
    return "$" in token and ansi_quoted(text)


def decoding(text: str) -> bool:
    """`${x@P}` or `${x@E}`: the value's escapes are decoded and, for `@P`, expanded, so it can run any command."""
    return _DECODING_EXPANSION.search(text) is not None


def shell_shape_problem(statements: list[list[str]]) -> str | None:
    """A reason when a statement passes positional arguments after a shell's `-c` body."""
    for tokens in statements:
        command = resolve(tokens)
        if command.body_arguments:
            return f"arguments after a {command.name} -c body are not allowed; put everything in the body"
    return None


def hidden_command(text: str, statements: list[list[str]], names: re.Pattern) -> str | None:
    """A reason when an opaque expansion could run a command matching `names`, or names the command itself, or a `-c` body takes arguments."""
    if decoding(text):
        return "${…@P} and ${…@E} decode a value into commands no gate can read; write the command literally"
    if opaque(text):
        commands = [resolve(t).argv for t in statements]
        if names.search(text) or any(argv and opaque(argv[0]) for argv in commands):
            return "substring, subscript, transformation or indirect expansions next to this command are not allowed; write it literally"
    return shell_shape_problem(statements)


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


def _walk_quietly(command: str, literal_operators: bool = False) -> list[list[str]]:
    try:
        return walk(command, literal_operators)
    except OpaqueCommand:
        raise
    except ValueError:
        return _rough(command)


def group_token(token: str) -> bool:
    """`(`, `)`, `$`, and punctuation runs that open or close one, such as `<(`, `>(` and `)<(`."""
    return token in _GROUPING or (bool(token) and set(token) <= _PUNCTUATION and bool(set(token) & {"(", ")"}))


def _groups(tokens: list[str]) -> list[list[str]]:
    if not any(group_token(t) for t in tokens):
        return []
    segments, current = [], []
    for token in tokens:
        if group_token(token):
            segments.append(current)
            current = []
        else:
            current.append(token)
    segments.append(current)
    return [s for s in segments if s]


def walk(command: str, literal_operators: bool = False) -> list[list[str]]:
    """Every statement, segment and nested body; raises ValueError on an unparseable top level or `-c` body, splits an unparseable `$(...)` body roughly."""
    result: list[list[str]] = []
    for tokens in parse(command, literal_operators):
        for unit in [tokens, *_groups(tokens)]:
            result.append(unit)
            nested = resolve(unit).nested
            extra = _nested_command(unit)
            for body in dict.fromkeys([*nested, *([extra] if extra is not None else [])]):
                result.extend(walk(body, literal_operators))
    for body in _bodies(command):
        result.extend(_walk_quietly(body, literal_operators))
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
