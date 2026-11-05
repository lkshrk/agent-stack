import importlib.util
import json
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TEMPLATE = ROOT / "templates" / "contribution-fork"
REGISTRY = ROOT / "config" / "public-contributions.yaml"
_spec = importlib.util.spec_from_file_location("render_contribution_fork", ROOT / "templates" / "render_contribution_fork.py")
assert _spec and _spec.loader
render = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(render)
sys.path.insert(0, str(ROOT / "policies" / "omnigent"))

from agent_stack_policies.public_contributions import Registry, RegistryError  # noqa: E402


def tree(directory: Path) -> dict[str, bytes]:
    return {str(p.relative_to(directory)): p.read_bytes() for p in sorted(directory.rglob("*")) if p.is_file() and "__pycache__" not in p.parts}


class RenderTest(unittest.TestCase):
    def test_committed_template_is_the_rendered_omnigent_fork(self):
        with tempfile.TemporaryDirectory() as directory:
            render.render(REGISTRY, "omnigent", Path(directory))
            self.assertEqual(tree(Path(directory)), tree(TEMPLATE))

    def test_contribution_config_matches_the_registry_entry(self):
        entry = next(r for r in Registry.load(REGISTRY).repositories if r.name == "omnigent")
        config = json.loads((TEMPLATE / "publish" / "contribution.json").read_text())
        self.assertEqual(
            config,
            {
                "upstream": "omnigent-ai/omnigent",
                "fork": entry.fork,
                "owner": "lkshrk",
                "default_branch": entry.default_branch,
                "title_format": entry.contribution.title_format,
                "ci_workflow": "contribution-ci.yml",
            },
        )

    def test_ci_runs_every_validation_command_with_scripts_ignored(self):
        entry = next(r for r in Registry.load(REGISTRY).repositories if r.name == "omnigent")
        ci = (TEMPLATE / ".github" / "workflows" / "contribution-ci.yml").read_text()
        install = " ".join(entry.validation.install.argv)
        self.assertIn("--ignore-scripts", entry.validation.install.argv)
        self.assertIn(f"run: {install}\n", ci)
        for command in entry.validation.commands:
            self.assertIn(f"working-directory: {command.cwd}\n        run: {' '.join(command.argv)}\n", ci)
        self.assertIn("runs-on: ubuntu-latest", ci)
        self.assertIn("persist-credentials: false", ci)

    def test_lists_every_other_workflow_for_disabling(self):
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory)
            workflows = out / ".github" / "workflows"
            workflows.mkdir(parents=True)
            for name in ("ci.yml", "release.yaml", "contribution-ci.yml", "publish-upstream.yml", "README.md"):
                (workflows / name).write_text("on: push\n")
            (workflows / "nested").mkdir()
            (workflows / "nested" / "x.yml").write_text("on: push\n")

            render.render(REGISTRY, "omnigent", out)

            self.assertEqual(render.workflows_to_disable(out), ["ci.yml", "release.yaml"])
            self.assertEqual(
                render.disable_commands("lkshrk/omnigent", render.workflows_to_disable(out)),
                ["gh workflow disable ci.yml --repo lkshrk/omnigent", "gh workflow disable release.yaml --repo lkshrk/omnigent"],
            )

    def test_main_prints_the_disable_commands(self):
        import contextlib
        import io

        with tempfile.TemporaryDirectory() as directory:
            workflows = Path(directory) / ".github" / "workflows"
            workflows.mkdir(parents=True)
            (workflows / "pr-review.yml").write_text("on: pull_request_target\n")
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout), unittest.mock.patch.object(sys, "argv", ["render", "omnigent", directory]):
                self.assertEqual(render.main(), 0)
            self.assertEqual(stdout.getvalue(), "gh workflow disable pr-review.yml --repo lkshrk/omnigent\n")

    def test_only_the_publish_job_names_the_token_and_it_runs_in_its_environment(self):
        import yaml

        workflow = yaml.safe_load((TEMPLATE / ".github" / "workflows" / "publish-upstream.yml").read_text())
        self.assertEqual(workflow["jobs"]["publish"]["environment"], "upstream-publish")
        self.assertNotIn("UPSTREAM_PUBLISH_TOKEN", (TEMPLATE / ".github" / "workflows" / "contribution-ci.yml").read_text())

    def test_refuses_an_unknown_repository(self):
        with tempfile.TemporaryDirectory() as directory, self.assertRaises(RegistryError):
            render.render(REGISTRY, "missing", Path(directory))

    def test_refuses_installs_that_run_lifecycle_scripts(self):
        entry = next(r for r in Registry.load(REGISTRY).repositories if r.name == "omnigent")
        install = type(entry.validation.install)(argv=("pnpm", "install"), lifecycle_scripts="denied")
        validation = type(entry.validation)(**{**entry.validation.__dict__, "install": install})
        broken = type(entry)(**{**entry.__dict__, "validation": validation})
        with self.assertRaises(RegistryError):
            render.ci_workflow(broken)


if __name__ == "__main__":
    unittest.main()
