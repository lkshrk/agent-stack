import os
import re
from collections.abc import Callable

from ._event import Decision, Event, allow, deny, tool_call
from ._shell import resolve, shell_command, walk
from .paths import SHELL_TOOLS
from .vcs import _BRANCH_LIST_FLAGS, _GIT_UNSAFE_FLAGS

READ_COMMANDS = frozenset({
    "rg", "grep", "cat", "head", "tail", "wc", "ls", "find", "git", "jq", "diff", "sort", "uniq", "cut", "tr",
    "echo", "printf", "pwd", "basename", "dirname", "realpath", "stat", "nl", "true", "false", "test", "[",
})
_GIT_READ = frozenset({
    "status", "diff", "log", "show", "rev-parse", "ls-files", "ls-tree", "cat-file", "blame", "grep",
    "merge-base", "describe", "shortlog", "rev-list", "branch",
})
_GIT_GLOBAL_FLAGS = frozenset({"--no-pager", "-P"})
_FD_DUP = re.compile(r"\d*[<>]&(?:\d+|-)(?![^\s;&|)])")
_FILE_REDIRECT = re.compile(r"&>|>&|>\|")
_FIND_WRITES = frozenset({"-delete", "-fprint", "-fprint0", "-fprintf", "-fls"})
_HINT = "this agent is read-only; report the change instead of making it"


def _check_git(args: list[str]) -> str | None:
    while args[:1] == ["-C"] or args[:1] and args[0] in _GIT_GLOBAL_FLAGS:
        args = args[2:] if args[0] == "-C" else args[1:]
    sub, rest = (args[0], args[1:]) if args else ("", [])
    if sub not in _GIT_READ:
        return f"git {sub or '(no verb)'} is not a read verb"
    if any(a.startswith(_GIT_UNSAFE_FLAGS) for a in rest):
        return f"git {sub} with an output or exec option is not allowed"
    if sub == "grep" and any(a.startswith(("--open-files-in-pager", "-O")) for a in rest):
        return "git grep -O runs a pager command"
    if sub == "branch" and not all(a in _BRANCH_LIST_FLAGS for a in rest):
        return "git branch may only list"
    return None


def _check(tokens: list[str], allowed: frozenset[str]) -> str | None:
    for i, token in enumerate(tokens):
        if token and set(token) <= {">"}:
            target = tokens[i + 1] if i + 1 < len(tokens) else ""
            if target != "/dev/null":
                return f"redirecting output to {target or 'a file'} is not allowed"
    if _FIND_WRITES & set(tokens):
        return "find with -delete or -fprint is not allowed"
    command = resolve(tokens)
    if command.stdin_exec:
        return "piping a script into a shell is not allowed"
    if not command.argv:
        return None
    name, args = os.path.basename(command.argv[0]), command.argv[1:]
    if name not in allowed:
        return f"{name} is not on the read-only command list"
    if name == "git":
        return _check_git(args)
    if name == "sort" and any(a.startswith(("-o", "--output")) for a in args):
        return "sort -o writes a file"
    if name == "uniq" and len([a for a in args if not a.startswith("-")]) > 1:
        return "uniq with an output file is not allowed"
    if name == "rg" and any(a.startswith("--pre") for a in args):
        return "rg --pre runs a command"
    return None


def read_only_shell(*, extra_commands: list[str] | None = None) -> Callable[[Event], Decision | None]:
    allowed = READ_COMMANDS | frozenset(extra_commands or [])

    def policy(event: Event) -> Decision | None:
        arguments = tool_call(event, SHELL_TOOLS)
        if arguments is None:
            return None
        if arguments.get("_unparseable"):
            return deny("unparseable tool arguments")
        command = shell_command(arguments)
        if command is None:
            return deny("shell command is not a string")
        command = _FD_DUP.sub("", command)
        if _FILE_REDIRECT.search(command):
            return deny(f"redirecting output to a file is not allowed; {_HINT}")
        try:
            statements = walk(command)
        except ValueError:
            return deny("unparseable shell command; simplify it")
        for tokens in statements:
            reason = _check(tokens, allowed)
            if reason:
                return deny(f"{reason}; {_HINT}")
        return allow()

    return policy
