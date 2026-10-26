import os
import shlex

_OPERATOR_CHARS = frozenset(";&|")
_PUNCTUATION = frozenset(";&|()<>")
_SHELLS = frozenset({"sh", "bash", "zsh"})


def _lines(command: str) -> list[str]:
    lines, current, quote, escaped = [], [], None, False
    for ch in command:
        if escaped:
            escaped = False
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


def _nested_command(tokens: list[str]) -> str | None:
    for i, token in enumerate(tokens):
        if os.path.basename(token) not in _SHELLS:
            continue
        for j in range(i + 1, len(tokens) - 1):
            flag = tokens[j]
            if flag.startswith("-") and not flag.startswith("--") and "c" in flag[1:]:
                return tokens[j + 1]
    return None


def walk(command: str) -> list[list[str]]:
    """All statements of a command, including those inside `sh|bash|zsh -c`; raises ValueError if unparseable."""
    result: list[list[str]] = []
    for tokens in parse(command):
        result.append(tokens)
        nested = _nested_command(tokens)
        if nested is not None:
            result.extend(walk(nested))
    return result


def shell_command(arguments: dict) -> str:
    command = arguments.get("command", arguments.get("cmd", ""))
    return command if isinstance(command, str) else ""
