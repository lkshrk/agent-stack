import gzip
import io
import os
import sys
import tarfile
import tempfile

from omnigent.db.utils import builtin_agent_id
from omnigent.server.app import _ensure_default_agents
from omnigent.server.managed_hosts import resolve_managed_agent_label
from omnigent.stores.agent_store.sqlalchemy_store import SqlAlchemyAgentStore
from omnigent.stores.artifact_store.local import LocalArtifactStore

MARKER = b"agent_stack_policies"


class _Cache:
    def replace(self, *args: object, **kwargs: object) -> None:
        pass

    def evict(self, *args: object, **kwargs: object) -> None:
        pass


def _config(bundle: bytes) -> bytes:
    with tarfile.open(fileobj=io.BytesIO(gzip.decompress(bundle))) as tar:
        member = next(m for m in tar.getmembers() if m.name.lstrip("./") == "config.yaml")
        return tar.extractfile(member).read()


def main() -> int:
    names = [os.path.basename(p) for p in os.environ["OMNIGENT_BUILTIN_AGENT_DIRS"].split(os.pathsep) if p]
    fail = 0
    with tempfile.TemporaryDirectory() as tmp:
        agents = SqlAlchemyAgentStore(f"sqlite:///{tmp}/omnigent.db")
        artifacts = LocalArtifactStore(f"{tmp}/artifacts")
        for boot in ("first", "second"):
            suppressed = _ensure_default_agents(agents, artifacts, _Cache()) or frozenset()
            for name in names:
                agent = agents.get_by_name(name)
                label = resolve_managed_agent_label(agents, agent.id, session_id="test") if agent else None
                ours = agent is not None and MARKER in _config(artifacts.get(agent.bundle_location))
                ok = agent is not None and agent.id == builtin_agent_id(name) and label == name and ours and name not in suppressed
                print(f"{'ok  ' if ok else 'FAIL'} {name} ({boot} boot): label {label!r}, our bundle {ours}, hidden {name in suppressed}")
                fail |= not ok
            print(f"     hidden packaged agents: {sorted(suppressed)}")
    return fail


if __name__ == "__main__":
    sys.exit(main())
