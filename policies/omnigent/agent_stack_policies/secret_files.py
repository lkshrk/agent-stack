import os
import posixpath
import re
from collections.abc import Callable
from fnmatch import fnmatchcase

from ._event import Decision, Event, deny, tool_call
from ._shell import _nested_command, resolve, shell_command, walk
from .merge import _gh_words
from .paths import SHELL_TOOLS

_GH_SECRET_COMMANDS = frozenset({"auth", "config", "alias", "extension", "extensions", "ext"})
_GH_RAW = re.compile(r"\bgh\b[^;&|\n]*\s(?:auth|config|alias|ext|extension|extensions|--web|-w)(?=$|[\s;&|)'\"=])")
_GLOB = frozenset("*?[")
_WILDCARDS = frozenset("*?")
_PATH_LEAD = frozenset("@=<:'\"")


def _ancestors(path: str, root: str) -> list[str]:
    out = [path]
    while path != root and path not in ("/", ""):
        path = posixpath.dirname(path)
        out.append(path)
    return out


def _glob_match(path: str, pattern: str) -> bool:
    """Shell-style: each `/`-separated component matches on its own, so `*` never crosses a `/`."""
    parts, wanted = path.split("/"), pattern.rstrip("/").split("/")
    return len(parts) == len(wanted) and all(fnmatchcase(p, w) for p, w in zip(parts, wanted))


def no_secret_reads(*, secret_dir: str, token_file: str) -> Callable[[Event], Decision | None]:
    """Best-effort: deny shell commands that name `secret_dir` other than as `GH_TOKEN_FILE=<token_file>` right before `gh`, and gh commands that print or hand out the token."""
    name = secret_dir.rstrip("/")
    assignment = f"GH_TOKEN_FILE={token_file}"
    protected = _ancestors(token_file, secret_dir)
    exempt_text = re.compile(r"(?<![^\s;&|('\"])" + re.escape(assignment) + r"(?=\s+gh\s)")
    hint = f"{secret_dir} is off limits; gh reads the review token itself when the command starts with `{assignment} gh`"

    def globbed(token: str) -> bool:
        for i, ch in enumerate(token):
            if ch == "/" and (i == 0 or token[i - 1] in _PATH_LEAD):
                candidate = token[i:]
                if not _GLOB & set(candidate):
                    continue
                if any(_glob_match(p, candidate) for p in protected if p != name):
                    return True
                first = candidate.split("/")[1]
                if _glob_match(name, candidate) and not set(first) <= _WILDCARDS:
                    return True
        return False

    def token_use(tokens: list[str]) -> bool:
        command = resolve(tokens)
        allowed = (
            command.name == "gh"
            and not command.wrappers
            and command.assignments == [assignment]
        )
        skip = set(command.nested) | {_nested_command(tokens)}
        for token in tokens:
            if token in skip:
                continue
            if name in token and not (allowed and token == assignment):
                return True
            if globbed(token):
                return True
        return False

    def gh_problem(tokens: list[str]) -> str | None:
        command = resolve(tokens)
        skip = set(command.nested) | {_nested_command(tokens)}
        runs = [command.argv[1:]] if command.name == "gh" else []
        runs += [tokens[i + 1 :] for i, t in enumerate(tokens) if t not in skip and os.path.basename(t) == "gh"]
        for rest in runs:
            words = _gh_words(rest)
            if words[:1] and words[0] in _GH_SECRET_COMMANDS:
                return f"gh {words[0]} can print or hand out the review token"
            if any(a == "-w" or a == "--web" or a.startswith("--web=") for a in rest):
                return "gh --web hands the review token's environment to a browser command"
        return None

    def check(command: str) -> Decision | None:
        mentioned = name in command
        if mentioned and "<<" in command:
            return deny(f"{hint}; heredocs and here-strings may not mention it")
        if name in exempt_text.sub("", command):
            return deny(hint)
        try:
            statements = walk(command)
        except ValueError:
            if mentioned:
                return deny(hint)
            if _GH_RAW.search(command):
                return deny("unparseable gh command that can print the review token")
            return None
        for tokens in statements:
            if token_use(tokens):
                return deny(hint)
            reason = gh_problem(tokens)
            if reason:
                return deny(reason)
        return None

    def policy(event: Event) -> Decision | None:
        arguments = tool_call(event, SHELL_TOOLS)
        if arguments is None:
            return None
        if arguments.get("_unparseable"):
            return deny("unparseable tool arguments")
        command = shell_command(arguments)
        if command is None:
            return deny("shell command is not a string")
        return check(command)

    return policy
