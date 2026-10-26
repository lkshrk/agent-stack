import unittest

from agent_stack_policies import spawn
from agent_stack_policies.spawn import DISPATCH_STATE_KEY, max_dispatches_per_session

from . import assert_not_ask, tool_call_event

EVENTS = [
    tool_call_event("sys_session_send", {"session_id": "s1"}, {DISPATCH_STATE_KEY: 0}),
    tool_call_event("sys_session_send", {}, {DISPATCH_STATE_KEY: 9}),
    tool_call_event("sys_session_send"),
    tool_call_event("bash", {"command": "ls"}, {}),
    {"type": "other"},
]


class SpawnTest(unittest.TestCase):
    def test_dispatch_counts_with_state_update(self):
        policy = max_dispatches_per_session(limit=3)
        decision = policy(tool_call_event("sys_session_send", {}, {DISPATCH_STATE_KEY: 2}))
        self.assertEqual(decision["result"], "ALLOW")
        self.assertEqual(
            decision["state_updates"],
            [{"key": DISPATCH_STATE_KEY, "action": "increment", "value": 1}],
        )

    def test_dispatch_missing_state_counts_from_zero(self):
        policy = max_dispatches_per_session(limit=1)
        self.assertEqual(policy(tool_call_event("sys_session_send"))["result"], "ALLOW")

    def test_dispatch_at_limit_denies(self):
        policy = max_dispatches_per_session(limit=3)
        decision = policy(tool_call_event("sys_session_send", {}, {DISPATCH_STATE_KEY: 3}))
        self.assertEqual(decision["result"], "DENY")
        self.assertTrue(decision["reason"].startswith("agent-stack: "))
        self.assertIn("dispatch cap", decision["reason"])

    def test_dispatch_ignores_other_tools(self):
        policy = max_dispatches_per_session(limit=1)
        self.assertIsNone(policy(tool_call_event("bash", {}, {DISPATCH_STATE_KEY: 5})))
        self.assertIsNone(policy({"type": "other"}))

    def test_custom_dispatch_tools(self):
        policy = max_dispatches_per_session(limit=1, dispatch_tools=["spawn"])
        self.assertEqual(policy(tool_call_event("spawn"))["result"], "ALLOW")
        self.assertIsNone(policy(tool_call_event("sys_session_send")))

    def test_invalid_limit(self):
        with self.assertRaises(ValueError):
            max_dispatches_per_session(limit=0)

    def test_no_ask(self):
        policy = max_dispatches_per_session(limit=3)
        for event in EVENTS:
            assert_not_ask(self, policy(event))

    def test_abstains_on_agent_start_probe(self):
        probe = {**tool_call_event("sys_agent_start"), "session_state": {DISPATCH_STATE_KEY: 99}}
        self.assertIsNone(max_dispatches_per_session(limit=1, dispatch_tools=["sys_agent_start"])(probe))
