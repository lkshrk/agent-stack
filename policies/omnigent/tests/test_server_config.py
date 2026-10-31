import os
import unittest
from pathlib import Path

SERVER_CONFIG = Path(__file__).resolve().parents[3] / "config" / "omnigent" / "config.yaml"
IN_IMAGE = os.environ.get("AGENT_STACK_IN_IMAGE") == "1"


def _secret_mounts(node: object) -> list:
    if isinstance(node, dict):
        found = list(node.get("secret_mounts") or [])
        for value in node.values():
            found += _secret_mounts(value)
        return found
    if isinstance(node, list):
        return [m for item in node for m in _secret_mounts(item)]
    return []


@unittest.skipUnless(IN_IMAGE, "needs PyYAML from the Omnigent image (AGENT_STACK_IN_IMAGE=1)")
class ServerConfigTest(unittest.TestCase):
    def test_no_shared_github_token_mount(self):
        import yaml

        # h-cloud's Kyverno policy agent-github-tokens mounts each agent's own token.
        mounts = _secret_mounts(yaml.safe_load(SERVER_CONFIG.read_text()))
        self.assertEqual([m for m in mounts if m.get("secret_name") == "agent-github-tokens"], [])

    def test_the_check_finds_a_nested_mount(self):
        config = {"sandbox": {"kubernetes": {"secret_mounts": [{"secret_name": "agent-github-tokens"}]}}}
        self.assertEqual(_secret_mounts(config), [{"secret_name": "agent-github-tokens"}])
