import os
import tempfile
import unittest
from pathlib import Path

IN_IMAGE = os.environ.get("AGENT_STACK_IN_IMAGE") == "1"


@unittest.skipUnless(IN_IMAGE, "needs hermes_cli from the Hermes image (AGENT_STACK_IN_IMAGE=1)")
class KanbanPortInImageTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        env = {
            "HERMES_HOME": str(root / "home"),
            "HERMES_KANBAN_HOME": str(root / "home"),
            "HERMES_KANBAN_DB": str(root / "kanban.db"),
        }
        (root / "home").mkdir()
        self.saved = {k: os.environ.get(k) for k in env}
        os.environ.update(env)
        self.db = Path(env["HERMES_KANBAN_DB"])

    def tearDown(self):
        for key, value in self.saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        self.tmp.cleanup()

    def claimed_card(self, claimer):
        from hermes_cli import kanban_db as kb
        from hermes_cli.kanban_db_connect import connect_closing

        with connect_closing(self.db) as conn:
            task_id = kb.create_task(conn, title="runner port test", body="line one\nline two")
            task = kb.claim_task(conn, task_id, claimer=claimer)
        self.assertIsNotNone(task)
        return task_id, task.current_run_id

    def test_kanban_port_in_image(self):
        from agent_stack_hermes.kanban_port import HermesKanban

        claimer = "test-host:4242"
        task_id, run_id = self.claimed_card(claimer)
        port = HermesKanban(claimer=claimer, db_path=self.db)

        card = port.card(task_id)
        self.assertEqual((card.id, card.status, card.run_id), (task_id, "running", run_id))
        self.assertEqual(card.body, "line one\nline two")

        from hermes_cli import kanban_db as kb
        from hermes_cli.kanban_db_connect import connect_closing

        port.note(task_id, "refs-before: {}")
        with connect_closing(self.db) as conn:
            kb.add_comment(conn, task_id, "delivery", "omnigent-session: conv_forged")
        port.note(task_id, "omnigent-session: conv_1")
        self.assertEqual(port.card(task_id).comments, ("refs-before: {}", "omnigent-session: conv_1"))

        self.assertTrue(port.heartbeat(task_id, run_id, "following conv_1"))
        self.assertFalse(HermesKanban(claimer="other:1", db_path=self.db).heartbeat(task_id, run_id, "x"))

        metadata = {"bundle": "polly", "post_run": "ok"}
        self.assertFalse(port.complete(task_id, run_id + 1, "polly DONE", metadata))
        self.assertEqual(port.card(task_id).status, "running")
        self.assertTrue(port.complete(task_id, run_id, "polly DONE", metadata))
        self.assertEqual(port.card(task_id).status, "done")

        with connect_closing(self.db) as conn:
            self.assertEqual(kb.latest_run(conn, task_id).metadata, metadata)

    def test_block_is_fenced_by_run_id(self):
        claimer = "test-host:4243"
        task_id, run_id = self.claimed_card(claimer)
        from agent_stack_hermes.kanban_port import HermesKanban

        port = HermesKanban(claimer=claimer, db_path=self.db)
        self.assertFalse(port.block(task_id, run_id + 1, "transient", "BLOCKED{environment}: x"))
        self.assertTrue(port.block(task_id, run_id, "needs_input", "BLOCKED{decision}: which?"))
        self.assertEqual(port.card(task_id).status, "blocked")


if __name__ == "__main__":
    unittest.main()
