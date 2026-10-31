import http.client
import json
import tempfile
import threading
import unittest

from agent_stack_hermes import gate as gate_mod
from agent_stack_hermes.gate import Gate, make_server
from agent_stack_hermes.omnigent import OmnigentError
from agent_stack_hermes.runner import SESSION_NOTE
from agent_stack_hermes.runspec import LinearLink, parse_card_body

TOKEN = "s3cret-token"
TERMINAL = ("done", "archived")


class FakeKanban:
    def __init__(self, calls=None):
        self.cards = {}
        self.bodies = {}
        self.notes = {}
        self.events = []
        self.calls = calls if calls is not None else []
        self.creates = 0
        self.recheck = True

    def create(self, *, title, body, idempotency_key):
        for task_id, card in self.cards.items():
            if self.recheck and card["key"] == idempotency_key and card["status"] != "archived":
                return task_id
        self.creates += 1
        task_id = f"t_{self.creates}"
        link, _ = parse_card_body(body)
        self.cards[task_id] = {
            "id": task_id, "status": "blocked", "assignee": "delivery", "run_id": 0, "outcome": None,
            "block_kind": None, "block_reason": "initial_status", "summary": None,
            "link": None if link is None else link.__dict__.copy(), "metadata": None, "key": idempotency_key,
        }
        return task_id

    def _public(self, card):
        return {k: v for k, v in card.items() if k != "key"}

    def get(self, task_id):
        card = self.cards.get(task_id)
        return None if card is None else self._public(card)

    def find_by_issue(self, issue_id):
        for card in self.cards.values():
            if card["key"] == f"linear:{issue_id}" and card["status"] != "archived":
                return self._public(card)
        return None

    def tree(self, root_issue_id):
        return [
            self._public(c)
            for c in self.cards.values()
            if c["status"] not in TERMINAL and c["link"] and c["link"]["root_issue_id"] == root_issue_id
        ]

    def comments(self, task_id):
        return list(self.notes.get(task_id, []))

    def archive(self, task_id):
        self.calls.append(("archive", task_id))
        self.cards[task_id]["status"] = "archived"
        return True

    def events_after(self, after, limit):
        return [e for e in self.events if e["id"] > after][:limit]


class FakeOmni:
    def __init__(self, calls, fail=()):
        self.calls = calls
        self.fail = set(fail)

    def interrupt(self, session_id):
        self.calls.append(("interrupt", session_id))
        if session_id in self.fail:
            raise OmnigentError(500, "boom")


def new_card(issue_id="i1", **extra):
    return {"issue_id": issue_id, "identifier": "HCL-1", "title": "t", "description": "d", "session_id": "ls_1", **extra}


class GateTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.calls = []
        self.kanban = FakeKanban(self.calls)
        self.omni = FakeOmni(self.calls)
        self.gate = Gate(self.kanban, self.omni, self.tmp.name)

    def call(self, method, path, query=None, body=None, gate=None):
        return (gate or self.gate).handle(method, path, query or {}, body)

    def test_create_is_idempotent_per_issue(self):
        s1, c1 = self.call("POST", "/v1/cards", body=new_card())
        s2, c2 = self.call("POST", "/v1/cards", body=new_card())
        self.assertEqual((s1, s2, self.kanban.creates), (200, 200, 1))
        self.assertEqual(c1["id"], c2["id"])
        self.assertEqual(self.kanban.cards[c1["id"]]["key"], "linear:i1")

    def test_concurrent_creates_for_one_issue_make_one_card(self):
        import time

        class SlowKanban(FakeKanban):
            def create(self, **kwargs):
                exists = self.find_by_issue(kwargs["idempotency_key"].removeprefix("linear:")) is not None
                time.sleep(0.1)
                if exists:
                    return "t_1"
                self.recheck = False
                return super().create(**kwargs)

        kanban = SlowKanban()
        gate = Gate(kanban, None, self.tmp.name)
        results = []
        threads = [
            threading.Thread(target=lambda: results.append(gate.handle("POST", "/v1/cards", {}, new_card())))
            for _ in range(4)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(kanban.creates, 1)
        self.assertEqual({r[1]["id"] for r in results}, {"t_1"})

    def test_repost_with_other_session_returns_existing_link_unchanged(self):
        _, first = self.call("POST", "/v1/cards", body=new_card())
        status, second = self.call("POST", "/v1/cards", body=new_card(session_id="ls_other", title="new"))
        self.assertEqual((status, second), (200, first))
        self.assertEqual(second["link"]["session_id"], "ls_1")
        self.assertEqual(self.kanban.creates, 1)

    def test_stop_continues_after_archive_failure(self):
        ids = []
        for n in ("a", "b"):
            ids.append(self.kanban.create(
                title=n,
                body=f'agent-stack-link: {json.dumps({"issue_id": n, "identifier": n, "session_id": "s", "root_issue_id": "r"})}\nagent-stack-run: null\n',
                idempotency_key=f"linear:{n}",
            ))
        real = self.kanban.archive

        def flaky(task_id):
            if task_id == ids[0]:
                raise RuntimeError("db locked")
            return real(task_id)

        self.kanban.archive = flaky
        status, body = self.call("POST", f"/v1/cards/{ids[0]}/stop", body={"reason": "r"})
        self.assertEqual((status, body["stopped"]), (200, [ids[1]]))
        self.assertEqual(len(body["archive_errors"]), 1)
        self.kanban.archive = lambda task_id: (_ for _ in ()).throw(RuntimeError("x"))
        status, body = self.call("POST", f"/v1/cards/{ids[0]}/stop", body={"reason": "r"})
        self.assertEqual((status, body["stopped"]), (500, []))

    def test_events_huge_numbers_are_400(self):
        self.assertEqual(self.call("GET", "/v1/events", {"after": str(2**70)})[0], 400)
        self.assertEqual(self.call("GET", "/v1/events", {"limit": "9" * 5000})[0], 400)
        self.assertEqual(self.call("GET", "/v1/events", {"after": str(2**63 - 1)})[0], 200)

    def test_create_assignee_always_delivery(self):
        status, card = self.call("POST", "/v1/cards", body=new_card(assignee="evil", root_issue_id="other"))
        self.assertEqual((status, card["assignee"], card["status"]), (200, "delivery", "blocked"))
        self.assertEqual(card["link"]["root_issue_id"], "i1")

    def test_create_rejects_bad_body(self):
        self.assertEqual(self.call("POST", "/v1/cards", body=None)[0], 400)
        self.assertEqual(self.call("POST", "/v1/cards", body={"issue_id": "i1"})[0], 400)
        self.assertEqual(self.call("POST", "/v1/cards", body=new_card(title=5))[0], 400)

    def test_card_json_has_link(self):
        _, created = self.call("POST", "/v1/cards", body=new_card())
        status, card = self.call("GET", f"/v1/cards/{created['id']}")
        self.assertEqual(status, 200)
        self.assertEqual(
            card["link"], {"issue_id": "i1", "identifier": "HCL-1", "session_id": "ls_1", "root_issue_id": "i1"}
        )
        by_issue = self.call("GET", "/v1/cards", {"linear_issue": "i1"})
        self.assertEqual(by_issue, (200, card))

    def test_unknown_card_404(self):
        self.assertEqual(self.call("GET", "/v1/cards/t_9")[0], 404)
        self.assertEqual(self.call("GET", "/v1/cards", {"linear_issue": "nope"})[0], 404)
        self.assertEqual(self.call("GET", "/v1/cards")[0], 400)
        self.assertEqual(self.call("POST", "/v1/cards/t_9/stop", body={"reason": "x"})[0], 404)

    def test_routing_404_and_405(self):
        self.assertEqual(self.call("GET", "/v1/nope")[0], 404)
        self.assertEqual(self.call("DELETE", "/v1/cards")[0], 405)
        self.assertEqual(self.call("POST", "/v1/events")[0], 405)

    def test_stop_interrupts_then_archives_tree(self):
        ids = []
        for n in ("a", "b", "c"):
            card_id = self.kanban.create(
                title=n, body=f'agent-stack-link: {json.dumps({"issue_id": n, "identifier": n, "session_id": "s", "root_issue_id": "r"})}\nagent-stack-run: null\n', idempotency_key=f"linear:{n}"
            )
            ids.append(card_id)
        self.kanban.cards[ids[2]]["status"] = "done"
        self.kanban.notes[ids[0]] = [SESSION_NOTE + "conv_a", "refs-before: {}"]
        self.kanban.notes[ids[1]] = [SESSION_NOTE + "conv_b"]
        self.kanban.notes[ids[2]] = [SESSION_NOTE + "conv_done"]
        status, body = self.call("POST", f"/v1/cards/{ids[0]}/stop", body={"reason": "user"})
        self.assertEqual(status, 200)
        self.assertEqual(sorted(body["stopped"]), sorted(ids[:2]))
        self.assertNotIn("interrupt_errors", body)
        kinds = [c[0] for c in self.calls]
        self.assertEqual(kinds, ["interrupt"] * 2 + ["archive"] * 2)
        self.assertEqual({c[1] for c in self.calls[:2]}, {"conv_a", "conv_b"})
        self.assertEqual(self.kanban.cards[ids[2]]["status"], "done")

    def _tree_cards(self, names):
        ids = []
        for n in names:
            ids.append(self.kanban.create(
                title=n,
                body=f'agent-stack-link: {json.dumps({"issue_id": n, "identifier": n, "session_id": "s", "root_issue_id": "r"})}\nagent-stack-run: null\n',
                idempotency_key=f"linear:{n}",
            ))
        return ids

    def test_stop_archives_nothing_when_any_interrupt_fails(self):
        ids = self._tree_cards(("a", "b"))
        self.kanban.notes[ids[0]] = [SESSION_NOTE + "conv_x"]
        self.kanban.notes[ids[1]] = [SESSION_NOTE + "conv_y"]
        gate = Gate(self.kanban, FakeOmni(self.calls, fail={"conv_x"}), self.tmp.name)
        status, body = self.call("POST", f"/v1/cards/{ids[0]}/stop", body={"reason": "r"}, gate=gate)
        self.assertEqual(status, 502)
        self.assertEqual(body["stopped"], [])
        self.assertEqual(len(body["interrupt_errors"]), 1)
        self.assertIn("conv_x", body["interrupt_errors"][0])
        self.assertNotIn("archive", [c[0] for c in self.calls])
        self.assertEqual({c[1] for c in self.calls}, {"conv_x", "conv_y"})
        self.assertEqual({self.kanban.cards[i]["status"] for i in ids}, {"blocked"})

    def test_stop_retry_after_interrupt_failure_archives(self):
        ids = self._tree_cards(("a",))
        self.kanban.notes[ids[0]] = [SESSION_NOTE + "conv_x"]
        omni = FakeOmni(self.calls, fail={"conv_x"})
        gate = Gate(self.kanban, omni, self.tmp.name)
        self.assertEqual(self.call("POST", f"/v1/cards/{ids[0]}/stop", body={"reason": "r"}, gate=gate)[0], 502)
        omni.fail.clear()
        status, body = self.call("POST", f"/v1/cards/{ids[0]}/stop", body={"reason": "r"}, gate=gate)
        self.assertEqual((status, body), (200, {"stopped": ids}))

    def test_stop_without_omnigent_refuses_when_sessions_exist(self):
        ids = self._tree_cards(("a",))
        self.kanban.notes[ids[0]] = [SESSION_NOTE + "conv_x"]
        gate = Gate(self.kanban, None, self.tmp.name)
        status, body = self.call("POST", f"/v1/cards/{ids[0]}/stop", body={"reason": "r"}, gate=gate)
        self.assertEqual((status, body["stopped"]), (502, []))
        self.assertIn("conv_x", body["interrupt_errors"][0])
        self.assertEqual(self.calls, [])
        self.assertEqual(self.kanban.cards[ids[0]]["status"], "blocked")

    def test_stop_without_sessions_archives(self):
        ids = self._tree_cards(("a",))
        gate = Gate(self.kanban, None, self.tmp.name)
        status, body = self.call("POST", f"/v1/cards/{ids[0]}/stop", body={"reason": "r"}, gate=gate)
        self.assertEqual((status, body), (200, {"stopped": ids}))

    def test_events_after_cursor_ordered(self):
        self.kanban.events = [
            {"id": i, "card_id": "t_1", "kind": "created", "created_at": 100 + i} for i in (1, 2, 3, 4)
        ]
        status, body = self.call("GET", "/v1/events", {"after": "1", "limit": "2"})
        self.assertEqual(status, 200)
        self.assertEqual([e["id"] for e in body["events"]], [2, 3])
        self.assertEqual(set(body["events"][0]), {"id", "card_id", "kind", "created_at"})

    def test_events_limit_bounds(self):
        for limit in ("0", "501", "-1", "x"):
            self.assertEqual(self.call("GET", "/v1/events", {"limit": limit})[0], 400, limit)
        self.assertEqual(self.call("GET", "/v1/events", {"after": "-1"})[0], 400)
        self.assertEqual(self.call("GET", "/v1/events", {"limit": "1"})[0], 200)
        self.assertEqual(self.call("GET", "/v1/events", {"limit": "500"})[0], 200)

    def test_cursor_round_trip_survives_restart(self):
        self.assertEqual(self.call("GET", "/v1/cursors/linear"), (200, {"value": 0}))
        self.assertEqual(self.call("PUT", "/v1/cursors/linear", body={"value": 42}), (200, {"value": 42}))
        restarted = Gate(FakeKanban(), None, self.tmp.name)
        self.assertEqual(self.call("GET", "/v1/cursors/linear", gate=restarted), (200, {"value": 42}))
        self.assertEqual(self.call("PUT", "/v1/cursors/linear", body={"value": "7"})[0], 400)
        self.assertEqual(self.call("PUT", "/v1/cursors/linear", body={"value": True})[0], 400)
        self.assertEqual(self.call("PUT", "/v1/cursors/bad name", body={"value": 1})[0], 404)


class ServerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.server = make_server(Gate(FakeKanban(), None, self.tmp.name), TOKEN, "127.0.0.1:0")
        self.port = self.server.server_address[1]
        thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join, 5)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def request(self, method, path, body=None, token=TOKEN, raw=None, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        self.addCleanup(conn.close)
        hdrs = dict(headers or {})
        if token is not None:
            hdrs["Authorization"] = f"Bearer {token}"
        data = raw if raw is not None else (None if body is None else json.dumps(body).encode())
        conn.request(method, path, body=data, headers=hdrs)
        response = conn.getresponse()
        payload = response.read()
        return response.status, payload

    def test_missing_or_wrong_token_401(self):
        for token in (None, "wrong", TOKEN + "x", ""):
            status, payload = self.request("GET", "/v1/events", token=token)
            self.assertEqual(status, 401)
            self.assertNotIn(b"detail", payload)
            self.assertNotIn(TOKEN.encode(), payload)
        status, _ = self.request("GET", "/v1/events", headers={"Authorization": f"Basic {TOKEN}"}, token=None)
        self.assertEqual(status, 401)
        self.assertEqual(self.request("GET", "/v1/events")[0], 200)

    def test_handler_has_socket_timeout(self):
        self.assertEqual(self.server.RequestHandlerClass.timeout, gate_mod.SOCKET_TIMEOUT)

    def test_token_compared_in_constant_time(self):
        import inspect

        self.assertIn("compare_digest", inspect.getsource(gate_mod))

    def test_body_over_64k_rejected(self):
        status, _ = self.request("POST", "/v1/cards", raw=b"x" * (64 * 1024 + 1))
        self.assertEqual(status, 413)
        ok = json.dumps(new_card()).encode()
        self.assertEqual(self.request("POST", "/v1/cards", raw=ok)[0], 200)

    def test_invalid_json_400_and_unknown_route_404(self):
        self.assertEqual(self.request("POST", "/v1/cards", raw=b"{nope")[0], 400)
        self.assertEqual(self.request("POST", "/v1/cards", raw=b"[1]")[0], 400)
        self.assertEqual(self.request("GET", "/v1/zzz")[0], 404)
        self.assertEqual(self.request("PATCH", "/v1/cards")[0], 405)

    def test_query_string_reaches_gate(self):
        status, payload = self.request("GET", "/v1/events?limit=0")
        self.assertEqual(status, 400)
        status, payload = self.request("GET", "/v1/events?after=0&limit=5")
        self.assertEqual(json.loads(payload), {"events": []})


class MainTest(unittest.TestCase):
    FULL = {
        "KANBAN_GATE_TOKEN": TOKEN,
        "OMNIGENT_URL": "http://omnigent",
        "OMNIGENT_TOKEN_URL": "http://idp/token",
        "OMNIGENT_CLIENT_ID": "id",
        "OMNIGENT_CLIENT_SECRET": "secret",
    }

    def test_main_refuses_without_token(self):
        from unittest import mock

        with mock.patch.dict("os.environ", {}, clear=True), mock.patch("sys.stderr"):
            self.assertNotEqual(gate_mod.main(), 0)

    def test_main_refuses_when_any_required_env_missing(self):
        from unittest import mock

        for name in self.FULL:
            env = {k: v for k, v in self.FULL.items() if k != name}
            with self.subTest(missing=name), mock.patch.dict("os.environ", env, clear=True), \
                    mock.patch("sys.stderr"), mock.patch.object(gate_mod, "make_server") as server:
                self.assertEqual(gate_mod.main(), 2)
                server.assert_not_called()

    def test_main_serves_with_omnigent_client(self):
        from unittest import mock

        with tempfile.TemporaryDirectory() as home, \
                mock.patch.dict("os.environ", {**self.FULL, "HERMES_HOME": home}, clear=True), \
                mock.patch.object(gate_mod, "make_server") as server:
            self.assertEqual(gate_mod.main(), 0)
        gate = server.call_args.args[0]
        self.assertIsInstance(gate._omni, gate_mod.OmnigentClient)
        server.return_value.serve_forever.assert_called_once()


class CardJsonTest(unittest.TestCase):
    def setUp(self):
        import sqlite3

        self.conn = sqlite3.connect(":memory:")
        self.addCleanup(self.conn.close)
        self.conn.execute("CREATE TABLE task_events (id INTEGER PRIMARY KEY, task_id TEXT, kind TEXT, payload TEXT)")
        self.conn.execute("CREATE TABLE task_runs (id INTEGER PRIMARY KEY, task_id TEXT, outcome TEXT, metadata TEXT)")

    def card(self, status, events, run=None):
        from types import SimpleNamespace

        for kind, payload in events:
            self.conn.execute(
                "INSERT INTO task_events (task_id, kind, payload) VALUES ('t1', ?, ?)", (kind, json.dumps(payload))
            )
        kb = SimpleNamespace(latest_run=lambda _conn, _id: run)
        task = SimpleNamespace(
            id="t1", status=status, assignee="omnigent-runner", current_run_id=None, block_kind="needs_input",
            result=None, body="plain",
        )
        return gate_mod.HermesKanbanAdmin()._card(self.conn, kb, task)

    def test_triage_card_has_block_reason_from_loop_event(self):
        card = self.card("triage", [
            ("blocked", {"reason": "first", "kind": "needs_input"}),
            ("unblocked", None),
            ("block_loop_detected", {"reason": "second", "kind": "needs_input", "recurrences": 2}),
        ])
        self.assertEqual((card["status"], card["block_kind"], card["block_reason"]), ("triage", "needs_input", "second"))
        self.assertIsNone(card["outcome"])

    def test_triage_card_prefers_blocked_run_summary(self):
        from types import SimpleNamespace

        run = SimpleNamespace(id=7, outcome="blocked", summary="BLOCKED{permission}: post-run check: x")
        card = self.card("triage", [("block_loop_detected", {"reason": "other"})], run=run)
        self.assertEqual((card["block_reason"], card["outcome"], card["run_id"]), (run.summary, "blocked", 7))

    def test_ready_card_has_no_block_reason(self):
        card = self.card("ready", [("block_loop_detected", {"reason": "old"}), ("unblocked", None)])
        self.assertIsNone(card["block_reason"])


if __name__ == "__main__":
    unittest.main()
