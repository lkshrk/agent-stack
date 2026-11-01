import hashlib
import json
import posixpath
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import yaml

from ._event import Decision, Event, allow, deny, tool_call
from ._shell import OpaqueCommand, resolve, shell_command, walk
from .paths import SHELL_TOOLS
from .tools import direct_public_write


RUNTIME_REGISTRY = Path("/opt/agent-stack/public-contributions.yaml")
HELPER_PATH = "/opt/agent/bin/agent-stack-contribute"
HELPER_NAMES = frozenset({HELPER_PATH, "agent-stack-contribute"})
CONTRIBUTION_WORKSPACE = Path("/home/omnigent/work")
_CONTROLLER_GIT = frozenset({"clone", "fetch", "worktree", "branch", "add", "commit", "remote"})


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
    checkout = _relative_path(entry["checkout"], f"repository {name}.checkout")
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


def _registered_path(value: object, registry: Registry) -> bool:
    if not isinstance(value, str) or not value:
        return False
    try:
        path = Path(value).resolve(strict=False)
    except OSError:
        return False
    return any(path == root or root in path.parents for root in (CONTRIBUTION_WORKSPACE / repository.checkout for repository in registry.repositories))


def _registered_tree(arguments: dict, registry: Registry) -> bool:
    return _registered_path(arguments.get("cwd", arguments.get("workdir", arguments.get("working_directory"))), registry)


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
        if direct_public_write(resolved.argv):
            return True
    return False


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
    registered_tree = _registered_tree(arguments, registry)
    registered_git_target = _registered_git_target(argv, registry)
    if registered_tree and _contains_direct_public_write(argv):
        return deny("direct public publishing in a registered contribution tree is not allowed; request the trusted gateway instead")
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
