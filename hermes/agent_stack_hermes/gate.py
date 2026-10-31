import hmac
import http.server
import json
import os
import re
import sys
import tempfile
import threading
import urllib.parse
from pathlib import Path
from typing import Protocol

from agent_stack_hermes.kanban_port import AUTHOR
from agent_stack_hermes.omnigent import MachineCredentials, OmnigentClient, TokenSource
from agent_stack_hermes.runner import SESSION_NOTE
from agent_stack_hermes.runspec import LinearLink, parse_card_body, render_card_body

ASSIGNEE = "delivery"
MAX_BODY_BYTES = 64 * 1024
MAX_EVENTS = 500
MAX_CURSOR = 2**63 - 1
SOCKET_TIMEOUT = 30
DEFAULT_EVENTS = 100
TERMINAL = ("done", "archived")
BLOCKED_STATUSES = ("blocked", "triage")
DEFAULT_LISTEN = "0.0.0.0:8091"
OMNIGENT_ENV = ("OMNIGENT_URL", "OMNIGENT_TOKEN_URL", "OMNIGENT_CLIENT_ID", "OMNIGENT_CLIENT_SECRET")

_CARD_ID = r"(?P<id>[A-Za-z0-9_-]+)"
_NAME = r"(?P<name>[A-Za-z0-9_.-]{1,64})"


class KanbanAdmin(Protocol):
    def create(self, *, title: str, body: str, idempotency_key: str) -> str: ...

    def get(self, task_id: str) -> dict | None: ...

    def find_by_issue(self, issue_id: str) -> dict | None: ...

    def tree(self, root_issue_id: str) -> list[dict]: ...

    def comments(self, task_id: str) -> list[str]: ...

    def archive(self, task_id: str) -> bool: ...

    def events_after(self, after: int, limit: int) -> list[dict]: ...


def _link_of(body: str) -> LinearLink | None:
    try:
        return parse_card_body(body or "")[0]
    except ValueError:
        return None


class HermesKanbanAdmin:
    def __init__(self, db_path: Path | None = None):
        self._db_path = Path(db_path) if db_path else None

    def _connect(self):
        from hermes_cli.kanban_db_connect import connect_closing

        return connect_closing(self._db_path)

    def create(self, *, title: str, body: str, idempotency_key: str) -> str:
        from hermes_cli import kanban_db as kb

        with self._connect() as conn:
            return kb.create_task(
                conn,
                title=title,
                body=body,
                assignee=ASSIGNEE,
                created_by=AUTHOR,
                idempotency_key=idempotency_key,
                initial_status="blocked",
            )

    def get(self, task_id: str) -> dict | None:
        from hermes_cli import kanban_db as kb

        with self._connect() as conn:
            task = kb.get_task(conn, task_id)
            return None if task is None else self._card(conn, kb, task)

    def find_by_issue(self, issue_id: str) -> dict | None:
        from hermes_cli import kanban_db as kb

        with self._connect() as conn:
            row = conn.execute(
                "SELECT id FROM tasks WHERE idempotency_key = ? AND status != 'archived' ORDER BY created_at DESC, id DESC LIMIT 1",
                (f"linear:{issue_id}",),
            ).fetchone()
            task = kb.get_task(conn, row[0]) if row else None
            return None if task is None else self._card(conn, kb, task)

    def tree(self, root_issue_id: str) -> list[dict]:
        from hermes_cli import kanban_db as kb

        with self._connect() as conn:
            rows = conn.execute(
                "SELECT id, body FROM tasks WHERE status NOT IN ('done', 'archived') AND body LIKE 'agent-stack-link: %' ORDER BY created_at, id"
            ).fetchall()
            ids = [r[0] for r in rows if (link := _link_of(r[1])) is not None and link.root_issue_id == root_issue_id]
            tasks = [kb.get_task(conn, task_id) for task_id in ids]
            return [self._card(conn, kb, t) for t in tasks if t is not None]

    def comments(self, task_id: str) -> list[str]:
        from hermes_cli import kanban_db as kb

        with self._connect() as conn:
            comments = sorted(kb.list_comments(conn, task_id), key=lambda c: c.id)
        return [c.body for c in comments if c.author == AUTHOR]

    def archive(self, task_id: str) -> bool:
        from hermes_cli import kanban_db as kb

        with self._connect() as conn:
            return bool(kb.archive_task(conn, task_id))

    def events_after(self, after: int, limit: int) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT id, task_id, kind, created_at FROM task_events WHERE id > ? ORDER BY id LIMIT ?",
                (after, limit),
            ).fetchall()
        return [{"id": r[0], "card_id": r[1], "kind": r[2], "created_at": r[3]} for r in rows]

    def _card(self, conn, kb, task) -> dict:
        run = kb.latest_run(conn, task.id)
        outcome = run.outcome if run else None
        reason = None
        if task.status in BLOCKED_STATUSES:
            newest = conn.execute(
                "SELECT kind FROM task_events WHERE task_id = ? AND kind IN ('blocked', 'block_loop_detected', 'unblocked', 'gave_up') ORDER BY id DESC LIMIT 1",
                (task.id,),
            ).fetchone()
            if newest and newest[0] == "gave_up":
                outcome = "gave_up"
            if run is not None and run.outcome == "blocked" and run.summary:
                reason = run.summary
            else:
                reason = self._blocked_reason(conn, task.id)
        done = conn.execute(
            "SELECT metadata FROM task_runs WHERE task_id = ? AND outcome = 'completed' ORDER BY id DESC LIMIT 1",
            (task.id,),
        ).fetchone()
        link = _link_of(task.body)
        return {
            "id": task.id,
            "status": task.status,
            "assignee": task.assignee,
            "run_id": task.current_run_id or (run.id if run else 0),
            "outcome": outcome,
            "block_kind": getattr(task, "block_kind", None),
            "block_reason": reason,
            "summary": (run.summary if run and run.summary else None) or task.result or None,
            "link": None if link is None else {
                "issue_id": link.issue_id,
                "identifier": link.identifier,
                "session_id": link.session_id,
                "root_issue_id": link.root_issue_id,
            },
            "metadata": _json_object(done[0]) if done else None,
        }

    def _blocked_reason(self, conn, task_id: str) -> str | None:
        row = conn.execute(
            "SELECT payload FROM task_events WHERE task_id = ? AND kind IN ('blocked', 'block_loop_detected') ORDER BY id DESC LIMIT 1",
            (task_id,),
        ).fetchone()
        payload = _json_object(row[0]) if row else None
        reason = payload.get("reason") if payload else None
        return reason if isinstance(reason, str) else None


def _json_object(raw) -> dict | None:
    if isinstance(raw, dict):
        return raw
    try:
        value = json.loads(raw) if raw else None
    except (TypeError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def _int_param(query: dict, name: str, default: int, low: int, high: int | None = None) -> int | None:
    raw = query.get(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError:
        return None
    return value if value >= low and (high is None or value <= high) else None


def _bad(message: str) -> tuple[int, dict]:
    return 400, {"error": message}


class Gate:
    def __init__(self, kanban: KanbanAdmin, omni: OmnigentClient | None, state_dir: str):
        self._kanban = kanban
        self._omni = omni
        self._state = Path(state_dir)
        self._cursor_lock = threading.Lock()
        self._create_lock = threading.Lock()
        self._routes = (
            (re.compile(r"/v1/cards"), {"POST": self._create, "GET": self._find}),
            (re.compile(rf"/v1/cards/{_CARD_ID}"), {"GET": self._get}),
            (re.compile(rf"/v1/cards/{_CARD_ID}/stop"), {"POST": self._stop}),
            (re.compile(r"/v1/events"), {"GET": self._events}),
            (re.compile(rf"/v1/cursors/{_NAME}"), {"GET": self._cursor_get, "PUT": self._cursor_put}),
        )

    def handle(self, method: str, path: str, query: dict, body: dict | None) -> tuple[int, dict]:
        for pattern, methods in self._routes:
            match = pattern.fullmatch(path)
            if match is None:
                continue
            handler = methods.get(method)
            if handler is None:
                return 405, {"error": "method not allowed"}
            return handler(match.groupdict(), query, body)
        return 404, {"error": "not found"}

    def _create(self, _args, _query, body):
        fields = body or {}
        for name in ("issue_id", "identifier", "title", "session_id"):
            if not isinstance(fields.get(name), str) or not fields[name]:
                return _bad(f"{name} is required")
        description = fields.get("description", "")
        if not isinstance(description, str):
            return _bad("description must be a string")
        link = LinearLink(fields["issue_id"], fields["identifier"], fields["session_id"], fields["issue_id"])
        with self._create_lock:
            existing = self._kanban.find_by_issue(fields["issue_id"])
            if existing is not None:
                return 200, existing
            task_id = self._kanban.create(
                title=fields["title"],
                body=render_card_body(link, None, description),
                idempotency_key=f"linear:{fields['issue_id']}",
            )
        return self._card(task_id)

    def _card(self, task_id: str):
        card = self._kanban.get(task_id)
        return (200, card) if card else (404, {"error": "unknown card"})

    def _get(self, args, _query, _body):
        return self._card(args["id"])

    def _find(self, _args, query, _body):
        issue_id = query.get("linear_issue")
        if not issue_id:
            return _bad("linear_issue is required")
        card = self._kanban.find_by_issue(issue_id)
        return (200, card) if card else (404, {"error": "unknown card"})

    def _stop(self, args, _query, _body):
        card = self._kanban.get(args["id"])
        if card is None:
            return 404, {"error": "unknown card"}
        root = (card.get("link") or {}).get("root_issue_id")
        cards = self._kanban.tree(root) if root else [card]
        cards = [c for c in cards if c["status"] not in TERMINAL]
        errors = self._interrupt(cards)
        if errors:
            return 502, {"stopped": [], "interrupt_errors": errors}
        stopped, archive_errors = [], []
        for c in cards:
            try:
                if self._kanban.archive(c["id"]):
                    stopped.append(c["id"])
            except Exception as exc:
                archive_errors.append(f"{c['id']}: {type(exc).__name__}")
        result = {"stopped": stopped}
        if archive_errors:
            result["archive_errors"] = archive_errors
        return (500 if archive_errors and not stopped else 200), result

    def _interrupt(self, cards: list[dict]) -> list[str]:
        sessions: list[str] = []
        for card in cards:
            for note in self._kanban.comments(card["id"]):
                session_id = note[len(SESSION_NOTE):].strip() if note.startswith(SESSION_NOTE) else ""
                if session_id and session_id not in sessions:
                    sessions.append(session_id)
        if self._omni is None:
            return [f"{session_id}: no Omnigent client" for session_id in sessions]
        errors = []
        for session_id in sessions:
            try:
                self._omni.interrupt(session_id)
            except Exception as exc:
                errors.append(f"{session_id}: {exc}")
        return errors

    def _events(self, _args, query, _body):
        after = _int_param(query, "after", 0, 0, MAX_CURSOR)
        limit = _int_param(query, "limit", DEFAULT_EVENTS, 1, MAX_EVENTS)
        if after is None or limit is None:
            return _bad(f"after must be >= 0 and limit within 1..{MAX_EVENTS}")
        return 200, {"events": self._kanban.events_after(after, limit)}

    def _cursor_path(self) -> Path:
        return self._state / "cursors.json"

    def _read_cursors(self) -> dict:
        try:
            data = json.loads(self._cursor_path().read_text())
        except (FileNotFoundError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def _cursor_get(self, args, _query, _body):
        with self._cursor_lock:
            return 200, {"value": self._read_cursors().get(args["name"], 0)}

    def _cursor_put(self, args, _query, body):
        value = (body or {}).get("value")
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            return _bad("value must be a non-negative integer")
        with self._cursor_lock:
            cursors = self._read_cursors()
            cursors[args["name"]] = value
            self._state.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=self._state, prefix=".cursors-")
            try:
                with os.fdopen(fd, "w") as handle:
                    json.dump(cursors, handle)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(tmp, self._cursor_path())
            except BaseException:
                Path(tmp).unlink(missing_ok=True)
                raise
        return 200, {"value": value}


def make_server(gate: Gate, token: str, listen: str) -> http.server.ThreadingHTTPServer:
    host, _, port = listen.rpartition(":")
    expected = token.encode()

    class Handler(http.server.BaseHTTPRequestHandler):
        timeout = SOCKET_TIMEOUT

        def log_message(self, format, *args):
            pass

        def _send(self, status: int, payload: dict | None = None):
            data = b"" if payload is None else json.dumps(payload).encode()
            self.send_response(status)
            if data:
                self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(data)

        def _authorized(self) -> bool:
            header = self.headers.get("Authorization", "")
            scheme, _, supplied = header.partition(" ")
            return scheme == "Bearer" and hmac.compare_digest(supplied.encode(), expected)

        def _dispatch(self):
            if not self._authorized():
                return self._send(401)
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                return self._send(400, {"error": "bad Content-Length"})
            if length < 0:
                return self._send(400, {"error": "bad Content-Length"})
            if length > MAX_BODY_BYTES:
                return self._send(413, {"error": "body too large"})
            body = None
            if length:
                try:
                    body = json.loads(self.rfile.read(length))
                except ValueError:
                    return self._send(400, {"error": "invalid JSON"})
                if not isinstance(body, dict):
                    return self._send(400, {"error": "body must be a JSON object"})
            parsed = urllib.parse.urlsplit(self.path)
            query = {k: v[0] for k, v in urllib.parse.parse_qs(parsed.query).items()}
            try:
                status, payload = gate.handle(self.command, parsed.path, query, body)
            except Exception as exc:
                print(f"gate: {self.command} {parsed.path}: {type(exc).__name__}", file=sys.stderr)
                status, payload = 500, {"error": "internal error"}
            self._send(status, payload)

        do_GET = do_POST = do_PUT = do_DELETE = do_PATCH = _dispatch

    return http.server.ThreadingHTTPServer((host or "0.0.0.0", int(port)), Handler)


def main() -> int:
    missing = [name for name in ("KANBAN_GATE_TOKEN", *OMNIGENT_ENV) if not os.environ.get(name)]
    if missing:
        print(f"gate: {', '.join(missing)} required", file=sys.stderr)
        return 2
    token = os.environ["KANBAN_GATE_TOKEN"]
    url, token_url, client_id, client_secret = (os.environ[name] for name in OMNIGENT_ENV)
    omni = OmnigentClient(url, TokenSource(MachineCredentials(token_url, client_id, client_secret)))
    home = os.environ.get("HERMES_HOME") or str(Path.home() / ".hermes")
    gate = Gate(HermesKanbanAdmin(), omni, str(Path(home) / "kanban-gate"))
    server = make_server(gate, token, os.environ.get("KANBAN_GATE_LISTEN", DEFAULT_LISTEN))
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
