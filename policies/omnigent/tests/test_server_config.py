import unittest
from pathlib import Path

SERVER_CONFIG = Path(__file__).resolve().parents[3] / "config" / "omnigent" / "config.yaml"


class ServerConfigTest(unittest.TestCase):
    def test_no_shared_github_token_mount(self):
        # h-cloud's Kyverno policy agent-github-tokens mounts each agent's own token.
        text = SERVER_CONFIG.read_text()
        self.assertNotIn("secret_mounts", text)
        self.assertNotIn("agent-github-tokens", text)
