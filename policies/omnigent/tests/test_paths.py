import unittest

from agent_stack_policies.paths import glob_match, path_guard

from . import assert_not_ask, tool_call_event

WORKFLOWS = ".github/workflows/**"
UNPARSEABLE = {"type": "tool_call", "target": "Write", "data": {"name": "Write", "arguments": "{bad"}}

EVENTS = [
    tool_call_event("Write", {"file_path": ".github/workflows/ci.yml"}),
    tool_call_event("Write", {"file_path": "docs/a.md"}),
    tool_call_event("Write", {}),
    tool_call_event("NotebookEdit", {"notebook_path": "n.ipynb"}),
    tool_call_event("bash", {"command": "echo x > .github/workflows/ci.yml"}),
    tool_call_event("bash", {"command": "pytest -q"}),
    tool_call_event("bash", {"command": 'echo "unterminated'}),
    tool_call_event("bash", {}),
    tool_call_event("Read", {"file_path": "a"}),
    UNPARSEABLE,
    {"type": "other"},
]


class GlobTest(unittest.TestCase):
    def test_glob_double_star(self):
        self.assertTrue(glob_match(".github/workflows/ci.yml", WORKFLOWS))
        self.assertTrue(glob_match(".github/workflows/a/b/ci.yml", WORKFLOWS))
        self.assertTrue(glob_match("a/b/.env", "**/.env"))
        self.assertTrue(glob_match(".env", "**/.env"))

    def test_glob_suffix_match_absolute(self):
        self.assertTrue(
            glob_match("/home/omnigent/work/repo/.github/workflows/ci.yml", WORKFLOWS)
        )
        self.assertFalse(glob_match("/home/omnigent/xgithub/workflows/ci.yml", WORKFLOWS))

    def test_glob_single_star_stays_in_segment(self):
        self.assertTrue(glob_match("src/a.py", "src/*.py"))
        self.assertFalse(glob_match("src/sub/a.py", "src/*.py"))
        self.assertTrue(glob_match("src/a.py", "src/?.py"))
        self.assertFalse(glob_match("src/ab.py", "src/?.py"))

    def test_glob_normalises_dot_slash_and_double_slash(self):
        self.assertTrue(glob_match("./.github//workflows/ci.yml", WORKFLOWS))


class WriteToolsTest(unittest.TestCase):
    def test_write_forbidden_denied(self):
        policy = path_guard(deny=[WORKFLOWS])
        for name in ("Write", "Edit", "MultiEdit", "sys_os_write"):
            decision = policy(tool_call_event(name, {"file_path": ".github/workflows/ci.yml"}))
            self.assertEqual(decision["result"], "DENY", name)
            self.assertTrue(decision["reason"].startswith("agent-stack: "))
            self.assertIn("forbidden path", decision["reason"])
        decision = policy(tool_call_event("NotebookEdit", {"notebook_path": ".github/workflows/n.ipynb"}))
        self.assertEqual(decision["result"], "DENY")

    def test_write_path_key_variant(self):
        decision = path_guard(deny=[WORKFLOWS])(
            tool_call_event("sys_os_write", {"path": ".github/workflows/ci.yml"})
        )
        self.assertEqual(decision["result"], "DENY")

    def test_write_no_path_denied(self):
        decision = path_guard(deny=[WORKFLOWS])(tool_call_event("Write", {}))
        self.assertEqual(decision["result"], "DENY")
        self.assertIn("path", decision["reason"])

    def test_write_unparseable_denied(self):
        self.assertEqual(path_guard(deny=[WORKFLOWS])(UNPARSEABLE)["result"], "DENY")

    def test_write_dotdot_denied(self):
        decision = path_guard(deny=[WORKFLOWS])(
            tool_call_event("Write", {"file_path": "docs/../.github/workflows/x.yml"})
        )
        self.assertEqual(decision["result"], "DENY")
        self.assertIn("escape", decision["reason"])

    def test_write_dotdot_denied_without_deny_rules(self):
        decision = path_guard()(tool_call_event("Write", {"file_path": "../x"}))
        self.assertEqual(decision["result"], "DENY")

    def test_env_example_exempt(self):
        policy = path_guard(deny=["**/.env*"], exempt=["**/.env.example"])
        self.assertEqual(policy(tool_call_event("Write", {"file_path": "backend/.env.example"}))["result"], "ALLOW")
        self.assertEqual(policy(tool_call_event("Write", {"file_path": "backend/.env"}))["result"], "DENY")

    def test_allow_only_docs(self):
        policy = path_guard(allow_only=["docs/**"])
        self.assertEqual(policy(tool_call_event("Write", {"file_path": "docs/a.md"}))["result"], "ALLOW")
        decision = policy(tool_call_event("Write", {"file_path": "src/a.py"}))
        self.assertEqual(decision["result"], "DENY")
        self.assertIn("outside allowed paths", decision["reason"])

    def test_other_tools_abstain(self):
        policy = path_guard(deny=[WORKFLOWS])
        self.assertIsNone(policy(tool_call_event("Read", {"file_path": ".github/workflows/ci.yml"})))
        self.assertIsNone(policy({"type": "other"}))


class ShellTest(unittest.TestCase):
    def test_shell_redirect_into_forbidden_denied(self):
        policy = path_guard(deny=[WORKFLOWS, "**/.env"])
        for command in (
            "echo x > .github/workflows/ci.yml",
            "echo x >> .github/workflows/ci.yml",
            "echo x >.github/workflows/ci.yml",
            "sed -i s/a/b/ backend/.env",
            "cat a && tee .github/workflows/ci.yml",
            "cp --target=.github/workflows/ci.yml a",
        ):
            decision = policy(tool_call_event("bash", {"command": command}))
            self.assertEqual(decision["result"], "DENY", command)
            self.assertIn("forbidden path", decision["reason"])
            self.assertIn("Read", decision["reason"])

    def test_shell_exempt(self):
        policy = path_guard(deny=["**/.env*"], exempt=["**/.env.example"])
        self.assertEqual(policy(tool_call_event("bash", {"command": "cat a/.env.example"}))["result"], "ALLOW")

    def test_shell_unrelated_allowed(self):
        policy = path_guard(deny=[WORKFLOWS])
        self.assertEqual(policy(tool_call_event("bash", {"command": "pytest -q"}))["result"], "ALLOW")

    def test_shell_allow_only_does_not_constrain(self):
        policy = path_guard(allow_only=["docs/**"])
        self.assertEqual(policy(tool_call_event("bash", {"command": "pytest -q src"}))["result"], "ALLOW")

    def test_shell_unparseable_denied(self):
        policy = path_guard(deny=[WORKFLOWS])
        decision = policy(tool_call_event("bash", {"command": 'echo "unterminated'}))
        self.assertEqual(decision["result"], "DENY")

    def test_shell_unparseable_allowed_without_deny_or_allow_only(self):
        decision = path_guard(exempt=["**/.env.example"])(tool_call_event("bash", {"command": 'echo "u'}))
        self.assertEqual(decision["result"], "ALLOW")

    def test_shell_arguments_unparseable_denied(self):
        event = {"type": "tool_call", "target": "bash", "data": {"name": "bash", "arguments": "{bad"}}
        self.assertEqual(path_guard(deny=[WORKFLOWS])(event)["result"], "DENY")

    def test_shell_without_command_allowed(self):
        self.assertEqual(path_guard(deny=[WORKFLOWS])(tool_call_event("bash"))["result"], "ALLOW")


class QuoteAwareTest(unittest.TestCase):
    def test_quoted_operators_allowed(self):
        policy = path_guard(deny=[WORKFLOWS])
        for command in (
            'grep -E "foo|bar" src',
            'git commit -m "fix: a; b"',
            "git commit -m \"$(cat <<'EOF'\nIt's done\nEOF\n)\"",
        ):
            self.assertEqual(policy(tool_call_event("bash", {"command": command}))["result"], "ALLOW", command)

    def test_newline_separates_statements(self):
        policy = path_guard(deny=[WORKFLOWS])
        decision = policy(tool_call_event("bash", {"command": "echo a\ncat > .github/workflows/x.yml"}))
        self.assertEqual(decision["result"], "DENY")

    def test_directory_itself_matches_trailing_double_star(self):
        self.assertTrue(glob_match(".github/workflows", WORKFLOWS))
        self.assertFalse(glob_match(".github/workflowsx", WORKFLOWS))
        policy = path_guard(deny=[WORKFLOWS])
        for command in (
            "cp new.yml .github/workflows",
            "mv x.yml .github/workflows",
            "rm -rf .github/workflows",
        ):
            decision = policy(tool_call_event("bash", {"command": command}))
            self.assertEqual(decision["result"], "DENY", command)

    def test_nested_shell_checked(self):
        policy = path_guard(deny=[WORKFLOWS])
        for command in (
            'bash -c "echo x > .github/workflows/ci.yml"',
            "sh -lc 'rm .github/workflows/ci.yml'",
        ):
            decision = policy(tool_call_event("bash", {"command": command}))
            self.assertEqual(decision["result"], "DENY", command)


class SubstitutionPathTest(unittest.TestCase):
    def test_substitution_body_checked(self):
        policy = path_guard(deny=[WORKFLOWS])
        for command in (
            'echo "$(cp a .github/workflows/x.yml)"',
            "echo `cp a .github/workflows/x.yml`",
            "cp a \\\n.github/workflows/x.yml",
            "dash -c 'rm .github/workflows/x.yml'",
            "eval 'rm .github/workflows/x.yml'",
        ):
            self.assertEqual(policy(tool_call_event("bash", {"command": command}))["result"], "DENY", command)

    def test_abstains_on_agent_start_probe(self):
        self.assertIsNone(path_guard(deny=[".github/**"])(tool_call_event("sys_agent_start")))


def patch(*headers):
    return "*** Begin Patch\n" + "".join(f"{h}\n+x\n" for h in headers) + "*** End Patch"


class ApplyPatchTest(unittest.TestCase):
    def setUp(self):
        self.policy = path_guard(deny=[WORKFLOWS, "AGENTS.md", "**/.env"], exempt=["**/.env.example"])

    def verdict(self, arguments):
        return self.policy(tool_call_event("apply_patch", arguments))["result"]

    def test_every_header_checked(self):
        for header in (
            "*** Add File: .github/workflows/x.yml",
            "*** Update File: AGENTS.md",
            "*** Delete File: deploy/.env",
            "*** Update File: src/a.py\n*** Move to: .github/workflows/a.yml",
        ):
            with self.subTest(header=header):
                self.assertEqual(self.verdict({"command": patch("*** Add File: src/ok.py", header)}), "DENY")

    def test_indented_header_checked(self):
        for indent in ("\t", "  ", " \t "):
            with self.subTest(indent=repr(indent)):
                text = patch("*** Add File: src/b.py", f"{indent}*** Update File: .github/workflows/ci.yml")
                self.assertEqual(self.verdict({"command": text}), "DENY")
        self.assertEqual(self.verdict({"command": patch("  *** Update File: src/a.py")}), "ALLOW")

    def test_allowed_paths(self):
        arguments = {"input": patch("*** Add File: src/a.py", "*** Update File: deploy/.env.example")}
        self.assertEqual(self.verdict(arguments), "ALLOW")
        self.assertEqual(self.verdict({"patch": patch("*** Delete File: docs/old.md")}), "ALLOW")

    def test_argument_shapes(self):
        self.assertEqual(self.verdict({"command": ["apply_patch", patch("*** Add File: AGENTS.md")]}), "DENY")
        self.assertEqual(self.verdict({"command": ["apply_patch", patch("*** Add File: src/a.py")]}), "ALLOW")
        changes = [{"path": "src/a.py", "kind": {"type": "update", "move_path": ".github/workflows/a.yml"}}]
        self.assertEqual(self.verdict({"changes": changes}), "DENY")
        self.assertEqual(self.verdict({"changes": [{"path": "src/a.py", "kind": {"type": "add"}}]}), "ALLOW")

    def test_escape_and_allow_only(self):
        self.assertEqual(self.verdict({"command": patch("*** Add File: ../x.py")}), "DENY")
        docs = path_guard(allow_only=["docs/**"])
        decision = docs(tool_call_event("apply_patch", {"command": patch("*** Add File: src/a.py")}))
        self.assertEqual(decision["result"], "DENY")

    def test_unreadable_patch_denied(self):
        for arguments in (
            {},
            {"command": "*** Begin Patch\n*** End Patch"},
            {"command": "*** Add File: \n"},
            {"command": 5},
            {"changes": []},
            {"changes": [{"kind": "add"}]},
            {"other": patch("*** Add File: src/a.py")},
        ):
            with self.subTest(arguments=arguments):
                self.assertEqual(self.verdict(arguments), "DENY")


class NonStringCommandTest(unittest.TestCase):
    def test_list_command_joined_and_checked(self):
        policy = path_guard(deny=[WORKFLOWS])
        event = tool_call_event("shell", {"command": ["bash", "-lc", "rm .github/workflows/ci.yml"]})
        self.assertEqual(policy(event)["result"], "DENY")
        event = tool_call_event("shell", {"command": ["bash", "-lc", "pytest -q"]})
        self.assertEqual(policy(event)["result"], "ALLOW")

    def test_other_shapes_denied(self):
        policy = path_guard(deny=[WORKFLOWS])
        for command in ({"a": 1}, ["bash", 1], 5):
            self.assertEqual(policy(tool_call_event("bash", {"command": command}))["result"], "DENY", command)


class HeredocTest(unittest.TestCase):
    def test_unterminated_heredoc_denied(self):
        decision = path_guard(deny=[WORKFLOWS])(tool_call_event("bash", {"command": "cat <<E\nx"}))
        self.assertEqual(decision["result"], "DENY")

    def test_forbidden_path_after_heredoc_seen(self):
        command = "cat <<E\nit's\nE\nrm .github/workflows/ci.yml #'"
        self.assertEqual(path_guard(deny=[WORKFLOWS])(tool_call_event("bash", {"command": command}))["result"], "DENY")


POLLY_SCOPE = [".polly/registry.json", ".polly/diffs/**", "polly/skills/**"]
DOCS_SCOPE = ["**/*.md", "docs/**", "**/README*", "CHANGELOG*", "**/adr/**"]


class ShellWriteScopeTest(unittest.TestCase):
    def verdict(self, command, scope=POLLY_SCOPE):
        return path_guard(deny=[WORKFLOWS], allow_only=scope)(tool_call_event("bash", {"command": command}))["result"]

    def test_polly_writes_outside_scope_denied(self):
        for command in (
            "sed -i x src/app.py",
            "sed -i.bak -e s/a/b/ src/app.py",
            "sed --in-place s/a/b/ src/app.py",
            "echo x > src/app.py",
            "echo x >> src/app.py",
            "echo x &> src/app.py",
            "tee src/app.py",
            "echo x | tee -a src/app.py",
            "cp a src/b",
            "cp -t src a",
            "mv a src/b",
            "install -m 644 a src/b",
            "ln -s a src/b",
            "rsync -a a/ src/",
            "touch src/a",
            "rm src/a",
            "rm -f /tmp/x src/a",
            "rmdir src",
            "truncate -s 0 src/a",
            "mkdir src/x",
            "chmod +x src/a",
            "chown u src/a",
            "dd if=a of=src/a",
            "bash -c 'echo > src/a'",
            "timeout 5 tee src/a",
            "echo x > $OUT",
        ):
            with self.subTest(command=command):
                self.assertEqual(self.verdict(command), "DENY")

    def test_polly_everyday_allowed(self):
        for command in (
            "make test",
            "pnpm test 2>&1 | tail -20",
            "echo x > /tmp/y",
            "echo x > .polly/registry.json",
            "gh pr diff 3 > .polly/diffs/t1-abc.diff",
            "git -C .worktrees/t1 diff a...b > .polly/diffs/t1-b.diff",
            "rm /tmp/x",
            "tee /dev/null",
            "cp a /tmp/b",
            "mkdir -p .polly/diffs",
            "sed -n 1p src/app.py",
            "cat src/app.py > /dev/null",
            "rg x 2>/dev/null",
            "git worktree add .worktrees/t1 -b polly/t1",
        ):
            with self.subTest(command=command):
                self.assertEqual(self.verdict(command), "ALLOW")

    def test_documenter_scope(self):
        for command in ("sed -i s/a/b/ backend/app/main.py", "echo x > backend/x.py", "cp README.md backend/", "mv docs/a.md src/a.py"):
            with self.subTest(command=command):
                self.assertEqual(self.verdict(command, DOCS_SCOPE), "DENY")
        for command in ("sed -i s/a/b/ docs/setup.md", "echo x > docs/a.md", "make fmt", "cp docs/a.md docs/b.md", "rm docs/old.md"):
            with self.subTest(command=command):
                self.assertEqual(self.verdict(command, DOCS_SCOPE), "ALLOW")

    def test_redirects_do_not_hide_destinations(self):
        for command in (
            "cp a src/x >/dev/null",
            "cp a src/x 2>/dev/null",
            "mv a src/x 2>/dev/null",
            "ln -sf /tmp/x src/x >/dev/null",
            "rsync -a a/ src/ >/dev/null",
            "install -D a src/a > /dev/null",
            "cp a src/x 2>&1",
            "cp a src/x < /dev/null",
            "cp a src/x 2>>/tmp/err",
        ):
            for scope in (POLLY_SCOPE, DOCS_SCOPE):
                with self.subTest(command=command, scope=scope):
                    self.assertEqual(self.verdict(command, scope), "DENY")

    def test_redirects_are_not_write_operands(self):
        for command in (
            "mkdir -p .polly/diffs 2>/dev/null",
            "rm -f .polly/diffs/x.diff 2>/dev/null",
            "rm -f .polly/diffs/x.diff 2>&1",
            "mkdir -p .polly/diffs &>/dev/null",
            "xargs rm < /tmp/list",
            "cp /tmp/r.json .polly/registry.json 2>/dev/null",
        ):
            with self.subTest(command=command):
                self.assertEqual(self.verdict(command), "ALLOW")
        self.assertEqual(self.verdict("sed -i s/a/b/ docs/a.md 2>/dev/null", DOCS_SCOPE), "ALLOW")

    def test_redirect_target_still_checked(self):
        for command in ("rm -f .polly/diffs/x 2>src/a", "mkdir -p .polly/diffs 2>>src/a"):
            with self.subTest(command=command):
                self.assertEqual(self.verdict(command), "DENY")

    def test_dotdot_targets_normalised(self):
        for command in (
            "echo x > /tmp/../src/a",
            "cp src/a.py /tmp/../src/b.py",
            "echo x > .polly/diffs/../../src/a.py",
            "cp a .polly/diffs/../../src/a.py",
            "echo x &>/tmp/../src/a",
        ):
            with self.subTest(command=command):
                self.assertEqual(self.verdict(command), "DENY")
        self.assertEqual(self.verdict("echo x > /tmp/a/../b"), "ALLOW")

    def test_list_form_fused_redirects(self):
        guard = path_guard(deny=[WORKFLOWS], allow_only=POLLY_SCOPE)
        for body, expected in (
            ("pnpm test > /tmp/log 2>&1", "ALLOW"),
            ("make test 2>&1", "ALLOW"),
            ("make test >&2", "ALLOW"),
            ("make test &>/dev/null", "ALLOW"),
            ("make test &>src/a", "DENY"),
            ("make test >&src/a", "DENY"),
        ):
            with self.subTest(body=body):
                decision = guard(tool_call_event("bash", {"command": ["bash", "-lc", body]}))
                self.assertEqual(decision["result"], expected)
        self.assertEqual(self.verdict('echo x &>"src/a"'), "DENY")

    def test_mv_sources_are_writes(self):
        for command in ("mv src/a.py /tmp/x", "mv src/a.py .polly/diffs/x", "mv -t /tmp src/a.py", "mv src/a.py /tmp/x 2>/dev/null"):
            with self.subTest(command=command):
                self.assertEqual(self.verdict(command), "DENY")
        for command in ("mv /tmp/r.json .polly/registry.json", "mv .polly/diffs/a .polly/diffs/b", "mv -t .polly/diffs /tmp/a"):
            with self.subTest(command=command):
                self.assertEqual(self.verdict(command), "ALLOW")
        self.assertEqual(self.verdict("mv docs/a.md /tmp/a", DOCS_SCOPE), "ALLOW")
        self.assertEqual(self.verdict("cp src/a.py /tmp/x"), "ALLOW")

    def test_quoted_operators_are_operands(self):
        for command in (
            "cp /tmp/x '<' src/",
            'cp /tmp/x "<" src/',
            "cp /tmp/x \\< src/",
            "mv /tmp/x '<' src/",
            "cp /tmp/x '<' src/ 2>/dev/null",
            "cp /tmp/x '>' src/",
            "cp /tmp/x '2>' src/",
            "tee '>'",
        ):
            for form in (command, ["bash", "-lc", command]):
                with self.subTest(form=form):
                    guard = path_guard(deny=[WORKFLOWS], allow_only=POLLY_SCOPE)
                    self.assertEqual(guard(tool_call_event("bash", {"command": form}))["result"], "DENY")
        for command in ("rg -n '<' src > /tmp/x", "cp /tmp/x '<' .polly/diffs/"):
            with self.subTest(command=command):
                self.assertEqual(self.verdict(command), "ALLOW")

    def test_escaping_targets_denied(self):
        for command in ("echo x > ../other/x.md", "cp x docs/../../other/x.md", "sed -i s/a/b/ ../x.md", "mv docs/a.md ..", "tee ../README.md"):
            with self.subTest(command=command):
                self.assertEqual(self.verdict(command, DOCS_SCOPE), "DENY")
        for command in ("echo x > docs/../README.md", "cp docs/a/../b.md docs/c.md"):
            with self.subTest(command=command):
                self.assertEqual(self.verdict(command, DOCS_SCOPE), "ALLOW")
        self.assertEqual(self.verdict("echo x > ../.polly/registry.json"), "DENY")

    def test_absolute_and_home_targets_denied(self):
        for command in (
            "echo x > ~/x.md",
            "echo x > ~",
            "echo x > ~/../x.md",
            "tee ~other/x.md",
            "echo x > /etc/x.md",
            "cp /tmp/a.md /etc/x.md",
            "sed -i s/a/b/ /home/agent/docs/README.md",
            "tee ../other/x.md",
            "sed -i s/a/b/ ../other/README.md",
            "mv /tmp/a.md ../../x.md",
        ):
            for form in (command, ["bash", "-lc", command]):
                with self.subTest(form=form):
                    guard = path_guard(deny=[WORKFLOWS], allow_only=DOCS_SCOPE)
                    self.assertEqual(guard(tool_call_event("bash", {"command": form}))["result"], "DENY")
        self.assertEqual(self.verdict("git -C .worktrees/t1 diff a...b > /repo/.polly/diffs/t1-b.diff"), "DENY")
        for command in ("echo x > /tmp/a.md", "cat docs/a.md > /dev/null", "cp /tmp/a.md docs/a.md", "sed -i s/a/b/ docs/a.md"):
            with self.subTest(command=command):
                self.assertEqual(self.verdict(command, DOCS_SCOPE), "ALLOW")

    def test_dollar_targets_denied(self):
        for command in (
            "echo x > $HOME/x.md",
            "echo x > ${HOME}/x.md",
            "echo x > docs/$NAME.md",
            'echo x > "$(pwd)/docs/a.md"',
            "tee $OUT",
            "cp /tmp/a.md $DEST",
            "echo x &>$HOME/a.md",
        ):
            for scope in (DOCS_SCOPE, POLLY_SCOPE):
                for form in (command, ["bash", "-lc", command]):
                    with self.subTest(form=form, scope=scope):
                        guard = path_guard(deny=[WORKFLOWS], allow_only=scope)
                        self.assertEqual(guard(tool_call_event("bash", {"command": form}))["result"], "DENY")
        for command in ("make test 2>&1", "make test >&2", "pnpm test > /tmp/log 2>&1", "echo $HOME > /tmp/x", "cp $SRC .polly/diffs/x"):
            for form in (command, ["bash", "-lc", command]):
                with self.subTest(form=form):
                    guard = path_guard(deny=[WORKFLOWS], allow_only=POLLY_SCOPE)
                    self.assertEqual(guard(tool_call_event("bash", {"command": form}))["result"], "ALLOW")

    def test_joined_arguments_keep_quoted_operators(self):
        for command in (
            "eval '>' src/a.py",
            "eval echo x '>' src/a.py",
            'eval "echo x" ">" src/a.py',
            "eval echo x '>>' src/a.py",
            "eval echo x \\> src/a.py",
            "eval cat /tmp/a '|' tee src/a.py",
            "sudo eval echo x '>' src/a.py",
            "command eval echo x '>' src/a.py",
            "timeout 5 eval echo x '>' src/a.py",
            "bash -c 'eval echo x \">\" src/a.py'",
            "watch -n1 echo x '>' src/a.py",
            "parallel echo x '>' {} ::: src/a.py",
            "eval cp /tmp/x '<' src/",
        ):
            for form in (command, ["bash", "-lc", command]):
                with self.subTest(form=form):
                    guard = path_guard(deny=[WORKFLOWS], allow_only=POLLY_SCOPE)
                    self.assertEqual(guard(tool_call_event("bash", {"command": form}))["result"], "DENY")
        self.assertEqual(self.verdict("eval echo x '>' ../x.md", DOCS_SCOPE), "DENY")

    def test_brace_and_backtick_targets_denied(self):
        cases = (
            (POLLY_SCOPE, "echo x | tee .polly/diffs/{x,../../src/a.py}"),
            (POLLY_SCOPE, "touch .polly/diffs/{x,../../src/a.py}"),
            (POLLY_SCOPE, "cp /tmp/a.py .polly/diffs/{x,../../src/a.py}"),
            (POLLY_SCOPE, "echo x > .polly/diffs/{x,../../src/a.py}"),
            (POLLY_SCOPE, "echo x | tee .polly/{diffs,../src}/a.py"),
            (POLLY_SCOPE, "echo x > .polly/diffs/`{echo,..}`/`{echo,..}`/src/a.py"),
            (POLLY_SCOPE, "echo x | tee .polly/diffs/`{echo,..}`/`{echo,..}`/src/a.py"),
            (POLLY_SCOPE, "touch .polly/diffs/x{1..3}"),
            (DOCS_SCOPE, "echo x | tee {..,docs}/x.md"),
            (DOCS_SCOPE, "tee {/etc/x,docs/y}.md < /tmp/a"),
            (DOCS_SCOPE, "echo x | tee docs/{a,../../other/x}.md"),
            (DOCS_SCOPE, "echo x > `{echo,..}`/x.md"),
            (DOCS_SCOPE, "echo x | tee `{echo,..}`/x.md"),
        )
        for scope, command in cases:
            for form in (command, ["bash", "-lc", command]):
                with self.subTest(form=form):
                    guard = path_guard(deny=[WORKFLOWS], allow_only=scope)
                    self.assertEqual(guard(tool_call_event("bash", {"command": form}))["result"], "DENY")
        for command in ("echo x > .polly/diffs/a{b}.diff", "find .polly/diffs -name '*.diff' -mtime +7 -delete", "echo '{a,b}' > /tmp/x"):
            with self.subTest(command=command):
                self.assertEqual(self.verdict(command), "ALLOW")

    def test_attached_and_bundled_target_directory(self):
        cases = (
            (POLLY_SCOPE, "cp -tsrc /tmp/a.py"),
            (POLLY_SCOPE, "cp -t.polly/../src /tmp/a.py"),
            (POLLY_SCOPE, "cp -rt src /tmp/a.py"),
            (POLLY_SCOPE, "cp -vt src /tmp/a.py"),
            (POLLY_SCOPE, "install -Dt src /tmp/a.py"),
            (POLLY_SCOPE, "mv -t../x /tmp/a"),
            (POLLY_SCOPE, "cp --target-directory=src /tmp/a.py"),
            (POLLY_SCOPE, "cp -r --target-directory src /tmp/a.py"),
            (DOCS_SCOPE, "cp -t../other /tmp/a.md"),
            (DOCS_SCOPE, "cp -rt ../other /tmp/a.md"),
        )
        for scope, command in cases:
            for form in (command, ["bash", "-lc", command]):
                with self.subTest(form=form):
                    guard = path_guard(deny=[WORKFLOWS], allow_only=scope)
                    self.assertEqual(guard(tool_call_event("bash", {"command": form}))["result"], "DENY")
        for command in ("cp -rt .polly/diffs /tmp/a", "cp -t.polly/diffs /tmp/a", "install -Dm644 /tmp/r.json .polly/registry.json", "mkdir -pm 755 .polly/diffs"):
            with self.subTest(command=command):
                self.assertEqual(self.verdict(command), "ALLOW")
        self.assertEqual(self.verdict("sed -i.bak -e s/a/b/ docs/a.md", DOCS_SCOPE), "ALLOW")

    def test_exempt_does_not_widen_shell_scope(self):
        guard = path_guard(allow_only=["docs/**"], deny=["**/.env*"], exempt=["**/.env.example"])
        for command in ("echo x > backend/.env.example", "cp /tmp/a backend/.env.example", "tee .env.example"):
            for form in (command, ["bash", "-lc", command]):
                with self.subTest(form=form):
                    self.assertEqual(guard(tool_call_event("bash", {"command": form}))["result"], "DENY")
        write = guard(tool_call_event("Write", {"file_path": "backend/.env.example", "content": "x"}))
        self.assertEqual(write["result"], "DENY")
        self.assertEqual(guard(tool_call_event("bash", {"command": "echo x > docs/.env.example"}))["result"], "ALLOW")
        self.assertEqual(guard(tool_call_event("bash", {"command": "echo x > docs/.env"}))["result"], "DENY")

    def test_unparseable_denied_with_allow_only(self):
        guard = path_guard(allow_only=["docs/**"])
        for command in ("echo 'x > src/a.py", 'echo "x > src/a.py'):
            with self.subTest(command=command):
                self.assertEqual(guard(tool_call_event("bash", {"command": command}))["result"], "DENY")
        self.assertEqual(path_guard()(tool_call_event("bash", {"command": "echo 'x"}))["result"], "ALLOW")

    def test_relative_targets_anchored_at_root(self):
        docs = path_guard(deny=[WORKFLOWS], allow_only=DOCS_SCOPE, exempt=["**/.env.example"])
        polly = path_guard(deny=[WORKFLOWS], allow_only=POLLY_SCOPE)
        for guard, path in (
            (docs, "backend/app/docs/gen.py"),
            (docs, "src/CHANGELOG.py"),
            (docs, "x/docs/a.py"),
            (polly, "x/.polly/registry.json"),
            (polly, "src/.polly/diffs/a.diff"),
            (polly, "bundles/x/polly/skills/a/SKILL.md"),
        ):
            with self.subTest(path=path):
                self.assertEqual(guard(tool_call_event("Write", {"file_path": path, "content": "x"}))["result"], "DENY")
                for form in (f"echo x > {path}", ["bash", "-lc", f"echo x > {path}"]):
                    self.assertEqual(guard(tool_call_event("bash", {"command": form}))["result"], "DENY")
        for guard, path in (
            (docs, "docs/a.md"),
            (docs, "docs/x/gen.py"),
            (docs, "backend/README.md"),
            (docs, "a/b/notes.md"),
            (docs, "CHANGELOG.md"),
            (docs, "backend/adr/0001.md"),
            (docs, "./docs/a.py"),
            (polly, ".polly/registry.json"),
            (polly, ".polly/diffs/t1-abc.diff"),
            (polly, "polly/skills/x/SKILL.md"),
        ):
            with self.subTest(path=path):
                self.assertEqual(guard(tool_call_event("Write", {"file_path": path, "content": "x"}))["result"], "ALLOW")
                self.assertEqual(guard(tool_call_event("bash", {"command": f"echo x > {path}"}))["result"], "ALLOW")

    def test_absolute_write_paths_match_at_any_depth(self):
        docs = path_guard(deny=[WORKFLOWS], allow_only=DOCS_SCOPE)
        for path in ("/workspace/repo/docs/a.md", "/workspace/repo/backend/app/docs/gen.py", "/agents/polly/x.md"):
            with self.subTest(path=path):
                self.assertEqual(docs(tool_call_event("Write", {"file_path": path, "content": "x"}))["result"], "ALLOW")
                self.assertEqual(docs(tool_call_event("bash", {"command": f"echo x > {path}"}))["result"], "DENY")
        self.assertEqual(docs(tool_call_event("Write", {"file_path": "/workspace/repo/src/a.py", "content": "x"}))["result"], "DENY")

    def test_deny_still_matches_at_any_depth(self):
        guard = path_guard(deny=[WORKFLOWS, "**/.env"], allow_only=["**"])
        for path in ("sub/.github/workflows/ci.yml", ".github/workflows/ci.yml", "a/b/.env"):
            with self.subTest(path=path):
                self.assertEqual(guard(tool_call_event("Write", {"file_path": path, "content": "x"}))["result"], "DENY")

    def test_startup_files(self):
        startup = [".bashrc", ".bash_profile", ".bash_login", ".bash_logout", ".profile", ".zshrc", ".zshenv", ".zprofile", ".zlogin", ".zlogout", ".inputrc"]
        guard = path_guard(startup_files=startup, startup_home="/home/omnigent")
        for path in (
            *[f"/home/omnigent/{name}" for name in startup],
            *[f"~/{name}" for name in startup],
            *[f"~omnigent/{name}" for name in startup],
            *["$HOME/" + name for name in startup],
            *["${HOME}/" + name for name in startup],
            "/home/omnigent/./.bashrc",
            "/home/omnigent/x/../.bashrc",
            "~/.bash{rc,}",
            "/home/omnigent/.bash{rc,}",
            "~/.bash?c",
            "/home/omnigen?/.bashrc",
            "/home/*/.bashrc",
            "{~,/tmp/x}/.bashrc",
            "/{home,x}/omnigent/.bashrc",
            "~/.bash{r{c,},}",
        ):
            with self.subTest(path=path):
                self.assertEqual(guard(tool_call_event("Write", {"file_path": path, "content": "x"}))["result"], "DENY")
                self.assertEqual(guard(tool_call_event("bash", {"command": f"echo x > {path}"}))["result"], "DENY")
        for path in ("~/.bash{rc,_profile}", "~/.{bashrc,x}", "~/{.bashrc,x}", "~/.bashr{c..c}", "/home/omnigen{s..u}/.bashrc", "~/.bash[^x]c", "~/.bash[[:lower:]]c"):
            with self.subTest(path=path):
                self.assertEqual(guard(tool_call_event("Write", {"file_path": path, "content": "x"}))["result"], "DENY")
                self.assertEqual(guard(tool_call_event("bash", {"command": f"tee -a {path}"}))["result"], "DENY")
        for command in ("ls *", "du -sh *", "rm -rf build/*", "cp -r src/* /tmp/out/", "git branch --list 'feat/*'", "pytest tests/*", "rg -g '*' x", "rg -n '.*' README.md"):
            with self.subTest(command=command):
                self.assertEqual(guard(tool_call_event("bash", {"command": command}))["result"], "ALLOW")

        for command in ("sort -o ~/.bashrc /tmp/rc", "curl -so ~/.bashrc https://x", "wget -qO ~/.bashrc https://x", "perl -i -pe 's/a/b/' ~/.bashrc", "gawk -i inplace '{print}' ~/.bashrc", "git diff --output=/home/omnigent/.bashrc", "patch ~/.bashrc /tmp/p", "cat ~/.bashrc", "grep x ~/.bashrc", "cp ~/.bashrc /tmp/x"):
            with self.subTest(command=command):
                self.assertEqual(guard(tool_call_event("bash", {"command": command}))["result"], "DENY")
        for path in (".bashrc", "./.bashrc", "./.profile", "x/../.bashrc", "zsh/.zshrc", "dotfiles/.profile", "dotfiles/.bashrc", "/home/omnigent/work/dotfiles/zsh/.zshrc", "config/.profile", "~other/.bashrc", "/tmp/.bashrc"):
            with self.subTest(path=path):
                self.assertEqual(guard(tool_call_event("Write", {"file_path": path, "content": "x"}))["result"], "ALLOW")
                self.assertEqual(guard(tool_call_event("bash", {"command": f"echo x > {path}"}))["result"], "ALLOW")

    def test_without_allow_only_unchanged(self):
        guard = path_guard(deny=[WORKFLOWS])
        self.assertEqual(guard(tool_call_event("bash", {"command": "echo x > src/app.py"}))["result"], "ALLOW")
