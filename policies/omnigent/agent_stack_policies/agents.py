import os
import re
from collections.abc import Callable

from ._event import Decision, Event, allow, deny, tool_call
from ._shell import Command, resolve, shell_command, walk
from .paths import SHELL_TOOLS

_PRINT_AGENTS = frozenset({"claude", "omp"})
_RUN_SUBCOMMANDS = {"codex": frozenset({"exec", "e", "review"}), "opencode": frozenset({"run"})}
_PACKAGES = {"claude-code": "claude", "codex": "codex", "pi-coding-agent": "omp", "opencode-ai": "opencode"}
_INFO_FLAGS = {
    "claude": frozenset({"-h", "--help", "-v", "--version"}),
    "codex": frozenset({"-h", "--help", "-V", "--version"}),
    "omp": frozenset({"-h", "--help", "-v", "--version"}),
    "opencode": frozenset({"-h", "--help", "-v", "--version"}),
}
_RUNTIMES = frozenset({"node", "bun", "deno"})
_AGENT_TEXT = re.compile(r"\b(claude|codex|omp|opencode|apply_patch)\b|\|\s*(sh|bash|zsh|dash|ksh)\b")
_HINT = "agent CLIs started from the shell bypass this session's policies; do the work with your own tools"


def _agent(argv: list[str]) -> tuple[str, list[str]]:
    name = os.path.basename(argv[0])
    args = argv[1:]
    if name in _RUNTIMES and args:
        for package, cli in _PACKAGES.items():
            if f"/{package}/" in args[0]:
                return cli, args[1:]
    return _PACKAGES.get(name, name), args


def _print_flag(arg: str) -> bool:
    if arg.startswith("--"):
        return arg.split("=", 1)[0] == "--print"
    return arg.startswith("-") and "p" in arg[1:]


def _check(command: Command, extra: frozenset[str]) -> str | None:
    if command.stdin_exec:
        return "piping a script into a shell is not allowed; run the commands directly"
    if not command.argv:
        return None
    name, args = _agent(command.argv)
    if name == "apply_patch":
        return "apply_patch through the shell is not allowed; use the file edit tools"
    if name in extra:
        return f"{name} is not allowed; {_HINT}"
    if _INFO_FLAGS.get(name, frozenset()) & set(args):
        return None
    if name in _PRINT_AGENTS and any(_print_flag(a) for a in args):
        return f"{name} -p is not allowed; {_HINT}"
    sub = next((a for a in args if a in _RUN_SUBCOMMANDS.get(name, ())), None)
    if sub:
        return f"{name} {sub} is not allowed; {_HINT}"
    return None


def no_nested_agents(*, extra_commands: list[str] | None = None) -> Callable[[Event], Decision | None]:
    extra = frozenset(extra_commands or [])
    text_pattern = (
        re.compile("|".join([_AGENT_TEXT.pattern, *(rf"\b{re.escape(c)}\b" for c in extra)])) if extra else _AGENT_TEXT
    )

    def policy(event: Event) -> Decision | None:
        arguments = tool_call(event, SHELL_TOOLS)
        if arguments is None:
            return None
        if arguments.get("_unparseable"):
            return deny("unparseable tool arguments")
        command = shell_command(arguments)
        if command is None:
            return deny("shell command is not a string")
        try:
            statements = walk(command)
        except ValueError:
            if text_pattern.search(command):
                return deny("unparseable command naming an agent CLI or piping into a shell; simplify it")
            return allow()
        for tokens in statements:
            reason = _check(resolve(tokens), extra)
            if reason:
                return deny(reason)
        return allow()

    return policy
