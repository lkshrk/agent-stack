import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
REGISTRY = ROOT / "config" / "public-contributions.yaml"
FORKS = ROOT / "cmd" / "contribution-approver" / "forks.json"
_spec = importlib.util.spec_from_file_location("render_approver_config", ROOT / "templates" / "render_approver_config.py")
assert _spec and _spec.loader
render = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(render)


class ApproverConfigTest(unittest.TestCase):
    def test_committed_forks_json_is_the_registry_render(self):
        self.assertEqual(FORKS.read_text(), render.render(REGISTRY))

    def test_omnigent_entry(self):
        self.assertEqual(
            json.loads(render.render(REGISTRY)),
            [
                {
                    "upstream": "omnigent-ai/omnigent",
                    "fork": "lkshrk/omnigent",
                    "owner": "lkshrk",
                    "default_branch": "main",
                    "title_format": "conventional-scoped",
                    "ci_workflow": "contribution-ci.yml",
                }
            ],
        )

    def test_check_flags_drift_and_main_writes(self):
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory) / "forks.json"
            self.assertEqual(render.main(["--registry", str(REGISTRY), "--out", str(out), "--check"]), 1)
            self.assertEqual(render.main(["--registry", str(REGISTRY), "--out", str(out)]), 0)
            self.assertEqual(out.read_text(), render.render(REGISTRY))
            self.assertEqual(render.main(["--registry", str(REGISTRY), "--out", str(out), "--check"]), 0)


if __name__ == "__main__":
    unittest.main()
