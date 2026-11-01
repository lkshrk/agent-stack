import contextlib
import os
import subprocess
import tempfile
from pathlib import Path
from unittest.mock import patch

from agent_stack_policies import public_contributions

REGISTRY = Path(__file__).resolve().parents[3] / "config" / "public-contributions.yaml"
UPSTREAM_URL = "https://github.com/omnigent-ai/omnigent.git"
FORK = "lkshrk/omnigent"
FORK_URL = f"https://github.com/{FORK}.git"
GIT_ENV = {
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_AUTHOR_NAME": "Test",
    "GIT_AUTHOR_EMAIL": "test@example.invalid",
    "GIT_COMMITTER_NAME": "Test",
    "GIT_COMMITTER_EMAIL": "test@example.invalid",
}


def _environment() -> dict[str, str]:
    """os.environ without GIT_* variables, which a git hook running these tests sets to the outer repository."""
    return {**{key: value for key, value in os.environ.items() if not key.startswith("GIT_")}, **GIT_ENV}


def git(tree: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(tree), *args], check=True, capture_output=True, text=True, env=_environment()
    ).stdout


def write(tree: Path, name: str, text: str) -> None:
    path = tree / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def commit(tree: Path, name: str, text: str, message: str) -> None:
    write(tree, name, text)
    git(tree, "add", "--", name)
    git(tree, "commit", "-q", "-m", message)


@contextlib.contextmanager
def fork_checkout():
    """A registered omnigent checkout with `origin` = upstream, `fork` = the owner's fork and one unpushed commit."""
    with tempfile.TemporaryDirectory() as directory:
        workspace = Path(directory).resolve()
        tree = workspace / "omnigent"
        tree.mkdir()
        git(tree, "init", "-q", "-b", "main")
        commit(tree, ".github/workflows/ci.yml", "on: push\n", "chore: initial")
        git(tree, "remote", "add", "origin", UPSTREAM_URL)
        git(tree, "remote", "add", "fork", FORK_URL)
        git(tree, "update-ref", "refs/remotes/origin/main", "HEAD")
        git(tree, "update-ref", "refs/remotes/fork/main", "HEAD")
        commit(tree, "web/a.ts", "export const a = 1\n", "fix(web): change a")
        with contextlib.ExitStack() as stack:
            stack.enter_context(patch.object(public_contributions, "RUNTIME_REGISTRY", REGISTRY))
            stack.enter_context(patch.object(public_contributions, "CONTRIBUTION_WORKSPACE", workspace))
            stack.enter_context(patch.dict(os.environ, _environment(), clear=True))
            yield tree
