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

    def test_abstains_on_agent_start_probe(self):
        self.assertIsNone(deny_tools(patterns=["*"])(tool_call_event("sys_agent_start")))


class GhApiMutatesTest(unittest.TestCase):
    def test_last_method_wins_and_bodies_are_writes(self):
        from agent_stack_policies.tools import gh_api_mutates

        writes = (
            ["-X", "GET", "-X", "POST", "repos/o/r/issues", "-f", "title=x"],
            ["-X", "GET", "-X", "POST", "repos/o/r/issues"],
            ["--method=GET", "--method=POST", "repos/o/r/issues"],
            ["--method", "GET", "--method", "DELETE", "repos/o/r"],
            ["-XGET", "-XPOST", "repos/o/r/issues"],
            ["-X=GET", "-X=PATCH", "repos/o/r"],
            ["-iXPOST", "repos/o/r/issues"],
            ["-iX", "POST", "repos/o/r/issues"],
            ["repos/o/r/issues", "-f", "title=x"],
            ["repos/o/r/issues", "-ftitle=x"],
            ["repos/o/r/issues", "-F", "title=x"],
            ["repos/o/r/issues", "--field", "title=x"],
            ["repos/o/r/issues", "--raw-field=title=x"],
            ["repos/o/r/issues", "--input", "body.json"],
            ["-X", "GET", "repos/o/r/issues", "-f", "q=x"],
            ["graphql", "-f", "query=mutation{x}"],
            ["-X"],
            ["--method"],
            ["-Z", "repos/o/r"],
        )
        for args in writes:
            with self.subTest(args=args):
                self.assertTrue(gh_api_mutates(args))
        reads = (
            ["repos/o/r/issues"],
            ["-X", "GET", "repos/o/r/issues"],
            ["-X", "POST", "-X", "GET", "repos/o/r/issues"],
            ["--method=GET", "repos/o/r"],
            ["-XGET", "repos/o/r"],
            ["repos/o/r/pulls", "--jq", ".[].number", "--paginate"],
            ["repos/o/r/pulls", "-q", ".[].title", "-H", "Accept: application/json"],
        )
        for args in reads:
            with self.subTest(args=args):
                self.assertFalse(gh_api_mutates(args))
