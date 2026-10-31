import unittest

from agent_stack_policies.process_secrets import no_process_secrets

from . import tool_call_event

EVENTS = [
    tool_call_event("Bash", {"command": "cat /proc/1/environ"}),
    tool_call_event("Bash", {"command": "rg -n subprocess src"}),
    tool_call_event("Bash", {}),
    tool_call_event("Read", {"file_path": "a"}),
    {"type": "tool_call", "target": "Bash", "data": {"name": "Bash", "arguments": "{bad"}},
    {"type": "other"},
]


def run(command, tool="Bash"):
    return no_process_secrets()(tool_call_event(tool, {"command": command}))


class ProcessSecretsTest(unittest.TestCase):
    def assertDenied(self, command):
        for form in (command, ["bash", "-lc", command]):
            with self.subTest(form=form):
                decision = run(form)
                self.assertIsNotNone(decision)
                self.assertEqual(decision["result"], "DENY")
                self.assertTrue(decision["reason"].startswith("agent-stack: "))

    def assertAbstains(self, command):
        for form in (command, ["bash", "-lc", command]):
            with self.subTest(form=form):
                self.assertIsNone(run(form))

    def test_proc_environ_and_cmdline_denied(self):
        for command in (
            "cat /proc/1/environ",
            "cat /proc/self/environ",
            "cat /proc/$$/environ",
            "cat /proc/$PPID/cmdline",
            "cat /proc/${PPID}/environ",
            "cat /proc/*/environ",
            "cat /proc/[0-9]*/cmdline",
            "tr '\\0' '\\n' < /proc/12/environ",
            "cat /proc/self/envi''ron",
            "cat /pro?/self/environ",
            "cat /proc/self/e*",
            "cd /proc/self && cat environ",
            "xargs -0 -a /proc/self/environ",
            "python3 -c \"print(open('/proc/self/environ').read())\"",
            "ls -l /proc/self/cwd/../environ",
        ):
            self.assertDenied(command)

    def test_codex_home_denied(self):
        for command in (
            "cat $CODEX_HOME/auth.json",
            "cat ${CODEX_HOME}/config.toml",
            "cat ~/.codex/config.toml",
            "cat $HOME/.codex/config.toml",
            "cat ${HOME}/.codex/config.toml",
            "cat /home/omnigent/.codex/config.toml",
            "cat /root/.codex/config.toml",
            "ls .codex",
            "cat ~/.cod''ex/config.toml",
            "echo $CODEX_HOME",
        ):
            self.assertDenied(command)

    def test_environment_dumps_denied(self):
        for command in (
            "env",
            "env | sort",
            "env -0",
            "printenv",
            "printenv LITELLM_API_KEY",
            "/usr/bin/printenv",
            "export -p",
            "declare -p",
            "compgen -e",
            "cd /tmp && env",
            "git status; printenv | grep KEY",
        ):
            self.assertDenied(command)

    def test_ordinary_commands_allowed(self):
        for command in (
            "rg -n subprocess src",
            "sed -n 1,20p src/a.py",
            "git diff HEAD~1",
            "git log --oneline -5",
            "env FOO=1 semgrep --config auto src",
            "env -i PATH=/usr/bin gitleaks version",
            "cat src/environ.py",
            "cat docs/cmdline.md",
            "rg environment src",
            "rg -n 'codex' README.md",
            "cat src/codex_home.py",
            "GH_TOKEN_FILE=/agent-secrets/github/review gh pr view 3 --repo o/r",
            "python3 -c 'import os; print(os.sep)'",
        ):
            self.assertAbstains(command)

    def test_non_shell_tools_and_bad_arguments(self):
        self.assertIsNone(no_process_secrets()(tool_call_event("Read", {"file_path": "/proc/self/environ"})))
        self.assertIsNone(no_process_secrets()({"type": "other"}))
        self.assertEqual(run({}).get("result"), "DENY")
