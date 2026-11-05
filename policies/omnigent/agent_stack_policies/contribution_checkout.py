"""Create the registered contribution checkout: the upstream default branch as `origin`, the owner's fork as `fork`."""

import os
import shutil
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

from .contribution_vcs import git as checkout_git

if TYPE_CHECKING:
    from .public_contributions import RegisteredRepository

CLONE_TIMEOUT = 600


class CheckoutError(RuntimeError):
    pass


def _clone_environment() -> dict[str, str]:
    return {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": os.environ.get("HOME", "/"),
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_TERMINAL_PROMPT": "0",
        "LC_ALL": "C",
    }


def _clone(url: str, branch: str, target: Path, protocol: str) -> None:
    overrides = ["-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false", "-c", "protocol.allow=never", "-c", f"protocol.{protocol}.allow=always"]
    try:
        completed = subprocess.run(
            ["git", *overrides, "clone", "--quiet", "--no-recurse-submodules", "--origin", "origin", "--branch", branch, "--", url, str(target)],
            capture_output=True,
            text=True,
            timeout=CLONE_TIMEOUT,
            env=_clone_environment(),
            stdin=subprocess.DEVNULL,
        )
    except (OSError, subprocess.SubprocessError) as error:
        shutil.rmtree(target, ignore_errors=True)
        raise CheckoutError(f"clone of the upstream failed: {type(error).__name__}") from None
    if completed.returncode != 0:
        shutil.rmtree(target, ignore_errors=True)
        raise CheckoutError("clone of the upstream failed")


def _urls(tree: Path, remote: str, push: bool) -> list[str] | None:
    output = checkout_git(str(tree), "remote", "get-url", *(["--push"] if push else []), "--all", remote)
    return None if output is None else output.splitlines()


def _check_remotes(tree: Path, upstream_url: str, fork_url: str) -> None:
    if checkout_git(str(tree), "rev-parse", "--is-inside-work-tree") != "true\n" or not (tree / ".git").is_dir():
        raise CheckoutError(f"{tree} exists but is not a git checkout; remove it and prepare again")
    if _urls(tree, "origin", False) != [upstream_url] or _urls(tree, "origin", True) != [upstream_url]:
        raise CheckoutError(f"{tree} has other remotes than the registry: origin must be {upstream_url}")
    fork = (_urls(tree, "fork", False), _urls(tree, "fork", True))
    if fork == (None, None):
        if checkout_git(str(tree), "remote", "add", "fork", fork_url) is None:
            raise CheckoutError(f"cannot add the fork remote to {tree}")
    elif fork != ([fork_url], [fork_url]):
        raise CheckoutError(f"{tree} has other remotes than the registry: fork must be {fork_url}")


def prepare_checkout(
    repository: "RegisteredRepository",
    workspace: Path,
    upstream_url: str | None = None,
    protocol: str = "https",
) -> dict:
    tree = workspace / repository.checkout
    upstream_url = upstream_url or repository.upstream
    fork_url = f"https://github.com/{repository.fork}.git"
    state = "existing"
    if not tree.exists():
        workspace.mkdir(parents=True, exist_ok=True)
        _clone(upstream_url, repository.default_branch, tree, protocol)
        state = "cloned"
    _check_remotes(tree, upstream_url, fork_url)
    return {"checkout": str(tree), "branch": repository.default_branch, "remotes": {"origin": upstream_url, "fork": fork_url}, "state": state}
