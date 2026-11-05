import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from . import require_yaml

require_yaml()

from agent_stack_policies import public_contributions
from agent_stack_policies.public_contributions import prepare_operation

from . import tool_call_event


REGISTRY = Path(__file__).resolve().parents[3] / "config" / "public-contributions.yaml"
HELPER = "/opt/agent/bin/agent-stack-contribute"
TREE = "/home/omnigent/work/omnigent"


class PublicPublishDenialTest(unittest.TestCase):
    def setUp(self):
        registry_path = patch.object(public_contributions, "RUNTIME_REGISTRY", REGISTRY)
        registry_path.start()
        self.addCleanup(registry_path.stop)

    def evaluate(self, command):
        return prepare_operation()(tool_call_event("Bash", {"command": command, "cwd": TREE}))

    def assertDenied(self, command):
        decision = self.evaluate(command)
        self.assertIsNotNone(decision, command)
        self.assertEqual(decision["result"], "DENY", decision)
        self.assertTrue(decision["reason"].startswith("agent-stack: "), decision)

    def test_denies_direct_public_writes_from_structured_native_calls(self):
        for command in (
            ["git", "push", "origin", "HEAD:contribution/fix"],
            ["git", "remote", "add", "fork", "https://github.com/example/fork.git"],
            ["git", "remote", "set-url", "origin", "https://github.com/example/fork.git"],
            ["git", "tag", "v1.2.3"],
            ["git", "push", "origin", "v1.2.3"],
            ["gh", "issue", "create", "--title", "fix"],
            ["gh", "pr", "create", "--title", "fix"],
            ["gh", "pr", "comment", "1", "--body", "note"],
            ["gh", "api", "-X", "POST", "repos/example/project/issues"],
        ):
            with self.subTest(command=command):
                self.assertDenied(command)

    def test_denies_structured_and_raw_wrappers_in_registered_context(self):
        for command in (
            ["bash", "-lc", "git push origin HEAD:contribution/fix"],
            ["env", "git", "push", "origin", "HEAD:contribution/fix"],
            ["sh", "-c", "gh issue create --title fix"],
            ["zsh", "-c", "git status"],
            ["command", "git", "status"],
            ["builtin", "echo", "x"],
            ["nice", "git", "status"],
            ["nohup", "git", "status"],
            "bash -lc 'git push origin HEAD:contribution/fix'",
            "env git push origin HEAD:contribution/fix",
            "sh -c 'gh issue create --title fix'",
        ):
            with self.subTest(command=command):
                self.assertDenied(command)

    def test_allows_structured_helper_prepare(self):
        decision = self.evaluate([HELPER, "prepare", "omnigent"])
        self.assertEqual(decision["result"], "ALLOW", decision)

    def test_allows_internal_feature_push_outside_registered_tree(self):
        decision = prepare_operation()(
            tool_call_event("Bash", {"command": ["git", "push", "origin", "HEAD:feature/internal"], "cwd": "/home/omnigent/work/agent-stack"})
        )
        self.assertIsNone(decision, decision)

    def test_allows_registered_read_only_gh_calls(self):
        for command in (
            ["gh", "pr", "view", "1"],
            ["gh", "api", "repos/example/project/issues/1"],
            ["gh", "api", "-X", "GET", "repos/example/project/issues/1"],
        ):
            with self.subTest(command=command):
                self.assertIsNone(self.evaluate(command), command)


if __name__ == "__main__":
    unittest.main()
