from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

AUTHOR = "agent-stack/omnigent-runner"


@dataclass(frozen=True)
class CardView:
    id: str
    status: str
    body: str
    run_id: int | None
    comments: tuple[str, ...]


class KanbanPort(Protocol):
    def card(self, task_id: str) -> CardView: ...

    def note(self, task_id: str, body: str) -> None: ...

    def heartbeat(self, task_id: str, run_id: int, note: str) -> bool: ...

    def complete(self, task_id: str, run_id: int, summary: str, metadata: dict) -> bool: ...

    def block(self, task_id: str, run_id: int, kind: str, reason: str) -> bool: ...


class HermesKanban:
    def __init__(self, claimer: str | None = None, db_path: Path | None = None, author: str = AUTHOR):
        self._claimer = claimer
        self._db_path = Path(db_path) if db_path else None
        self._author = author

    def _connect(self):
        from hermes_cli.kanban_db_connect import connect_closing

        return connect_closing(self._db_path)

    def card(self, task_id: str) -> CardView:
        from hermes_cli import kanban_db as kb

        with self._connect() as conn:
            task = kb.get_task(conn, task_id)
            if task is None:
                raise LookupError(f"unknown card {task_id}")
            comments = sorted(kb.list_comments(conn, task_id), key=lambda c: c.id)
        own = tuple(c.body for c in comments if c.author == self._author)
        return CardView(task.id, task.status, task.body or "", task.current_run_id, own)

    def note(self, task_id: str, body: str) -> None:
        from hermes_cli import kanban_db as kb

        with self._connect() as conn:
            kb.add_comment(conn, task_id, self._author, body)

    def heartbeat(self, task_id: str, run_id: int, note: str) -> bool:
        from hermes_cli import kanban_db as kb
        from hermes_cli.kanban_db_dispatch import heartbeat_worker

        if not self._claimer:
            return False
        with self._connect() as conn:
            if not kb.heartbeat_claim(conn, task_id, claimer=self._claimer):
                return False
            return bool(heartbeat_worker(conn, task_id, note=note or None, expected_run_id=run_id))

    def complete(self, task_id: str, run_id: int, summary: str, metadata: dict) -> bool:
        from hermes_cli import kanban_db as kb

        with self._connect() as conn:
            return bool(
                kb.complete_task(
                    conn, task_id, result=summary, summary=summary, metadata=metadata, expected_run_id=run_id
                )
            )

    def block(self, task_id: str, run_id: int, kind: str, reason: str) -> bool:
        from hermes_cli import kanban_db as kb

        with self._connect() as conn:
            return bool(kb.block_task(conn, task_id, reason=reason, kind=kind, expected_run_id=run_id))
