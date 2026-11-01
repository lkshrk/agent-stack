"""Git and gh rules inside a registered public contribution checkout: exact allowlists, everything else denied."""

import os
import re
import subprocess
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from ._event import Decision, allow, deny
from ._shell import DEFAULT_PROTECTED, push_branch

if TYPE_CHECKING:
    from .public_contributions import RegisteredRepository


GIT_TIMEOUT = 10
_GIT_SAFE_CONFIG = (
    "core.fsmonitor=false",
    "core.hooksPath=/dev/null",
    "core.pager=cat",
    "diff.external=",
    "log.showSignature=false",
    "submodule.recurse=false",
)
_GIT_ENV_KEEP = ("PATH", "HOME", "XDG_CONFIG_HOME", "GIT_CONFIG_GLOBAL", "GIT_CONFIG_NOSYSTEM")
_FILTER_DRIVER = re.compile(r"[A-Za-z0-9_.-]+")
_WORKFLOWS = ".github/workflows/"
_OWNER_LABEL = "approved-upstream"
_REMOTE_NAME = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]*")
_BRANCH_NAME = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_./-]*")
_REVISION = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_./~^@{}:-]*")
_CONFIG_KEY = re.compile(r"[A-Za-z][A-Za-z0-9-]*(\.[A-Za-z0-9_.-]+)*")
_SECRET_CONFIG = ("extraheader", "credential", "token", "password", "secret", "askpass")
_GIT_PAGER_OFF = frozenset({"--no-pager", "-P"})
_LOG_COUNT = re.compile(r"-[0-9]+")
_ENDPOINT = re.compile(
    r"/?repos/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+"
    r"(/(pulls|issues|commits|compare|branches|check-runs|check-suites|actions/runs|actions/jobs|contents|statuses|labels)(/[A-Za-z0-9_.,/=-]*)?)?"
    r"(\?[A-Za-z0-9_.,&=%/-]*)?"
)

LOCAL_GIT_FORM = (
    "in a registered contribution git allows status, diff, log, show, rev-parse, ls-files, merge-base, add, commit -m, "
    "restore, rebase <upstream>, fetch <remote> [<branch>], `switch -c`/`checkout -b <branch>` onto a non-default branch, "
    "branch listing, `config --get <key>`, `remote -v`, `remote get-url <name>` and the fork push, each with its documented options only"
)
FORK_PUSH_FORM = "in a registered contribution only `git push [-u] <fork-remote> HEAD:<branch>` to a non-default branch of the owner's fork is allowed"
FORK_GH_FORM = "in a registered contribution gh may write only through `gh pr create|edit` and `gh issue create|edit` with `--repo <fork>`"


@dataclass(frozen=True)
class _Options:
    flags: frozenset[str] = frozenset()
    values: frozenset[str] = frozenset()
    counts: bool = False


@dataclass
class _Parsed:
    positional: list[str] = field(default_factory=list)
    options: dict[str, list[str]] = field(default_factory=dict)


def _options(flags: str = "", values: str = "", counts: bool = False) -> _Options:
    return _Options(frozenset(flags.split()), frozenset(values.split()), counts)


_GIT_OPTIONS = {
    "status": _options("-s --short -b --branch --porcelain --porcelain=v1 --porcelain=v2 -z --untracked-files=no --untracked-files=normal --untracked-files=all"),
    "diff": _options(
        "--cached --staged --stat --shortstat --numstat --name-only --name-status -p --patch --no-color --check -w "
        "--ignore-all-space --quiet --exit-code -z --no-ext-diff --no-textconv --word-diff --merge-base -R",
        "--unified --diff-filter",
    ),
    "log": _options(
        "--oneline --graph --decorate --no-decorate --stat --shortstat --name-only --name-status -p --patch --no-color "
        "--all --reverse --first-parent --no-merges --merges -z --follow --abbrev-commit --no-ext-diff --no-textconv",
        "--format --pretty -n --max-count --since --until --after --before --author --grep --date --skip",
        counts=True,
    ),
    "show": _options(
        "--stat --shortstat --name-only --name-status --oneline -p --patch --no-patch -s --no-color --no-ext-diff --no-textconv --abbrev-commit",
        "--format --pretty",
    ),
    "rev-parse": _options(
        "--abbrev-ref --short --show-toplevel --git-dir --verify --symbolic-full-name -q --quiet --is-inside-work-tree --show-prefix",
        "--short",
    ),
    "ls-files": _options("-m --modified -o --others --exclude-standard -s --stage -z -c --cached -d --deleted -u --unmerged"),
    "merge-base": _options("--is-ancestor --all --fork-point"),
    "restore": _options("--staged -S --worktree -W --ours --theirs -q --quiet", "--source -s"),
    "add": _options("-A --all -u --update -N --intent-to-add -v --verbose -n --dry-run -f --force --chmod=+x --chmod=-x"),
    "commit": _options(
        "-s --signoff -a --all -q --quiet --amend --no-edit -v --verbose --allow-empty --no-verify -n",
        "-m --message --author --trailer --date --fixup",
    ),
    "fetch": _options("--prune -p --no-tags --tags --all -q --quiet -v --verbose --unshallow --prune-tags", "--depth"),
    "rebase": _options("-q --quiet --continue --abort --skip --quit", "--onto"),
    "branch": _options("--show-current -a --all -r --remotes -l --list -v -vv --verbose"),
    "push": _options("-u --set-upstream"),
}
_PATHSPEC_COMMANDS = frozenset({"status", "diff", "log", "show", "ls-files", "restore", "add", "commit"})


def _parse(args: list[str], spec: _Options) -> _Parsed | None:
    """Exact options only: no abbreviations, no bundled short flags, `--name=value` only for long value options."""
    parsed = _Parsed()
    index = 0
    after_separator = False
    while index < len(args):
        token = args[index]
        if after_separator or token == "-" or not token.startswith("-"):
            parsed.positional.append(token)
            index += 1
            continue
        if token == "--":
            after_separator = True
            index += 1
            continue
        if token in spec.flags or (spec.counts and _LOG_COUNT.fullmatch(token)):
            parsed.options.setdefault(token, [])
            index += 1
            continue
        name, equals, value = token.partition("=") if token.startswith("--") else (token, "", "")
        if name not in spec.values:
            return None
        if not equals:
            if index + 1 >= len(args):
                return None
            index += 1
            value = args[index]
        parsed.options.setdefault(name, []).append(value)
        index += 1
    return parsed


def into_git_dir(value: str) -> bool:
    return any(part == ".git" for candidate in (value, value.partition("=")[2]) for part in candidate.replace("\\", "/").split("/"))


def _run_git(cwd: str, overrides: list[str], args: tuple[str, ...], environment: dict[str, str] | None = None) -> str | None:
    if environment is None:
        environment = {key: os.environ[key] for key in _GIT_ENV_KEEP if key in os.environ}
    try:
        completed = subprocess.run(
            ["git", "--no-optional-locks", *overrides, "-C", cwd, *args],
            capture_output=True,
            text=True,
            timeout=GIT_TIMEOUT,
            env={**environment, "GIT_TERMINAL_PROMPT": "0", "LC_ALL": "C"},
            stdin=subprocess.DEVNULL,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return completed.stdout if completed.returncode == 0 else None


def _safe_overrides() -> list[str]:
    return [item for setting in _GIT_SAFE_CONFIG for item in ("-c", setting)]


def _filter_overrides(cwd: str, base: list[str]) -> list[str] | None:
    listing = _run_git(cwd, base, ("config", "--name-only", "-z", "--get-regexp", r"^filter\."))
    drivers = {key.removeprefix("filter.").rpartition(".")[0] for key in (listing or "").split("\0") if key}
    if not all(_FILTER_DRIVER.fullmatch(driver) for driver in drivers):
        return None
    return [
        item
        for driver in sorted(drivers)
        for setting in (f"filter.{driver}.clean=cat", f"filter.{driver}.smudge=cat", f"filter.{driver}.process=", f"filter.{driver}.required=false")
        for item in ("-c", setting)
    ]


def git(cwd: str, *args: str) -> str | None:
    """Run git in the agent's checkout with every setting that can execute a program neutralised; None on any failure or timeout."""
    base = _safe_overrides()
    filters = _filter_overrides(cwd, base)
    if filters is None:
        return None
    return _run_git(cwd, base + filters, args)


def fork_default_head(repository: "RegisteredRepository") -> str | None:
    """The fork's default-branch commit, read from the registry's fork URL outside the agent's checkout and config."""
    environment = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "GIT_CONFIG_GLOBAL": os.devnull}
    overrides = [*_safe_overrides(), "-c", "protocol.allow=never", "-c", "protocol.https.allow=always"]
    url = f"https://github.com/{repository.fork}.git"
    output = _run_git("/", overrides, ("ls-remote", "--heads", url, f"refs/heads/{repository.default_branch}"), environment)
    if output is None:
        return None
    for line in output.splitlines():
        sha, _, ref = line.partition("\t")
        if ref == f"refs/heads/{repository.default_branch}" and re.fullmatch(r"[0-9a-f]{40}", sha):
            return sha
    return None


def _fork_urls(repository: "RegisteredRepository") -> frozenset[str]:
    return frozenset({f"https://github.com/{repository.fork}.git", f"https://github.com/{repository.fork}"})


def _remote_is_fork(cwd: str, remote: str, repository: "RegisteredRepository") -> bool:
    if git(cwd, "config", "--get-all", f"remote.{remote}.vcs") is not None:
        return False
    for key in (f"remote.{remote}.mirror", "push.followTags"):
        if git(cwd, "config", "--type=bool", "--get", key) != "false\n" and git(cwd, "config", "--get", key) is not None:
            return False
    urls = git(cwd, "remote", "get-url", "--push", "--all", remote)
    if urls is None:
        return False
    lines = urls.splitlines()
    return bool(lines) and all(line in _fork_urls(repository) for line in lines)


def _pushed_paths(cwd: str, repository: "RegisteredRepository") -> list[str] | None:
    base = fork_default_head(repository)
    if base is None or git(cwd, "cat-file", "-e", f"{base}^{{commit}}") is None:
        return None
    output = git(cwd, "log", "--no-ext-diff", "--no-textconv", "--ignore-submodules=all", "-z", "--format=", "--name-only", "--no-renames", "-m", "HEAD", "--not", base)
    if output is None:
        return None
    return [path.strip("\n") for path in output.split("\0") if path.strip("\n")]


def _feature_branch(destination: str, repository: "RegisteredRepository") -> bool:
    branch = destination.removeprefix("refs/heads/")
    protected = {name.casefold() for name in (*DEFAULT_PROTECTED, repository.default_branch, "HEAD")}
    return (
        bool(_BRANCH_NAME.fullmatch(branch))
        and ".." not in branch
        and not branch.startswith("refs/")
        and push_branch(destination) == branch
        and branch.casefold() not in protected
    )


def _workflow_paths(paths: list[str]) -> bool:
    return any(path.strip('"').startswith(_WORKFLOWS) for path in paths)


def _check_add(args: list[str], cwd: str) -> Decision:
    output = git(cwd, "add", "--dry-run", *args)
    if output is None:
        return deny("cannot check what `git add` would stage")
    staged = [line.removeprefix("add '").removesuffix("'") for line in output.splitlines()]
    if _workflow_paths(staged):
        return deny("staging changes under .github/workflows/ is not allowed")
    return allow()


def _check_commit(parsed: _Parsed, cwd: str) -> Decision:
    if not ({"-m", "--message", "--no-edit"} & parsed.options.keys()):
        return deny("git commit needs -m <message> (or --amend --no-edit); no editor runs here")
    output = git(cwd, "diff", "--no-ext-diff", "--no-textconv", "--ignore-submodules=all", "-z", "--name-only", "HEAD")
    if output is None:
        return deny("cannot check what `git commit` would record")
    if _workflow_paths([path for path in output.split("\0") if path]):
        return deny("committing changes under .github/workflows/ is not allowed")
    return allow()


def _check_new_branch(args: list[str], flag: str, repository: "RegisteredRepository") -> Decision:
    if args[:1] != [flag] or len(args) not in (2, 3) or any(a.startswith("-") for a in args[1:]):
        return deny(LOCAL_GIT_FORM)
    if not _feature_branch(args[1], repository) or (len(args) == 3 and not _REVISION.fullmatch(args[2])):
        return deny(f"a new branch must not be {repository.default_branch}, HEAD or another protected branch; {LOCAL_GIT_FORM}")
    return allow()


def _check_fetch(parsed: _Parsed, cwd: str) -> Decision:
    if not parsed.positional:
        return allow() if "--all" in parsed.options else deny(f"name the remote to fetch; {LOCAL_GIT_FORM}")
    remote, refspecs = parsed.positional[0], parsed.positional[1:]
    remotes = git(cwd, "remote")
    if not _REMOTE_NAME.fullmatch(remote) or remotes is None or remote not in remotes.split():
        return deny(f"git fetch takes only a configured remote name, not a URL or path; {LOCAL_GIT_FORM}")
    if any(not _BRANCH_NAME.fullmatch(r) or ":" in r for r in refspecs):
        return deny("git fetch refspecs may name only a source branch, never a destination ref")
    return allow()


def _check_rebase(parsed: _Parsed) -> Decision:
    control = {"--continue", "--abort", "--skip", "--quit"} & parsed.options.keys()
    if control:
        return allow() if len(parsed.options) == 1 and not parsed.positional else deny(LOCAL_GIT_FORM)
    if len(parsed.positional) != 1 or not _REVISION.fullmatch(parsed.positional[0]):
        return deny(f"git rebase takes one upstream revision; {LOCAL_GIT_FORM}")
    if any(not _REVISION.fullmatch(v) for v in parsed.options.get("--onto", [])):
        return deny(LOCAL_GIT_FORM)
    return allow()


def _check_config(args: list[str]) -> Decision:
    if len(args) == 2 and args[0] in ("--get", "--get-all") and _CONFIG_KEY.fullmatch(args[1]):
        if not any(word in args[1].casefold() for word in _SECRET_CONFIG):
            return allow()
    return deny("only `git config --get <key>` reads are allowed in a registered contribution, and never credential settings")


def _check_remote(args: list[str]) -> Decision:
    if args in ([], ["-v"]) or (len(args) == 2 and args[0] == "get-url" and _REMOTE_NAME.fullmatch(args[1])):
        return allow()
    return deny("changing git remotes is not allowed in a registered contribution")


def registered_git(argv: list[str], cwd: str, repository: "RegisteredRepository") -> Decision:
    rest = argv[1:]
    while rest[:1] and rest[0] in _GIT_PAGER_OFF:
        rest = rest[1:]
    if not rest or rest[0].startswith("-"):
        return deny(f"git global options other than --no-pager are not allowed here; {LOCAL_GIT_FORM}")
    sub, args = rest[0], rest[1:]
    if sub == "switch":
        return _check_new_branch(args, "-c", repository)
    if sub == "checkout":
        return _check_new_branch(args, "-b", repository)
    if sub == "config":
        return _check_config(args)
    if sub == "remote":
        return _check_remote(args)
    spec = _GIT_OPTIONS.get(sub)
    if spec is None:
        return deny(f"git {sub} is not allowed in a registered contribution; {LOCAL_GIT_FORM}")
    parsed = _parse(args, spec)
    if parsed is None:
        return deny(f"git {sub} with an option outside its allowlist is not allowed here; {LOCAL_GIT_FORM}")
    if sub in _PATHSPEC_COMMANDS and any(into_git_dir(p) for p in parsed.positional):
        return deny("paths inside .git/ are not allowed")
    if sub == "push":
        return _fork_push(parsed, cwd, repository)
    if sub == "add":
        return _check_add(args, cwd)
    if sub == "commit":
        return _check_commit(parsed, cwd)
    if sub == "fetch":
        return _check_fetch(parsed, cwd)
    if sub == "rebase":
        return _check_rebase(parsed)
    if sub == "branch" and parsed.positional:
        return deny(f"git branch may only list here; {LOCAL_GIT_FORM}")
    if sub == "restore" and not parsed.positional:
        return deny("git restore needs a path")
    return allow()


def _fork_push(parsed: _Parsed, cwd: str, repository: "RegisteredRepository") -> Decision:
    if len(parsed.positional) != 2 or not _REMOTE_NAME.fullmatch(parsed.positional[0]) or not parsed.positional[1].startswith("HEAD:"):
        return deny(FORK_PUSH_FORM)
    remote, refspec = parsed.positional
    if not _feature_branch(refspec.removeprefix("HEAD:"), repository):
        return deny(f"{FORK_PUSH_FORM} (got {refspec})")
    if not _remote_is_fork(cwd, remote, repository):
        return deny(f"git remote {remote} does not push only to the registered fork {repository.fork}; {FORK_PUSH_FORM}")
    paths = _pushed_paths(cwd, repository)
    if paths is None:
        return deny(f"cannot list the commits this push sends; fetch the fork first with `git fetch {remote} {repository.default_branch}`")
    if _workflow_paths(paths):
        return deny("a push that changes .github/workflows/ is not allowed")
    return allow()


_GH_SHORT = {
    "-R": "--repo", "-t": "--title", "-b": "--body", "-B": "--base", "-H": "--head", "-l": "--label",
    "-a": "--assignee", "-m": "--milestone", "-p": "--project", "-r": "--reviewer", "-d": "--draft",
}
_GH_COMMON = {"--repo": True, "--title": True, "--body": True, "--milestone": True}
_GH_CREATE = {**_GH_COMMON, "--label": True, "--assignee": True, "--project": True}
_GH_EDIT = {
    **_GH_COMMON,
    "--add-label": True, "--remove-label": True, "--add-assignee": True, "--remove-assignee": True,
    "--add-project": True, "--remove-project": True, "--remove-milestone": False,
}
_GH_FORK_WRITES: dict[tuple[str, str], dict[str, bool]] = {
    ("pr", "create"): {**_GH_CREATE, "--base": True, "--head": True, "--reviewer": True, "--draft": False, "--no-maintainer-edit": False},
    ("pr", "edit"): {**_GH_EDIT, "--base": True, "--add-reviewer": True, "--remove-reviewer": True},
    ("issue", "create"): _GH_CREATE,
    ("issue", "edit"): _GH_EDIT,
}
_GH_READS = frozenset(
    {("pr", verb) for verb in ("view", "diff", "checks", "list", "status")}
    | {("issue", verb) for verb in ("view", "list", "status")}
    | {("run", verb) for verb in ("view", "list", "watch")}
    | {("repo", "view")}
)
_GH_API_READ = _options("--paginate", "--jq -q --method -X")


def gh_exposes_token(argv: list[str]) -> bool:
    if argv[1:3] == ["auth", "token"]:
        return True
    if argv[1:3] != ["auth", "status"]:
        return False
    return any(a == "--show-token" or a.startswith("--show-token=") or (a.startswith("-") and not a.startswith("--") and "t" in a) for a in argv[3:])


def _gh_api_read(args: list[str]) -> bool:
    parsed = _parse(args, _GH_API_READ)
    if parsed is None or len(parsed.positional) != 1 or not _ENDPOINT.fullmatch(parsed.positional[0]):
        return False
    methods = parsed.options.get("--method", []) + parsed.options.get("-X", [])
    return all(method.upper() == "GET" for method in methods)


def _gh_read(argv: list[str]) -> bool:
    if gh_exposes_token(argv):
        return False
    if argv[1:2] == ["api"]:
        return _gh_api_read(argv[2:])
    return tuple(argv[1:3]) in _GH_READS


def _gh_options(command: tuple[str, str], args: list[str]) -> dict[str, list[str]] | None:
    """Values per long flag name, or None for an unknown flag, a fused short flag or a positional other than one number."""
    flags = _GH_FORK_WRITES[command]
    values: dict[str, list[str]] = {}
    positional: list[str] = []
    index = 0
    while index < len(args):
        token = args[index]
        if not token.startswith("-"):
            positional.append(token)
            index += 1
            continue
        name, equals, value = token.partition("=")
        name = _GH_SHORT.get(name, name) if not name.startswith("--") else name
        if name not in flags:
            return None
        if flags[name]:
            if not equals:
                if index + 1 >= len(args):
                    return None
                index += 1
                value = args[index]
            values.setdefault(name, []).append(value)
        elif equals:
            return None
        index += 1
    if len(positional) != (1 if command[1] == "edit" else 0) or not all(p.isdigit() for p in positional):
        return None
    return values


def fork_gh(argv: list[str], repository: "RegisteredRepository") -> Decision | None:
    command = (argv[1], argv[2]) if len(argv) >= 3 else None
    if command is None or command not in _GH_FORK_WRITES:
        if _gh_read(argv):
            return None
        return deny(f"{FORK_GH_FORM}; reads are limited to pr/issue/run/repo views and GET `gh api` on repository endpoints")
    values = _gh_options(command, argv[3:])
    if values is None:
        return deny(f"{FORK_GH_FORM}; use only documented flags with separate values, --body instead of a file, and a PR or issue number")
    if values.get("--repo") != [repository.fork]:
        return deny(f"{FORK_GH_FORM}; name the fork exactly once with --repo {repository.fork}")
    labels = [label.strip().casefold() for name in ("--label", "--add-label") for value in values.get(name, []) for label in value.split(",")]
    if _OWNER_LABEL in labels:
        return deny(f"only the owner applies the {_OWNER_LABEL} label")
    if command == ("pr", "create") and len(values.get("--head", [])) != 1:
        return deny(f"{FORK_GH_FORM}; gh pr create needs exactly one --head <branch>")
    return allow()
