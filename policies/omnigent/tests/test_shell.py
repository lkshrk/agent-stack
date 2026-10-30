import time
import unittest

from agent_stack_policies._shell import STATEMENT_LIMIT, WORD_LIMIT, OpaqueCommand, has_quoted_operator, parse, resolve, shell_command, shell_name, walk


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


class DoubleQuoteEscapeTest(unittest.TestCase):
    def test_bash_double_quote_escapes(self):
        cases = (
            ('bash -c "echo \\$X"', "echo $X"),
            ('bash -c "a \\`b\\` \\\\ \\" \\n"', 'a `b` \\ " \\n'),
            ('sh -c "gh api graphql -f query=\\"\\$(cat /tmp/q)\\""', 'gh api graphql -f query="$(cat /tmp/q)"'),
            ("bash -c 'echo \\$X'", "echo \\$X"),
        )
        for command, body in cases:
            with self.subTest(command=command):
                self.assertEqual(resolve(parse(command)[0]).nested, [body])
        self.assertEqual(parse('echo "x\\$y" \\$z'), [["echo", "x$y", "$z"]])

class WalkTest(unittest.TestCase):
    def test_shell_families_and_busybox_bodies_walked(self):
        for shell in ("rbash", "ash", "mksh", "fish", "tcsh", "yash", "posh"):
            with self.subTest(shell=shell):
                self.assertEqual(walk(f"{shell} -c 'echo x > out'"), [[shell, "-c", "echo x > out"], ["echo", "x", ">", "out"]])
        self.assertEqual(walk("busybox sh -c 'echo x > out'"), [["busybox", "sh", "-c", "echo x > out"], ["echo", "x", ">", "out"]])

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


class HeredocInQuotedSubstitutionTest(unittest.TestCase):
    def test_body_inside_quoted_substitution_is_text(self):
        command = "git commit -m \"$(cat <<'EOF'\nx\"\nEOF\n)\"\ngit push origin main\n#\""
        self.assertEqual(parse(command)[-1], ["git", "push", "origin", "main"])
        command = "gh pr create --title t --body \"$(cat <<'EOF'\nit's \"done\"\nEOF\n)\"\nclaude -p hi"
        self.assertEqual(parse(command)[-1], ["claude", "-p", "hi"])

    def test_commit_form_stays_one_statement(self):
        statements = parse("git commit -m \"$(cat <<'EOF'\nfix: it's done\n\nMore.\nEOF\n)\"")
        self.assertEqual(len(statements), 1)
        self.assertEqual(statements[0][:3], ["git", "commit", "-m"])


class CommentAfterSubstitutionTest(unittest.TestCase):
    def test_hash_after_substitution_continues_the_word(self):
        self.assertEqual(parse("echo $(true)#; git push origin main")[-1], ["git", "push", "origin", "main"])
        self.assertEqual(parse("echo $((1))#x; claude -p hi")[-1], ["claude", "-p", "hi"])

    def test_hash_after_subshell_is_a_comment(self):
        self.assertEqual(parse("(true)# c; git push origin main"), [["(", "true", ")"]])
        self.assertEqual(parse("(true)# c\ngit push origin main")[-1], ["git", "push", "origin", "main"])


class FindExecTest(unittest.TestCase):
    def test_all_find_exec_runs_are_nested(self):
        tokens = ["find", "."]
        for i in range(2_000):
            tokens += ["-exec", "echo", str(i), ";"]
        started = time.perf_counter()
        command = resolve(tokens)
        self.assertEqual(len(command.nested), 1_999)
        self.assertLess(time.perf_counter() - started, 0.05)


class OperatorWordTest(unittest.TestCase):
    def test_unquoted_operators_end_every_statement(self):
        for command in ("find . ; claude -p hi", "find . -name x;claude -p hi", "nice find . ; claude -p hi", r"find . -exec ls {} \; ; claude -p hi", "find . & claude -p hi", "find . || claude -p hi", "find . && claude -p hi", "find . -maxdepth 0\nclaude -p hi"):
            with self.subTest(command=command):
                self.assertEqual(parse(command)[-1], ["claude", "-p", "hi"])

    def test_quoted_and_escaped_operator_words_are_arguments(self):
        cases = {
            "echo ';' claude -p hi": [["echo", ";", "claude", "-p", "hi"]],
            'echo ";;" x': [["echo", ";;", "x"]],
            r"echo \; \| \&\& x": [["echo", ";", "|", "&&", "x"]],
            "printf '%s' a '|' b": [["printf", "%s", "a", "|", "b"]],
            "echo ''; ls": [["echo", ""], ["ls"]],
            r"echo \;; ls": [["echo", ";"], ["ls"]],
            "echo x';' | wc": [["echo", "x;"], ["wc"]],
            "echo '' ';' x": [["echo", "", ";", "x"]],
        }
        for command, statements in cases.items():
            with self.subTest(command=command):
                self.assertEqual(parse(command), statements)

    def test_quoted_operator_readings(self):
        for command in ("echo ';' x", r"echo \| x", "echo x $'\\x3b' y", 'echo $"|" x', "echo ''\\& x"):
            with self.subTest(command=command):
                self.assertTrue(has_quoted_operator(command))
        for command in ("echo x; y", "echo 'a;b'", "find . -name x", "echo 'x' | wc"):
            with self.subTest(command=command):
                self.assertFalse(has_quoted_operator(command))

    def test_joined_bodies_read_operators_again(self):
        self.assertIn(["claude", "-p", "hi"], walk("eval echo ';' claude -p hi"))
        self.assertIn(["claude", "-p", "hi"], walk("watch echo ';' claude -p hi"))


class FindRunTest(unittest.TestCase):
    def test_runs_end_at_semicolon_words_or_braces_plus(self):
        cases = {
            r"find . -exec ls {} \; -exec claude -p hi \;": (["ls", "{}"], ["claude -p hi"]),
            "find . -exec ls {} ';' -o -exec claude -p hi \";\"": (["ls", "{}"], ["claude -p hi"]),
            r"find . -exec ls {} + -exec claude -p hi \;": (["ls", "{}"], ["claude -p hi"]),
            r"find . -exec claude + -p hi \;": (["claude", "+", "-p", "hi"], []),
            r"find . -exec echo {}+ \;": (["echo", "{}+"], []),
            r"find . -exec env -u + claude -p hi \;": (["claude", "-p", "hi"], []),
            "find . -execdir rg x {} +": (["rg", "x", "{}"], []),
        }
        for command, (first, rest) in cases.items():
            with self.subTest(command=command):
                resolved = resolve(parse(command)[0])
                self.assertEqual(resolved.argv, first)
                self.assertEqual(resolved.nested, rest)

    def test_exec_word_inside_another_run_is_ambiguous(self):
        for command in (r"find . -name -exec -exec claude -p hi \;", r"find . -exec claude -exec -p hi \;", r"find . -exec echo -exec claude -p hi \;"):
            with self.subTest(command=command):
                with self.assertRaises(OpaqueCommand):
                    walk(command)

    def test_statement_limit(self):
        runs = lambda n: "find . " + " ".join(f"-exec echo {i} \\;" for i in range(n))
        bodies = lambda n: "mise exec " + " ".join(f"-c 'echo {i}'" for i in range(n))
        statements = lambda n: "; ".join(["true"] * n)
        groups = lambda n: "; ".join(["(true)"] * n)
        for build, size in ((runs, STATEMENT_LIMIT), (bodies, STATEMENT_LIMIT - 1), (statements, STATEMENT_LIMIT), (groups, STATEMENT_LIMIT // 2)):
            with self.subTest(build=build, size=size):
                self.assertEqual(len(walk(build(size))), STATEMENT_LIMIT)
                with self.assertRaisesRegex(OpaqueCommand, f"more than {STATEMENT_LIMIT} statements"):
                    walk(build(size + 1))

    def test_word_limit(self):
        self.assertEqual(len(walk("echo " + " ".join(["x"] * (WORD_LIMIT - 1)))[0]), WORD_LIMIT)
        for command in ("echo " + " ".join(["x"] * WORD_LIMIT), "echo x; echo " + " ".join(["x"] * (WORD_LIMIT - 2)), "echo " + " ".join(["x"] * (WORD_LIMIT // 2)) + "; sh -c 'echo " + " ".join(["x"] * (WORD_LIMIT // 2)) + "'"):
            with self.subTest(command=command[:40]):
                with self.assertRaisesRegex(OpaqueCommand, f"more than {WORD_LIMIT} words"):
                    walk(command)

    def test_large_inputs_fail_fast(self):
        inputs = {
            "2000 find runs": "find . " + " ".join(f"-exec echo {i} \\;" for i in range(2_000)),
            "2000 find {} + runs": "find . " + " ".join("-exec ls {} +" for _ in range(2_000)),
            "2000 statements": "; ".join(f"echo x > /tmp/f{i}" for i in range(2_000)),
            "2000 targets": "echo x" + "".join(f" > /tmp/a{i}" for i in range(2_000)),
        }
        for label, command in inputs.items():
            with self.subTest(input=label):
                started = time.perf_counter()
                with self.assertRaises(OpaqueCommand):
                    walk(command)
                self.assertLess(time.perf_counter() - started, 0.05)

    def test_walk_results_are_independent_copies(self):
        first = walk("ls -l; echo x")
        first[0].append("mutated")
        self.assertEqual(walk("ls -l; echo x"), [["ls", "-l"], ["echo", "x"]])

    def test_spans_bash_does_not_run_are_text(self):
        spans = "\n".join(f"- update `opt_{i}`" for i in range(300))
        cases = {
            "quoted heredoc in $()": f"gh pr create --title x --body \"$(cat <<'EOF'\n{spans}\nEOF\n)\"",
            "commit heredoc": f"git commit -m \"$(cat <<'EOF'\n{spans}\nEOF\n)\"",
            "quoted heredoc": f"cat > docs/api.md <<'EOF'\n{spans}\nEOF",
            "double-quoted delimiter": f'cat > docs/api.md <<"EOF"\n{spans}\nEOF',
            "escaped delimiter": f"cat > docs/api.md <<\\EOF\n{spans}\nEOF",
            "single quotes": "gh pr comment 5 --body '" + " ".join(f"`opt_{i}`" for i in range(300)) + "'",
            "ANSI-C quotes": "echo $'" + " ".join(f"`opt_{i}`" for i in range(300)) + "'",
            "escaped in double quotes": 'echo "' + " ".join(f"\\`opt_{i}\\`" for i in range(300)) + '"',
            "escaped unquoted": "echo " + " ".join(f"\\`opt_{i}\\`" for i in range(300)),
            "comment": "echo hi # " + " ".join(f"`opt_{i}`" for i in range(300)),
        }
        for label, command in cases.items():
            for form in (command, shell_command({"command": ["bash", "-lc", command]})):
                with self.subTest(case=label, form=form[:12]):
                    statements = walk(form)
                    self.assertFalse(any(s and s[0].startswith("opt_") for s in statements), statements[:5])

    def test_substitutions_bash_runs_are_walked(self):
        cases = {
            "unquoted heredoc": "cat > docs/a.md <<EOF\n`claude -p hi`\nEOF",
            "unquoted heredoc $()": "cat <<EOF\n$(claude -p hi)\nEOF",
            "double quotes": 'echo "`claude -p hi`"',
            "$() in double quotes": 'echo "$(claude -p hi)"',
            "after a comment with a quote": "echo hi # it's\necho $(claude -p hi)",
            "after a comment with <<": "# use <<EOF\necho $(claude -p hi)",
            "after a $() heredoc": "x=$(cat <<'EOF'\nfoo (\nEOF\n)\necho `claude -p hi`",
            "nested backticks": "echo `echo \\`claude -p hi\\``",
            "inside $() in double quotes": 'echo "$(echo "a" `claude -p hi`)"',
            "unclosed $()": "echo $(claude -p hi",
        }
        for label, command in cases.items():
            with self.subTest(case=label):
                self.assertIn(["claude", "-p", "hi"], walk(command))

    def test_rough_bodies_count_toward_the_limits(self):
        body = "echo it's; " + "; ".join(["true"] * STATEMENT_LIMIT)
        with self.assertRaisesRegex(OpaqueCommand, f"more than {STATEMENT_LIMIT} statements"):
            walk(f'echo "$({body})"')
        words = "echo it's " + " ".join(["x"] * WORD_LIMIT)
        with self.assertRaisesRegex(OpaqueCommand, f"more than {WORD_LIMIT} words"):
            walk(f'echo "$({words})"')

    def test_limit_reasons_say_how_to_recover(self):
        for command, limit in (("; ".join(["true"] * (STATEMENT_LIMIT + 1)), "statements"), ("echo " + " ".join(["x"] * WORD_LIMIT), "words")):
            with self.subTest(limit=limit):
                with self.assertRaises(OpaqueCommand) as caught:
                    walk(command)
                self.assertIn(limit, str(caught.exception))
                self.assertIn("put a long script in a file under the workspace", str(caught.exception))


class WrapperOptionTest(unittest.TestCase):
    def test_help_value_options_and_abbreviations(self):
        cases = {
            "env -a x claude -p hi": ["claude", "-p", "hi"],
            "env --argv0=x claude -p hi": ["claude", "-p", "hi"],
            "env --arg x claude -p hi": ["claude", "-p", "hi"],
            "env -ia x claude -p hi": ["claude", "-p", "hi"],
            "env -S '-a x' claude -p hi": ["claude", "-p", "hi"],
            "env -S claude -p hi": ["claude", "-p", "hi"],
            "env -u X -S 'Y=1 claude' -p hi": ["claude", "-p", "hi"],
            "timeout --sig KILL 5 claude -p hi": ["claude", "-p", "hi"],
            "nice --adj 5 claude -p hi": ["claude", "-p", "hi"],
            "stdbuf --out L claude -p hi": ["claude", "-p", "hi"],
            "ionice --classd 7 claude -p hi": ["claude", "-p", "hi"],
            "flock --time 5 /tmp/l claude -p hi": ["claude", "-p", "hi"],
            "xargs -iI claude -p hi": ["claude", "-p", "hi"],
            "xargs --eof claude -p hi": ["claude", "-p", "hi"],
            "su -s /usr/bin/claude root": ["/usr/bin/claude"],
        }
        for command, expected in cases.items():
            with self.subTest(command=command):
                self.assertEqual(argv(command), expected)

    def test_watch_and_su_command_values(self):
        cases = {
            "watch -q 2 claude -p hi": ["claude -p hi"],
            "watch --equexit 2 claude -p hi": ["claude -p hi"],
            "watch --equ 2 -n 1 claude -p hi": ["claude -p hi"],
            "watch -q2 claude -p hi": ["claude -p hi"],
            "su --session-command 'claude -p hi' root": ["claude -p hi"],
            "su --session-command='claude -p hi'": ["claude -p hi"],
            "su --sess 'claude -p hi' root": ["claude -p hi"],
            "script -q /dev/null --comm 'claude -p hi'": ["claude -p hi"],
        }
        for command, nested in cases.items():
            with self.subTest(command=command):
                self.assertEqual(resolve(parse(command)[0]).nested, nested)

    def test_unknown_or_ambiguous_long_options_are_opaque(self):
        for command in (
            "ionice --cl 3 claude -p hi",
            "env --frobnicate claude -p hi",
            "env --=x claude -p hi",
            "nice --x 5 claude -p hi",
            "flock --no /tmp/l claude -p hi",
            "watch --c claude -p hi",
            "su --s 'claude -p hi' root",
            "timeout --ver 5 claude -p hi",
            "ionice --clas 3 claude -p hi",
            "nice env --unknown claude -p hi",
        ):
            with self.subTest(command=command):
                with self.assertRaisesRegex(OpaqueCommand, "long options"):
                    walk(command)
        for command in ("ionice --class 3 ls", "ionice --classd 7 ls", "env --ignore-env ls", "timeout --foreground 5 ls", "sudo --user root ls", "git --no-pager log"):
            with self.subTest(command=command):
                walk(command)

    def test_argv0_suppresses_only_the_lookup(self):
        self.assertTrue(resolve(parse("command -v capsh")[0]).lookup)
        for command in ("env -a x command -v capsh", "env --arg x command -v capsh", "env -S '-a x' command -v capsh"):
            with self.subTest(command=command):
                resolved = resolve(parse(command)[0])
                self.assertFalse(resolved.lookup)
                self.assertEqual(resolved.argv, ["capsh"])
                self.assertEqual(resolved.nested, [])


class ShellNameTest(unittest.TestCase):
    def test_shell_stems_and_versions(self):
        for name in ("sh", "bash", "rbash", "dash", "ash", "zsh", "rzsh", "ksh", "mksh", "csh", "tcsh", "yash", "posh", "fish", "zsh5", "bash5.2", "ksh93", "zsh-5.9", "bash-5.2"):
            with self.subTest(name=name):
                self.assertTrue(shell_name(name))
        for name in ("push", "hash", "ssh", "busybox", "capsh", "chsh", "fsh", "bash-", "zsh-x", "test.sh", "flash"):
            with self.subTest(name=name):
                self.assertFalse(shell_name(name))

    def test_busybox_needs_a_shell_applet(self):
        self.assertEqual(resolve(["busybox", "sh", "-c", "gh pr merge 5"]).nested, ["gh pr merge 5"])
        self.assertEqual(resolve(["busybox", "ls", "-l"]).argv, ["busybox", "ls", "-l"])
        self.assertFalse(resolve(["busybox", "ls"]).stdin_exec)
