import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent_stack_policies import public_contributions
from agent_stack_policies.public_contributions import prepare_operation

from . import tool_call_event


REGISTRY = Path(__file__).resolve().parents[3] / "config" / "public-contributions.yaml"
HELPER = "/opt/agent/bin/agent-stack-contribute"
TREE = "/home/omnigent/work/omnigent"


class TestPrepareOperation(unittest.TestCase):
    def setUp(self):
        self.registry_path = patch.object(public_contributions, "RUNTIME_REGISTRY", REGISTRY)
        self.registry_path.start()
        self.addCleanup(self.registry_path.stop)

    def evaluate(self, argv, **arguments):
        return prepare_operation(tool_call_event("Bash", {"command": argv, **arguments}))

    def assertAllowed(self, argv, **arguments):
        decision = self.evaluate(argv, **arguments)
        self.assertEqual(decision["result"], "ALLOW", decision)

    def assertDenied(self, argv, **arguments):
        decision = self.evaluate(argv, **arguments)
        self.assertEqual(decision["result"], "DENY", decision)
        self.assertTrue(decision["reason"].startswith("agent-stack: "), decision)

    def test_allows_only_exact_registered_helper_operations(self):
        for helper in (HELPER, "agent-stack-contribute"):
            with self.subTest(helper=helper):
                self.assertAllowed([helper, "prepare", "omnigent"])
                self.assertAllowed([helper, "preflight", "omnigent"])
                self.assertAllowed([helper, "validate", "omnigent", "web"])

    def test_rejects_unregistered_or_altered_helper_arguments(self):
        for argv in (
            [HELPER, "prepare", "unknown"],
            [HELPER, "prepare", "omnigent", "https://github.com/evil/evil.git"],
            [HELPER, "prepare", "omnigent", "main"],
            [HELPER, "validate", "omnigent", "unknown"],
            [HELPER, "validate", "omnigent", "web", "--base", "other"],
            [HELPER, "publish", "omnigent"],
            [HELPER, "list"],
            ["agent-stack-contribute", "publish", "omnigent"],
            ["agent-stack-contribute", "list"],
        ):
            with self.subTest(argv=argv):
                self.assertDenied(argv)

    def test_rejects_shell_and_alternate_helper_targets(self):
        for command in (
            ["bash", "-c", f"{HELPER} prepare omnigent"],
            ["sh", "-c", f"{HELPER} validate omnigent web"],
            ["echo", "$(/opt/agent/bin/agent-stack-contribute prepare omnigent)"],
            ["/tmp/agent-stack-contribute", "prepare", "omnigent"],
            ["/opt/agent/bin/agent-stack-contribute-link", "prepare", "omnigent"],
        ):
            with self.subTest(command=command):
                self.assertDenied(command)

    def test_denies_direct_controller_git_and_validation_in_registered_tree(self):
        for argv in (
            ["git", "clone", "https://github.com/omnigent-ai/omnigent.git", TREE],
            ["git", "fetch", "origin"],
            ["git", "worktree", "add", "/tmp/other"],
            ["git", "branch", "contribution/fix"],
            ["git", "add", "src/a.py"],
            ["git", "commit", "-m", "fix"],
            ["pnpm", "install", "--ignore-scripts"],
            ["pnpm", "run", "lint"],
            ["pnpm", "test"],
        ):
            with self.subTest(argv=argv):
                self.assertDenied(argv, cwd=TREE)

    def test_denies_registered_git_targets_without_a_registered_cwd(self):
        for argv in (
            ["git", "clone", "https://github.com/omnigent-ai/omnigent.git", TREE],
            ["git", "-c", "advice.detachedHead=false", "clone", "https://github.com/omnigent-ai/omnigent.git", TREE],
            ["git", "worktree", "add", TREE],
            ["git", "-C", TREE, "fetch", "origin"],
            ["git", "-c", "advice.detachedHead=false", "-C", TREE, "fetch", "origin"],
            ["git", "--config-env", "core.editor=EDITOR", "-C", TREE, "fetch", "origin"],
            ["git", "--config-env=core.editor=EDITOR", "--work-tree", f"{TREE}/web", "status"],
            ["git", "-C", TREE, "clone", "https://github.com/omnigent-ai/omnigent.git", "/tmp/other"],
            ["git", "-C", TREE, "worktree", "add", "/tmp/other"],
            ["git", "--work-tree", f"{TREE}/web", "status"],
            ["git", f"--git-dir={TREE}/.git", "status"],
            ["git", "--git-dir", f"{TREE}/.git", "status"],
            ["git", "--mystery", TREE, "status"],
            ["git", f"--mystery={TREE}", "status"],
        ):
            with self.subTest(argv=argv):
                self.assertDenied(argv)
        self.assertIsNone(self.evaluate(["git", "-C", "/home/omnigent/work/other", "fetch", "origin"]))
        self.assertIsNone(self.evaluate(["git", "clone", "https://github.com/omnigent-ai/omnigent.git", "/tmp/omnigent"]))

    def test_denies_raw_helper_and_registered_git_commands(self):
        for command in (
            f"git clone https://github.com/omnigent-ai/omnigent.git {TREE}",
            f"git worktree add {TREE}",
            f"git -C /home/omnigent/work/./omnigent commit -m x",
            f"git clone https://github.com/omnigent-ai/omnigent.git /home/omnigent/work/./omnigent",
            f"git worktree add /home/omnigent/work/omnigent/../omnigent",
            f"git -C {TREE} fetch origin",
            f"{HELPER} prepare omnigent",
            f"{HELPER} publish omnigent",
            f"bash -c '{HELPER} prepare omnigent'",
        ):
            with self.subTest(command=command):
                self.assertDenied(command)

    def test_allows_helper_only_as_structured_argv(self):
        self.assertAllowed([HELPER, "prepare", "omnigent"])
        self.assertDenied(f"{HELPER} prepare omnigent")

    def test_rejects_registered_tree_symlink_path(self):
        with tempfile.TemporaryDirectory() as directory:
            link = Path(directory) / "link"
            link.symlink_to(TREE, target_is_directory=True)
            self.assertDenied(["git", "fetch", "origin"], cwd=str(link))

    def test_ignores_unrelated_tools_and_trees(self):
        self.assertIsNone(prepare_operation(tool_call_event("Read", {"file_path": "README.md"})))
        self.assertIsNone(self.evaluate(["git", "fetch", "origin"], cwd="/home/omnigent/work/other"))
        self.assertIsNone(self.evaluate(["pnpm", "test"], cwd="/home/omnigent/work/other"))


if __name__ == "__main__":
    unittest.main()
