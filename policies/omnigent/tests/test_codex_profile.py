import tomllib
import unittest
from pathlib import Path

CODEX_HOME = Path(__file__).resolve().parents[3] / "images" / "omnigent-host" / "codex"


def load(name: str) -> dict:
    return tomllib.loads((CODEX_HOME / name).read_text())


class SecurityReviewProfileTest(unittest.TestCase):
    def setUp(self):
        self.profile = load("security-review.config.toml")

    def test_never_asks_and_turns_the_codex_sandbox_off(self):
        self.assertEqual(self.profile["approval_policy"], "never")
        self.assertEqual(self.profile["sandbox_mode"], "danger-full-access")

    def test_no_permission_profile_to_conflict_with_the_sandbox_mode(self):
        self.assertNotIn("default_permissions", self.profile)
        self.assertNotIn("permissions", self.profile)

    def test_secret_env_vars_dropped(self):
        policy = self.profile["shell_environment_policy"]
        self.assertIs(policy["ignore_default_excludes"], False)
        self.assertLessEqual(
            {"*_KEY", "*_TOKEN", "*_SECRET", "LITELLM_*", "GH_*", "GITHUB_TOKEN"}, set(policy["exclude"])
        )

    def test_unhooked_tools_disabled(self):
        self.assertEqual(self.profile["features"], {"unified_exec": False, "multi_agent": False})

    def test_tool_caches_live_in_tmp(self):
        env = self.profile["shell_environment_policy"]["set"]
        self.assertTrue(env)
        for key, value in env.items():
            if key.startswith("MISE_"):
                continue
            with self.subTest(key=key):
                self.assertTrue(value.startswith("/tmp/"), value)

    def test_mise_shims_resolve_without_inherited_env(self):
        env = self.profile["shell_environment_policy"]["set"]
        self.assertEqual(env["MISE_DATA_DIR"], "/opt/mise")
        self.assertEqual(env["MISE_GLOBAL_CONFIG_FILE"], "/etc/mise/config.toml")
        self.assertTrue(env["MISE_CACHE_DIR"].startswith("/tmp/"))


class DefaultCodexConfigTest(unittest.TestCase):
    def test_unified_exec_disabled(self):
        self.assertIs(load("config.toml.tmpl")["features"]["unified_exec"], False)

    def test_no_bypass_in_default_config(self):
        config = load("config.toml.tmpl")
        self.assertNotIn("approval_policy", config)
        self.assertNotIn("sandbox_mode", config)
