import os
import shlex

_OPERATOR_CHARS = frozenset(";&|")
_PUNCTUATION = frozenset(";&|()<>")
_SHELLS = frozenset({"sh", "bash", "zsh", "dash", "ksh"})


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


def _nested_command(tokens: list[str]) -> str | None:
    for i, token in enumerate(tokens):
        name = os.path.basename(token)
        if name == "eval" and i + 1 < len(tokens):
            return " ".join(tokens[i + 1 :])
        if name not in _SHELLS:
            continue
        for j in range(i + 1, len(tokens) - 1):
            flag = tokens[j]
            if flag.startswith("-") and not flag.startswith("--") and "c" in flag[1:]:
                return tokens[j + 1]
    return None


def substituted(token: str) -> bool:
    return "`" in token or "$(" in token


def _bodies(token: str) -> list[str]:
    bodies = []
    i = 0
    while (start := token.find("$(", i)) >= 0:
        depth, j = 1, start + 2
        while j < len(token) and depth:
            depth += {"(": 1, ")": -1}.get(token[j], 0)
            j += 1
        bodies.append(token[start + 2 : j - 1 if depth == 0 else j])
        i = j
    return bodies + token.split("`")[1::2]


def _walk_quietly(command: str) -> list[list[str]]:
    try:
        return walk(command)
    except ValueError:
        return []


def walk(command: str) -> list[list[str]]:
    """All statements of a command, including `sh -c`, `eval` and `$(...)` bodies; raises ValueError if the top level is unparseable (unparseable bodies, e.g. heredocs, are skipped)."""
    result: list[list[str]] = []
    for tokens in parse(command):
        result.append(tokens)
        nested = _nested_command(tokens)
        if nested is not None:
            result.extend(walk(nested))
        for token in tokens:
            if substituted(token):
                for body in _bodies(token):
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


def shell_command(arguments: dict) -> str:
    command = arguments.get("command", arguments.get("cmd", ""))
    return command if isinstance(command, str) else ""
