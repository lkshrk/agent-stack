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
    {"type": "tool_call", "target": "bash", "data": {"name": "bash", "arguments": "{bad"}},
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

    def test_no_merge_short_ref_and_tag_pushes_denied(self):
        for command in (
            "git push origin HEAD:heads/main",
            "git push origin heads/main",
            "git push origin HEAD:heads/master",
            "git push origin +HEAD:heads/main",
            "git push origin HEAD:tags/v1",
            "git push origin HEAD:refs/tags/v1",
            "git push origin refs/tags/v1",
            "git push origin tags/v1",
            "git push origin HEAD:refs/notes/x",
            "git push origin HEAD:refs/heads/refs/heads/main",
            "git push --tags origin",
            "git push origin --tags",
            "git push --follow-tags origin HEAD:feat/x",
            "git push --mirror origin",
            "git push --all origin",
        ):
            self.assertDenied(command)
        self.assertDenied("git push origin HEAD:heads/main", "main")
        self.assertDenied("git push origin HEAD:refs/tags/v1", "tag")

    def test_no_merge_branch_pushes_still_allowed(self):
        for command in ("git push origin HEAD:feat/x", "git push -u origin feat/x", "git push origin refs/heads/feat/x"):
            self.assertEqual(run(command)["result"], "ALLOW", command)

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
        event = {"type": "tool_call", "target": "bash", "data": {"name": "bash", "arguments": "{bad"}}
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


class SubstitutionTest(unittest.TestCase):
    def test_quoted_and_nested_substitution_denied(self):
        for command in (
            'git push origin "$(echo main)"',
            'git push origin "$(git branch --show-current)"',
            "git push origin HEAD:$(git branch --show-current)",
            "gh api -X PUT repos/o/r/pulls/$(echo 5)/merge",
            'echo "$(gh pr merge 5)"',
            'x="$(git push origin main)"',
        ):
            self.assertEqual(run(command)["result"], "DENY", command)

    def test_backslash_newline_joined(self):
        self.assertEqual(run("git push origin \\\nmain")["result"], "DENY")

    def test_other_shells_and_eval(self):
        self.assertEqual(run("dash -c 'git push origin main'")["result"], "DENY")
        self.assertEqual(run("ksh -c 'gh pr merge 5'")["result"], "DENY")
        self.assertEqual(run("eval 'git push origin main'")["result"], "DENY")
        self.assertEqual(run("eval git push origin main")["result"], "DENY")

    def test_abstains_on_agent_start_probe(self):
        self.assertIsNone(no_merge()(tool_call_event("sys_agent_start")))


class FinalReviewMergeTest(unittest.TestCase):
    def test_substitution_reason_names_the_fix(self):
        reason = run("git push -u origin $(git branch --show-current)")["reason"]
        self.assertIn("name the branch literally", reason)

    def test_graphql_merge_mutations_denied(self):
        for command in (
            "gh api graphql -f query='mutation { mergePullRequest(input: {pullRequestId: \"x\"}) { clientMutationId } }'",
            "gh api graphql -F query=@q.graphql -f q='enablePullRequestAutoMerge'",
        ):
            decision = run(command)
            self.assertEqual(decision["result"], "DENY", command)
            self.assertIn("graphql", decision["reason"])
        self.assertEqual(run("gh api graphql -f query='{ viewer { login } }'")["result"], "ALLOW")

    def test_wrappers_resolved(self):
        for command in (
            "timeout 60 git push origin main",
            "setsid git push --force origin feat/x",
            "flock /tmp/l -c 'gh pr merge 5'",
            "script -qc 'git push origin main' /dev/null",
            "parallel ::: 'gh pr merge 5'",
            "env -S 'git push origin main'",
        ):
            self.assertEqual(run(command)["result"], "DENY", command)

    def test_list_and_other_command_shapes(self):
        policy = no_merge()
        self.assertEqual(policy(tool_call_event("shell", {"command": ["bash", "-lc", "gh pr merge 5"]}))["result"], "DENY")
        self.assertEqual(policy(tool_call_event("shell", {"command": ["git", "status"]}))["result"], "ALLOW")
        self.assertEqual(policy(tool_call_event("shell", {"command": {"a": 1}}))["result"], "DENY")


class StdinArgumentsTest(unittest.TestCase):
    def test_xargs_into_push_or_merge_denied(self):
        for command in (
            "echo main | xargs git push origin",
            "echo feat/x | xargs git push origin",
            "echo 5 | xargs gh pr merge",
            "echo 5 | xargs -I{} gh pr merge {}",
            "ls | parallel git push origin",
            "cat <(git push origin main)",
            "diff <(gh pr merge 5) a",
        ):
            with self.subTest(command=command):
                self.assertEqual(run(command)["result"], "DENY")

    def test_xargs_elsewhere_allowed(self):
        for command in ("ls | xargs wc -l", "echo a | xargs git log -1", "cat <(git log -1)"):
            with self.subTest(command=command):
                self.assertEqual(run(command)["result"], "ALLOW")


class VariableTargetTest(unittest.TestCase):
    def test_variable_in_push_or_merge_denied(self):
        for command in (
            "printf -v b %s main; git push origin $b",
            "b=main; git push origin $b",
            'git push origin "$BRANCH"',
            "git push origin HEAD:${B}",
            "gh api -X PUT repos/o/r/pulls/$N/merge",
        ):
            with self.subTest(command=command):
                self.assertEqual(run(command)["result"], "DENY")

    def test_literal_push_allowed(self):
        for command in ("git push -u origin feat/x", "echo $HOME && git push origin feat/x"):
            with self.subTest(command=command):
                self.assertEqual(run(command)["result"], "ALLOW")
