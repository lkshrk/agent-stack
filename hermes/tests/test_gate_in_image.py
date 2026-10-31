import os
import tempfile
import unittest
from pathlib import Path

IN_IMAGE = os.environ.get("AGENT_STACK_IN_IMAGE") == "1"


@unittest.skipUnless(IN_IMAGE, "needs hermes_cli from the Hermes image (AGENT_STACK_IN_IMAGE=1)")
class GateInImageTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        (root / "home").mkdir()
        env = {
            "HERMES_HOME": str(root / "home"),
            "HERMES_KANBAN_HOME": str(root / "home"),
            "HERMES_KANBAN_DB": str(root / "kanban.db"),
        }
        self.saved = {k: os.environ.get(k) for k in env}
        os.environ.update(env)
        self.db = Path(env["HERMES_KANBAN_DB"])
        self.state = str(root / "state")

    def tearDown(self):
        for key, value in self.saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        self.tmp.cleanup()

    def test_gate_in_image(self):
        from hermes_cli import kanban_db as kb
        from hermes_cli.kanban_db_connect import connect_closing

        from agent_stack_hermes.gate import Gate, HermesKanbanAdmin
        from agent_stack_hermes.kanban_port import AUTHOR
        from agent_stack_hermes.runner import SESSION_NOTE

        interrupted = []

        class Omni:
            def interrupt(self, session_id):
                interrupted.append(session_id)

        gate = Gate(HermesKanbanAdmin(self.db), Omni(), self.state)
        body = {"issue_id": "i1", "identifier": "HCL-1", "title": "Do it", "description": "text", "session_id": "ls_1"}
        status, card = gate.handle("POST", "/v1/cards", {}, body)
        self.assertEqual(status, 200)
        self.assertEqual((card["status"], card["assignee"], card["run_id"]), ("blocked", "delivery", 0))
        self.assertIsNone(card["outcome"])
        self.assertEqual(card["block_reason"], "initial_status")
        self.assertEqual(card["link"]["root_issue_id"], "i1")
        self.assertIsNone(card["metadata"])

        again = gate.handle("POST", "/v1/cards", {}, body)[1]
        self.assertEqual(again["id"], card["id"])
        self.assertEqual(gate.handle("GET", "/v1/cards", {"linear_issue": "i1"}, None)[1]["id"], card["id"])
        self.assertEqual(gate.handle("GET", f"/v1/cards/{card['id']}", {}, None)[1], card)

        status, events = gate.handle("GET", "/v1/events", {"after": "0", "limit": "10"}, None)
        self.assertEqual(status, 200)
        kinds = [e["kind"] for e in events["events"]]
        self.assertIn("created", kinds)
        self.assertIn("blocked", kinds)
        ids = [e["id"] for e in events["events"]]
        self.assertEqual(ids, sorted(ids))
        self.assertTrue(all(set(e) == {"id", "card_id", "kind", "created_at"} for e in events["events"]))

        with connect_closing(self.db) as conn:
            kb.add_comment(conn, card["id"], AUTHOR, SESSION_NOTE + "conv_1")
            kb.add_comment(conn, card["id"], "someone-else", SESSION_NOTE + "conv_forged")

        status, stopped = gate.handle("POST", f"/v1/cards/{card['id']}/stop", {}, {"reason": "user"})
        self.assertEqual((status, stopped), (200, {"stopped": [card["id"]]}))
        self.assertEqual(interrupted, ["conv_1"])
        self.assertEqual(gate.handle("GET", f"/v1/cards/{card['id']}", {}, None)[1]["status"], "archived")
        self.assertEqual(gate.handle("GET", "/v1/cards", {"linear_issue": "i1"}, None)[0], 404)

    def test_completed_and_gave_up_card_json(self):
        from hermes_cli import kanban_db as kb
        from hermes_cli.kanban_db_connect import connect_closing

        from agent_stack_hermes.gate import HermesKanbanAdmin

        admin = HermesKanbanAdmin(self.db)
        with connect_closing(self.db) as conn:
            task_id = kb.create_task(conn, title="x", body="plain body")
            task = kb.claim_task(conn, task_id, claimer="h:1")
            run_id = task.current_run_id
            kb.complete_task(conn, task_id, summary="done it", metadata={"bundle": "polly"}, expected_run_id=run_id)
        card = admin.get(task_id)
        self.assertEqual((card["status"], card["run_id"], card["outcome"]), ("done", run_id, "completed"))
        self.assertEqual(card["metadata"], {"bundle": "polly"})
        self.assertEqual(card["summary"], "done it")
        self.assertIsNone(card["link"])

    def test_reblocked_card_in_triage_keeps_block_info(self):
        from hermes_cli import kanban_db as kb
        from hermes_cli.kanban_db_connect import connect_closing

        from agent_stack_hermes.gate import HermesKanbanAdmin

        with connect_closing(self.db) as conn:
            task_id = kb.create_task(conn, title="x", body="plain body")
            for reason in ("first question", "second question"):
                run_id = kb.claim_task(conn, task_id, claimer="h:1").current_run_id
                self.assertTrue(kb.block_task(conn, task_id, reason=reason, kind="needs_input", expected_run_id=run_id))
                if reason == "first question":
                    self.assertTrue(kb.unblock_task(conn, task_id))
            kinds = [r[0] for r in conn.execute("SELECT kind FROM task_events WHERE task_id = ? ORDER BY id", (task_id,))]
        self.assertIn("block_loop_detected", kinds)
        card = HermesKanbanAdmin(self.db).get(task_id)
        self.assertEqual(
            (card["status"], card["block_kind"], card["block_reason"]), ("triage", "needs_input", "second question")
        )


if __name__ == "__main__":
    unittest.main()
