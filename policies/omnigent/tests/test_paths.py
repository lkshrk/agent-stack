import unittest

from agent_stack_policies.paths import glob_match, path_guard

from . import assert_not_ask, tool_call_event

WORKFLOWS = ".github/workflows/**"
UNPARSEABLE = {"type": "tool_call", "data": {"name": "Write", "arguments": "{bad"}}

EVENTS = [
    tool_call_event("Write", {"file_path": ".github/workflows/ci.yml"}),
    tool_call_event("Write", {"file_path": "docs/a.md"}),
    tool_call_event("Write", {}),
    tool_call_event("NotebookEdit", {"notebook_path": "n.ipynb"}),
    tool_call_event("bash", {"command": "echo x > .github/workflows/ci.yml"}),
    tool_call_event("bash", {"command": "pytest -q"}),
    tool_call_event("bash", {"command": 'echo "unterminated'}),
    tool_call_event("bash", {}),
    tool_call_event("Read", {"file_path": "a"}),
    UNPARSEABLE,
    {"type": "other"},
]


class GlobTest(unittest.TestCase):
    def test_glob_double_star(self):
        self.assertTrue(glob_match(".github/workflows/ci.yml", WORKFLOWS))
        self.assertTrue(glob_match(".github/workflows/a/b/ci.yml", WORKFLOWS))
        self.assertTrue(glob_match("a/b/.env", "**/.env"))
        self.assertTrue(glob_match(".env", "**/.env"))

    def test_glob_suffix_match_absolute(self):
        self.assertTrue(
            glob_match("/home/omnigent/work/repo/.github/workflows/ci.yml", WORKFLOWS)
        )
        self.assertFalse(glob_match("/home/omnigent/xgithub/workflows/ci.yml", WORKFLOWS))

    def test_glob_single_star_stays_in_segment(self):
        self.assertTrue(glob_match("src/a.py", "src/*.py"))
        self.assertFalse(glob_match("src/sub/a.py", "src/*.py"))
        self.assertTrue(glob_match("src/a.py", "src/?.py"))
        self.assertFalse(glob_match("src/ab.py", "src/?.py"))

    def test_glob_normalises_dot_slash_and_double_slash(self):
        self.assertTrue(glob_match("./.github//workflows/ci.yml", WORKFLOWS))


class WriteToolsTest(unittest.TestCase):
    def test_write_forbidden_denied(self):
        policy = path_guard(deny=[WORKFLOWS])
        for name in ("Write", "Edit", "MultiEdit", "sys_os_write"):
            decision = policy(tool_call_event(name, {"file_path": ".github/workflows/ci.yml"}))
            self.assertEqual(decision["result"], "DENY", name)
            self.assertTrue(decision["reason"].startswith("agent-stack: "))
            self.assertIn("forbidden path", decision["reason"])
        decision = policy(tool_call_event("NotebookEdit", {"notebook_path": ".github/workflows/n.ipynb"}))
        self.assertEqual(decision["result"], "DENY")

    def test_write_path_key_variant(self):
        decision = path_guard(deny=[WORKFLOWS])(
            tool_call_event("sys_os_write", {"path": ".github/workflows/ci.yml"})
        )
        self.assertEqual(decision["result"], "DENY")

    def test_write_no_path_denied(self):
        decision = path_guard(deny=[WORKFLOWS])(tool_call_event("Write", {}))
        self.assertEqual(decision["result"], "DENY")
        self.assertIn("path", decision["reason"])

    def test_write_unparseable_denied(self):
        self.assertEqual(path_guard(deny=[WORKFLOWS])(UNPARSEABLE)["result"], "DENY")

    def test_write_dotdot_denied(self):
        decision = path_guard(deny=[WORKFLOWS])(
            tool_call_event("Write", {"file_path": "docs/../.github/workflows/x.yml"})
        )
        self.assertEqual(decision["result"], "DENY")
        self.assertIn("escape", decision["reason"])

    def test_write_dotdot_denied_without_deny_rules(self):
        decision = path_guard()(tool_call_event("Write", {"file_path": "../x"}))
        self.assertEqual(decision["result"], "DENY")

    def test_env_example_exempt(self):
        policy = path_guard(deny=["**/.env*"], exempt=["**/.env.example"])
        self.assertEqual(policy(tool_call_event("Write", {"file_path": "backend/.env.example"}))["result"], "ALLOW")
        self.assertEqual(policy(tool_call_event("Write", {"file_path": "backend/.env"}))["result"], "DENY")

    def test_allow_only_docs(self):
        policy = path_guard(allow_only=["docs/**"])
        self.assertEqual(policy(tool_call_event("Write", {"file_path": "docs/a.md"}))["result"], "ALLOW")
        decision = policy(tool_call_event("Write", {"file_path": "src/a.py"}))
        self.assertEqual(decision["result"], "DENY")
        self.assertIn("outside allowed paths", decision["reason"])

    def test_other_tools_abstain(self):
        policy = path_guard(deny=[WORKFLOWS])
        self.assertIsNone(policy(tool_call_event("Read", {"file_path": ".github/workflows/ci.yml"})))
        self.assertIsNone(policy({"type": "other"}))


class ShellTest(unittest.TestCase):
    def test_shell_redirect_into_forbidden_denied(self):
        policy = path_guard(deny=[WORKFLOWS, "**/.env"])
        for command in (
            "echo x > .github/workflows/ci.yml",
            "echo x >> .github/workflows/ci.yml",
            "echo x >.github/workflows/ci.yml",
            "sed -i s/a/b/ backend/.env",
            "cat a && tee .github/workflows/ci.yml",
            "cp --target=.github/workflows/ci.yml a",
        ):
            decision = policy(tool_call_event("bash", {"command": command}))
            self.assertEqual(decision["result"], "DENY", command)
            self.assertIn("forbidden path", decision["reason"])
            self.assertIn("Read", decision["reason"])

    def test_shell_exempt(self):
        policy = path_guard(deny=["**/.env*"], exempt=["**/.env.example"])
        self.assertEqual(policy(tool_call_event("bash", {"command": "cat a/.env.example"}))["result"], "ALLOW")

    def test_shell_unrelated_allowed(self):
        policy = path_guard(deny=[WORKFLOWS])
        self.assertEqual(policy(tool_call_event("bash", {"command": "pytest -q"}))["result"], "ALLOW")

    def test_shell_allow_only_does_not_constrain(self):
        policy = path_guard(allow_only=["docs/**"])
        self.assertEqual(policy(tool_call_event("bash", {"command": "pytest -q src"}))["result"], "ALLOW")

    def test_shell_unparseable_denied(self):
        policy = path_guard(deny=[WORKFLOWS])
        decision = policy(tool_call_event("bash", {"command": 'echo "unterminated'}))
        self.assertEqual(decision["result"], "DENY")

    def test_shell_unparseable_allowed_without_deny(self):
        decision = path_guard(allow_only=["docs/**"])(tool_call_event("bash", {"command": 'echo "u'}))
        self.assertEqual(decision["result"], "ALLOW")

    def test_shell_arguments_unparseable_denied(self):
        event = {"type": "tool_call", "data": {"name": "bash", "arguments": "{bad"}}
        self.assertEqual(path_guard(deny=[WORKFLOWS])(event)["result"], "DENY")

    def test_shell_without_command_allowed(self):
        self.assertEqual(path_guard(deny=[WORKFLOWS])(tool_call_event("bash"))["result"], "ALLOW")


class QuoteAwareTest(unittest.TestCase):
    def test_quoted_operators_allowed(self):
        policy = path_guard(deny=[WORKFLOWS])
        for command in (
            'grep -E "foo|bar" src',
            'git commit -m "fix: a; b"',
            "git commit -m \"$(cat <<'EOF'\nIt's done\nEOF\n)\"",
        ):
            self.assertEqual(policy(tool_call_event("bash", {"command": command}))["result"], "ALLOW", command)

    def test_newline_separates_statements(self):
        policy = path_guard(deny=[WORKFLOWS])
        decision = policy(tool_call_event("bash", {"command": "echo a\ncat > .github/workflows/x.yml"}))
        self.assertEqual(decision["result"], "DENY")

    def test_directory_itself_matches_trailing_double_star(self):
        self.assertTrue(glob_match(".github/workflows", WORKFLOWS))
        self.assertFalse(glob_match(".github/workflowsx", WORKFLOWS))
        policy = path_guard(deny=[WORKFLOWS])
        for command in (
            "cp new.yml .github/workflows",
            "mv x.yml .github/workflows",
            "rm -rf .github/workflows",
        ):
            decision = policy(tool_call_event("bash", {"command": command}))
            self.assertEqual(decision["result"], "DENY", command)

    def test_nested_shell_checked(self):
        policy = path_guard(deny=[WORKFLOWS])
        for command in (
            'bash -c "echo x > .github/workflows/ci.yml"',
            "sh -lc 'rm .github/workflows/ci.yml'",
        ):
            decision = policy(tool_call_event("bash", {"command": command}))
            self.assertEqual(decision["result"], "DENY", command)
