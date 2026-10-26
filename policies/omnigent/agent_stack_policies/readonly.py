import os
import re
import shlex
from collections.abc import Callable

from ._event import Decision, Event, allow, deny, tool_call
from ._shell import SHELLS, Command, resolve, shell_command, walk
from .paths import SHELL_TOOLS
from .vcs import _BRANCH_LIST_FLAGS, _GIT_UNSAFE_FLAGS

READ_COMMANDS = frozenset({
    "rg", "grep", "cat", "head", "tail", "wc", "ls", "find", "git", "jq", "diff", "sort", "uniq", "cut", "tr",
    "echo", "printf", "pwd", "basename", "dirname", "realpath", "stat", "nl", "true", "false", "test", "[",
    "sed", "cd",
})
GIT_READ = frozenset({
    "status", "diff", "log", "show", "rev-parse", "ls-files", "ls-tree", "cat-file", "blame", "grep",
    "merge-base", "describe", "shortlog", "rev-list", "branch",
})
_GIT_GLOBAL_FLAGS = frozenset({"--no-pager", "-P"})
_GIT_WORKTREE = frozenset({"add", "remove", "list", "prune"})
_GH_READ = {
    "pr": frozenset({"view", "diff", "checks", "list", "status"}),
    "issue": frozenset({"view", "list", "status"}),
    "repo": frozenset({"view"}),
    "run": frozenset({"view", "list"}),
    "release": frozenset({"view", "list"}),
    "search": None,
    "api": None,
}
_GH_API_WRITE = ("-f", "-F", "--field", "--raw-field", "--input", "-X", "--method")
_STDERR_ONLY = re.compile(r"(?<=\s)2>\s*(?:&1|/dev/null)(?=$|[\s;|&)'\"])")
_FILE_REDIRECT = re.compile(r"&>|>&|>\|")
_FIND_WRITES = frozenset({"-delete", "-fprint", "-fprint0", "-fprintf", "-fls"})
_SED_FLAGS = frozenset({"-n", "-E", "-r", "-s", "-u", "-z", "--quiet", "--silent", "--posix"})
_SED_PRINT = re.compile(r"^(?:\d+|\$)(?:,(?:\d+|\$))?p$")
_SHELL_SHORT = frozenset("celuxvo")
_SHELL_OPTIONS = frozenset({"pipefail", "errexit", "nounset", "xtrace"})
_UNIQ_VALUES = frozenset({"-f", "-s", "-w"})
_SAFE_WRAPPERS = frozenset({"command", "builtin", "find"})
ASSIGNING_BUILTINS = frozenset({
    "read", "mapfile", "readarray", "declare", "typeset", "local", "export", "readonly", "let", "getopts",
})
_EXPANSION = re.compile(r"\$\(\(|\$\[|\(\(|\$\{!|\$\{[^}]*[:\[]")
_SYSTEM_SHELL_DIRS = ("/bin/", "/usr/bin/")
_HINT = "this agent is read-only; report the change instead of making it"


def check_git(args: list[str], verbs: frozenset[str]) -> str | None:
    """None when a git argv (after `git`) runs only a verb in `verbs` with no output, exec or config option."""
    while args[:1] == ["-C"] or args[:1] and args[0] in _GIT_GLOBAL_FLAGS:
        args = args[2:] if args[0] == "-C" else args[1:]
    sub, rest = (args[0], args[1:]) if args else ("", [])
    if sub not in verbs:
        return f"git {sub or '(no verb)'} is not allowed here"
    if any(a.startswith(_GIT_UNSAFE_FLAGS) for a in rest):
        return f"git {sub} with an output or exec option is not allowed"
    if sub == "grep" and any(a.startswith("--open") or (a[:1] == "-" and a[1:2] != "-" and "O" in a) for a in rest):
        return "git grep -O runs a pager command"
    if sub == "branch" and not all(a in _BRANCH_LIST_FLAGS for a in rest):
        return "git branch may only list"
    if sub == "worktree" and rest[:1] and rest[0] not in _GIT_WORKTREE:
        return f"git worktree {rest[0]} is not allowed here"
    return None


def _check_gh(args: list[str]) -> str | None:
    sub = args[0] if args else ""
    if sub not in _GH_READ:
        return f"gh {sub or '(no command)'} is not a read command"
    verbs = _GH_READ[sub]
    if verbs is not None and (len(args) < 2 or args[1] not in verbs):
        return f"gh {sub} {args[1] if len(args) > 1 else ''} is not a read command".rstrip()
    if sub == "api" and any(a.startswith(_GH_API_WRITE) for a in args[1:]):
        return "gh api may only GET without fields or input"
    return None


def _check_sed(args: list[str]) -> str | None:
    scripts, operands, i = [], [], 0
    while i < len(args):
        arg = args[i]
        if arg in ("-e", "--expression") and i + 1 < len(args):
            scripts.append(args[i + 1])
            i += 2
            continue
        if arg.startswith("-") and arg != "-":
            if arg not in _SED_FLAGS:
                return f"sed {arg} is not allowed; use `sed -n <from>,<to>p <file>`"
        else:
            operands.append(arg)
        i += 1
    if not scripts and operands:
        scripts.append(operands[0])
    if not scripts or not all(_SED_PRINT.match(s) for s in scripts):
        return "sed may only print line ranges (`sed -n <from>,<to>p <file>`)"
    return None


def _shell_body_only(argv: list[str]) -> bool:
    """True for `bash -lc '<body>'` style calls whose flags only set errexit, pipefail and the like."""
    args, i = argv[1:], 0
    while i < len(args):
        arg = args[i]
        if arg in ("-o", "+o") and i + 1 < len(args) and args[i + 1] in _SHELL_OPTIONS:
            i += 2
            continue
        if not (arg[:1] in "-+" and len(arg) > 1 and set(arg[1:]) <= _SHELL_SHORT):
            return False
        if "c" in arg[1:]:
            return True
        i += 2 if arg.endswith("o") and i + 1 < len(args) and args[i + 1] in _SHELL_OPTIONS else 1
    return False


def _long(arg: str, option: str, shortest: int) -> bool:
    name = arg.split("=", 1)[0]
    return len(name) >= shortest and option.startswith(name)


def _check_args(name: str, args: list[str]) -> str | None:
    if name == "git":
        return check_git(args, GIT_READ)
    if name == "gh":
        return _check_gh(args)
    if name == "sed":
        return _check_sed(args)
    if name == "sort" and any(
        _long(a, "--output", 3) or _long(a, "--compress-program", 4) or (a[:1] == "-" and a[1:2] != "-" and "o" in a)
        for a in args
    ):
        return "sort with an output file or a compress program is not allowed"
    if name == "uniq":
        operands, i = [], 0
        while i < len(args):
            if args[i] in _UNIQ_VALUES:
                i += 2
                continue
            if not args[i].startswith("-") or args[i] == "-":
                operands.append(args[i])
            i += 1
        if len(operands) > 1:
            return "uniq with an output file is not allowed"
    if name in ("test", "[") and "-v" in args:
        return "test -v evaluates an array subscript"
    if name == "rg" and any(a.startswith(("--pre", "--hostname-bin")) for a in args):
        return "rg --pre and --hostname-bin run a command"
    return None


def _check(tokens: list[str], allowed: frozenset[str], exact: list[list[str]]) -> str | None:
    if tokens in exact:
        return None
    for i, token in enumerate(tokens):
        if ">" in token:
            target = tokens[i + 1] if i + 1 < len(tokens) else ""
            return f"redirecting output to {target or 'a file'} is not allowed; only `2>/dev/null` and `2>&1` are"
    if _FIND_WRITES & set(tokens):
        return "find with -delete or -fprint is not allowed"
    command: Command = resolve(tokens)
    if command.assignments:
        return f"environment assignments ({command.assignments[0].split('=', 1)[0]}=) are not allowed"
    unsafe = [w for w in command.wrappers if w not in _SAFE_WRAPPERS]
    if unsafe:
        return f"{unsafe[0]} is not allowed; run the read command directly"
    if command.stdin_exec:
        return "piping a script into a shell is not allowed"
    if not command.argv:
        return None
    program, args = command.argv[0], command.argv[1:]
    name = os.path.basename(program)
    if assigns(command):
        return f"{name} assigns shell variables, which is not allowed"
    if name in SHELLS:
        if "find" in command.wrappers:
            return "a shell through find -exec is not allowed"
        system = "/" not in program or (program.startswith(_SYSTEM_SHELL_DIRS) and program.count("/") <= 3)
        if system and command.nested and _shell_body_only(command.argv):
            return None
    if "/" in program:
        return f"{program} is not allowed; run commands by bare name"
    if name not in allowed:
        return f"{name} is not on the read-only command list"
    return _check_args(name, args)


def read_only_shell(
    *, extra_commands: list[str] | None = None, exact_commands: list[str] | None = None
) -> Callable[[Event], Decision | None]:
    allowed = READ_COMMANDS | frozenset(extra_commands or [])
    exact = [shlex.split(c) for c in exact_commands or []]

    def policy(event: Event) -> Decision | None:
        arguments = tool_call(event, SHELL_TOOLS)
        if arguments is None:
            return None
        if arguments.get("_unparseable"):
            return deny("unparseable tool arguments")
        command = shell_command(arguments)
        if command is None:
            return deny("shell command is not a string")
        command = _STDERR_ONLY.sub("", command)
        if _FILE_REDIRECT.search(command):
            return deny(f"redirecting output to a file is not allowed; {_HINT}")
        if _EXPANSION.search(command):
            return deny(f"arithmetic, subscript, substring and indirect expansions are not allowed; {_HINT}")
        try:
            statements = walk(command)
        except ValueError:
            return deny("unparseable shell command; simplify it")
        for tokens in statements:
            reason = _check(tokens, allowed, exact)
            if reason:
                return deny(f"{reason}; {_HINT}")
        return allow()

    return policy


def assigns(command: Command) -> bool:
    """True when a statement sets shell variables: `VAR=`, `env VAR=`, an assigning builtin or `printf -v`."""
    if command.assignments or "env" in command.wrappers:
        return True
    if not command.argv:
        return False
    name = os.path.basename(command.argv[0])
    return name in ASSIGNING_BUILTINS or (name == "printf" and any(a.startswith("-v") for a in command.argv[1:]))


def git_verbs(*, extra_verbs: list[str] | None = None) -> Callable[[Event], Decision | None]:
    verbs = GIT_READ | frozenset(extra_verbs or [])

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
            if re.search(r"\bgit\b", command):
                return deny("unparseable command naming git; simplify it")
            return allow()
        resolved_all = [resolve(tokens) for tokens in statements]
        runs_git = any(r.argv and os.path.basename(r.argv[0]) == "git" for r in resolved_all)
        if runs_git and any(assigns(r) for r in resolved_all):
            return deny("setting variables in a command that runs git is not allowed; hand repository changes to the implementer")
        for resolved in resolved_all:
            argv = resolved.argv
            if argv and os.path.basename(argv[0]) == "git":
                if {"xargs", "parallel"} & set(resolved.wrappers):
                    return deny("git through xargs or parallel is not allowed, since stdin can add arguments")
                reason = check_git(argv[1:], verbs)
                if reason:
                    return deny(f"{reason}; hand repository changes to the implementer")
        return allow()

    return policy
