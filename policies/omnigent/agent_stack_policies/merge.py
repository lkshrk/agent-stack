import os
import re
from collections.abc import Callable

from ._event import Decision, Event, allow, deny, tool_call, tool_name
from ._shell import DEFAULT_PROTECTED, SHELLS, Command, shell_name, OpaqueCommand, _nested_command, ansi_quoted, hidden_command, opaque_name, push_branch, push_destination, resolve, shell_command, substituted, walk
from .paths import SHELL_TOOLS

_MERGE_API = re.compile(r"pulls/\d+/merge(?:$|[/?#])|/merges(?:$|[/?#])", re.IGNORECASE)
_MERGE_PATH = re.compile(
    r"(?:^|/)pulls/[^/\s]+/merge(?:$|[/?#])|(?:^|/)repos/[^/\s]+/[^/\s]+/merges(?:$|[/?#])|/merges?(?:$|[?#])",
    re.IGNORECASE,
)
_GH_VALUE_FLAGS = frozenset({"-R", "--repo", "--hostname"})
_MERGE_MUTATION = re.compile(r"mergePullRequest|mergeBranch|enablePullRequestAutoMerge|enqueuePullRequest")
_GRAPHQL_ENDPOINT = re.compile(r"(?:^|/)graphql/?(?:$|[?#])", re.IGNORECASE)
_FIELD_ASSIGNMENT = re.compile(r"^[A-Za-z_][\w.\[\]]*=")
_GH_GROUPS = frozenset({"alias", "extension", "extensions", "ext"})
# gh 2.102 `gh api` flags (pflag): long options that take a value, and short letters that do.
_GH_API_LONG_VALUES = frozenset({
    "--field", "--raw-field", "--header", "--jq", "--method", "--preview", "--template", "--cache", "--hostname", "--input",
})
_GH_API_LONG_FIELDS = frozenset({"--field", "--raw-field"})
_GH_API_SHORT_VALUES = frozenset("fFHqXpt")
_LITERAL_KEY = re.compile(r"[^$`*?\[\]{}]+")
_GH_CONFIG_ENV = re.compile(r"^(?:GH_CONFIG_DIR|XDG_CONFIG_HOME)=")
_FORCE_LONG = frozenset({"force", "force-with-lease", "force-if-includes"})
_REF_FLAGS = frozenset({"tags", "follow-tags", "mirror", "all", "branches"})
_HEADS_PREFIX = re.compile(r"^(?:refs/)?heads/")
_VCS = frozenset({"git", "gh"})
_PR_HINT = "open or update a pull request instead"
_PUSH_HINT = "name the destination: git push origin HEAD:<branch>"
# git push -h (git 2.56): long options, which of them take a separate value, and the short letters.
_PUSH_LONG = frozenset({
    "verbose", "quiet", "repo", "all", "branches", "mirror", "delete", "tags", "dry-run", "porcelain", "force",
    "force-with-lease", "force-if-includes", "recurse-submodules", "thin", "receive-pack", "exec", "set-upstream",
    "progress", "prune", "verify", "follow-tags", "signed", "atomic", "push-option", "ipv4", "ipv6",
})
_PUSH_LONG_VALUES = frozenset({"repo", "recurse-submodules", "receive-pack", "exec", "push-option"})
_PUSH_SHORT = {"v": "verbose", "q": "quiet", "d": "delete", "n": "dry-run", "f": "force", "u": "set-upstream", "4": "ipv4", "6": "ipv6"}
_IMPLICIT_SOURCES = frozenset({"HEAD", "@"})
_STDIN_ARGUMENTS = frozenset({"xargs", "parallel"})
_STDIN_SHELLS = SHELLS | frozenset({"eval", "env", "exec", "command", "sudo"})
_STDIN_INTERPRETERS = frozenset({"python", "python3", "node", "perl", "ruby", "php", "lua", "awk"})
_STDIN_TEMPLATE = re.compile(r"\{\d*\}")
_GIT_SENSITIVE = frozenset({"push", "merge", "config"})
_VCS_TEXT = re.compile(r"\b(git|gh)\b")
_GLOB = re.compile(r"[*?\[{]")
_REDIRECT_KEY = re.compile(
    r"^(?:alias\.|push\.|url\.|include\.|includeif\.|remote\.pushdefault$|remote\..+\.(?:push|url|pushurl)$|branch\..+\.(?:merge|remote)$)",
    re.IGNORECASE,
)
_REDIRECT_TEXT = re.compile(r"(?:^|['\"\s])(?:alias|push|url|remote|branch|include|includeif)\.", re.IGNORECASE)
_CONFIG_READS = frozenset(
    {"--get", "--get-all", "--get-regexp", "--get-urlmatch", "--get-color", "--get-colorbool", "--list", "-l", "get", "list"}
)
_CONFIG_EDITS = frozenset({"-e", "--edit", "edit"})
_CONFIG_FILE_ENV = re.compile(r"^GIT_CONFIG(?:_GLOBAL|_SYSTEM)?=")
_CONFIG_ENV = re.compile(r"^GIT_CONFIG_(?:KEY_\d+|PARAMETERS)=(.*)$", re.DOTALL)
# git 2.47 and 2.56 global options; a long one outside these sets, with no `=`, fails closed.
_GIT_VALUE_OPTIONS = frozenset({"-C", "--git-dir", "--work-tree", "--namespace", "--super-prefix", "--attr-source", "--shallow-file"})
_GIT_FLAG_OPTIONS = frozenset({
    "--paginate", "--no-pager", "--no-replace-objects", "--no-lazy-fetch", "--no-optional-locks", "--no-advice",
    "--bare", "--literal-pathspecs", "--no-literal-pathspecs", "--glob-pathspecs", "--noglob-pathspecs",
    "--icase-pathspecs", "--exec-path", "--html-path", "--man-path", "--info-path", "--version", "--help",
})
_CONFIG_HINT = "git config that redirects a push or merge (alias, remote, branch, push, url or include keys) is not allowed"


def _has_substitution(tokens: list[str]) -> bool:
    return any(substituted(t) for t in tokens) or any(
        a.endswith("$") and b == "(" for a, b in zip(tokens, tokens[1:])
    )


def _mentions_merge_or_push(tokens: list[str]) -> bool:
    return any(t in ("merge", "push") or _MERGE_PATH.search(t) for t in tokens)


def _vcs_runs(tokens: list[str]) -> list[tuple[list[str], bool]]:
    """(argv, resolved) for the resolved git/gh argv first, then every later git/gh token for launchers the resolver doesn't know."""
    runs = []
    argv = resolve(tokens).argv
    if argv and os.path.basename(argv[0]) in _VCS:
        runs.append((argv, True))
    runs.extend((tokens[i:], False) for i, t in enumerate(tokens) if os.path.basename(t) in _VCS)
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


def _graphql_request(rest: list[str]) -> bool:
    """A gh api call whose endpoint is GraphQL in any spelling gh routes there (`graphql`, `/graphql`, a full or GHES URL)."""
    return "api" in rest and any(
        _GRAPHQL_ENDPOINT.search(t) and not _FIELD_ASSIGNMENT.match(t) for t in rest if not t.startswith("-")
    )


def _api_parts(rest: list[str]) -> tuple[list[str], list[str]]:
    """(positional words, -f/-F/--field/--raw-field values) of a `gh … api` argv, with short clusters read as pflag reads them."""
    args = rest[rest.index("api") + 1 :] if "api" in rest else []
    positional, fields, i = [], [], 0
    while i < len(args):
        arg = args[i]
        i += 1
        if arg == "--":
            positional.extend(args[i:])
            break
        if arg.startswith("--"):
            name, eq, value = arg.partition("=")
            if name in _GH_API_LONG_VALUES:
                if not eq:
                    value = args[i] if i < len(args) else ""
                    i += 1
                if name in _GH_API_LONG_FIELDS:
                    fields.append(value)
        elif arg.startswith("-") and arg != "-":
            for k, letter in enumerate(arg[1:], start=1):
                if letter in _GH_API_SHORT_VALUES:
                    value = arg[k + 1 :]
                    value = value[1:] if value.startswith("=") else value
                    if not value:
                        value = args[i] if i < len(args) else ""
                        i += 1
                    if letter in "fF":
                        fields.append(value)
                    break
        else:
            positional.append(arg)
    return positional, fields


def _graphql_field_problem(rest: list[str]) -> str | None:
    positional, fields = _api_parts(rest)
    if len(positional) > 1:
        return "gh api graphql with an extra argument (such as \"$@\") is not allowed"
    for field in fields:
        key, eq, _ = field.partition("=")
        if not eq or not _LITERAL_KEY.fullmatch(key):
            return f"gh api graphql field {field!r} needs a literal key=value; write each field literally"
    return None


def _expanding_query(text: str) -> bool:
    """A word whose shell-dequoted form contains `query=` and has a `$` or backtick the shell expands."""
    plain, quote, expands, i = [], None, False, 0

    def flagged() -> bool:
        return expands and "query=" in "".join(plain)

    while i < len(text):
        ch = text[i]
        if quote == "'":
            if ch == "'":
                quote = None
            else:
                plain.append(ch)
        elif ch == "\\":
            plain.append(text[i + 1 : i + 2])
            i += 2
            continue
        elif quote == '"':
            if ch == '"':
                quote = None
            else:
                expands = expands or ch in "$`"
                plain.append(ch)
        elif ch in "'\"":
            quote = ch
        elif ch.isspace() or ch in ";&|()":
            if flagged():
                return True
            plain, expands = [], False
        else:
            expands = expands or ch in "$`"
            plain.append(ch)
        i += 1
    return flagged()


def _check_gh(rest: list[str], expanding_query: bool = False) -> str | None:
    words = _gh_words(rest)
    if words[:2] == ["pr", "merge"] or any(a == "pr" and b == "merge" for a, b in zip(words, words[1:])):
        return f"gh pr merge is not allowed; {_PR_HINT}"
    if "api" in rest and any(_MERGE_API.search(t) for t in rest):
        return f"merge through gh api is not allowed; {_PR_HINT}"
    if words[:1] and words[0] in _GH_GROUPS and words[1:2] not in (["list"], ["ls"]):
        return f"gh {words[0]} can define a command that merges; only `gh {words[0]} list` is allowed; {_PR_HINT}"
    if _graphql_request(rest):
        problem = _graphql_field_problem(rest)
        if problem:
            return f"{problem}; {_PR_HINT}"
        if any(t == "--input" or t.startswith("--input=") or "query=@" in t for t in rest) or expanding_query:
            return f"gh api graphql with a query from a file, stdin, a variable, a command substitution or $'…' quoting is not allowed; write the query inline in single quotes; {_PR_HINT}"
        if any(_MERGE_MUTATION.search(t) for t in rest):
            return f"merge through gh api graphql is not allowed; {_PR_HINT}"
    return None


def _git_parts(args: list[str]) -> tuple[list[str], str, list[str]]:
    """(`-c`/`--config-env` values, subcommand, its arguments) for a git argv after `git`; an unknown long option without `=` is returned as the subcommand."""
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
        elif arg.startswith("--") and "=" not in arg and arg not in _GIT_FLAG_OPTIONS:
            return configs, arg, args[i + 1 :]
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


def _push_long(name: str) -> tuple[str | None, bool]:
    """(option, negated) for a long push option as git resolves it, unique prefixes included; option is None when unknown and "" when ambiguous."""
    candidates = set()
    for option in _PUSH_LONG:
        for negated, full in ((False, option), (True, f"no-{option}")):
            if full == name:
                return option, negated
            if full.startswith(name):
                candidates.add((option, negated))
    if len(candidates) == 1:
        return next(iter(candidates))
    return ("" if candidates else None), False


def _push_parts(args: list[str]) -> tuple[set[str], list[str], str | None]:
    """(options set, positional arguments, problem) of a git push argv, with values of `-o`, `--repo`, `--recurse-submodules` and the like skipped."""
    options, positional, i = set(), [], 0
    while i < len(args):
        arg = args[i]
        i += 1
        if arg in ("--", "--end-of-options"):
            positional.extend(args[i:])
            break
        if arg.startswith("--"):
            name, eq, _ = arg[2:].partition("=")
            option, negated = _push_long(name)
            if option == "":
                return options, positional, f"ambiguous git push option {arg}"
            if option is not None and not negated:
                options.add(option)
            if option in _PUSH_LONG_VALUES and not negated and not eq:
                i += 1
        elif arg.startswith("-") and arg != "-":
            for k, letter in enumerate(arg[1:], start=1):
                if letter == "o":
                    i += 0 if arg[k + 1 :] else 1
                    break
                if letter in _PUSH_SHORT:
                    options.add(_PUSH_SHORT[letter])
        else:
            positional.append(arg)
    return options, positional, None


def _check_push(args: list[str], protected: frozenset[str]) -> str | None:
    options, positional, problem = _push_parts(args)
    if problem:
        return f"{problem}; {_PUSH_HINT}"
    if any(_GLOB.search(a) for a in positional):
        return f"glob or brace characters in a push are not allowed, since the shell expands them; {_PR_HINT}"
    refspecs = positional[1:]
    if options & _FORCE_LONG or any(r.startswith("+") for r in refspecs):
        return f"force-push is not allowed; {_PR_HINT}"
    if options & _REF_FLAGS:
        return f"pushing tags or all refs is not allowed; {_PR_HINT}"
    for refspec in refspecs:
        resolved = _HEADS_PREFIX.sub("", push_destination(refspec))
        if resolved in protected:
            return f"push to protected branch {resolved} is not allowed; {_PR_HINT}"
        if push_branch(refspec) is None:
            return f"push to tag or non-branch ref {refspec} is not allowed; {_PR_HINT}"
    if not refspecs or any(":" not in r and r in _IMPLICIT_SOURCES for r in refspecs):
        return f"git push without an explicit destination can push to {'/'.join(sorted(protected))}; {_PUSH_HINT}"
    return None


def _runs_vcs(command: Command) -> bool:
    """The resolved command is git or gh, or a shell or parallel body that mentions either."""
    name = command.name
    if name in _VCS:
        return True
    texts = list(command.nested)
    if shell_name(name):
        texts += command.argv[1:]
    return any(_VCS_TEXT.search(t) for t in texts)


_STDIN_ALLOWED = frozenset({"rg", "grep", "egrep", "fgrep", "wc", "cat", "head", "tail", "ls", "stat", "file", "echo", "printf", "basename", "dirname", "realpath", "readlink", "sort", "uniq", "cut", "tr", "jq", "yq", "sha256sum", "md5sum", "du", "test", "true"})


def _unsafe_stdin_launcher(command: Command) -> bool:
    """xargs/parallel may start only a literal allowlisted read or format program."""
    if not (_STDIN_ARGUMENTS & set(command.wrappers)):
        return False
    if not command.argv:
        return True
    name, raw = command.name, command.argv[0]
    if name not in _STDIN_ALLOWED or raw.startswith(":::") or _STDIN_TEMPLATE.search(raw):
        return True
    if any(ch in raw for ch in "'\\\"$`"):
        return True
    args = command.argv[1:]
    return name == "sort" and any(a == "-o" or a == "--output" or a.startswith("--output=") or (a.startswith("-o") and len(a) > 2) for a in args)


def _check_statement(tokens: list[str], protected: frozenset[str], expanding_query: bool = False) -> str | None:
    if _has_substitution(tokens) and _mentions_merge_or_push(tokens):
        return "command substitution in a merge or push is not allowed; name the branch literally, e.g. `git push -u origin feat/x`"
    command = resolve(tokens)
    if _STDIN_ARGUMENTS & set(command.wrappers) and (_runs_vcs(command) or _unsafe_stdin_launcher(command)):
        return f"run gh/git directly, not through xargs/parallel; run only a literal, non-shell program there; {_PR_HINT}"
    for run, resolved in _vcs_runs(tokens):
        rest = run[1:]
        if _mentions_merge_or_push(run) and any("$" in t for t in rest):
            return "a variable in a merge or push is not allowed; name the branch literally, e.g. `git push -u origin feat/x`"
        if os.path.basename(run[0]) == "gh":
            reason = _check_gh(rest, expanding_query)
        else:
            _, sub, sub_rest = _git_parts(rest)
            if sub.startswith("--"):
                if resolved or _GIT_SENSITIVE & set(rest):
                    return f"unknown git option {sub} before the subcommand; {_PR_HINT}"
                continue
            reason = _check_config(rest) or (_check_push(sub_rest, protected) if sub == "push" else None)
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
        gh_runs = any(os.path.basename(run[0]) == "gh" for tokens in statements for run, _ in _vcs_runs(tokens))
        if gh_runs and any(_GH_CONFIG_ENV.match(t) for tokens in statements for t in tokens):
            return deny(f"gh with GH_CONFIG_DIR or XDG_CONFIG_HOME set can load aliases from another config; {_PR_HINT}")
        texts = [command]
        for tokens in statements:
            texts += resolve(tokens).nested
            extra = _nested_command(tokens)
            if extra is not None:
                texts.append(extra)
        expanding_query = gh_runs and any(_expanding_query(t) or ansi_quoted(t) for t in texts)
        for tokens in statements:
            reason = _check_statement(tokens, protected, expanding_query)
            if reason:
                return deny(reason)
        return allow()

    return policy
