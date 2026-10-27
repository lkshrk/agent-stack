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
            "bash -euo pipefail -c 'git push'": ["git push"],
            "bash -O extglob -c 'git push'": ["git push"],
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

    def test_quoted_heredoc_body_in_substitution_is_text(self):
        statements = walk("git commit -m \"$(cat <<'EOF'\nclaude -p it's\nEOF\n)\"")
        self.assertFalse(any(s[:2] == ["claude", "-p"] for s in statements), statements)
        self.assertIn(["cat", "<<", "EOF"], statements)

    def test_unparseable_substitution_body_split_roughly(self):
        statements = walk("echo \"$(claude -p it's)\"")
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


class ProcessSubstitutionTest(unittest.TestCase):
    def test_bodies_become_statements(self):
        cases = {
            "cat <(touch x)": ["touch", "x"],
            "rg x <(git push origin feat/x)": ["git", "push", "origin", "feat/x"],
            "diff <(rg a)<(rm b)": ["rm", "b"],
            "tee >(git commit -am x)": ["git", "commit", "-am", "x"],
            "bash -lc 'cat <(gh pr review 1 --approve)'": ["gh", "pr", "review", "1", "--approve"],
        }
        for command, expected in cases.items():
            with self.subTest(command=command):
                self.assertIn(expected, [resolve(t).argv for t in walk(command)])


class OpaqueTest(unittest.TestCase):
    def test_opaque_and_decoding(self):
        from agent_stack_policies._shell import decoding, opaque

        for text in ("${x@P}", "${x:1}", "${a[0]}", "${!x}", "${x@Q}", "${x:-y}"):
            self.assertTrue(opaque(text), text)
        for text in ("${HOME}", "$HOME", "${#x}", "${x/a/b}"):
            self.assertFalse(opaque(text), text)
        self.assertTrue(decoding("${x@P}"))
        self.assertTrue(decoding("${_@E}"))
        self.assertFalse(decoding("${x@Q}"))

    def test_body_arguments(self):
        cases = {
            "bash -c 'x' _ a": ["_", "a"],
            "bash -lc 'x' 2>&1": [],
            "bash -lc 'x' > out": [],
            "bash -lc 'x'": [],
            "sh -c 'x' 2>/dev/null": [],
        }
        for command, expected in cases.items():
            with self.subTest(command=command):
                self.assertEqual(resolve(parse(command)[0]).body_arguments, expected)


class AnsiCDecodeTest(unittest.TestCase):
    def test_words_decoded(self):
        cases = {
            "g$'it' push": ["git", "push"],
            "$'\\x67it' push": ["git", "push"],
            "$'\\147it' push": ["git", "push"],
            "$'\\u0067it' push": ["git", "push"],
            "g$\"it\" push": ["git", "push"],
            "echo $'it\\'s'": ["echo", "it's"],
            "echo $'a\\\\b'": ["echo", "a\\b"],
            "echo '$'\"'\"x": ["echo", "$'x"],
            "echo \"$'x'\"": ["echo", "$'x'"],
        }
        for command, expected in cases.items():
            with self.subTest(command=command):
                self.assertEqual(parse(command)[0], expected)

    def test_newline_and_tab_stay_inside_the_word(self):
        self.assertEqual(parse("git commit -m $'a\\nb'"), [["git", "commit", "-m", "a\nb"]])
        self.assertEqual(parse("printf $'a\\tb'"), [["printf", "a\tb"]])


class AnsiCOpaqueTest(unittest.TestCase):
    def test_nul_and_control_forms_are_opaque(self):
        from agent_stack_policies._shell import OPAQUE_WORD

        for word in ("$'git\\0'", "$'git\\x00'", "$'git\\u0000'", "$'git\\000x'", "$'\\cA'", "$'\\c'", "$'\\x80'", "$'\\200'", "$'\\c\\\\'"):
            with self.subTest(word=word):
                self.assertIn(OPAQUE_WORD, parse("echo " + word)[0][1])

    def test_nul_truncates_like_bash(self):
        self.assertTrue(parse("echo $'git\\0x'")[0][1].startswith("git$"))

    def test_c_before_the_closing_quote_closes_it(self):
        statements = parse("echo $'\\c'; git push origin main")
        self.assertEqual(statements[1], ["git", "push", "origin", "main"])


class HeredocTest(unittest.TestCase):
    def test_body_is_text(self):
        cases = {
            "cat <<E\nit's\nE\ngit push origin main #'": ["git", "push", "origin", "main"],
            "cat <<E\n$'\nE\ngit push origin main\n#'": ["git", "push", "origin", "main"],
            "cat <<'E'\n'\nE\ngit commit -am x #'": ["git", "commit", "-am", "x"],
            'cat <<"E"\n"\nE\nclaude -p hi': ["claude", "-p", "hi"],
            "cat <<-E\n\tit's\n\tE\ngit push origin main": ["git", "push", "origin", "main"],
            "cat <<A <<'B'\nit's\nA\n\"\nB\ngit push origin main": ["git", "push", "origin", "main"],
            "cat <<A; cat <<B\na'\nA\nb'\nB\ngit push origin main": ["git", "push", "origin", "main"],
        }
        for command, expected in cases.items():
            with self.subTest(command=command):
                self.assertEqual(parse(command)[-1], expected)
                self.assertNotIn(["it's"], parse(command))

    def test_unterminated_heredoc_is_opaque(self):
        from agent_stack_policies._shell import OpaqueCommand

        for command in ("cat <<E\nit's", "cat <<E", "cat <<A <<B\na\nA\nb", "cat <<-E\nx\n E"):
            with self.subTest(command=command):
                with self.assertRaises(OpaqueCommand):
                    walk(command)

    def test_here_string_and_arithmetic_unchanged(self):
        self.assertEqual(parse("cat <<< 'x'"), [["cat", "<<<", "x"]])
        self.assertEqual(parse("echo $((1<<2))")[0][0], "echo")
        self.assertEqual(parse("echo a#b 'c # d' # note"), [["echo", "a#b", "c # d"]])

    def test_unquoted_heredoc_substitution_is_walked(self):
        statements = walk("cat <<E\n$(claude -p x)\nE")
        self.assertTrue(any(s[:2] == ["claude", "-p"] for s in statements), statements)
