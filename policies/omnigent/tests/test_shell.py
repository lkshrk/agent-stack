import unittest

from agent_stack_policies._shell import parse, resolve, shell_command, walk


def argv(command):
    return resolve(parse(command)[0]).argv


class ResolveTest(unittest.TestCase):
    def test_wrappers_stripped(self):
        cases = {
            "env X=1 git push": ["git", "push"],
            "env -u HOME -C /tmp git push": ["git", "push"],
            "command git push": ["git", "push"],
            "exec -a x git push": ["git", "push"],
            "nohup git push": ["git", "push"],
            "setsid -w git push": ["git", "push"],
            "ionice -c 2 -n 7 git push": ["git", "push"],
            "nice -n 5 git push": ["git", "push"],
            "timeout --signal=KILL 30 git push": ["git", "push"],
            "flock -n /tmp/l git push": ["git", "push"],
            "stdbuf -o0 -e L git push": ["git", "push"],
            "time -p git push": ["git", "push"],
            "xargs -n 1 -I {} git push": ["git", "push"],
            "find . -exec git add {} ;": ["git", "add", "{}"],
            "sudo -Eu root git push": ["git", "push"],
            "doas -u root git push": ["git", "push"],
            "parallel -j 2 git push ::: a": ["git", "push"],
            "npx -y @scope/tool@1.2 run": ["@scope/tool", "run"],
            "pnpm dlx tool@3 run": ["tool", "run"],
            "timeout 5 env A=1 nice git push": ["git", "push"],
            "! git push": ["git", "push"],
            "uv run git push": ["git", "push"],
            "uv run --with rich -p 3.12 --frozen git push": ["git", "push"],
            "uv run --directory=backend -- git push": ["git", "push"],
            "uv tool run --from foo git push": ["git", "push"],
            "uvx --with rich -p 3.12 git push": ["git", "push"],
            "uvx tool@1.2 run": ["tool", "run"],
            "mise exec -- git push": ["git", "push"],
            "mise x node@20 -C backend -- git push": ["git", "push"],
            "mise --cd backend exec -- git push": ["git", "push"],
        }
        for command, expected in cases.items():
            with self.subTest(command=command):
                self.assertEqual(argv(command), expected)

    def test_wrapper_details_kept(self):
        command = resolve(parse("timeout 5 env GIT_DIR=x git status")[0])
        self.assertEqual(command.wrappers, ["timeout", "env"])
        self.assertEqual(command.assignments, ["GIT_DIR=x"])
        self.assertEqual(command.name, "git")

    def test_nested_strings(self):
        cases = {
            "bash -lc 'git push'": ["git push"],
            "eval git push": ["git push"],
            "env -S 'git push origin'": [],
            "script -qc 'git push' /dev/null": ["git push"],
            "su -c 'git push' me": ["git push"],
            "flock /tmp/l -c 'git push'": ["git push"],
            "parallel ::: 'git push' 'git pull'": ["git push", "git pull"],
            "sh <<< 'git push'": ["git push"],
            "watch -n 1 git status": ["git status"],
            "mise exec -c 'git push'": ["git push"],
            "mise x node@20 --command='git push'": ["git push"],
        }
        for command, expected in cases.items():
            with self.subTest(command=command):
                self.assertEqual(resolve(parse(command)[0]).nested, expected)
        self.assertEqual(argv("env -S 'git push origin'"), ["git", "push", "origin"])

    def test_launcher_without_a_command(self):
        self.assertEqual(argv("mise exec node@20"), [])
        self.assertEqual(argv("uv sync --frozen"), ["uv", "sync", "--frozen"])
        self.assertEqual(argv("mise install"), ["mise", "install"])

    def test_stdin_exec(self):
        for command in ("sh", "bash -s", "bash -e", "bash < x.sh", "zsh -x", "parallel"):
            self.assertTrue(resolve(parse(command)[0]).stdin_exec, command)
        for command in ("bash x.sh", "bash -c 'ls'", "sh -o pipefail x.sh", "sh <<< ls", "ls"):
            self.assertFalse(resolve(parse(command)[0]).stdin_exec, command)

    def test_variable_command_is_a_documented_limit(self):
        self.assertEqual(argv("$G push origin main"), ["$G", "push", "origin", "main"])


class WalkTest(unittest.TestCase):
    def test_wrapper_bodies_walked(self):
        statements = walk("script -qc 'claude -p x' /dev/null")
        self.assertIn(["claude", "-p", "x"], statements)

    def test_unquoted_substitution_segments(self):
        self.assertIn(["git", "branch", "--show-current"], walk("git push origin $(git branch --show-current)"))

    def test_backticks_across_tokens(self):
        self.assertIn(["claude", "-p", "x"], walk("echo `claude -p x`"))

    def test_unparseable_substitution_body_split_roughly(self):
        statements = walk("git commit -m \"$(cat <<'EOF'\nclaude -p it's\nEOF\n)\"")
        self.assertTrue(any(s[:2] == ["claude", "-p"] for s in statements), statements)

    def test_unparseable_top_level_raises(self):
        with self.assertRaises(ValueError):
            walk('echo "unterminated')


class ShellCommandTest(unittest.TestCase):
    def test_shapes(self):
        self.assertEqual(shell_command({"command": "ls"}), "ls")
        self.assertEqual(shell_command({"cmd": "ls"}), "ls")
        self.assertEqual(shell_command({}), "")
        self.assertEqual(shell_command({"command": ["bash", "-lc", "gh pr merge 5"]}), "bash -lc 'gh pr merge 5'")
        self.assertIsNone(shell_command({"command": ["bash", 1]}))
        self.assertIsNone(shell_command({"command": {"a": 1}}))
        self.assertIsNone(shell_command({"command": 5}))
