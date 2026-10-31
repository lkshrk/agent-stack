import copy
import hashlib
import json
import unittest
from pathlib import Path

from agent_stack_hermes.runspec import (
    LinearLink,
    RunSpec,
    builtin_agent_id,
    evaluate,
    parse_card_body,
    render_card_body,
    session_request,
)

TESTDATA = Path(__file__).parent / "testdata" / "omnigent"
LINK = LinearLink("iss-1", "ENG-7", "sess-1", "iss-0")


def snapshot(name):
    return json.loads((TESTDATA / f"snapshot_{name}.json").read_text())


def assistant(text):
    return {"type": "message", "data": {"role": "assistant", "content": [{"type": "output_text", "text": text}]}}


def spec(bundle):
    return RunSpec(bundle, "example-org/example-repo", "main", "do the work")


class RunSpecTest(unittest.TestCase):
    def test_body_round_trip(self):
        s = spec("planner")
        body = render_card_body(LINK, s, "free text\nmore")
        self.assertEqual(parse_card_body(body), (LINK, s))
        for link, run in ((None, None), (LINK, None), (None, s)):
            self.assertEqual(parse_card_body(render_card_body(link, run, "x")), (link, run))

    def test_injected_run_line_in_text_ignored(self):
        injected = 'agent-stack-run: {"bundle":"polly","repo":"o/r","ref":"main","input":"x"}'
        for link in (LINK, None):
            self.assertEqual(parse_card_body(render_card_body(link, None, injected)), (link, None))
        self.assertEqual(parse_card_body(render_card_body(None, None, "a\n" + injected)), (None, None))

    def test_body_without_header(self):
        self.assertEqual(parse_card_body("just text"), (None, None))
        self.assertEqual(parse_card_body(""), (None, None))
        self.assertEqual(parse_card_body("agent-stack-run: {}\nx"), (None, None))

    def test_malformed_link_line_raises(self):
        for line in ("agent-stack-link: {nope", 'agent-stack-link: {"issue_id": "x"}', "agent-stack-link: []"):
            with self.assertRaises(ValueError):
                parse_card_body(line + "\nagent-stack-run: null\ntext")

    def test_unknown_bundle_rejected(self):
        with self.assertRaises(ValueError):
            RunSpec("coder", "o/r", "main", "x")
        body = 'agent-stack-run: {"bundle":"coder","repo":"o/r","ref":"main","input":"x"}'
        with self.assertRaises(ValueError):
            parse_card_body("agent-stack-link: null\n" + body)

    def test_repo_with_hash_rejected(self):
        for repo in ("o/r#x", "o", "o/r r"):
            with self.assertRaises(ValueError):
                RunSpec("polly", repo, "main", "x")
        for ref in ("", "a b", "a#b"):
            with self.assertRaises(ValueError):
                RunSpec("polly", "o/r", ref, "x")

    def test_builtin_agent_id_polly(self):
        self.assertEqual(builtin_agent_id("polly"), hashlib.sha256(b"builtin:polly").hexdigest()[:32])

    def test_security_request_is_top_level(self):
        req = session_request(spec("security-review"), "card-1", LINK)
        self.assertEqual(req["terminal_launch_args"], ["--profile", "security-review"])
        self.assertEqual(req["model_override"], "openai/gpt-astra")
        for key in ("parent_session_id", "git", "host_id"):
            self.assertNotIn(key, req)
        self.assertEqual(req["host_type"], "managed")
        self.assertEqual(req["workspace"], "https://github.com/example-org/example-repo#main")
        self.assertEqual(req["labels"], {"agent-stack/card": "card-1", "agent-stack/linear-issue": "ENG-7"})
        self.assertEqual(req["initial_input"]["data"]["content"][0], {"type": "input_text", "text": "do the work"})

    def test_planner_request_model(self):
        req = session_request(spec("planner"), "card-1", None)
        self.assertEqual(req["model_override"], "anthropic/claude-fable")
        self.assertEqual(req["labels"]["agent-stack/linear-issue"], "")
        self.assertNotIn("terminal_launch_args", req)

    def test_polly_request_model(self):
        req = session_request(spec("polly"), "card-1", LINK)
        self.assertEqual(req["model_override"], "anthropic-auto")
        self.assertNotIn("terminal_launch_args", req)

    def test_planner_first_valid_block_wins(self):
        snap = snapshot("planner")
        out = evaluate("planner", snap)
        self.assertTrue(out.done, out)
        later = copy.deepcopy(snap)
        later["items"].append(assistant("Follow-up.\n\n```json\n{\"status\": \"DONE\"}\n```"))
        self.assertTrue(evaluate("planner", later).done)

    def test_last_message_wins_for_other_bundles(self):
        snap = snapshot("security")
        snap["items"].append(assistant("Afterthought.\n\n```json\n{\"status\": \"DONE\"}\n```"))
        out = evaluate("security-review", snap)
        self.assertFalse(out.done)
        self.assertEqual(out.block_class, "environment")

    def test_nested_items_and_multiple_blocks(self):
        snap = snapshot("polly")
        self.assertTrue(evaluate("polly", snap).done)
        nested = copy.deepcopy(snap)
        nested["items"] = {"data": nested["items"]}
        self.assertTrue(evaluate("polly", nested).done)
        flat = copy.deepcopy(snap)
        flat["items"] = [
            {"type": "message", "role": "assistant", "content": [b]}
            for b in flat["items"][-1]["data"]["content"]
        ]
        self.assertTrue(evaluate("polly", flat).done)

    def test_missing_block_blocks_environment(self):
        snap = {"status": "idle", "items": [assistant("All done, trust me.")]}
        out = evaluate("polly", snap)
        self.assertFalse(out.done)
        self.assertEqual((out.block_class, out.reason), ("environment", "no result block"))
        self.assertEqual(evaluate("polly", {"status": "idle", "items": []}).reason, "no result block")
        self.assertFalse(evaluate("polly", {"status": "idle", "items": None}).done)

    def test_invalid_block_lists_errors(self):
        snap = {"status": "idle", "items": [assistant('```json\n{"status": "DONE"}\n```')]}
        out = evaluate("polly", snap)
        self.assertFalse(out.done)
        self.assertEqual(out.block_class, "environment")
        self.assertIn("pr must be", out.reason)
        self.assertIn("branch must be", out.reason)

    def test_result_blocked_decision_passes_class(self):
        block = {"status": "BLOCKED", "blocked": {"class": "decision", "question": "Which API version?"}}
        snap = {"status": "idle", "items": [assistant("```json\n" + json.dumps(block) + "\n```")]}
        out = evaluate("polly", snap)
        self.assertFalse(out.done)
        self.assertEqual((out.block_class, out.reason), ("decision", "Which API version?"))

    def test_planner_blocked_decision_passes_class(self):
        block = {"status": "BLOCKED", "blocked": {"class": "decision", "question": "Split by service?"}}
        snap = {"status": "idle", "items": [assistant("```json\n" + json.dumps(block) + "\n```")]}
        out = evaluate("planner", snap)
        self.assertFalse(out.done)
        self.assertEqual((out.block_class, out.reason), ("decision", "Split by service?"))

    def test_failed_session_blocks_environment(self):
        snap = snapshot("polly")
        snap["status"] = "failed"
        snap["last_task_error"] = {"code": "x", "message": "runner crashed"}
        out = evaluate("polly", snap)
        self.assertFalse(out.done)
        self.assertEqual(out.block_class, "environment")
        self.assertIn("runner crashed", out.reason)


if __name__ == "__main__":
    unittest.main()
