import hashlib
import json
import os
import posixpath
import re
from collections.abc import Callable
from dataclasses import dataclass
from fnmatch import fnmatchcase
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import yaml

from ._event import Decision, Event, allow, deny, tool_call
from ._shell import OpaqueCommand, resolve, shell_command, walk
from .contribution_vcs import fork_gh, gh_exposes_token, into_git_dir, registered_git
from .paths import SHELL_TOOLS
from .tools import direct_public_write, gh_api_mutates


RUNTIME_REGISTRY = Path("/opt/agent-stack/public-contributions.yaml")
HELPER_PATH = "/opt/agent/bin/agent-stack-contribute"
HELPER_NAMES = frozenset({HELPER_PATH, "agent-stack-contribute"})
CONTRIBUTION_WORKSPACE = Path("/home/omnigent/work")
FORK_OWNER = "lkshrk"
CI_PROVIDERS = frozenset({"github-hosted"})
TITLE_FORMATS = frozenset({"conventional-scoped"})
_VCS_NAMES = frozenset({"git", "gh"})
_UNQUOTED_LITERAL = re.compile(r"[A-Za-z0-9_./:=@,+%^-]")
_QUOTED_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")
_GLOB = re.compile(r"[*?\[]")
_GH_SLUG = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")
_WORD = re.compile(r"[^\s;&|<>()`'\"]+")


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
    title_format: str


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


def _roots(registry: Registry) -> list[tuple[Path, RegisteredRepository]]:
    return [(CONTRIBUTION_WORKSPACE / repository.checkout, repository) for repository in registry.repositories]


def _registered_repository(value: object, registry: Registry, cwd: str | None = None) -> RegisteredRepository | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        path = (Path(cwd) / value if cwd and not os.path.isabs(value) else Path(value)).resolve(strict=False)
    except (OSError, ValueError):
        return None
    for root, repository in _roots(registry):
        if path == root or root in path.parents:
            return repository
    return None


def _registered_path(value: object, registry: Registry, cwd: str | None = None) -> bool:
    return _registered_repository(value, registry, cwd) is not None


def _glob_reaches_root(pattern: str, registry: Registry) -> bool:
    parts = Path(posixpath.normpath(pattern)).parts
    for root, _ in _roots(registry):
        root_parts = root.parts
        if len(parts) >= len(root_parts) and all(fnmatchcase(r, p) for r, p in zip(root_parts, parts)):
            return True
    return False


def _homes() -> list[str]:
    homes = {str(CONTRIBUTION_WORKSPACE.parent), str(Path.home())}
    if os.environ.get("HOME"):
        homes.add(os.environ["HOME"])
    return sorted(homes)


def _expansions(word: str, bases: list[str]) -> list[str]:
    candidates = [word]
    if word.startswith("-") and not word.startswith("--") and len(word) > 2:
        candidates.append(word[2:])
    candidates += [part for part in (word.partition("=")[2],) if part]
    expanded: list[str] = []
    for candidate in candidates:
        for prefix in ("${HOME}", "$HOME", "~"):
            if candidate == prefix or candidate.startswith(prefix + "/"):
                expanded += [home + candidate[len(prefix):] for home in _homes()]
                break
        else:
            if candidate.startswith("~"):
                expanded.append(os.path.expanduser(candidate))
            else:
                expanded.append(candidate)
    return [path if os.path.isabs(path) else os.path.join(base, path) for path in expanded for base in (bases if not os.path.isabs(path) else [""])]


def _refers_to_root(path: str, registry: Registry) -> bool:
    return _glob_reaches_root(path, registry) if _GLOB.search(path) else _registered_path(path, registry)


def _may_name_checkout(text: str, registry: Registry) -> bool:
    if any(posixpath.basename(repository.checkout) in text for repository in registry.repositories):
        return True
    return bool(_GLOB.search(text)) and bool(re.search(r"/|(^|[\s;&|(])(cd|pushd)\b", text))


def _references_registered_root(text: str, cwd: str | None, registry: Registry) -> bool:
    """Whether any word, after lexical ~/$HOME expansion and a walk through cd/pushd, can name a registered checkout."""
    if not _may_name_checkout(text, registry):
        return False
    try:
        statements = walk(text)
    except (OpaqueCommand, ValueError):
        statements = [_WORD.findall(text)]
    bases = [cwd] if cwd else []
    for tokens in statements:
        resolved = resolve(tokens)
        for assignment in resolved.assignments:
            if assignment.startswith("CDPATH="):
                bases += [path for entry in assignment.removeprefix("CDPATH=").split(":") for path in _expansions(entry or ".", bases or ["/"])]
        for token in tokens:
            if any(_refers_to_root(path, registry) for path in _expansions(token, bases or ["/"])):
                return True
        if resolved.name in ("cd", "pushd"):
            target = resolved.argv[-1] if len(resolved.argv) > 1 else "~"
            bases = _expansions(target, bases or ["/"]) + bases
    return False


def _cwd(arguments: dict) -> str | None:
    value = arguments.get("cwd", arguments.get("workdir", arguments.get("working_directory")))
    return value if isinstance(value, str) and value else None


def _literal_words(command: str) -> list[str] | None:
    """Words of a command built only from plain, single-quoted and expansion-free double-quoted text, or None."""
    words: list[str] = []
    word: str | None = None
    index = 0
    while index < len(command):
        char = command[index]
        if char in " \t":
            if word is not None:
                words.append(word)
                word = None
            index += 1
            continue
        if char in "'\"":
            end = command.find(char, index + 1)
            if end < 0:
                return None
            body = command[index + 1 : end]
            if _QUOTED_CONTROL.search(body) or (char == '"' and any(c in body for c in "$`\\")):
                return None
            word = (word or "") + body
            index = end + 1
            continue
        if not _UNQUOTED_LITERAL.fullmatch(char):
            return None
        word = (word or "") + char
        index += 1
    if word is not None:
        words.append(word)
    return words


def _simple_command(command: str) -> list[str] | None:
    words = _literal_words(command)
    if not words or (words[0] not in _VCS_NAMES and words[0] not in HELPER_NAMES):
        return None
    try:
        statements = walk(command)
    except (OpaqueCommand, ValueError):
        return None
    if statements != [words]:
        return None
    resolved = resolve(words)
    if resolved.argv != words or resolved.wrappers or resolved.assignments or resolved.nested or resolved.stdin_exec:
        return None
    return words


def _git_targets(argv: list[str]) -> tuple[str, ...]:
    if argv[:1] != ["git"]:
        return ()
    paths: list[str] = []
    index = 1
    while index < len(argv):
        token = argv[index]
        if token in {"--no-pager", "-P"}:
            index += 1
            continue
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


def _registered_git_target(argv: list[str], registry: Registry, cwd: str | None) -> bool:
    return any(
        _registered_path(value, registry, cwd)
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


def _exposes_gh_token(text: str) -> bool:
    if "gh" not in text or "auth" not in text:
        return False
    try:
        statements = walk(text)
    except (OpaqueCommand, ValueError):
        return bool(re.search(r"token|(^|\s)-[a-z]*t", text))
    return any(resolve(tokens).name == "gh" and gh_exposes_token(["gh", *resolve(tokens).argv[1:]]) for tokens in statements)


def _vcs_argv(argv: list[str]) -> list[str] | None:
    """argv with git's pager flags dropped, or None when git gets other global options."""
    if not argv or posixpath.basename(argv[0]) != "git":
        return argv
    rest = argv[1:]
    while rest[:1] and rest[0] in ("--no-pager", "-P"):
        rest = rest[1:]
    if rest[:1] and rest[0].startswith("-"):
        return None
    return ["git", *rest]


def _public_write(argv: list[str]) -> bool:
    name = posixpath.basename(argv[0]) if argv else ""
    if name == "gh":
        return argv[1:2] != ["api"] or gh_api_mutates(argv[2:]) or direct_public_write(argv)
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


def _indirect_vcs(argv: list[str]) -> bool:
    command = shell_command({"command": argv})
    if command is None:
        return True
    try:
        statements = walk(command)
    except (OpaqueCommand, ValueError):
        return True
    return any(resolve(tokens).name in ("git", "gh") for tokens in statements)


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
    cwd = _cwd(arguments)
    joined = shell_command({"command": argv}) if isinstance(argv, list) else None
    texts = [argv] if isinstance(argv, str) else [item for item in argv if isinstance(item, str)] if isinstance(argv, list) else []
    texts += [joined] if joined else []
    if any(_exposes_gh_token(text) for text in texts):
        return deny("gh commands that print the authentication token are not allowed")
    repository = _registered_repository(cwd, registry)
    if isinstance(argv, str):
        simple = _simple_command(argv)
        if simple is None:
            if repository is not None or _mentions_helper(argv) or _references_registered_root(argv, cwd, registry):
                return deny("public contribution operations need structured argv or one literal git, gh or helper command")
            return None
        argv = simple
    if _exact_helper(argv, registry):
        return allow()
    if _mentions_helper(argv):
        return deny("public contribution operations require the fixed helper path and exact argv")
    if not isinstance(argv, list) or any(not isinstance(item, str) for item in argv):
        if repository is not None:
            return deny("registered contribution operations require exact helper argv")
        return None
    if _registered_git_target(argv, registry, cwd):
        return deny("direct Git operation targeting a registered contribution tree from elsewhere is not allowed")
    if repository is None or cwd is None:
        if any(_references_registered_root(item, cwd, registry) for item in argv):
            return deny("a command that names a registered contribution checkout must run from inside it")
        return None
    checkout = str(Path(cwd).resolve(strict=False))
    if argv[:1] == ["git"]:
        return registered_git(argv, checkout, repository)
    if argv[:1] == ["gh"]:
        return fork_gh(argv, repository)
    if any(into_git_dir(item) for item in argv[1:]):
        return deny("paths inside .git/ are not allowed in a registered contribution")
    if _indirect_vcs(argv) or _contains_direct_public_write(argv):
        return deny("in a registered contribution run git and gh directly by bare name, with no wrapper or environment assignment")
    if _matches_validation(argv, registry):
        return deny("direct validation in a registered contribution tree is not allowed; request validation instead")
    return None


def prepare_operation() -> Callable[[Event], Decision | None]:
    return _prepare_operation


def _contribution(repository: str, entry: Any) -> ContributionRequirements:
    entry = _mapping(
        entry,
        f"repository {repository}.contribution",
        {"issue_before_bugfix_pr", "dco", "demo_for_ui", "no_ai_attribution", "title_format"},
    )
    title_format = entry["title_format"]
    if not isinstance(title_format, str) or title_format not in TITLE_FORMATS:
        raise RegistryError(f"repository {repository}.contribution.title_format must be one of {sorted(TITLE_FORMATS)}")
    if not isinstance(entry["issue_before_bugfix_pr"], bool) or not isinstance(entry["no_ai_attribution"], bool):
        raise RegistryError(f"repository {repository}.contribution booleans are invalid")
    dco = _string(entry["dco"], f"repository {repository}.contribution.dco")
    demo = _string(entry["demo_for_ui"], f"repository {repository}.contribution.demo_for_ui")
    if dco != "required" or demo != "required" or not entry["issue_before_bugfix_pr"] or not entry["no_ai_attribution"]:
        raise RegistryError(f"repository {repository}.contribution requirements are invalid")
    return ContributionRequirements(entry["issue_before_bugfix_pr"], dco, demo, entry["no_ai_attribution"], title_format)


def _unique_checkouts(repositories: tuple[RegisteredRepository, ...]) -> None:
    checkouts = [repository.checkout for repository in repositories]
    if len(checkouts) != len(set(checkouts)):
        raise RegistryError("duplicate checkout names")
