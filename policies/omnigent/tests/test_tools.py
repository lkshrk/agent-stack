import unittest

from agent_stack_policies.tools import deny_tools

from . import tool_call_event

EVENTS = [
    tool_call_event("mcp__litellm-tools__linear_app-save_issue", {"title": "x"}),
    tool_call_event("bash", {"command": "ls"}),
    tool_call_event("sys_session_send"),
    {"type": "other"},
]


class DenyToolsTest(unittest.TestCase):
    def test_deny_tools_glob(self):
        policy = deny_tools(patterns=["mcp__*linear_app-*"])
        decision = policy(tool_call_event("mcp__litellm-tools__linear_app-save_issue"))
        self.assertEqual(decision["result"], "DENY")
        self.assertTrue(decision["reason"].startswith("agent-stack: "))
        self.assertIn("tool", decision["reason"])

    def test_deny_tools_unmatched_abstains(self):
        policy = deny_tools(patterns=["mcp__*linear_app-*"])
        self.assertIsNone(policy(tool_call_event("bash", {"command": "ls"})))

    def test_deny_tools_ignores_non_tool_call(self):
        policy = deny_tools(patterns=["*"])
        self.assertIsNone(policy({"type": "other", "data": {"name": "bash"}}))

    def test_deny_tools_is_case_sensitive(self):
        self.assertIsNone(deny_tools(patterns=["Write"])(tool_call_event("write")))
