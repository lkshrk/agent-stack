import os
import re
from collections.abc import Callable

from ._event import Decision, Event, allow, deny, tool_call, tool_name
from ._shell import DEFAULT_PROTECTED, OpaqueCommand, ansi_quoted, hidden_command, opaque_name, push_branch, push_destination, resolve, shell_command, substituted, walk
from .paths import SHELL_TOOLS

_MERGE_API = re.compile(r"pulls/\d+/merge(?:$|[/?#])|/merges(?:$|[/?#])", re.IGNORECASE)
_MERGE_PATH = re.compile(
    r"(?:^|/)pulls/[^/\s]+/merge(?:$|[/?#])|(?:^|/)repos/[^/\s]+/[^/\s]+/merges(?:$|[/?#])|/merges?(?:$|[?#])",
    re.IGNORECASE,
)
_GH_VALUE_FLAGS = frozenset({"-R", "--repo", "--hostname"})
_MERGE_MUTATION = re.compile(r"mergePullRequest|enablePullRequestAutoMerge")
_FORCE_LONG = ("--force", "--force-with-lease", "--force-if-includes")
_REF_FLAGS = ("--tags", "--follow-tags", "--mirror", "--all")
_HEADS_PREFIX = re.compile(r"^(?:refs/)?heads/")
_VCS = frozenset({"git", "gh"})
_PR_HINT = "open or update a pull request instead"
_STDIN_ARGUMENTS = frozenset({"xargs", "parallel"})
_VCS_TEXT = re.compile(r"\b(git|gh)\b")
_GLOB = re.compile(r"[*?\[{]")
_REDIRECT_KEY = re.compile(
    r"^(?:alias\.|push\.|url\.|remote\.pushdefault$|remote\..+\.(?:push|url|pushurl)$|branch\..+\.(?:merge|remote)$)",
    re.IGNORECASE,
)
_REDIRECT_TEXT = re.compile(r"(?:^|['\"\s])(?:alias|push|url|remote|branch)\.", re.IGNORECASE)
_CONFIG_READS = frozenset(
    {"--get", "--get-all", "--get-regexp", "--get-urlmatch", "--get-color", "--get-colorbool", "--list", "-l", "get", "list"}
)
_CONFIG_EDITS = frozenset({"-e", "--edit", "edit"})
_CONFIG_FILE_ENV = re.compile(r"^GIT_CONFIG(?:_GLOBAL|_SYSTEM)?=")
_CONFIG_ENV = re.compile(r"^GIT_CONFIG_(?:KEY_\d+|PARAMETERS)=(.*)$", re.DOTALL)
_GIT_VALUE_OPTIONS = frozenset({"-C", "--git-dir", "--work-tree", "--namespace", "--super-prefix"})
_CONFIG_HINT = "git config that redirects a push or merge (alias, remote, branch, push or url keys) is not allowed"


def _is_force_flag(token: str) -> bool:
    if token.startswith("--"):
        return token.split("=", 1)[0] in _FORCE_LONG
    return token.startswith("-") and "f" in token[1:]


def _has_substitution(tokens: list[str]) -> bool:
    return any(substituted(t) for t in tokens) or any(
        a.endswith("$") and b == "(" for a, b in zip(tokens, tokens[1:])
    )


def _mentions_merge_or_push(tokens: list[str]) -> bool:
    return any(t in ("merge", "push") or _MERGE_PATH.search(t) for t in tokens)


def _vcs_runs(tokens: list[str]) -> list[list[str]]:
    """The resolved git/gh argv first, then every later git/gh token for launchers the resolver doesn't know."""
    runs = []
    argv = resolve(tokens).argv
    if argv and os.path.basename(argv[0]) in _VCS:
        runs.append(argv)
    runs.extend(tokens[i:] for i, t in enumerate(tokens) if os.path.basename(t) in _VCS)
    return runs


def _gh_words(rest: list[str]) -> list[str]:
    """gh's command words without flags and their values (`-R o/r`, `--repo=o/r`, `-Ro/r`)."""
    words, i = [], 0
    while i < len(rest):
        arg = rest[i]
        if arg.startswith("-"):
            i += 2 if arg in _GH_VALUE_FLAGS else 1
            continue
        words.append(arg)
        i += 1
    return words


def _check_gh(rest: list[str]) -> str | None:
    words = _gh_words(rest)
    if words[:2] == ["pr", "merge"] or any(a == "pr" and b == "merge" for a, b in zip(words, words[1:])):
        return f"gh pr merge is not allowed; {_PR_HINT}"
    if "api" in rest and any(_MERGE_API.search(t) for t in rest):
        return f"merge through gh api is not allowed; {_PR_HINT}"
    if "api" in rest and "graphql" in rest and any(_MERGE_MUTATION.search(t) for t in rest):
        return f"merge through gh api graphql is not allowed; {_PR_HINT}"
    return None


def _git_parts(args: list[str]) -> tuple[list[str], str, list[str]]:
    """(`-c`/`--config-env` values, subcommand, its arguments) for a git argv after `git`."""
    configs, i = [], 0
    while i < len(args):
        arg = args[i]
        if arg in ("-c", "--config-env") and i + 1 < len(args):
            configs.append(args[i + 1])
            i += 2
        elif arg.startswith("--config-env="):
            configs.append(arg.split("=", 1)[1])
            i += 1
        elif arg.startswith("-c") and len(arg) > 2 and not arg.startswith("--"):
            configs.append(arg[2:])
            i += 1
        elif arg in _GIT_VALUE_OPTIONS:
            i += 2
        elif arg.startswith("-"):
            i += 1
        else:
            return configs, arg, args[i + 1 :]
    return configs, "", []


def _redirect_key(text: str) -> bool:
    return _REDIRECT_KEY.match(text.split("=", 1)[0].strip("'\"")) is not None


def _check_config(args: list[str]) -> str | None:
    configs, sub, rest = _git_parts(args)
    if any(_redirect_key(c) for c in configs):
        return f"{_CONFIG_HINT} through git -c or --config-env; {_PR_HINT}"
    if sub != "config" or _CONFIG_READS & set(rest):
        return None
    if _CONFIG_EDITS & set(rest) or any(_redirect_key(a) for a in rest if not a.startswith("-")):
        return f"{_CONFIG_HINT}; {_PR_HINT}"
    return None


def _config_environment(statements: list[list[str]]) -> str | None:
    for tokens in statements:
        for token in tokens:
            if _CONFIG_FILE_ENV.match(token):
                return f"pointing git at another config file is not allowed next to git; {_PR_HINT}"
            match = _CONFIG_ENV.match(token)
            if match and _REDIRECT_TEXT.search(match.group(1)):
                return f"{_CONFIG_HINT} through GIT_CONFIG_*; {_PR_HINT}"
    return None


def _quoted_vcs(text: str, statements: list[list[str]]) -> str | None:
    """A git/gh name or subcommand hidden in `$'…'`, `$"…"` or a variable."""
    for tokens in statements:
        argv = resolve(tokens).argv
        if not argv:
            continue
        if opaque_name(text, argv[0]):
            return f"a command name in $'…' or $\"…\" quoting is not allowed; {_PR_HINT}"
        name = os.path.basename(argv[0])
        if name == "git" and "$" in _git_parts(argv[1:])[1]:
            return f"a git subcommand in a variable or $'…' quoting is not allowed; {_PR_HINT}"
        if name == "gh" and any("$" in a for a in argv[1:3]):
            return f"a gh subcommand in a variable or $'…' quoting is not allowed; {_PR_HINT}"
    return None


def _check_push(args: list[str], protected: frozenset[str]) -> str | None:
    flags = [a for a in args if a.startswith("-")]
    positional = [a for a in args if not a.startswith("-")]
    if any(_GLOB.search(a) for a in positional):
        return f"glob or brace characters in a push are not allowed, since the shell expands them; {_PR_HINT}"
    refspecs = positional[1:]
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
            reason = _check_config(rest) or (
                _check_push(rest[rest.index("push") + 1 :], protected) if "push" in rest else None
            )
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
        except OpaqueCommand:
            return deny("a heredoc without its delimiter line hides what runs after it; close it")
        except ValueError:
            if "merge" in command or "push" in command or ansi_quoted(command):
                return deny("unparseable merge or push command; simplify the command")
            return allow()
        reason = hidden_command(command, statements, _VCS_TEXT) or _quoted_vcs(command, statements)
        if not reason and _VCS_TEXT.search(command):
            reason = _config_environment(statements)
        if reason:
            return deny(reason)
        for tokens in statements:
            reason = _check_statement(tokens, protected)
            if reason:
                return deny(reason)
        return allow()

    return policy
