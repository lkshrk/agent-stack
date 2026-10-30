import tomllib
import unittest
from pathlib import Path

CODEX_HOME = Path(__file__).resolve().parents[3] / "images" / "omnigent-host" / "codex"


def load(name: str) -> dict:
    return tomllib.loads((CODEX_HOME / name).read_text())


class SecurityReviewProfileTest(unittest.TestCase):
    def setUp(self):
        self.profile = load("security-review.config.toml")

    def test_never_asks_and_uses_its_permission_profile(self):
        self.assertEqual(self.profile["approval_policy"], "never")
        self.assertEqual(self.profile["default_permissions"], "security-review")
        self.assertNotIn("sandbox_mode", self.profile)

    def test_filesystem_reads_are_narrow_and_only_tmp_is_writable(self):
        filesystem = self.profile["permissions"]["security-review"]["filesystem"]
        self.assertNotIn(":root", filesystem)
        self.assertEqual({path for path, access in filesystem.items() if access == "write"}, {"/tmp"})
        secrets = {path: access for path, access in filesystem.items() if path.startswith("/agent-secrets")}
        self.assertEqual(secrets, {"/agent-secrets/github/review": "read"})
        self.assertEqual(filesystem[":workspace_roots"], "read")
        readable = {path for path, access in filesystem.items() if access == "read"}
        self.assertFalse({p for p in readable if p.startswith(("/home", "/sandbox", "/root", "~"))})

    def test_secret_env_vars_dropped(self):
        policy = self.profile["shell_environment_policy"]
        self.assertIs(policy["ignore_default_excludes"], False)
        self.assertLessEqual(
            {"*_KEY", "*_TOKEN", "*_SECRET", "LITELLM_*", "GH_*", "GITHUB_TOKEN"}, set(policy["exclude"])
        )

    def test_network_enabled(self):
        self.assertIs(self.profile["permissions"]["security-review"]["network"]["enabled"], True)

    def test_unhooked_tools_disabled(self):
        self.assertEqual(self.profile["features"], {"unified_exec": False, "multi_agent": False})

    def test_tool_caches_live_in_tmp(self):
        env = self.profile["shell_environment_policy"]["set"]
        self.assertTrue(env)
        for key, value in env.items():
            with self.subTest(key=key):
                self.assertTrue(value.startswith("/tmp/"), value)


class DefaultCodexConfigTest(unittest.TestCase):
    def test_unified_exec_disabled(self):
        self.assertIs(load("config.toml.tmpl")["features"]["unified_exec"], False)

    def test_no_bypass_in_default_config(self):
        config = load("config.toml.tmpl")
        self.assertNotIn("approval_policy", config)
        self.assertNotIn("sandbox_mode", config)
