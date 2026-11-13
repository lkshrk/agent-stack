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

    def test_token_file_per_repository(self):
        env = {"PATH": os.environ["PATH"]}
        cases = {
            "lkshrk/h-cloud": "/var/run/agent/github/hcloud",
            "lkshrk/h-cloud.git": "/var/run/agent/github/hcloud",
            "lkshrk/knowledge": "/var/run/agent/github/knowledge",
            "routivo/routivo": "/var/run/agent/github/code",
            "": "/var/run/agent/github/code",
        }
        for repo, want in cases.items():
            out = subprocess.run([BIN / "github-token-file", repo], env=env, capture_output=True, text=True, check=True)
            self.assertEqual(out.stdout.strip(), want, repo)

    def test_git_credentials_follow_the_repository(self):
        with tempfile.TemporaryDirectory() as tmp:
            files = {}
            for key in ("code", "knowledge", "hcloud"):
                files[key] = Path(tmp, key)
                files[key].write_text(f"token-{key}\n")
            env = {
                "PATH": os.environ["PATH"],
                "GH_TOKEN_FILE": str(files["code"]),
                "KNOWLEDGE_TOKEN_FILE": str(files["knowledge"]),
                "HCLOUD_TOKEN_FILE": str(files["hcloud"]),
            }
            cases = {
                "lkshrk/h-cloud.git": "token-hcloud",
                "lkshrk/h-cloud/info/lfs": "token-hcloud",
                "lkshrk/knowledge": "token-knowledge",
                "routivo/routivo.git": "token-code",
            }
            for path, want in cases.items():
                out = subprocess.run([BIN / "git-credential-agent", "get"], input=f"protocol=https\nhost=github.com\npath={path}\n\n",
                                     env=env, capture_output=True, text=True, check=True)
                self.assertIn(f"password={want}\n", out.stdout, path)
            out = subprocess.run([BIN / "git-credential-agent", "get"], input="protocol=https\nhost=example.com\npath=a/b\n\n",
                                 env=env, capture_output=True, text=True, check=True)
            self.assertEqual(out.stdout, "")
