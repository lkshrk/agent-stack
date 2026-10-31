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

    def test_extract_reads_only_the_last_block(self):
        for last in ("[1]", "{oops"):
            text = f"{fenced({'n': 1})}\n```json\n{last}\n```"
            self.assertIsNone(result.extract_result(text), last)

    def test_extract_none_without_block(self):
        self.assertIsNone(result.extract_result("no block here"))

    def test_extract_fence_inside_string_value(self):
        data = {"design": "Use:\n```python\nx = 1\n```\nthen ```json\n{}\n``` again"}
        self.assertEqual(result.extract_result(f"Plan.\n{fenced(data)}"), data)

    def test_extract_trailing_prose_fence(self):
        text = f"{fenced({'n': 1})}\nRun it with:\n```sh\nmake test\n```\n"
        self.assertEqual(result.extract_result(text), {"n": 1})

    def test_extract_opener_case_insensitive(self):
        self.assertEqual(result.extract_result('```JSON\n{"n": 3}\n```'), {"n": 3})

    def test_extract_requires_closing_fence_after_object(self):
        self.assertIsNone(result.extract_result('```json\n{"n": 1} trailing\n```'))
        self.assertIsNone(result.extract_result('```json\n{"n": 1}'))


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

    def test_planner_criterion_id_names_its_child(self):
        planner = load("planner-valid.json")
        for bad in ("AC-1", "b-1", "a-0", "a-x", "a1"):
            planner["children"][0]["criteria"][0]["id"] = bad
            planner["critic"]["coverage"][0]["criterion"] = bad
            errors = result.validate_result("planner", planner)
            self.assertTrue(any("<child-key>-<n>" in e for e in errors), (bad, errors))
        planner["children"][0]["criteria"][0]["id"] = "a-12"
        planner["critic"]["coverage"][0]["criterion"] = "a-12"
        self.assertEqual(result.validate_result("planner", planner), [])

    def test_planner_dependent_children_may_share_files(self):
        planner = load("planner-valid.json")
        self.assertIn("src/a.py", planner["children"][1]["files"])
        self.assertEqual(result.validate_result("planner", planner), [])

    def test_planner_unknown_blocked_by(self):
        planner = load("planner-valid.json")
        planner["children"][0]["blocked_by"] = ["zzz"]
        self.assertTrue(any("unknown" in e for e in result.validate_result("planner", planner)))

    def test_planner_requires_gate(self):
        planner = load("planner-valid.json")
        for bad in (None, "gate:merge", "plan", 1, ["gate:plan"]):
            planner["gate"] = bad
            self.assertTrue(any(e.startswith("gate must be") for e in result.validate_result("planner", planner)), bad)
        del planner["gate"]
        self.assertTrue(any(e.startswith("gate must be") for e in result.validate_result("planner", planner)))
        for gate in result.GATES:
            planner["gate"] = gate
            self.assertEqual(result.validate_result("planner", planner), [], gate)

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


def stub_gap(gid="gap-1", gap_class="internal", location="src/a.py:12"):
    note = f"Open question ({gid}): x or y." if isinstance(gid, str) else "Open question."
    return {"id": gid, "class": gap_class, "question": "Name it x or y?", "stub": note, "location": location}


def waiting_gap(gid="gap-1", gap_class="behaviour"):
    return {"id": gid, "class": gap_class, "question": "Retry or drop on timeout?"}


BLOCKED_DECISION = {"class": "decision", "question": "Retry or drop on timeout?"}


class NoAssumptionsTest(unittest.TestCase):
    def results(self):
        return {
            "planner": load("planner-valid.json"),
            "security": load("security-valid.json"),
            "documenter": load("documenter-valid.json"),
        }

    def test_fixtures(self):
        cases = {
            "planner-valid-blocked-gap.json": ("planner", None),
            "security-valid-blocked-gap.json": ("security", None),
            "documenter-valid-stub-gap.json": ("documenter", None),
            "planner-invalid-done-assumptions.json": ("planner", "assumptions must be empty for DONE"),
            "security-invalid-done-assumptions.json": ("security", "assumptions must be empty for DONE"),
            "planner-invalid-stub.json": ("planner", "in a plan waits"),
            "security-invalid-stub-gap.json": ("security", "in a security review waits"),
            "security-invalid-done-gap.json": ("security", "gaps must be empty for DONE"),
            "security-invalid-waiting-gap.json": ("security", "of class security"),
        }
        for name, (kind, expected) in cases.items():
            errors = result.validate_result(kind, load(name))
            if expected is None:
                self.assertEqual(errors, [], name)
            else:
                self.assertTrue(any(expected in e for e in errors), (name, errors))

    def test_done_requires_empty_assumptions_for_every_kind(self):
        for kind, data in self.results().items():
            self.assertEqual(result.validate_result(kind, {**data, "assumptions": []}), [], kind)
            self.assertNotIn("assumptions", data)
            errors = result.validate_result(kind, {**data, "assumptions": ["x is never null"]})
            self.assertTrue(any("empty for DONE" in e for e in errors), (kind, errors))
            errors = result.validate_result(kind, {**data, "gaps": [stub_gap()], "assumptions": ["gap-1: x"]})
            self.assertTrue(any("assumptions must be empty for DONE" in e for e in errors), (kind, errors))

    def test_done_requires_empty_gaps_for_every_kind(self):
        for kind, data in self.results().items():
            self.assertEqual(result.validate_result(kind, {**data, "gaps": []}), [], kind)
            for gap in (stub_gap(), stub_gap(gap_class="cosmetic"), waiting_gap()):
                errors = result.validate_result(kind, {**data, "gaps": [gap]})
                self.assertIn("gaps must be empty for DONE: a gap makes the result DONE_WITH_CONCERNS at best", errors, (kind, gap))

    def test_assumptions_must_be_strings(self):
        data = load("security-valid.json")
        for bad in ("x", {"a": 1}, [1], [""], [" "], [None], [["gap-1"]]):
            errors = result.validate_result("security", {**data, "assumptions": bad})
            self.assertIn("assumptions must be a list of non-empty strings", errors, bad)

    def test_concerns_assumptions_name_a_gap(self):
        for kind in ("documenter",):
            data = self.results()[kind]
            data.update(status="DONE_WITH_CONCERNS", gaps=[stub_gap("gap-1"), stub_gap("gap-12")])
            for good in (["gap-1: assumed x"], ["gap-12 assumed y", "gap-1"], []):
                self.assertEqual(result.validate_result(kind, {**data, "assumptions": good}), [], (kind, good))
            for bad in ("assumed x", "gap-2: unknown id", "see gap-1", "gap-1x: no", "gap-120: no", "gap-1-a"):
                errors = result.validate_result(kind, {**data, "assumptions": [bad]})
                self.assertTrue(any("must start with a gap id" in e for e in errors), (kind, bad, errors))

    def test_blocked_assumptions_name_a_gap(self):
        blocked = {"status": "BLOCKED", "blocked": BLOCKED_DECISION, "gaps": [waiting_gap()]}
        self.assertEqual(result.validate_result("security", {**blocked, "assumptions": ["gap-1: retries"]}), [])
        errors = result.validate_result("security", {**blocked, "assumptions": ["retries are safe"]})
        self.assertTrue(any("must start with a gap id" in e for e in errors), errors)

    def test_stub_gap_needs_stub_and_location(self):
        data = {**load("documenter-valid.json"), "status": "DONE_WITH_CONCERNS"}
        for location in ("src/a.py:1", "docs/orders.md:120", "Makefile:3"):
            gap = stub_gap(location=location)
            self.assertEqual(result.validate_result("documenter", {**data, "gaps": [gap]}), [], location)
        bad = (None, "", "src/a.py", "Makefile", "src/a.py:0", "src/a.py:x", "src/a.py:", "a b.py:1", ":12", 12, "a:1:2")
        for location in bad:
            errors = result.validate_result("documenter", {**data, "gaps": [stub_gap(location=location)]})
            self.assertTrue(any("location must be path:line" in e for e in errors), (location, errors))
        for stub in (None, "", " ", 1):
            gap = {**stub_gap(), "stub": stub}
            errors = result.validate_result("documenter", {**data, "gaps": [gap]})
            self.assertTrue(any("stub must say what was stubbed" in e for e in errors), (stub, errors))
        for stub in ("Used the term x.", "Open question (gap-12): x", "Open question (gap-1x).", "see xgap-1", "gap-1-b"):
            gap = {**stub_gap(), "stub": stub}
            errors = result.validate_result("documenter", {**data, "gaps": [gap]})
            self.assertIn("gap-1.stub must quote the visible note naming gap-1", errors, stub)
        for stub in ("gap-1", "Note gap-1: x", "(gap-1)"):
            self.assertEqual(result.validate_result("documenter", {**data, "gaps": [{**stub_gap(), "stub": stub}]}), [], stub)
        for gap_class in result.STUB_CLASSES:
            gap = stub_gap(gap_class=gap_class)
            self.assertEqual(result.validate_result("documenter", {**data, "gaps": [gap]}), [], gap_class)

    def test_waiting_classes_require_blocked_decision(self):
        waiting = [c for c in result.GAP_CLASSES if c not in result.STUB_CLASSES]
        self.assertEqual(waiting, ["behaviour", "data", "security", "public_api", "cost"])
        for kind, data in self.results().items():
            for gap_class in waiting:
                gap = waiting_gap(gap_class=gap_class)
                for status in ("DONE", "DONE_WITH_CONCERNS"):
                    errors = result.validate_result(kind, {**data, "status": status, "gaps": [gap]})
                    self.assertTrue(any("must be BLOCKED with blocked.class decision" in e for e in errors), (kind, gap_class, status))
                blocked = {"status": "BLOCKED", "blocked": BLOCKED_DECISION, "gaps": [gap]}
                self.assertEqual(result.validate_result(kind, blocked), [], (kind, gap_class))
                self.assertEqual(result.validate_result(kind, {**blocked, "gaps": [{**gap, "stub": None, "location": None}]}), [])
                permission = {**blocked, "blocked": {"class": "permission", "question": "q?"}}
                errors = result.validate_result(kind, permission)
                self.assertTrue(any("blocked.class decision" in e for e in errors), (kind, gap_class))
                for field in ("stub", "location"):
                    errors = result.validate_result(kind, {**blocked, "gaps": [{**gap, field: "src/a.py"}]})
                    self.assertTrue(any(f"{field} must be null" in e for e in errors), (kind, gap_class, field))

    def test_planner_never_stubs(self):
        data = load("planner-valid.json")
        data.update(status="DONE_WITH_CONCERNS", assumptions=["gap-1: x"])
        blocked = {"status": "BLOCKED", "blocked": BLOCKED_DECISION}
        for gate in (*result.GATES, None):
            for gap_class in result.STUB_CLASSES:
                stubbed = {**data, "gate": gate, "gaps": [stub_gap(gap_class=gap_class)]}
                errors = result.validate_result("planner", stubbed)
                self.assertTrue(any("in a plan waits for the owner" in e for e in errors), (gate, errors))
                self.assertTrue(any("must be BLOCKED" in e for e in errors), (gate, errors))
                bare = {**data, "gate": gate, "gaps": [waiting_gap(gap_class=gap_class)]}
                self.assertTrue(any("must be BLOCKED" in e for e in result.validate_result("planner", bare)), gate)
                asked = {**blocked, "gate": gate, "gaps": [waiting_gap(gap_class=gap_class)]}
                self.assertEqual(result.validate_result("planner", asked), [], (gate, gap_class))
                errors = result.validate_result("planner", {**asked, "gaps": [stub_gap(gap_class=gap_class)]})
                self.assertTrue(any("in a plan waits for the owner" in e for e in errors), (gate, errors))
        self.assertTrue(result.validate_result("planner", {**blocked, "gate": "gate:merge"}))

    def test_security_and_audit_never_stub(self):
        security = {**load("security-valid.json"), "status": "DONE_WITH_CONCERNS"}
        audit = {"status": "DONE_WITH_CONCERNS", "mode": "audit", "changed_files": [], "stale": [], "pr": None}
        blocked = {"status": "BLOCKED", "blocked": BLOCKED_DECISION}
        cases = [("security", security, blocked), ("documenter", audit, {**blocked, "mode": "audit"}), ("documenter", None, blocked)]
        for kind, data, asked in cases:
            for gap_class in result.STUB_CLASSES:
                if data is not None:
                    errors = result.validate_result(kind, {**data, "gaps": [stub_gap(gap_class=gap_class)]})
                    self.assertTrue(any("stub must be null" in e for e in errors), (kind, errors))
                    self.assertTrue(any("must be BLOCKED" in e for e in errors), (kind, errors))
                errors = result.validate_result(kind, {**asked, "gaps": [stub_gap(gap_class=gap_class)]})
                self.assertTrue(any("stub must be null" in e for e in errors), (kind, errors))
                self.assertEqual(result.validate_result(kind, {**asked, "gaps": [waiting_gap(gap_class=gap_class)]}), [], kind)

    def test_gap_ids_unique_and_numbered(self):
        data = {**load("documenter-valid.json"), "status": "DONE_WITH_CONCERNS"}
        for bad in ("gap-0", "gap-x", "gap-01", "GAP-1", "G-1", "gap1", "gap-1 ", 1, None):
            errors = result.validate_result("documenter", {**data, "gaps": [stub_gap(gid=bad)]})
            self.assertTrue(any("id must be gap-<n>" in e for e in errors), (bad, errors))
        errors = result.validate_result("documenter", {**data, "gaps": [stub_gap("gap-1"), stub_gap("gap-1")]})
        self.assertIn("gap id 'gap-1' is duplicated", errors)
        self.assertEqual(result.validate_result("documenter", {**data, "gaps": [stub_gap("gap-1"), stub_gap("gap-2")]}), [])

    def test_gap_needs_class_and_question(self):
        data = {**load("documenter-valid.json"), "status": "DONE_WITH_CONCERNS"}
        for bad in (None, "style", "public API", "Behaviour", 1):
            errors = result.validate_result("documenter", {**data, "gaps": [{**stub_gap(), "class": bad}]})
            self.assertTrue(any(".class must be one of" in e for e in errors), (bad, errors))
        for bad in (None, "", " "):
            errors = result.validate_result("documenter", {**data, "gaps": [{**stub_gap(), "question": bad}]})
            self.assertTrue(any(".question must be" in e for e in errors), (bad, errors))
        for bad in ("x", {"a": 1}, ["x"], [None], [[]]):
            self.assertTrue(result.validate_result("documenter", {**data, "gaps": bad}), bad)

    def test_contract_denies_assumptions_and_waiting_gaps(self):
        policy = result.result_contract(kind="security")
        for name in ("security-invalid-done-assumptions.json", "security-invalid-waiting-gap.json"):
            self.assertEqual(policy(response(fenced(load(name)), True))["result"], "DENY", name)
        self.assertEqual(policy(response(fenced(load("security-valid-blocked-gap.json")), True))["result"], "ALLOW")


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
            for key in [*good, "gaps", "assumptions", "gate"]:
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
        clean_pass = {"status": "DONE", "verdict": "pass", "findings": [], "reviewed": ["a.py"]}
        self.assertEqual(result.validate_result("security", clean_pass), [])

    def test_security_blocked_cannot_carry_a_verdict(self):
        blocked = load("security-valid-blocked-gap.json")
        self.assertNotIn("verdict", blocked)
        self.assertEqual(result.validate_result("security", blocked), [])
        self.assertTrue(result.validate_result("security", {**blocked, "verdict": "pass"}))
        for verdict in ("fail", None):
            with self.subTest(verdict=verdict):
                data = {**blocked, "verdict": verdict}
                if verdict is None:
                    del data["verdict"]
                self.assertEqual(result.validate_result("security", data), [])
        self.assertTrue(result.validate_result("security", {**blocked, "verdict": "approve"}))

    def test_security_pass_needs_reviewed_evidence(self):
        clean = {"status": "DONE", "verdict": "pass", "findings": [], "reviewed": ["src/a.py"]}
        self.assertEqual(result.validate_result("security", clean), [])
        for reviewed in (None, [], [""], "src/a.py", [1]):
            with self.subTest(reviewed=reviewed):
                data = {**clean, "reviewed": reviewed}
                if reviewed is None:
                    del data["reviewed"]
                self.assertTrue(any("reviewed" in e for e in result.validate_result("security", data)))
        failing = {**load("security-valid.json")}
        self.assertEqual(result.validate_result("security", failing), [])

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


    def test_documenter_audit_gaps_fixture(self):
        self.assertEqual(result.validate_result("documenter", load("documenter-valid-audit-gaps.json")), [])

    def test_documenter_blocked_keeps_doc_gaps(self):
        data = load("documenter-valid-audit-gaps.json")
        data.update(status="BLOCKED", blocked={"class": "decision", "question": "Document module x or drop it?"})
        self.assertEqual(result.validate_result("documenter", data), [])

    def test_documenter_doc_gaps(self):
        gap = {"file": "docs/setup.md", "reason": "ORDERS_QUEUE is read in settings.py and not documented."}
        data = {"status": "DONE_WITH_CONCERNS", "mode": "audit", "changed_files": [], "stale": [], "doc_gaps": [gap], "pr": None}
        self.assertEqual(result.validate_result("documenter", data), [])
        data["status"] = "DONE"
        self.assertTrue(any("doc_gaps" in e for e in result.validate_result("documenter", data)))
        data.update(status="DONE", doc_gaps=[])
        self.assertEqual(result.validate_result("documenter", data), [])
        for bad in ([{"file": "docs/a.md"}], ["x"], {"a": 1}, "x"):
            data.update(status="DONE_WITH_CONCERNS", doc_gaps=bad)
            self.assertTrue(result.validate_result("documenter", data), bad)

    def test_documenter_legacy_audit_gaps_rejected(self):
        errors = result.validate_result("documenter", load("documenter-invalid-legacy-gaps.json"))
        self.assertTrue(any(e.startswith("gaps[0].id") for e in errors), errors)


class NonObjectTest(unittest.TestCase):
    def test_validate_result_never_raises_on_junk(self):
        junk = [None, 0, 1.5, True, "s", [], [1], ({"status": "DONE"},), b"x", object()]
        for kind in result.KINDS:
            for value in junk:
                self.assertEqual(result.validate_result(kind, value), ["result must be a JSON object"], (kind, value))
        for kind in (None, 1, ["planner"], {"a": 1}, "coder"):
            self.assertEqual(len(result.validate_result(kind, {"status": "DONE"})), 1, kind)
            self.assertIn("unknown kind", result.validate_result(kind, {"status": "DONE"})[0])


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

    def test_contract_waits_while_a_dispatch_is_uncollected(self):
        policy = result.result_contract(kind="planner", await_dispatches=True)
        waiting = {**response("Sent the draft to the critic.", True), "session_state": {"agent_stack_dispatches": 1}}
        self.assertIsNone(policy(waiting))
        self.assertEqual(self.policy(waiting)["result"], "DENY")
        collected = {**waiting, "session_state": {"agent_stack_dispatches": 1, "agent_stack_collections": 1}}
        self.assertEqual(policy(collected)["result"], "DENY")
        self.assertEqual(policy(response("all done", True))["result"], "DENY")
        invalid = {**response(fenced({"status": "DONE"}), True), "session_state": {"agent_stack_dispatches": 1}}
        self.assertEqual(policy(invalid)["result"], "DENY")

    def test_contract_requires_the_critic_for_a_plan(self):
        policy = result.result_contract(kind="planner", await_dispatches=True)
        plan = fenced(load("planner-valid.json"))

        def final(state, text=plan):
            return {**response(text, True), "session_state": state}

        consulted = {"agent_stack_dispatches": 2, "agent_stack_collections": 2}
        self.assertEqual(policy(final(consulted))["result"], "ALLOW")
        self.assertEqual(policy(final({}))["result"], "DENY")
        self.assertEqual(policy(final({"agent_stack_dispatches": 2, "agent_stack_collections": 1}))["result"], "DENY")
        self.assertEqual(policy(final({"agent_stack_dispatches": 1, "agent_stack_collections": 1}))["result"], "DENY")
        self.assertEqual(policy(final({"agent_stack_dispatches": 3, "agent_stack_collections": 3}))["result"], "DENY")
        self.assertIn("critic", policy(final({}))["reason"])
        concerns = load("planner-valid.json") | {"status": "DONE_WITH_CONCERNS"}
        self.assertEqual(policy(final({}, fenced(concerns)))["result"], "DENY")
        blocked = {"status": "BLOCKED", "blocked": {"class": "decision", "question": "A or B?"}}
        self.assertEqual(policy(final({}, fenced(blocked)))["result"], "ALLOW")
        self.assertEqual(policy({**response(plan), "session_state": {}})["result"], "DENY")
        self.assertEqual(result.result_contract(kind="planner")(final({}))["result"], "ALLOW")

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


def nested(depth, leaf="x"):
    value = leaf
    for _ in range(depth):
        value = [value]
    return value


class DepthTest(unittest.TestCase):
    def test_over_deep_result_is_an_error_not_a_raise(self):
        for depth in (300, 900, 5000):
            data = {"status": "DONE", "verdict": "pass", "findings": [], "extra": nested(depth)}
            errors = result.validate_result("security", data)
            self.assertTrue(any("nest" in e for e in errors), (depth, errors))

    def test_over_deep_planner_files_do_not_raise(self):
        data = load("planner-valid.json")
        data["children"][0]["files"] = [nested(2000)]
        self.assertTrue(result.validate_result("planner", data))

    def test_placeholder_found_within_depth_limit(self):
        data = {"status": "DONE", "verdict": "pass", "findings": [], "extra": nested(20, "TBD")}
        self.assertTrue(any("placeholder" in e for e in result.validate_result("security", data)))

    def test_extract_unparseably_deep_last_block_is_no_result(self):
        deep = "[" * 100000 + "]" * 100000
        text = f"{fenced({'n': 1})}\n```json\n{deep}\n```"
        self.assertIsNone(result.extract_result(text))

    def test_contract_denies_over_deep_block(self):
        data = {"status": "DONE", "verdict": "pass", "findings": [], "extra": nested(500)}
        decision = result.result_contract(kind="security")(response(fenced(data)))
        self.assertEqual(decision["result"], "DENY")


class PollyResultTest(unittest.TestCase):
    def test_polly_valid_fixture(self):
        self.assertEqual(result.validate_result("polly", load("polly-valid.json")), [])

    def test_polly_done_requires_pr(self):
        errors = result.validate_result("polly", load("polly-invalid-no-pr.json"))
        self.assertTrue(any("pr" in e for e in errors), errors)

    def test_polly_pr_must_be_github_pull_url(self):
        errors = result.validate_result("polly", load("polly-invalid-gitlab-pr.json"))
        self.assertTrue(any(e.startswith("pr ") for e in errors), errors)
        data = load("polly-valid.json")
        for bad in ("https://github.com/a/b/pull/0", "https://github.com/a/b/pull/1/files", "http://github.com/a/b/pull/1", 7, None):
            errors = result.validate_result("polly", {**data, "pr": bad})
            self.assertTrue(any(e.startswith("pr ") for e in errors), (bad, errors))

    def test_polly_done_requires_branch(self):
        for bad in ("", " ", None, 3):
            errors = result.validate_result("polly", {**load("polly-valid.json"), "branch": bad})
            self.assertTrue(any("branch" in e for e in errors), (bad, errors))

    def test_polly_done_with_assumptions_rejected(self):
        errors = result.validate_result("polly", load("polly-invalid-done-assumptions.json"))
        self.assertTrue(any("assumptions must be empty for DONE" in e for e in errors), errors)

    def test_polly_gap_without_stub_location_rejected(self):
        errors = result.validate_result("polly", load("polly-invalid-gap-no-location.json"))
        self.assertTrue(any("location must be path:line" in e for e in errors), errors)

    def test_polly_stubbed_gap_accepted(self):
        data = load("polly-invalid-gap-no-location.json")
        data["gaps"][0]["location"] = "src/export.py:12"
        self.assertEqual(result.validate_result("polly", data), [])

    def test_polly_blocked_needs_no_pr(self):
        blocked = {"status": "BLOCKED", "blocked": BLOCKED_DECISION}
        self.assertEqual(result.validate_result("polly", blocked), [])
