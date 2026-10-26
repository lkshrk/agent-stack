import unittest

from agent_stack_policies import spawn
from agent_stack_policies.spawn import COLLECT_STATE_KEY, DISPATCH_STATE_KEY, awaiting, max_dispatches_per_session

from . import assert_not_ask, tool_call_event, tool_result_event

EVENTS = [
    tool_call_event("sys_session_send", {"session_id": "s1"}, {DISPATCH_STATE_KEY: 0}),
    tool_call_event("sys_session_send", {}, {DISPATCH_STATE_KEY: 9}),
    tool_call_event("sys_session_send"),
    tool_call_event("bash", {"command": "ls"}, {}),
    tool_call_event("sys_read_inbox", {}, {DISPATCH_STATE_KEY: 1}),
    tool_result_event("sys_read_inbox", {"result": "x"}, {DISPATCH_STATE_KEY: 1}),
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

    def test_collections_count_only_critic_replies(self):
        policy = max_dispatches_per_session(limit=3, collect_tools=["sys_read_inbox"], collect_from="critic")
        reply = "[System: sub-agent task t-1 completed \u2014 critic:plan-critic returned: {\"pass\": false}]"
        self.assertIsNone(policy(tool_call_event("sys_read_inbox", {}, {DISPATCH_STATE_KEY: 1})))
        for data in ({"result": reply}, reply):
            decision = policy(tool_result_event("sys_read_inbox", data, {DISPATCH_STATE_KEY: 1}))
            self.assertEqual(decision["result"], "ALLOW")
            self.assertEqual(decision["state_updates"], [{"key": COLLECT_STATE_KEY, "action": "increment", "value": 1}])
        for text in (
            "(inbox empty)",
            "[System: sub-agent task t-1 failed \u2014 critic:plan-critic error: boom]",
            "[System: sub-agent task t-2 completed \u2014 claude_code:fix-x returned: done]",
            "[System: sub-agent task t-1 completed \u2014 critic:plan-critic produced no output]",
        ):
            with self.subTest(text=text):
                self.assertIsNone(policy(tool_result_event("sys_read_inbox", {"result": text}, {DISPATCH_STATE_KEY: 1})))
        self.assertIsNone(policy(tool_result_event("sys_os_read", {"result": reply}, {DISPATCH_STATE_KEY: 1})))
        self.assertEqual(
            policy(tool_result_event("omnigent__sys_read_inbox", {"result": reply}, {DISPATCH_STATE_KEY: 1}))["result"], "ALLOW"
        )

    def test_collections_capped_at_dispatches(self):
        policy = max_dispatches_per_session(limit=3, collect_tools=["sys_read_inbox"], collect_from="critic")
        reply = "[System: sub-agent task t-1 completed \u2014 critic:plan-critic returned: x]"
        twice = reply + "\n" + reply.replace("t-1", "t-2")
        decision = policy(tool_result_event("sys_read_inbox", {"result": twice}, {DISPATCH_STATE_KEY: 2, COLLECT_STATE_KEY: 1}))
        self.assertEqual(decision["state_updates"], [{"key": COLLECT_STATE_KEY, "action": "increment", "value": 1}])
        state = {DISPATCH_STATE_KEY: 1, COLLECT_STATE_KEY: 1}
        self.assertIsNone(policy(tool_result_event("sys_read_inbox", {"result": reply}, state)))

    def test_awaiting(self):
        self.assertFalse(awaiting({}))
        self.assertTrue(awaiting({DISPATCH_STATE_KEY: 1}))
        self.assertFalse(awaiting({DISPATCH_STATE_KEY: 1, COLLECT_STATE_KEY: 1}))
        self.assertTrue(awaiting({DISPATCH_STATE_KEY: 2, COLLECT_STATE_KEY: 1}))
        self.assertFalse(awaiting({DISPATCH_STATE_KEY: "x"}))
