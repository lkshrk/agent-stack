import re
from collections.abc import Callable

from ._event import Decision, Event, deny, tool_call
from ._shell import shell_command
from .paths import SHELL_TOOLS

_LEAD = r"(?:^|[;&|(`\n]|\$\(|-\w*c)\s*(?:\S*/)?"
_ENV_READ = r"os\.environ|os\.getenv|process\.env|%ENV|\$ENV\{|ENVIRON\b|ENV\["
_PATTERNS = (
    (re.compile(r"(?<![\w-])/proc(?![\w.-])|(?<![\w-])/pro[?*\[]|\*\S*/(?:environ|cmdline)(?![\w.])"), "process environment and command lines"),
    (re.compile(r"CODEX_HOME|(?<![\w])\.codex(?![\w-])"), "the Codex home"),
    (re.compile(_LEAD + r"printenv\b"), "environment dumps"),
    (re.compile(_LEAD + r"env(?:\s+-[0a-z]+)*\s*(?:$|[;&|)`])"), "environment dumps"),
    (re.compile(_LEAD + r"(?:export\s+-p|declare\s+-\w*[px]|compgen\s+-\w*e)\b"), "environment dumps"),
    (re.compile(_LEAD + r"(?:export|declare|set)\s*(?:$|[;&|)`])"), "environment dumps"),
    (re.compile(_LEAD + r"ps\s+(?:-\w*E|(?!-)[a-zA-Z]*e[a-zA-Z]*(?:\s|$|[;&|)`]))"), "process environments"),
    (re.compile(_LEAD + r"(?:python[\d.]*|node|perl|ruby)\b(?:\s+-[A-Za-z]\w*)*(?:\s+-)?\s*(?:<<|$|[;&|)`])", re.M), "STDIN"),
    (re.compile(_LEAD + r"(?:(?:python[\d.]*|node|perl|ruby)\b[^\n]*?(?:\s-\w*[cepE]\b|\s--(?:eval|print)\b)|awk\b|gawk\b)[^\n]*(?:" + _ENV_READ + ")"), "interpreter environment reads"),
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
            if what == "STDIN":
                if pattern.search(text) and re.search(_ENV_READ, text):
                    return deny("interpreter environment reads are off limits to agents")
            elif pattern.search(text):
                return deny(f"{what} are off limits to agents")
        return None

    return policy
