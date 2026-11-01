import hashlib
import json
import os
import posixpath
import re
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import yaml

from ._event import Decision, Event, allow, deny, tool_call
from ._shell import DEFAULT_PROTECTED, OpaqueCommand, push_branch, resolve, shell_command, walk
from .paths import SHELL_TOOLS
from .tools import direct_public_write, gh_api_mutates


RUNTIME_REGISTRY = Path("/opt/agent-stack/public-contributions.yaml")
HELPER_PATH = "/opt/agent/bin/agent-stack-contribute"
HELPER_NAMES = frozenset({HELPER_PATH, "agent-stack-contribute"})
CONTRIBUTION_WORKSPACE = Path("/home/omnigent/work")
FORK_OWNER = "lkshrk"
CI_PROVIDERS = frozenset({"github-hosted"})
_CONTROLLER_GIT = frozenset({"clone", "fetch", "worktree", "branch", "add", "commit", "remote"})
_GIT_PAGER_OFF = frozenset({"--no-pager", "-P"})
_GIT_TIMEOUT = 10
_WORKFLOWS = ".github/workflows/"
_OWNER_LABEL = "approved-upstream"
_REMOTE_NAME = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]*")
_BRANCH_NAME = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_./-]*")
_GH_SLUG = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")
_GH_READS = frozenset(
    {("pr", verb) for verb in ("view", "diff", "checks", "list", "status")}
    | {("issue", verb) for verb in ("view", "list", "status")}
    | {("run", verb) for verb in ("view", "list", "watch")}
    | {("repo", "view"), ("auth", "status")}
)
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
FORK_PUSH_FORM = "in a registered contribution only `git push [-u] <fork-remote> HEAD:<branch>` to a non-default branch of the owner's fork is allowed"
FORK_GH_FORM = "in a registered contribution gh may write only through `gh pr create|edit` and `gh issue create|edit` with `--repo <fork>`"


class RegistryError(ValueError):
    pass


class _StrictLoader(yaml.SafeLoader):
    pass


def _construct_mapping(loader: yaml.SafeLoader, node: yaml.nodes.MappingNode, deep: bool = False) -> dict:
    mapping = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in mapping:
            raise RegistryError(f"duplicate YAML key: {key}")
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


_StrictLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _construct_mapping)


@dataclass(frozen=True)
class ValidationCommand:
    name: str
    cwd: str
    argv: tuple[str, ...]


@dataclass(frozen=True)
class ValidationInstall:
    argv: tuple[str, ...]
    lifecycle_scripts: str


@dataclass(frozen=True)
class ValidationProfile:
    name: str
    image: str
    install: ValidationInstall
    commands: tuple[ValidationCommand, ...]
    package_registries: tuple[str, ...]
    github_read_access: str


@dataclass(frozen=True)
class ContributionRequirements:
    issue_before_bugfix_pr: bool
    dco: str
    demo_for_ui: str
    no_ai_attribution: bool


@dataclass(frozen=True)
class RegisteredRepository:
    name: str
    upstream: str
    fork: str
    ci: str
    checkout: str
    default_branch: str
    visibility: str
    upstream_commit: str
    validation: ValidationProfile
    contribution: ContributionRequirements
    lifecycle: str

    def validation_profile(self, name: str) -> ValidationProfile:
        if self.validation.name != name:
            raise RegistryError(f"unknown validation profile for {self.name}: {name}")
        return self.validation


@dataclass(frozen=True)
class Registry:
    version: int
    repositories: tuple[RegisteredRepository, ...]
    revision: str

    @classmethod
    def load(cls, path: Path) -> "Registry":
        try:
            data = yaml.load(path.read_text(), Loader=_StrictLoader)
        except (OSError, yaml.YAMLError) as error:
            raise RegistryError(f"cannot load registry: {error}") from error
        _mapping(data, "registry", {"version", "repositories"})
        if type(data["version"]) is not int or data["version"] != 1:
            raise RegistryError("registry version must be the integer 1")
        repositories_data = data["repositories"]
        if not isinstance(repositories_data, dict) or not repositories_data:
            raise RegistryError("repositories must be a non-empty mapping")
        repositories = tuple(
            _repository(name, entry) for name, entry in repositories_data.items()
        )
        _unique_checkouts(repositories)
        canonical = json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        return cls(version=1, repositories=repositories, revision=hashlib.sha256(canonical.encode()).hexdigest())

    def repository(self, name: str) -> RegisteredRepository:
        for repository in self.repositories:
            if repository.name == name:
                return repository
        raise RegistryError(f"unknown registered repository: {name}")


def _mapping(value: Any, context: str, allowed: set[str]) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise RegistryError(f"{context} must be a mapping")
    unknown = set(value) - allowed
    missing = allowed - set(value)
    if unknown:
        raise RegistryError(f"{context} has unknown field: {sorted(unknown)[0]}")
    if missing:
        raise RegistryError(f"{context} is missing field: {sorted(missing)[0]}")
    return value


def _string(value: Any, context: str) -> str:
    if not isinstance(value, str) or not value:
        raise RegistryError(f"{context} must be a non-empty string")
    return value


def _repository(name: Any, entry: Any) -> RegisteredRepository:
    name = _string(name, "repository name")
    entry = _mapping(
        entry,
        f"repository {name}",
        {
            "upstream",
            "fork",
            "ci",
            "checkout",
            "default_branch",
            "visibility",
            "upstream_commit",
            "validation",
            "contribution",
            "lifecycle",
        },
    )
    upstream = _string(entry["upstream"], f"repository {name}.upstream")
    _github_upstream(upstream)
    fork = _fork(entry["fork"], upstream, f"repository {name}.fork")
    ci = entry["ci"]
    if not isinstance(ci, str) or ci not in CI_PROVIDERS:
        raise RegistryError(f"repository {name}.ci must be one of {sorted(CI_PROVIDERS)}")
    checkout =_relative_path(entry["checkout"], f"repository {name}.checkout")
    default_branch = _string(entry["default_branch"], f"repository {name}.default_branch")
    visibility = _string(entry["visibility"], f"repository {name}.visibility")
    if visibility != "public":
        raise RegistryError(f"repository {name} must be public HTTPS GitHub")
    upstream_commit = _string(entry["upstream_commit"], f"repository {name}.upstream_commit")
    if not re.fullmatch(r"[0-9a-f]{40}", upstream_commit):
        raise RegistryError(f"repository {name}.upstream_commit must be a full lowercase SHA")
    lifecycle = _string(entry["lifecycle"], f"repository {name}.lifecycle")
    if lifecycle != "registered":
        raise RegistryError(f"repository {name}.lifecycle must be registered")
    return RegisteredRepository(
        name=name,
        upstream=upstream,
        fork=fork,
        ci=ci,
        checkout=checkout,
        default_branch=default_branch,
        visibility=visibility,
        upstream_commit=upstream_commit,
        validation=_validation(name, entry["validation"]),
        contribution=_contribution(name, entry["contribution"]),
        lifecycle=lifecycle,
    )


def _github_upstream(upstream: str) -> None:
    parsed = urlparse(upstream)
    if parsed.scheme != "https" or parsed.netloc != "github.com" or parsed.params or parsed.query or parsed.fragment:
        raise RegistryError("upstream must be a public HTTPS GitHub URL")
    if not re.fullmatch(r"/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\.git", parsed.path):
        raise RegistryError("upstream must be a canonical GitHub URL ending in .git")


def _fork(value: Any, upstream: str, context: str) -> str:
    fork = _string(value, context)
    upstream_owner, upstream_repo = urlparse(upstream).path.removesuffix(".git").strip("/").split("/")
    if upstream_owner == FORK_OWNER:
        raise RegistryError(f"{context}: upstream {upstream} is in the owner's account, so it needs no fork")
    if not _GH_SLUG.fullmatch(fork) or fork != f"{FORK_OWNER}/{upstream_repo}":
        raise RegistryError(f"{context} must be {FORK_OWNER}/{upstream_repo}, the owner's fork of the upstream")
    return fork


def _relative_path(value: Any, context: str) -> str:
    value = _string(value, context)
    normalized = posixpath.normpath(value)
    if "\\" in value or value != normalized or normalized in {".", ".."} or normalized.startswith("../") or normalized.startswith("/"):
        raise RegistryError(f"{context} must be a normalized relative path")
    return normalized


def _validation(repository: str, entry: Any) -> ValidationProfile:
    entry = _mapping(
        entry,
        f"repository {repository}.validation",
        {"image", "profile", "install", "commands", "package_registries", "github_read_access"},
    )
    profile = _relative_path(entry["profile"], f"repository {repository}.validation.profile")
    image = _string(entry["image"], f"repository {repository}.validation.image")
    if not re.fullmatch(r"[a-z0-9./_-]+@sha256:[0-9a-f]{64}", image):
        raise RegistryError(f"repository {repository}.validation.image must use a pinned digest")
    install_entry = _mapping(entry["install"], f"repository {repository}.validation.install", {"argv", "lifecycle_scripts"})
    install = ValidationInstall(
        argv=_argv(install_entry["argv"], f"repository {repository}.validation.install.argv"),
        lifecycle_scripts=_string(install_entry["lifecycle_scripts"], f"repository {repository}.validation.install.lifecycle_scripts"),
    )
    if install.lifecycle_scripts != "denied" or "--ignore-scripts" not in install.argv:
        raise RegistryError(f"repository {repository}.validation.install must disable lifecycle scripts")
    commands_data = entry["commands"]
    if not isinstance(commands_data, list) or not commands_data:
        raise RegistryError(f"repository {repository}.validation.commands must be a non-empty list")
    commands = tuple(_command(repository, profile, command) for command in commands_data)
    names = [command.name for command in commands]
    if len(names) != len(set(names)):
        raise RegistryError(f"repository {repository}.validation has duplicate command names")
    registries = entry["package_registries"]
    if not isinstance(registries, list) or not registries or any(url != "https://registry.npmjs.org" for url in registries):
        raise RegistryError(f"repository {repository}.validation.package_registries must contain the public npm registry")
    access = _string(entry["github_read_access"], f"repository {repository}.validation.github_read_access")
    if access != "public-read":
        raise RegistryError(f"repository {repository}.validation.github_read_access must be public-read")
    return ValidationProfile(profile, image, install, commands, tuple(registries), access)


def _command(repository: str, profile: str, entry: Any) -> ValidationCommand:
    entry = _mapping(entry, f"repository {repository}.validation command", {"name", "cwd", "argv"})
    cwd = _relative_path(entry["cwd"], f"repository {repository}.validation command.cwd")
    if cwd != profile and not cwd.startswith(f"{profile}/"):
        raise RegistryError(f"repository {repository}.validation command path is outside profile {profile}")
    return ValidationCommand(
        name=_string(entry["name"], f"repository {repository}.validation command.name"),
        cwd=cwd,
        argv=_argv(entry["argv"], f"repository {repository}.validation command.argv"),
    )


def _argv(value: Any, context: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not value or any(not isinstance(item, str) or not item for item in value):
        raise RegistryError(f"{context} must be a non-empty argv list")
    if any(re.search(r"[;&|`$<>\n\r()\[\]*?\\\\]", item) for item in value):
        raise RegistryError(f"{context} contains shell syntax")
    return tuple(value)


def _registered_repository(value: object, registry: Registry) -> RegisteredRepository | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        path = Path(value).resolve(strict=False)
    except OSError:
        return None
    for repository in registry.repositories:
        root = CONTRIBUTION_WORKSPACE / repository.checkout
        if path == root or root in path.parents:
            return repository
    return None


def _registered_path(value: object, registry: Registry) -> bool:
    return _registered_repository(value, registry) is not None


def _cwd(arguments: dict) -> object:
    return arguments.get("cwd", arguments.get("workdir", arguments.get("working_directory")))


def _registered_tree(arguments: dict, registry: Registry) -> bool:
    return _registered_path(_cwd(arguments), registry)


def _git_targets(argv: list[str]) -> tuple[str, ...]:
    if argv[:1] != ["git"]:
        return ()
    paths: list[str] = []
    index = 1
    while index < len(argv):
        token = argv[index]
        if token in {"-c", "--config-env"}:
            index += 2
            continue
        if (token.startswith("-c") and len(token) > 2) or token.startswith("--config-env="):
            index += 1
            continue
        if token in {"-C", "--work-tree", "--git-dir"}:
            if index + 1 < len(argv):
                paths.append(argv[index + 1])
            index += 2
            continue
        if token.startswith("-C") and len(token) > 2:
            paths.append(token[2:])
            index += 1
            continue
        if token.startswith("--work-tree=") or token.startswith("--git-dir="):
            paths.append(token.split("=", 1)[1])
            index += 1
            continue
        if token.startswith("-"):
            return tuple(paths + argv[index:])
        break
    if index == len(argv):
        return tuple(paths)
    subcommand, args = argv[index], argv[index + 1 :]
    if subcommand == "clone" and args:
        paths.append(args[-1])
    elif subcommand == "worktree" and args[:1] == ["add"]:
        index = 1
        while index < len(args):
            token = args[index]
            if token in {"-b", "-B", "--orphan", "--track", "--reason"}:
                index += 2
                continue
            if token.startswith("-"):
                return tuple(paths + args[index:])
            paths.append(token)
            break
    return tuple(paths)


def _registered_git_target(argv: list[str], registry: Registry) -> bool:
    return any(
        _registered_path(value, registry)
        for token in _git_targets(argv)
        for value in (token, token.partition("=")[2])
    )


def _matches_validation(argv: list[str], registry: Registry) -> bool:
    return any(
        tuple(argv[:2]) == repository.validation.install.argv[:2]
        or any(tuple(argv[: len(command.argv)]) == command.argv for command in repository.validation.commands)
        for repository in registry.repositories
    )


def _mentions_helper(argv: object) -> bool:
    if isinstance(argv, str):
        return "agent-stack-contribute" in argv
    return isinstance(argv, list) and any(
        isinstance(item, str) and "agent-stack-contribute" in item for item in argv
    )


def _raw_contribution_operation(command: str, registry: Registry) -> bool:
    if _mentions_helper(command):
        return True
    return any(
        _registered_path(token, registry)
        for token in re.findall(r"/(?:[^\t \r\n;&|<>()'\"]+)", command)
    )


def _vcs_argv(argv: list[str]) -> list[str] | None:
    """argv with git's pager flags dropped, or None when git gets other global options."""
    if not argv or posixpath.basename(argv[0]) != "git":
        return argv
    rest = argv[1:]
    while rest[:1] and rest[0] in _GIT_PAGER_OFF:
        rest = rest[1:]
    if rest[:1] and rest[0].startswith("-"):
        return None
    return ["git", *rest]


def _gh_read(argv: list[str]) -> bool:
    if argv[1:2] == ["api"]:
        return not gh_api_mutates(argv[2:])
    return tuple(argv[1:3]) in _GH_READS


def _public_write(argv: list[str]) -> bool:
    name = posixpath.basename(argv[0]) if argv else ""
    if name == "gh":
        return not _gh_read(argv)
    if name == "git":
        normalized = _vcs_argv(argv)
        return normalized is None or direct_public_write(normalized)
    return False


def _contains_direct_public_write(argv: list[str]) -> bool:
    command = shell_command({"command": argv})
    if command is None:
        return True
    try:
        statements = walk(command)
    except (OpaqueCommand, ValueError):
        return True
    for tokens in statements:
        resolved = resolve(tokens)
        if resolved.wrappers or resolved.stdin_exec or resolved.nested:
            return True
        if _public_write(resolved.argv):
            return True
    return False


def _git(cwd: str, *args: str) -> str | None:
    try:
        completed = subprocess.run(
            ["git", "--no-optional-locks", "-C", cwd, *args],
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT,
            env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
            stdin=subprocess.DEVNULL,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return completed.stdout if completed.returncode == 0 else None


def _fork_urls(repository: RegisteredRepository) -> frozenset[str]:
    return frozenset({f"https://github.com/{repository.fork}.git", f"https://github.com/{repository.fork}"})


def _remote_is_fork(cwd: str, remote: str, repository: RegisteredRepository) -> bool:
    if _git(cwd, "config", "--get-all", f"remote.{remote}.vcs") is not None:
        return False
    for key in (f"remote.{remote}.mirror", "push.followTags"):
        if _git(cwd, "config", "--type=bool", "--get", key) != "false\n" and _git(cwd, "config", "--get", key) is not None:
            return False
    urls = _git(cwd, "remote", "get-url", "--push", "--all", remote)
    if urls is None:
        return False
    lines = urls.splitlines()
    return bool(lines) and all(line in _fork_urls(repository) for line in lines)


def _pushed_paths(cwd: str) -> list[str] | None:
    if not _git(cwd, "for-each-ref", "--count=1", "refs/remotes/"):
        return None
    output = _git(cwd, "log", "-z", "--format=", "--name-only", "--no-renames", "-m", "HEAD", "--not", "--remotes")
    if output is None:
        return None
    return [path.strip("\n") for path in output.split("\0") if path.strip("\n")]


def _fork_push(argv: list[str], cwd: str, repository: RegisteredRepository) -> Decision:
    args = argv[2:]
    if args[:1] in (["-u"], ["--set-upstream"]):
        args = args[1:]
    if len(args) != 2 or not _REMOTE_NAME.fullmatch(args[0]) or not args[1].startswith("HEAD:"):
        return deny(FORK_PUSH_FORM)
    remote, destination = args[0], args[1].removeprefix("HEAD:")
    branch = destination.removeprefix("refs/heads/")
    if (
        not _BRANCH_NAME.fullmatch(branch)
        or ".." in branch
        or push_branch(destination) != branch
        or branch in DEFAULT_PROTECTED
        or branch == repository.default_branch
    ):
        return deny(f"{FORK_PUSH_FORM} (got {args[1]})")
    if not _remote_is_fork(cwd, remote, repository):
        return deny(f"git remote {remote} does not push only to the registered fork {repository.fork}; {FORK_PUSH_FORM}")
    paths = _pushed_paths(cwd)
    if paths is None:
        return deny("cannot list the commits this push sends; fetch the fork and upstream first")
    if any(path.startswith(_WORKFLOWS) for path in paths):
        return deny("a push that changes .github/workflows/ is not allowed")
    return allow()


def _gh_options(command: tuple[str, str], args: list[str]) -> dict[str, list[str]] | None:
    """Values per long flag name, or None for an unknown flag, a fused short flag or a positional other than one number."""
    flags = _GH_FORK_WRITES[command]
    values: dict[str, list[str]] = {"positional": []}
    index = 0
    while index < len(args):
        token = args[index]
        if not token.startswith("-"):
            values["positional"].append(token)
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
    positional = values.pop("positional")
    if len(positional) != (1 if command[1] == "edit" else 0) or not all(p.isdigit() for p in positional):
        return None
    return values


def _fork_gh(argv: list[str], repository: RegisteredRepository) -> Decision | None:
    command = tuple(argv[1:3])
    if command not in _GH_FORK_WRITES:
        return None if _gh_read(argv) else deny(FORK_GH_FORM)
    values = _gh_options(command, argv[3:])
    if values is None:
        return deny(f"{FORK_GH_FORM}; use only documented flags with separate values, --body instead of a file, and a PR or issue number")
    if values.get("--repo") != [repository.fork]:
        return deny(f"{FORK_GH_FORM}; name the fork exactly once with --repo {repository.fork}")
    labels = [label.strip() for name in ("--label", "--add-label") for value in values.get(name, []) for label in value.split(",")]
    if _OWNER_LABEL in labels:
        return deny(f"only the owner applies the {_OWNER_LABEL} label")
    if command == ("pr", "create") and len(values.get("--head", [])) != 1:
        return deny(f"{FORK_GH_FORM}; gh pr create needs exactly one --head <branch>")
    return allow()


def _exact_helper(argv: object, registry: Registry) -> bool:
    if not isinstance(argv, list) or any(not isinstance(item, str) for item in argv):
        return False
    if len(argv) == 3 and argv[0] in HELPER_NAMES and argv[1] in {"prepare", "preflight"}:
        try:
            registry.repository(argv[2])
        except RegistryError:
            return False
        return True
    if len(argv) == 4 and argv[0] in HELPER_NAMES and argv[1] == "validate":
        try:
            registry.repository(argv[2]).validation_profile(argv[3])
        except RegistryError:
            return False
        return True
    return False


def _prepare_operation(event: Event) -> Decision | None:
    arguments = tool_call(event, SHELL_TOOLS)
    if arguments is None:
        return None
    if arguments.get("_unparseable"):
        return deny("unparseable tool arguments")
    try:
        registry = Registry.load(RUNTIME_REGISTRY)
    except RegistryError:
        return deny("public contribution registry is unavailable")
    argv = arguments.get("command")
    if isinstance(argv, str):
        if _raw_contribution_operation(argv, registry) or _registered_tree(arguments, registry):
            return deny("public contribution operations require trusted structured argv")
        return None
    if _exact_helper(argv, registry):
        return allow()
    if _mentions_helper(argv):
        return deny("public contribution operations require the fixed helper path and exact argv")
    if not isinstance(argv, list) or any(not isinstance(item, str) for item in argv):
        if _registered_tree(arguments, registry):
            return deny("registered contribution operations require exact helper argv")
        return None
    repository = _registered_repository(_cwd(arguments), registry)
    registered_tree = repository is not None
    registered_git_target = _registered_git_target(argv, registry)
    if repository is not None and argv[:2] == ["git", "push"]:
        return _fork_push(argv, str(Path(_cwd(arguments)).resolve(strict=False)), repository)
    if repository is not None and argv[:1] == ["gh"]:
        return _fork_gh(argv, repository)
    if registered_tree and _contains_direct_public_write(argv):
        return deny("direct public publishing in a registered contribution tree is not allowed; stage it in the owner's fork instead")
    if registered_git_target:
        return deny("direct Git operation targeting a registered contribution tree is not allowed; request preparation instead")
    if registered_tree and argv[:1] == ["git"] and any(item in _CONTROLLER_GIT for item in argv[1:]):
        return deny("direct Git controller operation in a registered contribution tree is not allowed; request preparation instead")
    if registered_tree:
        if _matches_validation(argv, registry):
            return deny("direct validation in a registered contribution tree is not allowed; request validation instead")
        return None
    return None


def prepare_operation() -> Callable[[Event], Decision | None]:
    return _prepare_operation


def _contribution(repository: str, entry: Any) -> ContributionRequirements:
    entry = _mapping(
        entry,
        f"repository {repository}.contribution",
        {"issue_before_bugfix_pr", "dco", "demo_for_ui", "no_ai_attribution"},
    )
    if not isinstance(entry["issue_before_bugfix_pr"], bool) or not isinstance(entry["no_ai_attribution"], bool):
        raise RegistryError(f"repository {repository}.contribution booleans are invalid")
    dco = _string(entry["dco"], f"repository {repository}.contribution.dco")
    demo = _string(entry["demo_for_ui"], f"repository {repository}.contribution.demo_for_ui")
    if dco != "required" or demo != "required" or not entry["issue_before_bugfix_pr"] or not entry["no_ai_attribution"]:
        raise RegistryError(f"repository {repository}.contribution requirements are invalid")
    return ContributionRequirements(entry["issue_before_bugfix_pr"], dco, demo, entry["no_ai_attribution"])


def _unique_checkouts(repositories: tuple[RegisteredRepository, ...]) -> None:
    checkouts = [repository.checkout for repository in repositories]
    if len(checkouts) != len(set(checkouts)):
        raise RegistryError("duplicate checkout names")
