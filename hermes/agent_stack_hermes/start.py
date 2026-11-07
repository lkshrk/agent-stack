import sys
from pathlib import Path
from typing import Protocol

from agent_stack_hermes.runspec import REPOS, RunSpec, parse_card_body, render_card_body

ASSIGNEE = "omnigent-runner"
CREATOR = "default"
REF = "main"
DEFAULT_INPUT = {"infra": "Run the Renovate sweep on lkshrk/h-cloud."}
CLOSED = ("done", "archived")
EXIT_OPEN = 3
EXIT_USAGE = 2


class RunnerCards(Protocol):
    def start(self, bundle: str, title: str, body: str) -> tuple[bool, str, str]: ...


def bundle_of(body: str | None) -> str | None:
    try:
        spec = parse_card_body(body or "")[1]
    except ValueError:
        return None
    return spec.bundle if spec else None


def card_body(bundle: str, text: str | None) -> str:
    repo = REPOS.get(bundle)
    if repo is None:
        raise ValueError(f"bundle {bundle!r} has no pinned repository")
    request = (text or "").strip() or DEFAULT_INPUT.get(bundle) or f"Run {bundle} on {repo}."
    return render_card_body(None, RunSpec(bundle, repo, REF, request), "")


def start(cards: RunnerCards, bundle: str, text: str | None) -> dict:
    body = card_body(bundle, text)
    created, card_id, status = cards.start(bundle, f"{bundle}: {REPOS[bundle]}", body)
    return {"started": created, "card": card_id, "status": status}


class HermesRunnerCards:
    def __init__(self, db_path: Path | None = None):
        self._db_path = Path(db_path) if db_path else None

    def start(self, bundle: str, title: str, body: str) -> tuple[bool, str, str]:
        from hermes_cli import kanban_db as kb
        from hermes_cli.kanban_db_connect import connect_closing, write_txn

        with connect_closing(self._db_path) as conn:
            with write_txn(conn):
                rows = conn.execute(
                    "SELECT id, status, body FROM tasks WHERE assignee = ? AND status NOT IN (?, ?) ORDER BY created_at, id",
                    (ASSIGNEE, *CLOSED),
                ).fetchall()
                for row in rows:
                    if bundle_of(row[2]) == bundle:
                        return False, row[0], row[1]
                task_id = kb.create_task(conn, title=title, body=body, assignee=ASSIGNEE, created_by=CREATOR)
            return True, task_id, kb.get_task(conn, task_id).status


def main(argv: list[str], cards: RunnerCards | None = None) -> int:
    if not argv or argv[0] not in REPOS:
        print(f"usage: runner-start <{'|'.join(sorted(REPOS))}> [request text]", file=sys.stderr)
        return EXIT_USAGE
    bundle = argv[0]
    outcome = start(cards or HermesRunnerCards(), bundle, " ".join(argv[1:]))
    if outcome["started"]:
        print(f"started {bundle} card {outcome['card']} ({outcome['status']})")
        return 0
    print(f"not started: {bundle} card {outcome['card']} is still {outcome['status']}")
    return EXIT_OPEN


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
