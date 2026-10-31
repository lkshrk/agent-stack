import sys
import tempfile
from pathlib import Path

from omnigent.cli import _preregister_agent
from omnigent.db.utils import builtin_agent_id
from omnigent.server.managed_hosts import resolve_managed_agent_label
from omnigent.stores.agent_store.sqlalchemy_store import SqlAlchemyAgentStore
from omnigent.stores.artifact_store.local import LocalArtifactStore


class _Cache:
    def replace(self, *args: object, **kwargs: object) -> None:
        pass

    def evict(self, *args: object, **kwargs: object) -> None:
        pass


def main(bundles: list[str]) -> int:
    fail = 0
    with tempfile.TemporaryDirectory() as tmp:
        agents = SqlAlchemyAgentStore(f"sqlite:///{tmp}/omnigent.db")
        artifacts = LocalArtifactStore(f"{tmp}/artifacts")
        for bundle in bundles:
            name = Path(bundle).name
            for run in ("first", "repeat"):
                agent_id = _preregister_agent(Path(bundle), agents, artifacts, _Cache())
                label = resolve_managed_agent_label(agents, agent_id, session_id="test")
                ok = agent_id == builtin_agent_id(name) and label == name
                print(f"{'ok  ' if ok else 'FAIL'} {name} ({run} registration): id {agent_id}, label {label!r}")
                fail |= not ok
    return fail


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
