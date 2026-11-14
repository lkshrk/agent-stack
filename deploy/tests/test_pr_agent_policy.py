"""Check the shipped PR-Agent policy against the contract of h-cloud's policy init container."""

import json
from pathlib import Path
import tomllib
import unittest

POLICY = Path(__file__).resolve().parents[1] / "pr-agent"
MARKER = 'extra_instructions = ""'
MAX_BYTES = 1048576


class PolicyContractTest(unittest.TestCase):
    def setUp(self):
        self.config_bytes = (POLICY / "pr-agent.toml").read_bytes()
        self.guide_bytes = (POLICY / "review.md").read_bytes()
        self.config = self.config_bytes.decode("utf-8")
        self.guide = self.guide_bytes.decode("utf-8")

    def test_sources_are_non_empty_and_within_size(self):
        for data in (self.config_bytes, self.guide_bytes):
            self.assertTrue(data.strip())
            self.assertLessEqual(len(data), MAX_BYTES)

    def test_config_has_one_placeholder_under_pr_reviewer(self):
        self.assertEqual(self.config.count(MARKER), 1)
        self.assertEqual(tomllib.loads(self.config)["pr_reviewer"]["extra_instructions"], "")

    def test_guide_round_trips_into_config(self):
        merged = self.config.replace(MARKER, "extra_instructions = " + json.dumps(self.guide, ensure_ascii=False))
        result = tomllib.loads(merged)
        self.assertEqual(result["pr_reviewer"]["extra_instructions"], self.guide)
        self.assertEqual(
            {k: v for k, v in result["pr_reviewer"].items() if k != "extra_instructions"},
            {k: v for k, v in tomllib.loads(self.config)["pr_reviewer"].items() if k != "extra_instructions"},
        )


if __name__ == "__main__":
    unittest.main()
