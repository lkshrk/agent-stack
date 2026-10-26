import unittest

from agent_stack_policies.merge import no_merge

from . import tool_call_event

EVENTS = [
    tool_call_event("bash", {"command": "gh pr merge 5 --squash"}),
    tool_call_event("bash", {"command": "git push -u origin feat/x"}),
    tool_call_event("bash", {"command": "git push --force origin feat/x"}),
    tool_call_event("bash", {"command": "echo `date` push"}),
    tool_call_event("bash", {"command": 'echo "unterminated push'}),
    tool_call_event("bash", {}),
    tool_call_event("mcp__github__merge_pull_request", {"number": 1}),
    tool_call_event("Read", {"file_path": "a"}),
    {"type": "tool_call", "data": {"name": "bash", "arguments": "{bad"}},
    {"type": "other"},
]


def run(command, **kwargs):
    return no_merge(**kwargs)(tool_call_event("bash", {"command": command}))


class NoMergeTest(unittest.TestCase):
    def assertDenied(self, command, text=None, **kwargs):
        decision = run(command, **kwargs)
        self.assertIsNotNone(decision, command)
        self.assertEqual(decision["result"], "DENY", command)
        self.assertTrue(decision["reason"].startswith("agent-stack: "))
        if text:
            self.assertIn(text, decision["reason"], command)

    def test_no_merge_gh_pr_merge(self):
        self.assertDenied("gh pr merge 5 --squash")
        self.assertDenied("gh pr merge")
        self.assertDenied("cd x && gh pr merge 5")
        self.assertDenied("gh -R o/r pr merge 5")

    def test_no_merge_gh_api_put(self):
        self.assertDenied("gh api -X PUT repos/o/r/pulls/5/merge")
        self.assertDenied("gh api -X POST repos/o/r/merges -f base=main")

    def test_no_merge_gh_api_read_allowed(self):
        self.assertEqual(run("gh api repos/o/r/pulls/5")["result"], "ALLOW")

    def test_no_merge_push_main(self):
        self.assertDenied("git push origin HEAD:main")
        self.assertDenied("git push origin main")
        self.assertDenied("git push origin refs/heads/master")
        self.assertDenied("git push origin HEAD:refs/heads/main")
        self.assertDenied("git push origin --delete main")
        self.assertDenied("git push origin :main")

    def test_no_merge_custom_protected_branches(self):
        self.assertDenied("git push origin release", protected_branches=["release"])
        self.assertEqual(run("git push origin main", protected_branches=["release"])["result"], "ALLOW")

    def test_no_merge_feature_push_allowed(self):
        self.assertEqual(run("git push -u origin feat/x")["result"], "ALLOW")
        self.assertEqual(run("git push origin HEAD:feat/x")["result"], "ALLOW")
        self.assertEqual(run("git push")["result"], "ALLOW")

    def test_no_merge_force_push_denied(self):
        for command in (
            "git push --force origin feat/x",
            "git push -f origin feat/x",
            "git push --force-with-lease origin feat/x",
            "git push --force-with-lease=feat/x origin feat/x",
            "git push --force-if-includes origin feat/x",
            "git push origin +feat/x",
            "git push origin +HEAD:feat/x",
        ):
            self.assertDenied(command, "force-push")

    def test_no_merge_substitution_denied(self):
        self.assertDenied("git push origin `echo main`")
        self.assertDenied("git push origin $(echo main)")
        self.assertDenied("gh pr $(echo merge) 5")

    def test_no_merge_substitution_unrelated_allowed(self):
        self.assertEqual(run("echo $(date)")["result"], "ALLOW")

    def test_no_merge_nested_shell_denied(self):
        self.assertDenied("bash -c 'gh pr merge 5'")
        self.assertDenied("sh -c \"git push origin main\"")

    def test_no_merge_unparseable_statement_denied(self):
        self.assertDenied('git push origin "main')

    def test_no_merge_unrelated_allowed(self):
        self.assertEqual(run("pytest -q && git status")["result"], "ALLOW")

    def test_no_merge_arguments_unparseable_denied(self):
        event = {"type": "tool_call", "data": {"name": "bash", "arguments": "{bad"}}
        self.assertEqual(no_merge()(event)["result"], "DENY")

    def test_no_merge_mcp_merge_tool(self):
        decision = no_merge()(tool_call_event("mcp__github__merge_pull_request", {"number": 1}))
        self.assertEqual(decision["result"], "DENY")

    def test_no_merge_other_tools_abstain(self):
        self.assertIsNone(no_merge()(tool_call_event("Read", {"file_path": "a"})))
        self.assertIsNone(no_merge()({"type": "other"}))


class QuoteAwareMergeTest(unittest.TestCase):
    def test_quoted_text_allowed(self):
        for command in (
            'git commit -m "fix: a; b"',
            "git commit -m \"don't push yet\"",
            'gh pr create --title x --body "Don\'t merge until CI is green"',
            'grep -E "merge|push" log',
            "git commit -m \"$(cat <<'EOF'\nIt's done\nEOF\n)\"",
        ):
            self.assertEqual(run(command)["result"], "ALLOW", command)

    def test_newline_separates_statements(self):
        self.assertEqual(run("echo a\ngh pr merge 5")["result"], "DENY")

    def test_nested_shell_login_flag(self):
        self.assertEqual(run("bash -lc 'git push origin main'")["result"], "DENY")
