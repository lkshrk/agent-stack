import os
import sys
import tempfile
from pathlib import Path

from omnigent.cli import _validate_agent_policy_functions
from omnigent.db.utils import builtin_agent_id
from omnigent.server.app import _ensure_default_agents, _tar_gz_dir
from omnigent.server.managed_hosts import resolve_managed_agent_label
from omnigent.spec import load, materialize_bundle
from omnigent.stores.agent_store.sqlalchemy_store import SqlAlchemyAgentStore
from omnigent.stores.artifact_store.local import LocalArtifactStore

AGENTS_DIR = Path("/agents")
HARNESSES = ("claude-native-ui", "codex-native-ui")


class _Cache:
    def replace(self, *args: object, **kwargs: object) -> None:
        pass

    def evict(self, *args: object, **kwargs: object) -> None:
        pass


def _bundle(source: Path) -> bytes:
    with tempfile.TemporaryDirectory() as tmp:
        bundle_dir = materialize_bundle(source, Path(tmp) / "bundle")
        _validate_agent_policy_functions(load(bundle_dir), source)
        return _tar_gz_dir(bundle_dir)


def main() -> int:
    fail = 0

    def check(name: str, ok: bool, detail: str = "") -> None:
        nonlocal fail
        print(f"{'ok  ' if ok else 'FAIL'} {name}{': ' + detail if detail and not ok else ''}")
        fail |= not ok

    entries = [Path(p) for p in os.environ["OMNIGENT_BUILTIN_AGENT_DIRS"].split(os.pathsep) if p]
    bundles = sorted(p.name for p in AGENTS_DIR.iterdir() if (p / "config.yaml").is_file())
    check("every bundle in /agents is registered", sorted(p.name for p in entries) == bundles, f"{entries} vs {bundles}")
    expected = {p.name: _bundle(p) for p in entries}
    check("every bundle's policy functions resolve", True)

    with tempfile.TemporaryDirectory() as tmp:
        agents = SqlAlchemyAgentStore(f"sqlite:///{tmp}/omnigent.db")
        artifacts = LocalArtifactStore(f"{tmp}/artifacts")
        for boot in ("first", "second"):
            hidden = _ensure_default_agents(agents, artifacts, _Cache())
            for name, bundle in expected.items():
                agent = agents.get_by_name(name)
                label = resolve_managed_agent_label(agents, agent.id, session_id="test") if agent else None
                check(f"{name} has the built-in id ({boot} boot)", agent is not None and agent.id == builtin_agent_id(name))
                check(f"{name} runs our bundle ({boot} boot)", agent is not None and artifacts.get(agent.bundle_location) == bundle)
                check(f"{name} pods get its agent label ({boot} boot)", label == name, repr(label))
                check(f"{name} stays in the picker ({boot} boot)", name not in hidden)
            for name in HARNESSES:
                check(f"{name} stays in the picker ({boot} boot)", agents.get_by_name(name) is not None and name not in hidden)
            check(f"other packaged agents are hidden ({boot} boot)", "debby" in hidden and "cursor-native-ui" in hidden, repr(sorted(hidden)))
    return fail


if __name__ == "__main__":
    sys.exit(main())
