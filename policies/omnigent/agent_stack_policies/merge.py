import os
import re
from collections.abc import Callable

from ._event import Decision, Event, allow, deny, tool_call, tool_name
from ._shell import push_branch, push_destination, shell_command, substituted, walk
from .paths import SHELL_TOOLS

_MERGE_API = re.compile(r"pulls/\d+/merge$|/merges$")
_FORCE_LONG = ("--force", "--force-with-lease", "--force-if-includes")
_REF_FLAGS = ("--tags", "--follow-tags", "--mirror", "--all")
_HEADS_PREFIX = re.compile(r"^(?:refs/)?heads/")


def _is_force_flag(token: str) -> bool:
    if token.startswith("--"):
        return token.split("=", 1)[0] in _FORCE_LONG
    return token.startswith("-") and "f" in token[1:]


def _has_substitution(tokens: list[str]) -> bool:
    return any(substituted(t) for t in tokens) or any(
        a.endswith("$") and b == "(" for a, b in zip(tokens, tokens[1:])
    )


def _mentions_merge_or_push(tokens: list[str]) -> bool:
    return any(t in ("merge", "push") or t.endswith(("/merge", "/merges")) for t in tokens)


def _check_statement(tokens: list[str], protected: frozenset[str]) -> str | None:
    if _has_substitution(tokens) and _mentions_merge_or_push(tokens):
        return "command substitution around merge or push"
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
            if any(f.split("=", 1)[0] in _REF_FLAGS for f in flags):
                return "pushing tags or all refs"
            for refspec in refspecs:
                resolved = _HEADS_PREFIX.sub("", push_destination(refspec))
                if resolved in protected:
                    return f"push to protected branch {resolved}"
                if push_branch(refspec) is None:
                    return f"push to tag or non-branch ref {refspec}"
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
        command = shell_command(arguments)
        try:
            statements = walk(command)
        except ValueError:
            if "merge" in command or "push" in command:
                return deny("unparseable merge or push command; simplify the command")
            return allow()
        for tokens in statements:
            reason = _check_statement(tokens, protected)
            if reason:
                return deny(f"{reason} is not allowed; open or update a pull request instead")
        return allow()

    return policy
