import os
import re
import shlex
from collections.abc import Callable

from ._event import Decision, Event, allow, deny, tool_call, tool_name
from .paths import SHELL_TOOLS, shell_command, split_statements

_MERGE_API = re.compile(r"pulls/\d+/merge$|/merges$")
_FORCE_LONG = ("--force", "--force-with-lease", "--force-if-includes")


def _is_force_flag(token: str) -> bool:
    if token.startswith("--"):
        return token.split("=", 1)[0] in _FORCE_LONG
    return token.startswith("-") and "f" in token[1:]


def _refspec_destination(refspec: str) -> str:
    destination = refspec.lstrip("+").rsplit(":", 1)[-1]
    return destination.removeprefix("refs/heads/")


def _check_statement(statement: str, protected: frozenset[str]) -> str | None:
    if ("$(" in statement or "`" in statement) and ("merge" in statement or "push" in statement):
        return "command substitution around merge or push"
    try:
        tokens = shlex.split(statement)
    except ValueError:
        if "merge" in statement or "push" in statement:
            return "unparseable merge or push command"
        return None
    for token in tokens:
        if re.search(r"\s", token):
            for inner in split_statements(token):
                reason = _check_statement(inner, protected)
                if reason:
                    return reason
    names = [os.path.basename(t) for t in tokens]
    if "gh" in names:
        rest = tokens[names.index("gh") + 1 :]
        if any(a == "pr" and b == "merge" for a, b in zip(rest, rest[1:])):
            return "gh pr merge"
        if "api" in rest and any(_MERGE_API.search(t) for t in rest):
            return "merge through gh api"
    if "git" in names:
        rest = tokens[names.index("git") + 1 :]
        if "push" in rest:
            args = rest[rest.index("push") + 1 :]
            flags = [a for a in args if a.startswith("-")]
            positionals = [a for a in args if not a.startswith("-")]
            refspecs = positionals[1:]
            if any(_is_force_flag(f) for f in flags) or any(r.startswith("+") for r in refspecs):
                return "force-push"
            for refspec in refspecs:
                if _refspec_destination(refspec) in protected:
                    return f"push to protected branch {_refspec_destination(refspec)}"
    return None


def no_merge(*, protected_branches: list[str] | None = None) -> Callable[[Event], Decision | None]:
    protected = frozenset(protected_branches or ["main", "master"])

    def policy(event: Event) -> Decision | None:
        name = tool_name(event)
        if name is not None and name.endswith("merge_pull_request"):
            return deny(f"{name} merges are not allowed")
        arguments = tool_call(event, SHELL_TOOLS)
        if arguments is None:
            return None
        if arguments.get("_unparseable"):
            return deny("unparseable tool arguments")
        for statement in split_statements(shell_command(arguments)):
            reason = _check_statement(statement, protected)
            if reason:
                return deny(f"{reason} is not allowed; open or update a pull request instead")
        return allow()

    return policy
