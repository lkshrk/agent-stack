import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from . import require_git, require_yaml
from .fork_checkout import _environment

require_yaml()
require_git()

POLICIES = Path(__file__).resolve().parents[1]
APP = "kubernetes/apps/ai/litellm/app/"
RELEASE = """\
apiVersion: helm.toolkit.fluxcd.io/v2
kind: HelmRelease
metadata:
  name: litellm
spec:
  interval: 1h
  chartRef:
    kind: OCIRepository
    name: litellm
  values:
    image:
      tag: 1.0.0
"""
ROLE = """\
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRole
metadata:
  name: x
rules: []
"""


class ManifestsCliTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tree = Path(self._tmp.name)
        self.git("init", "-q", "-b", "main")
        self.write(APP + "helmrelease.yaml", RELEASE)
        self.write("talos/talconfig.yaml", "a: 1\n")
        self.commit("base")
        self.base = self.git("rev-parse", "HEAD").strip()

    def tearDown(self):
        self._tmp.cleanup()

    def git(self, *args):
        return subprocess.run(["git", "-C", str(self.tree), *args], check=True, capture_output=True, text=True,
                              env=_environment()).stdout

    def write(self, name, text):
        path = self.tree / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)

    def commit(self, message):
        self.git("add", "-A")
        self.git("commit", "-q", "-m", message)

    def run_cli(self, *args):
        env = {**_environment(), "PYTHONPATH": str(POLICIES)}
        return subprocess.run([sys.executable, "-m", "agent_stack_policies.manifests_cli", *args],
                              cwd=self.tree, capture_output=True, text=True, env=env, timeout=60)

    def check(self, *args):
        return self.run_cli(*(args or (self.base, "HEAD")))

    def test_clean_bump_passes(self):
        self.write(APP + "helmrelease.yaml", RELEASE.replace("tag: 1.0.0", "tag: 1.0.1"))
        self.commit("bump")
        out = self.check()
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        self.assertIn("1 file", out.stdout)

    def test_no_change_passes(self):
        out = self.check()
        self.assertEqual(out.returncode, 0, out.stderr)

    def test_protected_and_outside_paths_fail(self):
        for name in ("talos/talconfig.yaml", "kubernetes/apps/kube-system/cilium/app/values.yaml",
                     "kubernetes/apps/ai/litellm/ks.yaml", "README.md", APP + "secret.sops.yaml"):
            with self.subTest(name):
                self.git("reset", "-q", "--hard", self.base)
                self.write(name, "a: 2\n")
                self.commit("change")
                out = self.check()
                self.assertEqual(out.returncode, 1, out.stdout)
                self.assertIn(name, out.stdout)

    def test_content_violations_fail(self):
        self.write(APP + "role.yaml", ROLE)
        self.commit("role")
        out = self.check()
        self.assertEqual(out.returncode, 1, out.stdout)
        self.assertIn(APP + "role.yaml", out.stdout)

    def test_rename_from_a_protected_path_fails(self):
        self.git("mv", "talos/talconfig.yaml", APP + "talconfig.yaml")
        self.commit("move")
        out = self.check()
        self.assertEqual(out.returncode, 1, out.stdout)
        self.assertIn("talos/talconfig.yaml", out.stdout)

    def test_symlink_fails(self):
        os.symlink("helmrelease.yaml", self.tree / (APP + "link.yaml"))
        self.commit("link")
        out = self.check()
        self.assertEqual(out.returncode, 1, out.stdout)
        self.assertIn("link.yaml", out.stdout)

    def test_too_many_files_fail(self):
        for i in range(51):
            self.write(f"{APP}cm{i}.md", "x\n")
        self.commit("many")
        out = self.check()
        self.assertEqual(out.returncode, 1, out.stdout)

    def test_staged_changes_are_checked(self):
        self.write(APP + "role.yaml", ROLE)
        self.git("add", "-A")
        out = self.check("--staged", self.base)
        self.assertEqual(out.returncode, 1, out.stdout)
        self.assertIn("role.yaml", out.stdout)

    def test_usage_and_git_errors_exit_2(self):
        self.assertEqual(self.run_cli().returncode, 2)
        self.assertEqual(self.check("nope", "HEAD").returncode, 2)
