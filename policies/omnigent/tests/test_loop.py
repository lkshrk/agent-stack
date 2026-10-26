import unittest

from agent_stack_policies.loop import LOOP_STATE_KEY, _call_hash, detect_loop

from . import assert_not_ask, tool_call_event

ARGS = {"command": "make test"}
H = _call_hash("bash", ARGS)

EVENTS = [
    tool_call_event("bash", ARGS),
    tool_call_event("bash", ARGS, {LOOP_STATE_KEY: [H, H]}),
    tool_call_event("bash", ARGS, {LOOP_STATE_KEY: "garbage"}),
    tool_call_event("sys_agent_start"),
    {"type": "other"},
]


class DetectLoopTest(unittest.TestCase):
    def test_first_call_records_state(self):
        decision = detect_loop()(tool_call_event("bash", ARGS))
        self.assertEqual(decision["result"], "ALLOW")
        self.assertEqual(decision["state_updates"], [{"key": LOOP_STATE_KEY, "action": "set", "value": [H]}])

    def test_below_threshold_allows(self):
        decision = detect_loop(threshold=3)(tool_call_event("bash", ARGS, {LOOP_STATE_KEY: [H]}))
        self.assertEqual(decision["result"], "ALLOW")
        self.assertEqual(decision["state_updates"][0]["value"], [H, H])

    def test_threshold_denies(self):
        decision = detect_loop(threshold=3)(tool_call_event("bash", ARGS, {LOOP_STATE_KEY: [H, "x", H]}))
        self.assertEqual(decision["result"], "DENY")
        self.assertTrue(decision["reason"].startswith("agent-stack: loop: "))
        self.assertNotIn("state_updates", decision)

    def test_window_bounds_history(self):
        stale = [H, H] + ["x"] * 8
        decision = detect_loop(window=10, threshold=3)(tool_call_event("bash", ARGS, {LOOP_STATE_KEY: stale}))
        self.assertEqual(decision["result"], "ALLOW")
        self.assertEqual(len(decision["state_updates"][0]["value"]), 10)
        self.assertEqual(decision["state_updates"][0]["value"].count(H), 2)

    def test_different_arguments_do_not_count(self):
        decision = detect_loop()(tool_call_event("bash", {"command": "ls"}, {LOOP_STATE_KEY: [H, H]}))
        self.assertEqual(decision["result"], "ALLOW")

    def test_malformed_state_resets(self):
        decision = detect_loop()(tool_call_event("bash", ARGS, {LOOP_STATE_KEY: "garbage"}))
        self.assertEqual(decision["state_updates"][0]["value"], [H])

    def test_abstains_on_probe_and_non_tool_events(self):
        policy = detect_loop()
        self.assertIsNone(policy(tool_call_event("sys_agent_start", {}, {LOOP_STATE_KEY: [H, H, H]})))
        self.assertIsNone(policy({"type": "other"}))

    def test_state_key_is_not_reserved(self):
        self.assertFalse(LOOP_STATE_KEY.startswith("_policy_"))

    def test_invalid_params(self):
        for kwargs in ({"threshold": 1}, {"window": 2, "threshold": 3}):
            with self.subTest(**kwargs), self.assertRaises(ValueError):
                detect_loop(**kwargs)

    def test_no_ask(self):
        policy = detect_loop()
        for event in EVENTS:
            assert_not_ask(self, policy(event))
