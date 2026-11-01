import hashlib
import json
import posixpath
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import yaml


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
        if data["version"] != 1:
            raise RegistryError("registry version must be 1")
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
    if value != normalized or normalized in {".", ".."} or normalized.startswith("../") or normalized.startswith("/"):
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
    if not isinstance(registries, list) or not registries or any(not isinstance(url, str) or urlparse(url).scheme != "https" for url in registries):
        raise RegistryError(f"repository {repository}.validation.package_registries must contain HTTPS URLs")
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
    if any(re.search(r"[;&|`$<>\n\r]", item) for item in value):
        raise RegistryError(f"{context} contains shell syntax")
    return tuple(value)


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
