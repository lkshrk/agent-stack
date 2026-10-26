import contextlib
import io
import json
import subprocess
import sys
import unittest
from pathlib import Path

from agent_stack_policies import result

from . import assert_not_ask, tool_call_event

FIXTURES = Path(__file__).parent / "fixtures"
ROOT = Path(__file__).parent.parent


def load(name):
    return json.loads((FIXTURES / name).read_text())


def fenced(obj):
    return "```json\n" + json.dumps(obj) + "\n```"


def response(text, final=None):
    return {
        "type": "response",
        "target": "agent",
        "data": text,
        "context": {"turn_final": final},
        "session_state": {},
        "llm_client": None,
    }


EVENTS = [
    response("hello", True),
    response("hello", False),
    response(fenced(load("planner-valid.json")), True),
    response("```json\n{not json}\n```", True),
    {"type": "response", "data": None},
    tool_call_event("Bash"),
]


class ExtractTest(unittest.TestCase):
    def test_extract_last_block_wins(self):
        text = f"intro\n{fenced({'n': 1})}\nmiddle\n{fenced({'n': 2})}\ntrailing prose"
        self.assertEqual(result.extract_result(text), {"n": 2})

    def test_extract_skips_unparseable_and_non_dict(self):
        text = f"{fenced({'n': 1})}\n```json\n[1]\n```\n```json\n{{oops\n```"
        self.assertEqual(result.extract_result(text), {"n": 1})

    def test_extract_none_without_block(self):
        self.assertIsNone(result.extract_result("no block here"))


class PlannerTest(unittest.TestCase):
    def errors(self, name):
        return result.validate_result("planner", load(name))

    def test_planner_valid_fixture(self):
        self.assertEqual(self.errors("planner-valid.json"), [])

    def test_planner_bad_ears(self):
        self.assertTrue(any("EARS" in e for e in self.errors("planner-invalid-ears.json")))

    def test_planner_duplicate_criterion_id(self):
        self.assertTrue(any("duplicated" in e for e in self.errors("planner-invalid-duplicate-criterion.json")))

    def test_planner_cycle(self):
        self.assertTrue(any("cycle" in e for e in self.errors("planner-invalid-cycle.json")))

    def test_planner_parallel_children_overlap(self):
        self.assertTrue(any("share files" in e for e in self.errors("planner-invalid-overlap.json")))

    def test_planner_nine_children(self):
        self.assertTrue(any("1 to 8" in e for e in self.errors("planner-invalid-nine-children.json")))

    def test_planner_uncovered_criterion(self):
        self.assertTrue(any("not covered" in e for e in self.errors("planner-invalid-uncovered.json")))

    def test_planner_tbd_placeholder(self):
        self.assertTrue(any("placeholder" in e for e in self.errors("planner-invalid-tbd.json")))

    def test_planner_dependent_children_may_share_files(self):
        planner = load("planner-valid.json")
        self.assertIn("src/a.py", planner["children"][1]["files"])
        self.assertEqual(result.validate_result("planner", planner), [])

    def test_planner_unknown_blocked_by(self):
        planner = load("planner-valid.json")
        planner["children"][0]["blocked_by"] = ["zzz"]
        self.assertTrue(any("unknown" in e for e in result.validate_result("planner", planner)))

    def test_blocked_requires_class_and_question(self):
        blocked = {"status": "BLOCKED", "blocked": {"class": "decision", "question": "Which API?"}}
        self.assertEqual(result.validate_result("planner", blocked), [])
        for bad in ({"class": "oops", "question": "q"}, {"class": "decision", "question": " "}, None):
            errors = result.validate_result("planner", {"status": "BLOCKED", "blocked": bad})
            self.assertTrue(errors, bad)

    def test_bad_status(self):
        self.assertTrue(result.validate_result("security", {"status": "MAYBE"}))

    def test_needs_clarification_marker(self):
        blocked = {"status": "BLOCKED", "blocked": {"class": "decision", "question": "[NEEDS CLARIFICATION x]"}}
        self.assertTrue(result.validate_result("planner", blocked))


class MalformedInputTest(unittest.TestCase):
    def test_unhashable_coverage_criterion(self):
        for bad in (["x"], {"a": 1}, None, 3):
            data = load("planner-valid.json")
            data["critic"]["coverage"][0]["criterion"] = bad
            self.assertTrue(result.validate_result("planner", data), bad)
            decision = result.result_contract(kind="planner")(response(fenced(data), True))
            self.assertEqual(decision["result"], "DENY")

    def test_wrong_typed_shapes_never_raise(self):
        junk = [None, 0, "s", [], {}, [1], ["a"], {"a": 1}, [{}], [[]], True]
        base = {
            "planner": load("planner-valid.json"),
            "security": load("security-valid.json"),
            "documenter": load("documenter-valid.json"),
        }
        for kind, good in base.items():
            for key in list(good):
                for value in junk:
                    data = {**good, key: value}
                    self.assertIsInstance(result.validate_result(kind, data), list, (kind, key, value))
        planner = base["planner"]
        for field in ("key", "title", "files", "criteria", "blocked_by", "independent_test"):
            for value in junk:
                data = json.loads(json.dumps(planner))
                data["children"][1][field] = value
                self.assertIsInstance(result.validate_result("planner", data), list, (field, value))
        for value in junk:
            data = json.loads(json.dumps(planner))
            data["children"][0] = value
            self.assertIsInstance(result.validate_result("planner", data), list)
            data = json.loads(json.dumps(planner))
            data["children"][0]["criteria"] = [value]
            self.assertIsInstance(result.validate_result("planner", data), list)
            data = json.loads(json.dumps(planner))
            data["critic"] = {"rounds": value, "coverage": value}
            self.assertIsInstance(result.validate_result("planner", data), list)
            sec = json.loads(json.dumps(base["security"]))
            sec["findings"] = [value]
            self.assertIsInstance(result.validate_result("security", sec), list)
            doc = json.loads(json.dumps(base["documenter"]))
            doc["stale"] = [value]
            self.assertIsInstance(result.validate_result("documenter", doc), list)

    def test_documenter_missing_pr_counts_as_null(self):
        data = {"status": "DONE", "mode": "audit", "changed_files": [], "stale": []}
        self.assertEqual(result.validate_result("documenter", data), [])
        data.update(mode="delta", changed_files=["a.md"])
        self.assertTrue(result.validate_result("documenter", data))


class SecurityDocumenterTest(unittest.TestCase):
    def test_security_valid_fixture(self):
        self.assertEqual(result.validate_result("security", load("security-valid.json")), [])

    def test_security_verdict_must_match_findings(self):
        errors = result.validate_result("security", load("security-invalid-verdict.json"))
        self.assertTrue(any("verdict" in e for e in errors))
        clean_fail = {"status": "DONE", "verdict": "fail", "findings": []}
        self.assertTrue(result.validate_result("security", clean_fail))
        clean_pass = {"status": "DONE", "verdict": "pass", "findings": []}
        self.assertEqual(result.validate_result("security", clean_pass), [])

    def test_security_line_must_be_positive_int(self):
        data = load("security-valid.json")
        data["findings"][0]["line"] = 0
        self.assertTrue(any("line" in e for e in result.validate_result("security", data)))

    def test_documenter_valid_fixture(self):
        self.assertEqual(result.validate_result("documenter", load("documenter-valid.json")), [])

    def test_documenter_delta_requires_pr(self):
        errors = result.validate_result("documenter", load("documenter-invalid-delta-no-pr.json"))
        self.assertTrue(any("pr" in e for e in errors))

    def test_documenter_audit_has_no_changed_files(self):
        data = {"status": "DONE", "mode": "audit", "changed_files": ["a.md"], "stale": [], "pr": None}
        self.assertTrue(result.validate_result("documenter", data))
        data["changed_files"] = []
        self.assertEqual(result.validate_result("documenter", data), [])


class ContractTest(unittest.TestCase):
    def setUp(self):
        self.policy = result.result_contract(kind="planner")

    def test_contract_denies_final_without_block(self):
        decision = self.policy(response("all done", True))
        self.assertEqual(decision["result"], "DENY")
        self.assertTrue(decision["reason"].startswith("agent-stack: "))

    def test_contract_abstains_without_block_when_finality_unknown(self):
        self.assertIsNone(self.policy(response("all done")))

    def test_contract_abstains_intermediate(self):
        self.assertIsNone(self.policy(response("thinking", False)))
        self.assertIsNone(self.policy(response(fenced({"status": "bad"}), False)))

    def test_contract_abstains_outside_response(self):
        self.assertIsNone(self.policy(tool_call_event("Bash")))

    def test_contract_allows_valid(self):
        decision = self.policy(response(fenced(load("planner-valid.json")), True))
        self.assertEqual(decision["result"], "ALLOW")

    def test_contract_denies_invalid_listing_first_five(self):
        decision = self.policy(response(fenced({"status": "DONE"}), True))
        self.assertEqual(decision["result"], "DENY")
        self.assertLessEqual(decision["reason"].count(";"), 4)

    def test_contract_reads_content_blocks(self):
        event = {
            "type": "response",
            "target": "agent",
            "session_state": {},
            "data": {"content": [{"type": "text", "text": fenced(load("planner-valid.json"))}]},
            "context": {"turn_final": True},
        }
        self.assertEqual(self.policy(event)["result"], "ALLOW")

    def test_contract_rejects_unknown_kind(self):
        with self.assertRaises(ValueError):
            result.result_contract(kind="coder")

    def test_contract_never_asks(self):
        for event in EVENTS:
            assert_not_ask(self, self.policy(event))


class CliTest(unittest.TestCase):
    def run_cli(self, kind, text):
        proc = subprocess.run(
            [sys.executable, "-m", "agent_stack_policies.result", kind],
            input=text, capture_output=True, text=True, cwd=ROOT,
        )
        return proc.returncode, proc.stdout

    def test_cli_exit_codes(self):
        self.assertEqual(self.run_cli("planner", fenced(load("planner-valid.json")))[0], 0)
        code, out = self.run_cli("planner", fenced(load("planner-invalid-ears.json")))
        self.assertEqual(code, 1)
        self.assertIn("EARS", out)
        self.assertEqual(self.run_cli("planner", "no block")[0], 2)

    def test_main_in_process(self):
        stdin = io.StringIO(fenced(load("security-valid.json")))
        with contextlib.redirect_stdout(io.StringIO()):
            old, sys.stdin = sys.stdin, stdin
            try:
                self.assertEqual(result.main(["security"]), 0)
            finally:
                sys.stdin = old
