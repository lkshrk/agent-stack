import contextlib
import io
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from agent_stack_hermes import start as start_mod
from agent_stack_hermes.runspec import RunSpec, parse_card_body

IN_IMAGE = os.environ.get("AGENT_STACK_IN_IMAGE") == "1"
WRAPPER = Path("/opt/agent/bin/runner-start")


class FakeCards:
    def __init__(self, open_card=None):
        self.open_card = open_card
        self.created = []

    def start(self, bundle, title, body):
        if self.open_card:
            return False, *self.open_card
        self.created.append((bundle, title, body))
        return True, f"t_{len(self.created)}", "ready"


class CardBodyTest(unittest.TestCase):
    def test_infra_body_is_an_unlinked_run_spec(self):
        body = start_mod.card_body("infra", "  upgrade h-cloud, skip cilium \n")
        lines = body.split("\n")
        self.assertEqual(lines[0], "agent-stack-link: null")
        spec = json.loads(lines[1][len("agent-stack-run: "):])
        self.assertEqual(
            spec, {"bundle": "infra", "repo": "lkshrk/h-cloud", "ref": "main", "input": "upgrade h-cloud, skip cilium"}
        )
        self.assertEqual(parse_card_body(body), (None, RunSpec(**spec)))

    def test_blank_request_uses_default_input(self):
        for text in ("", "   ", None):
            spec = parse_card_body(start_mod.card_body("infra", text))[1]
            self.assertEqual(spec.input, "Run the Renovate sweep on lkshrk/h-cloud.")

    def test_request_text_is_json_escaped(self):
        text = 'say "hi"\nthen\tgo \\ now'
        spec = parse_card_body(start_mod.card_body("infra", text))[1]
        self.assertEqual(spec.input, text)

    def test_unpinned_bundle_refused(self):
        with self.assertRaises(ValueError):
            start_mod.card_body("polly", "x")

    def test_bundle_of(self):
        self.assertEqual(start_mod.bundle_of(start_mod.card_body("infra", "")), "infra")
        for body in (None, "", "plain body", "agent-stack-link: null\nagent-stack-run: {bad"):
            self.assertIsNone(start_mod.bundle_of(body), body)


class StartTest(unittest.TestCase):
    def test_creates_one_card(self):
        cards = FakeCards()
        self.assertEqual(start_mod.start(cards, "infra", "go"), {"started": True, "card": "t_1", "status": "ready"})
        bundle, title, body = cards.created[0]
        self.assertEqual((bundle, title), ("infra", "infra: lkshrk/h-cloud"))
        self.assertEqual(parse_card_body(body)[1].input, "go")

    def test_open_card_refuses(self):
        cards = FakeCards(open_card=("t_9", "running"))
        self.assertEqual(start_mod.start(cards, "infra", "go"), {"started": False, "card": "t_9", "status": "running"})
        self.assertEqual(cards.created, [])

    def run_main(self, argv, cards):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = start_mod.main(argv, cards)
        return code, out.getvalue(), err.getvalue()

    def test_main_joins_request_words(self):
        cards = FakeCards()
        code, out, _ = self.run_main(["infra", "upgrade", "h-cloud"], cards)
        self.assertEqual((code, out), (0, "started infra card t_1 (ready)\n"))
        self.assertEqual(parse_card_body(cards.created[0][2])[1].input, "upgrade h-cloud")

    def test_main_reports_open_card(self):
        code, out, _ = self.run_main(["infra"], FakeCards(open_card=("t_9", "blocked")))
        self.assertEqual((code, out), (start_mod.EXIT_OPEN, "not started: infra card t_9 is still blocked\n"))

    def test_main_usage(self):
        for argv in ([], ["polly", "x"], ["nope"]):
            cards = FakeCards()
            code, out, err = self.run_main(argv, cards)
            self.assertEqual((code, out, cards.created), (start_mod.EXIT_USAGE, "", []), argv)
            self.assertIn("usage: runner-start <infra>", err)


@unittest.skipUnless(IN_IMAGE, "needs hermes_cli from the Hermes image (AGENT_STACK_IN_IMAGE=1)")
class StartInImageTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        (root / "home").mkdir()
        self.env = {
            "HERMES_HOME": str(root / "home"),
            "HERMES_KANBAN_HOME": str(root / "home"),
            "HERMES_KANBAN_DB": str(root / "kanban.db"),
        }
        self.saved = {k: os.environ.get(k) for k in self.env}
        os.environ.update(self.env)
        self.db = Path(self.env["HERMES_KANBAN_DB"])

    def tearDown(self):
        for key, value in self.saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        self.tmp.cleanup()

    def test_guard_and_create_on_the_real_board(self):
        from hermes_cli import kanban_db as kb
        from hermes_cli.kanban_db_connect import connect_closing

        cards = start_mod.HermesRunnerCards(self.db)
        with connect_closing(self.db) as conn:
            other = kb.create_task(conn, title="p", body="agent-stack-link: null\nagent-stack-run: "
                                   '{"bundle":"polly","repo":"a/b","ref":"main","input":"x"}\n',
                                   assignee="omnigent-runner")
            kb.create_task(conn, title="d", body=start_mod.card_body("infra", ""), assignee="delivery")
        first = start_mod.start(cards, "infra", "upgrade h-cloud")
        self.assertEqual((first["started"], first["status"]), (True, "ready"))
        self.assertEqual(start_mod.start(cards, "infra", "again"),
                         {"started": False, "card": first["card"], "status": "ready"})
        with connect_closing(self.db) as conn:
            task = kb.get_task(conn, first["card"])
            self.assertEqual((task.assignee, task.created_by, task.title), ("omnigent-runner", "default", "infra: lkshrk/h-cloud"))
            self.assertEqual(parse_card_body(task.body)[1].input, "upgrade h-cloud")
            run_id = kb.claim_task(conn, first["card"], claimer="h:1").current_run_id
            self.assertTrue(kb.complete_task(conn, first["card"], summary="infra DONE", expected_run_id=run_id))
            self.assertEqual(kb.get_task(conn, other).status, "ready")
        second = start_mod.start(cards, "infra", "")
        self.assertTrue(second["started"])
        self.assertNotEqual(second["card"], first["card"])

    @unittest.skipUnless(WRAPPER.exists(), "needs the installed runner-start")
    def test_wrapper_from_a_login_shell(self):
        env = {**os.environ, "PYTHONPATH": "/nonexistent"}
        first = subprocess.run(["bash", "-lc", 'runner-start infra "upgrade h-cloud"'], env=env, cwd="/",
                               capture_output=True, text=True)
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertRegex(first.stdout, r"^started infra card \S+ \(ready\)\n$")
        again = subprocess.run(["bash", "-lc", "runner-start infra"], env=env, cwd="/", capture_output=True, text=True)
        self.assertEqual(again.returncode, start_mod.EXIT_OPEN, again.stderr)


if __name__ == "__main__":
    unittest.main()
