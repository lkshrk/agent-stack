import contextlib
import sys
import types
import unittest
from types import SimpleNamespace
from unittest import mock

from agent_stack_hermes import kanban_port
from agent_stack_hermes.kanban_port import HermesKanban


class FakeBoard:
    def __init__(self):
        self.task = SimpleNamespace(id="t_1", status="running", body="b", current_run_id=7)
        self.comments = []

    def add_comment(self, conn, task_id, author, body):
        self.comments.append(SimpleNamespace(id=len(self.comments) + 1, author=author, body=body))
        return len(self.comments)

    def modules(self):
        kb = types.ModuleType("hermes_cli.kanban_db")
        kb.get_task = lambda conn, task_id: self.task if task_id == self.task.id else None
        kb.list_comments = lambda conn, task_id: list(reversed(self.comments))
        kb.add_comment = self.add_comment
        connect = types.ModuleType("hermes_cli.kanban_db_connect")
        connect.connect_closing = lambda db_path=None: contextlib.nullcontext(object())
        package = types.ModuleType("hermes_cli")
        package.kanban_db, package.kanban_db_connect = kb, connect
        return {"hermes_cli": package, "hermes_cli.kanban_db": kb, "hermes_cli.kanban_db_connect": connect}


class HermesKanbanTest(unittest.TestCase):
    def setUp(self):
        self.board = FakeBoard()
        patcher = mock.patch.dict(sys.modules, self.board.modules())
        patcher.start()
        self.addCleanup(patcher.stop)
        self.port = HermesKanban(claimer="h:1")

    def test_notes_are_written_under_the_runner_author(self):
        self.port.note("t_1", "omnigent-session: conv_1")
        self.assertEqual([(c.author, c.body) for c in self.board.comments],
                         [(kanban_port.AUTHOR, "omnigent-session: conv_1")])

    def test_forged_notes_by_other_authors_are_ignored(self):
        self.port.note("t_1", "refs-before: {}")
        self.board.add_comment(None, "t_1", "delivery", "omnigent-session: conv_forged")
        self.board.add_comment(None, "t_1", "omnigent-runner", "refs-before: {\"refs/tags/v1\": \"x\"}")
        self.port.note("t_1", "omnigent-session: conv_1")
        card = self.port.card("t_1")
        self.assertEqual(card.comments, ("refs-before: {}", "omnigent-session: conv_1"))
        self.assertEqual((card.id, card.status, card.run_id), ("t_1", "running", 7))

    def test_runner_author_is_no_profile_name(self):
        self.assertIn("/", kanban_port.AUTHOR)


if __name__ == "__main__":
    unittest.main()
