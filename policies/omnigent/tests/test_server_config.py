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

    def test_sandbox_resources(self):
        import yaml

        # h-cloud's ResourceQuota in omnigent-sandboxes is sized for this request.
        resources = yaml.safe_load(SERVER_CONFIG.read_text())["sandbox"]["kubernetes"]["resources"]
        self.assertEqual(resources["requests"]["memory"], "1Gi")
        self.assertEqual(resources["requests"]["cpu"], "1")
        self.assertEqual(resources["limits"]["memory"], "12Gi")
        self.assertEqual(resources["limits"]["cpu"], "4")

    def test_offline_sandbox_reaper(self):
        import yaml

        # Backstop for h-cloud's sandbox-reaper, which deletes only the Job and leaves its token Secret.
        reaper = yaml.safe_load(SERVER_CONFIG.read_text())["sandbox"]["reaper"]
        self.assertEqual(reaper, {"enabled": True, "terminate_after_offline_days": 1, "sweep_interval_s": 3600})

    def test_the_check_finds_a_nested_mount(self):
        config = {"sandbox": {"kubernetes": {"secret_mounts": [{"secret_name": "agent-github-tokens"}]}}}
        self.assertEqual(_secret_mounts(config), [{"secret_name": "agent-github-tokens"}])
