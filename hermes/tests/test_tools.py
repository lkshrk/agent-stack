import base64
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

IN_IMAGE = os.environ.get("AGENT_STACK_IN_IMAGE") == "1"
BIN = Path("/opt/agent/bin")

TOOLS = {
    "kubectl": ["version", "--client"],
    "flux": ["version", "--client"],
    "helm": ["version"],
    "yq": ["--version"],
    "jq": ["--version"],
    "crane": ["version"],
    "flate": ["--help"],
    "just": ["--version"],
    "gh": ["--version"],
    "rg": ["--version"],
    "skopeo": ["--version"],
    "git": ["--version"],
}


@unittest.skipUnless(IN_IMAGE, "needs the Hermes image (AGENT_STACK_IN_IMAGE=1)")
class ToolsInImageTest(unittest.TestCase):
    def login_shell(self, script: str, cwd: str, *args: str) -> subprocess.CompletedProcess:
        env = {"HOME": cwd, "PATH": "/usr/bin:/bin"}
        return subprocess.run(["bash", "-lc", script, "_", *args], cwd=cwd, env=env, capture_output=True, text=True)

    def test_tools_run_in_a_login_shell(self):
        with tempfile.TemporaryDirectory() as home:
            for tool, args in TOOLS.items():
                out = self.login_shell('"$@"', home, tool, *args)
                self.assertEqual(out.returncode, 0, f"{tool}: {out.stderr[-300:]}")

    def test_project_mise_config_does_not_swap_tools(self):
        with tempfile.TemporaryDirectory() as project:
            Path(project, ".mise.toml").write_text('[tools]\n"github:home-operations/flate" = "v0.0.1"\nkubectl = "1.20.0"\n')
            for tool in ("flate", "kubectl"):
                out = self.login_shell('command -v "$1"', project, tool)
                self.assertEqual(out.stdout.strip(), f"/usr/local/bin/{tool}", tool)

    def test_registry_logins_stay_off_the_volume(self):
        self.assertEqual(os.environ["REGISTRY_AUTH_FILE"], "/tmp/registry-auth/auth.json")
        self.assertEqual(os.environ["DOCKER_CONFIG"], "/tmp/docker")
        with tempfile.TemporaryDirectory() as tmp:
            env = {"PATH": os.environ["PATH"], "HOME": tmp,
                   "REGISTRY_AUTH_FILE": f"{tmp}/registry-auth/auth.json", "DOCKER_CONFIG": f"{tmp}/docker"}
            subprocess.run(["crane", "auth", "login", "ghcr.io", "-u", "agent-npa", "--password-stdin"],
                           input="token-npa", env=env, capture_output=True, text=True, check=True)
            self.assertIn("ghcr.io", Path(tmp, "docker", "config.json").read_text())
            Path(tmp, "registry-auth").mkdir()
            auth = base64.b64encode(b"agent-npa:fake").decode()
            Path(tmp, "registry-auth", "auth.json").write_text(json.dumps({"auths": {"ghcr.io": {"auth": auth}}}))
            out = subprocess.run(["skopeo", "login", "--get-login", "ghcr.io"], env=env, capture_output=True, text=True, check=True)
            self.assertEqual(out.stdout.strip(), "agent-npa")
            self.assertEqual(sorted(p.name for p in Path(tmp).iterdir()), ["docker", "registry-auth"])

    def test_gh_reads_the_token_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            token = Path(tmp, "token")
            token.write_text("token-npa\n")
            wrapper = Path(tmp, "gh")
            wrapper.write_text((BIN / "gh").read_text().replace("/usr/local/bin/gh", "/usr/bin/env"))
            env = {"PATH": "/usr/bin:/bin", "GH_TOKEN_FILE": str(token)}
            out = subprocess.run(["sh", str(wrapper)], env=env, capture_output=True, text=True, check=True)
            self.assertIn("GH_TOKEN=token-npa\n", out.stdout)

    def test_git_credentials_from_the_token_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            token = Path(tmp, "token")
            token.write_text("token-npa\n")
            env = {"PATH": os.environ["PATH"], "GH_TOKEN_FILE": str(token)}
            for path in ("lkshrk/h-cloud.git", "lkshrk/knowledge", "routivo/routivo.git"):
                out = subprocess.run([BIN / "git-credential-agent", "get"], input=f"protocol=https\nhost=github.com\npath={path}\n\n",
                                     env=env, capture_output=True, text=True, check=True)
                self.assertIn("password=token-npa\n", out.stdout, path)
            out = subprocess.run([BIN / "git-credential-agent", "get"], input="protocol=https\nhost=example.com\npath=a/b\n\n",
                                 env=env, capture_output=True, text=True, check=True)
            self.assertEqual(out.stdout, "")
