"""Check the LiteLLM MCP server catalogue and skills seed list that h-cloud deploys."""

import json
from pathlib import Path
import re
import unittest

import yaml

LITELLM = Path(__file__).resolve().parents[1] / "litellm"
CREDENTIAL_KEYS = {"auth_value", "client_id", "client_secret", "api_key", "token"}
SHA = re.compile(r"^[0-9a-f]{40}$")


class McpServersTest(unittest.TestCase):
    def setUp(self):
        self.values = yaml.safe_load((LITELLM / "mcp-servers.yaml").read_text())

    def test_only_mcp_keys_are_merged_into_the_release(self):
        self.assertEqual(set(self.values), {"proxy_config"})
        self.assertEqual(set(self.values["proxy_config"]), {"mcp_servers", "litellm_settings"})
        self.assertEqual(set(self.values["proxy_config"]["litellm_settings"]), {"mcp_aliases"})

    def test_credentials_are_environment_references(self):
        for name, server in self.values["proxy_config"]["mcp_servers"].items():
            for key in CREDENTIAL_KEYS & set(server):
                with self.subTest(server=name, key=key):
                    self.assertRegex(server[key], r"^os\.environ/[A-Z0-9_]+$")

    def test_servers_have_url_and_transport(self):
        for name, server in self.values["proxy_config"]["mcp_servers"].items():
            with self.subTest(server=name):
                self.assertTrue(server["url"].startswith(("https://", "http://")))
                self.assertEqual(server["transport"], "http")

    def test_aliases_point_at_servers(self):
        servers = self.values["proxy_config"]["mcp_servers"]
        for alias, target in self.values["proxy_config"]["litellm_settings"]["mcp_aliases"].items():
            with self.subTest(alias=alias):
                self.assertIn(target, servers)


class SkillsSeedTest(unittest.TestCase):
    def setUp(self):
        self.seed = json.loads((LITELLM / "skills-seed.json").read_text())

    def test_names_are_unique(self):
        names = [s["name"] for s in self.seed]
        self.assertEqual(len(names), len(set(names)))

    def test_active_entries_are_pinned(self):
        for s in self.seed:
            if s.get("retired"):
                continue
            with self.subTest(plugin=s["name"]):
                source = s["source"]
                self.assertIn(source["source"], {"github", "git-subdir"})
                self.assertTrue(source.get("repo") or source.get("url", "").startswith("https://github.com/"))
                self.assertTrue(source["ref"])
                self.assertRegex(source["sha"], SHA)
                self.assertTrue(s["description"])

    def test_seed_script_compiles(self):
        compile((LITELLM / "skills-seed.py").read_text(), "skills-seed.py", "exec")


if __name__ == "__main__":
    unittest.main()
