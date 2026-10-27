import os
import re
from collections.abc import Callable

from ._event import Decision, Event, allow, deny, tool_call, tool_name
from ._shell import DEFAULT_PROTECTED, hidden_command, push_branch, push_destination, resolve, shell_command, substituted, walk
from .paths import SHELL_TOOLS

_MERGE_API = re.compile(r"pulls/\d+/merge$|/merges$")
_MERGE_MUTATION = re.compile(r"mergePullRequest|enablePullRequestAutoMerge")
_FORCE_LONG = ("--force", "--force-with-lease", "--force-if-includes")
_REF_FLAGS = ("--tags", "--follow-tags", "--mirror", "--all")
_HEADS_PREFIX = re.compile(r"^(?:refs/)?heads/")
_VCS = frozenset({"git", "gh"})
_PR_HINT = "open or update a pull request instead"
_STDIN_ARGUMENTS = frozenset({"xargs", "parallel"})
_VCS_TEXT = re.compile(r"\b(git|gh)\b")


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


def _vcs_runs(tokens: list[str]) -> list[list[str]]:
    """The resolved git/gh argv first, then every later git/gh token for launchers the resolver doesn't know."""
    runs = []
    argv = resolve(tokens).argv
    if argv and os.path.basename(argv[0]) in _VCS:
        runs.append(argv)
    runs.extend(tokens[i:] for i, t in enumerate(tokens) if os.path.basename(t) in _VCS)
    return runs


def _check_gh(rest: list[str]) -> str | None:
    if any(a == "pr" and b == "merge" for a, b in zip(rest, rest[1:])):
        return f"gh pr merge is not allowed; {_PR_HINT}"
    if "api" in rest and any(_MERGE_API.search(t) for t in rest):
        return f"merge through gh api is not allowed; {_PR_HINT}"
    if "api" in rest and "graphql" in rest and any(_MERGE_MUTATION.search(t) for t in rest):
        return f"merge through gh api graphql is not allowed; {_PR_HINT}"
    return None


def _check_push(args: list[str], protected: frozenset[str]) -> str | None:
    flags = [a for a in args if a.startswith("-")]
    refspecs = [a for a in args if not a.startswith("-")][1:]
    if any(_is_force_flag(f) for f in flags) or any(r.startswith("+") for r in refspecs):
        return f"force-push is not allowed; {_PR_HINT}"
    if any(f.split("=", 1)[0] in _REF_FLAGS for f in flags):
        return f"pushing tags or all refs is not allowed; {_PR_HINT}"
    for refspec in refspecs:
        resolved = _HEADS_PREFIX.sub("", push_destination(refspec))
        if resolved in protected:
            return f"push to protected branch {resolved} is not allowed; {_PR_HINT}"
        if push_branch(refspec) is None:
            return f"push to tag or non-branch ref {refspec} is not allowed; {_PR_HINT}"
    return None


def _check_statement(tokens: list[str], protected: frozenset[str]) -> str | None:
    if _has_substitution(tokens) and _mentions_merge_or_push(tokens):
        return "command substitution in a merge or push is not allowed; name the branch literally, e.g. `git push -u origin feat/x`"
    command = resolve(tokens)
    if _STDIN_ARGUMENTS & set(command.wrappers) and command.argv and os.path.basename(command.argv[0]) in _VCS:
        if _mentions_merge_or_push(command.argv):
            return f"{command.wrappers[0]} into a merge or push is not allowed, since stdin can supply the branch; {_PR_HINT}"
    for run in _vcs_runs(tokens):
        rest = run[1:]
        if _mentions_merge_or_push(run) and any("$" in t for t in rest):
            return "a variable in a merge or push is not allowed; name the branch literally, e.g. `git push -u origin feat/x`"
        if os.path.basename(run[0]) == "gh":
            reason = _check_gh(rest)
        else:
            reason = _check_push(rest[rest.index("push") + 1 :], protected) if "push" in rest else None
        if reason:
            return reason
    return None


def no_merge(*, protected_branches: list[str] | None = None) -> Callable[[Event], Decision | None]:
    protected = frozenset(protected_branches or DEFAULT_PROTECTED)

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
        if command is None:
            return deny("shell command is not a string")
        try:
            statements = walk(command)
        except ValueError:
            if "merge" in command or "push" in command:
                return deny("unparseable merge or push command; simplify the command")
            return allow()
        reason = hidden_command(command, statements, _VCS_TEXT)
        if reason:
            return deny(reason)
        for tokens in statements:
            reason = _check_statement(tokens, protected)
            if reason:
                return deny(reason)
        return allow()

    return policy
