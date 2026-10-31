import re
from collections.abc import Callable

from ._event import Decision, Event, deny, tool_call
from ._shell import shell_command
from .paths import SHELL_TOOLS

_LEAD = r"(?:^|[;&|(`\n]|\$\(|-\w*c)\s*(?:\S*/)?"
_PATTERNS = (
    (re.compile(r"/pro[c?*\[]|/self/|/(?:environ|cmdline)(?![\w.])"), "process environment and command lines"),
    (re.compile(r"CODEX_HOME|(?<![\w])\.codex(?![\w])"), "the Codex home"),
    (re.compile(_LEAD + r"printenv\b"), "environment dumps"),
    (re.compile(_LEAD + r"env(?:\s+-[0a-z]+)*\s*(?:$|[;&|)`])"), "environment dumps"),
    (re.compile(_LEAD + r"(?:export\s+-p|declare\s+-\w*p|compgen\s+-\w*e)\b"), "environment dumps"),
)


def no_process_secrets() -> Callable[[Event], Decision | None]:
    """Best-effort: pattern-match shell text that reads the process environment, command lines or the Codex home."""

    def policy(event: Event) -> Decision | None:
        arguments = tool_call(event, SHELL_TOOLS)
        if arguments is None:
            return None
        if arguments.get("_unparseable"):
            return deny("unparseable tool arguments")
        command = shell_command(arguments)
        if command is None:
            return deny("shell command is not a string")
        text = re.sub(r"['\"\\]", "", command)
        for pattern, what in _PATTERNS:
            if pattern.search(text):
                return deny(f"{what} are off limits to the security reviewer")
        return None

    return policy
