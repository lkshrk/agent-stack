import unittest

from agent_stack_policies.merge import no_merge

from . import tool_call_event

EVENTS = [
    tool_call_event("bash", {"command": "gh pr merge 5 --squash"}),
    tool_call_event("bash", {"command": "git push -u origin feat/x"}),
    tool_call_event("bash", {"command": "git push --force origin feat/x"}),
    tool_call_event("bash", {"command": "echo `date` push"}),
    tool_call_event("bash", {"command": 'echo "unterminated push'}),
    tool_call_event("bash", {}),
    tool_call_event("mcp__github__merge_pull_request", {"number": 1}),
    tool_call_event("Read", {"file_path": "a"}),
    {"type": "tool_call", "target": "bash", "data": {"name": "bash", "arguments": "{bad"}},
    {"type": "other"},
]


def run(command, **kwargs):
    return no_merge(**kwargs)(tool_call_event("bash", {"command": command}))


class NoMergeTest(unittest.TestCase):
    def assertDenied(self, command, text=None, **kwargs):
        decision = run(command, **kwargs)
        self.assertIsNotNone(decision, command)
        self.assertEqual(decision["result"], "DENY", command)
        self.assertTrue(decision["reason"].startswith("agent-stack: "))
        if text:
            self.assertIn(text, decision["reason"], command)

    def test_no_merge_gh_pr_merge(self):
        self.assertDenied("gh pr merge 5 --squash")
        self.assertDenied("gh pr merge")
        self.assertDenied("cd x && gh pr merge 5")
        self.assertDenied("gh -R o/r pr merge 5")

    def test_no_merge_gh_api_put(self):
        self.assertDenied("gh api -X PUT repos/o/r/pulls/5/merge")
        self.assertDenied("gh api -X POST repos/o/r/merges -f base=main")

    def test_no_merge_gh_api_put_path_suffixes(self):
        for path in (
            "repos/o/r/pulls/5/merge#x",
            "/repos/o/r/pulls/5/merge/",
            "repos/o/r/pulls/5/merge?x=1",
            "repos/o/r/merges/",
            "repos/o/r/merges#x",
        ):
            with self.subTest(path=path):
                self.assertDenied(f"gh api -X PUT {path}")
                self.assertDenied(f"bash -lc 'gh api -X PUT {path}'")
                listed = no_merge()(tool_call_event("bash", {"command": ["bash", "-lc", f"gh api -X PUT {path}"]}))
                self.assertEqual(listed["result"], "DENY")
        self.assertEqual(run("gh api repos/o/r/pulls/5/mergeable")["result"], "ALLOW")

    def test_no_merge_gh_api_path_case_insensitive(self):
        for path in ("repos/o/r/pulls/5/MERGE", "repos/o/r/PULLS/5/merge", "repos/o/r/Pulls/5/Merge", "repos/o/r/MERGES"):
            with self.subTest(path=path):
                self.assertDenied(f"gh api -X PUT {path}")
                listed = no_merge()(tool_call_event("bash", {"command": ["gh", "api", "-X", "PUT", path]}))
                self.assertEqual(listed["result"], "DENY")
        self.assertEqual(run("gh api repos/o/r/PULLS/5/MERGEABLE")["result"], "ALLOW")

    def test_merge_named_paths_with_substitution_allowed(self):
        for command in (
            "git diff $(git merge-base main HEAD) -- src/merge/x.py",
            'rg -n "$(git rev-parse HEAD)" src/Merge/',
            "git log --oneline $(git merge-base main HEAD)..HEAD -- lib/merges/",
        ):
            with self.subTest(command=command):
                self.assertEqual(run(command)["result"], "ALLOW")
        self.assertDenied("gh api -X PUT https://api.github.com/repos/$o/r/merges -f base=main")

    def test_no_merge_variable_pr_number_with_suffix(self):
        for command in (
            "n=5; gh api -X PUT repos/o/r/pulls/$n/merge#x",
            "gh api -X PUT repos/o/r/pulls/$N/MERGE",
            "gh api -X PUT repos/o/r/pulls/$n/merge/",
            "gh api -X PUT repos/o/r/pulls/$n/merge?x=1",
        ):
            with self.subTest(command=command):
                self.assertDenied(command)

    def test_no_merge_gh_api_read_allowed(self):
        self.assertEqual(run("gh api repos/o/r/pulls/5")["result"], "ALLOW")

    def test_no_merge_push_main(self):
        self.assertDenied("git push origin HEAD:main")
        self.assertDenied("git push origin main")
        self.assertDenied("git push origin refs/heads/master")
        self.assertDenied("git push origin HEAD:refs/heads/main")
        self.assertDenied("git push origin --delete main")
        self.assertDenied("git push origin :main")

    def test_no_merge_custom_protected_branches(self):
        self.assertDenied("git push origin release", protected_branches=["release"])
        self.assertEqual(run("git push origin main", protected_branches=["release"])["result"], "ALLOW")

    def test_no_merge_feature_push_allowed(self):
        self.assertEqual(run("git push -u origin feat/x")["result"], "ALLOW")
        self.assertEqual(run("git push origin HEAD:feat/x")["result"], "ALLOW")

    def test_no_merge_implicit_push_destination_denied(self):
        for command in (
            "git push",
            "git push origin",
            "git push -u origin",
            "git push origin HEAD",
            "git push -u origin HEAD",
            "git push --set-upstream origin HEAD",
            "git push origin @",
            "git push origin HEAD feat/x",
            "git -C .worktrees/t1 push",
            "git push -o ci.skip origin",
            "git push --push-option=ci.skip origin",
            "git push --push-option ci.skip origin HEAD",
            "bash -lc 'git push origin HEAD'",
        ):
            with self.subTest(command=command):
                self.assertDenied(command, "git push origin HEAD:<branch>")
        for command in (
            "git push origin HEAD:feat/x",
            "git push -u origin HEAD:feat/x",
            "git push -o ci.skip origin HEAD:feat/x",
            "git push --push-option=ci.skip origin feat/x",
            "git -C .worktrees/t1 push -u origin HEAD:polly/t1",
            "git push origin feat/x HEAD:feat/y",
        ):
            with self.subTest(command=command):
                self.assertEqual(run(command)["result"], "ALLOW")

    def test_no_merge_push_option_values_are_not_refspecs(self):
        for command in (
            "git push -uo ci.skip origin",
            "git push -qo ci.skip origin",
            "git push --push-opt ci.skip origin",
            "git push --recurse-submodules no origin",
            "git -C push push origin",
            "git --work-tree push push origin",
            "git push --rec no origin",
            "git push --rep origin",
            "git push --receive-pack x origin",
            "git push --exe x origin",
        ):
            for form in (command, f"bash -lc '{command}'"):
                with self.subTest(form=form):
                    self.assertDenied(form, "git push origin HEAD:<branch>")
                listed = no_merge()(tool_call_event("bash", {"command": ["bash", "-lc", command]}))
                self.assertEqual(listed["result"], "DENY")
        for command in (
            "git push -uo ci.skip origin HEAD:feat/x",
            "git push -o ci.skip -u origin HEAD:feat/x",
            "git push -oci.skip origin HEAD:feat/x",
            "git push -of origin HEAD:feat/x",
            "git push --recurse-submodules no origin HEAD:feat/x",
            "git push --recurse-submodules=check origin HEAD:feat/x",
            "git push --push-opt=ci.skip origin feat/x",
            "git -C push push origin HEAD:feat/x",
            "git push --no-force origin feat/x",
            "git push --no-tags origin HEAD:feat/x",
            "git push --no-verify origin HEAD:feat/x",
        ):
            with self.subTest(command=command):
                self.assertEqual(run(command)["result"], "ALLOW")

    def test_no_merge_git_global_value_options(self):
        for command in (
            "git --attr-source push push origin",
            "git --shallow-file push push origin",
            "git --frobnicate push push origin",
            "git --frobnicate push origin HEAD:feat/x",
            "git --end-of-options push origin HEAD:feat/x",
        ):
            for form in (command, ["bash", "-lc", command]):
                with self.subTest(form=form):
                    self.assertEqual(no_merge()(tool_call_event("bash", {"command": form}))["result"], "DENY")
        self.assertDenied("git --frobnicate status", "unknown git option --frobnicate")
        for command in (
            "git -C .worktrees/t1 status",
            "git --no-pager log -1",
            "git -P log -1",
            "git --git-dir=.git status",
            "git --literal-pathspecs push origin HEAD:feat/x",
            "git --attr-source HEAD push origin HEAD:feat/x",
            "git --shallow-file x push -u origin HEAD:feat/x",
            "git --no-optional-locks status",
            "git --exec-path",
            "git --version",
            "git --list-cmds=main",
        ):
            with self.subTest(command=command):
                self.assertEqual(run(command)["result"], "ALLOW")

    def test_no_merge_word_git_before_long_option_allowed(self):
        for command in (
            "rg -n git --hidden",
            "grep -rn git --include '*.md' docs",
            "rg -w git --type md docs",
            "apt-get install -y git --no-install-recommends",
            "echo use git --frob carefully",
        ):
            for form in (command, ["bash", "-lc", command]):
                with self.subTest(form=form):
                    self.assertEqual(no_merge()(tool_call_event("bash", {"command": form}))["result"], "ALLOW")
        for command in ("frob git --shallow-file push push origin", "frob git --frobnicate push origin HEAD:feat/x", "frob git --frob config alias.p push"):
            with self.subTest(command=command):
                self.assertDenied(command)

    def test_no_merge_end_of_options_in_push(self):
        for command in (
            "git push --end-of-options origin HEAD:feat/x -o HEAD:main",
            "git push --end-of-options origin",
        ):
            for form in (command, ["bash", "-lc", command]):
                with self.subTest(form=form):
                    self.assertEqual(no_merge()(tool_call_event("bash", {"command": form}))["result"], "DENY")
        self.assertEqual(run("git push --end-of-options origin HEAD:feat/x")["result"], "ALLOW")

    def test_no_merge_abbreviated_force_and_tag_flags_denied(self):
        for command in (
            "git push --force-w origin HEAD:feat/x",
            "git push --force-with origin HEAD:feat/x",
            "git push --force-w=feat/x origin HEAD:feat/x",
            "git push --force-i origin HEAD:feat/x",
            "git push -uf origin HEAD:feat/x",
        ):
            for form in (command, ["bash", "-lc", command]):
                with self.subTest(form=form):
                    decision = no_merge()(tool_call_event("bash", {"command": form}))
                    self.assertEqual(decision["result"], "DENY")
                    self.assertIn("force", decision["reason"])
        for command in (
            "git push --tag origin HEAD:feat/x",
            "git push --follow origin HEAD:feat/x",
            "git push --mirr origin",
            "git push --al origin",
            "git push --branches origin",
            "git push --bran origin HEAD:feat/x",
        ):
            for form in (command, ["bash", "-lc", command]):
                with self.subTest(form=form):
                    decision = no_merge()(tool_call_event("bash", {"command": form}))
                    self.assertEqual(decision["result"], "DENY")
                    self.assertIn("tags or all refs", decision["reason"])
        for command in ("git push --f origin HEAD:feat/x", "git push --forc origin HEAD:feat/x", "git push --no-forc origin HEAD:feat/x"):
            with self.subTest(command=command):
                self.assertDenied(command, "ambiguous")

    def test_no_merge_force_push_denied(self):
        for command in (
            "git push --force origin feat/x",
            "git push -f origin feat/x",
            "git push --force-with-lease origin feat/x",
            "git push --force-with-lease=feat/x origin feat/x",
            "git push --force-if-includes origin feat/x",
            "git push origin +feat/x",
            "git push origin +HEAD:feat/x",
        ):
            self.assertDenied(command, "force-push")

    def test_no_merge_short_ref_and_tag_pushes_denied(self):
        for command in (
            "git push origin HEAD:heads/main",
            "git push origin heads/main",
            "git push origin HEAD:heads/master",
            "git push origin +HEAD:heads/main",
            "git push origin HEAD:tags/v1",
            "git push origin HEAD:refs/tags/v1",
            "git push origin refs/tags/v1",
            "git push origin tags/v1",
            "git push origin HEAD:refs/notes/x",
            "git push origin HEAD:refs/heads/refs/heads/main",
            "git push --tags origin",
            "git push origin --tags",
            "git push --follow-tags origin HEAD:feat/x",
            "git push --mirror origin",
            "git push --all origin",
        ):
            self.assertDenied(command)
        self.assertDenied("git push origin HEAD:heads/main", "main")
        self.assertDenied("git push origin HEAD:refs/tags/v1", "tag")

    def test_no_merge_branch_pushes_still_allowed(self):
        for command in ("git push origin HEAD:feat/x", "git push -u origin feat/x", "git push origin refs/heads/feat/x"):
            self.assertEqual(run(command)["result"], "ALLOW", command)

    def test_no_merge_substitution_denied(self):
        self.assertDenied("git push origin `echo main`")
        self.assertDenied("git push origin $(echo main)")
        self.assertDenied("gh pr $(echo merge) 5")

    def test_no_merge_substitution_unrelated_allowed(self):
        self.assertEqual(run("echo $(date)")["result"], "ALLOW")

    def test_no_merge_nested_shell_denied(self):
        self.assertDenied("bash -c 'gh pr merge 5'")
        self.assertDenied("sh -c \"git push origin main\"")

    def test_no_merge_unparseable_statement_denied(self):
        self.assertDenied('git push origin "main')

    def test_no_merge_unrelated_allowed(self):
        self.assertEqual(run("pytest -q && git status")["result"], "ALLOW")

    def test_no_merge_arguments_unparseable_denied(self):
        event = {"type": "tool_call", "target": "bash", "data": {"name": "bash", "arguments": "{bad"}}
        self.assertEqual(no_merge()(event)["result"], "DENY")

    def test_no_merge_mcp_merge_tool(self):
        decision = no_merge()(tool_call_event("mcp__github__merge_pull_request", {"number": 1}))
        self.assertEqual(decision["result"], "DENY")

    def test_no_merge_other_tools_abstain(self):
        self.assertIsNone(no_merge()(tool_call_event("Read", {"file_path": "a"})))
        self.assertIsNone(no_merge()({"type": "other"}))


class QuoteAwareMergeTest(unittest.TestCase):
    def test_quoted_text_allowed(self):
        for command in (
            'git commit -m "fix: a; b"',
            "git commit -m \"don't push yet\"",
            'gh pr create --title x --body "Don\'t merge until CI is green"',
            'grep -E "merge|push" log',
            "git commit -m \"$(cat <<'EOF'\nIt's done\nEOF\n)\"",
        ):
            self.assertEqual(run(command)["result"], "ALLOW", command)

    def test_newline_separates_statements(self):
        self.assertEqual(run("echo a\ngh pr merge 5")["result"], "DENY")

    def test_nested_shell_login_flag(self):
        self.assertEqual(run("bash -lc 'git push origin main'")["result"], "DENY")


class SubstitutionTest(unittest.TestCase):
    def test_quoted_and_nested_substitution_denied(self):
        for command in (
            'git push origin "$(echo main)"',
            'git push origin "$(git branch --show-current)"',
            "git push origin HEAD:$(git branch --show-current)",
            "gh api -X PUT repos/o/r/pulls/$(echo 5)/merge",
            'echo "$(gh pr merge 5)"',
            'x="$(git push origin main)"',
        ):
            self.assertEqual(run(command)["result"], "DENY", command)

    def test_backslash_newline_joined(self):
        self.assertEqual(run("git push origin \\\nmain")["result"], "DENY")

    def test_other_shells_and_eval(self):
        self.assertEqual(run("dash -c 'git push origin main'")["result"], "DENY")
        self.assertEqual(run("ksh -c 'gh pr merge 5'")["result"], "DENY")
        self.assertEqual(run("eval 'git push origin main'")["result"], "DENY")
        self.assertEqual(run("eval git push origin main")["result"], "DENY")

    def test_abstains_on_agent_start_probe(self):
        self.assertIsNone(no_merge()(tool_call_event("sys_agent_start")))


class FinalReviewMergeTest(unittest.TestCase):
    def test_substitution_reason_names_the_fix(self):
        reason = run("git push -u origin $(git branch --show-current)")["reason"]
        self.assertIn("name the branch literally", reason)

    def test_graphql_merge_mutations_denied(self):
        for command in (
            "gh api graphql -f query='mutation { mergePullRequest(input: {pullRequestId: \"x\"}) { clientMutationId } }'",
            "gh api graphql -F query=@q.graphql -f q='enablePullRequestAutoMerge'",
        ):
            decision = run(command)
            self.assertEqual(decision["result"], "DENY", command)
            self.assertIn("graphql", decision["reason"])
        self.assertEqual(run("gh api graphql -f query='{ viewer { login } }'")["result"], "ALLOW")

    def test_wrappers_resolved(self):
        for command in (
            "timeout 60 git push origin main",
            "setsid git push --force origin feat/x",
            "flock /tmp/l -c 'gh pr merge 5'",
            "script -qc 'git push origin main' /dev/null",
            "parallel ::: 'gh pr merge 5'",
            "env -S 'git push origin main'",
        ):
            self.assertEqual(run(command)["result"], "DENY", command)

    def test_list_and_other_command_shapes(self):
        policy = no_merge()
        self.assertEqual(policy(tool_call_event("shell", {"command": ["bash", "-lc", "gh pr merge 5"]}))["result"], "DENY")
        self.assertEqual(policy(tool_call_event("shell", {"command": ["git", "status"]}))["result"], "ALLOW")
        self.assertEqual(policy(tool_call_event("shell", {"command": {"a": 1}}))["result"], "DENY")


class StdinArgumentsTest(unittest.TestCase):
    def test_xargs_into_push_or_merge_denied(self):
        for command in (
            "echo main | xargs git push origin",
            "echo feat/x | xargs git push origin",
            "echo 5 | xargs gh pr merge",
            "echo 5 | xargs -I{} gh pr merge {}",
            "ls | parallel git push origin",
            "cat <(git push origin main)",
            "diff <(gh pr merge 5) a",
        ):
            with self.subTest(command=command):
                self.assertEqual(run(command)["result"], "DENY")

    def test_xargs_elsewhere_allowed(self):
        for command in ("ls | xargs wc -l", "echo a | xargs git log -1", "cat <(git log -1)"):
            with self.subTest(command=command):
                self.assertEqual(run(command)["result"], "ALLOW")


class VariableTargetTest(unittest.TestCase):
    def test_variable_in_push_or_merge_denied(self):
        for command in (
            "printf -v b %s main; git push origin $b",
            "b=main; git push origin $b",
            'git push origin "$BRANCH"',
            "git push origin HEAD:${B}",
            "gh api -X PUT repos/o/r/pulls/$N/merge",
        ):
            with self.subTest(command=command):
                self.assertEqual(run(command)["result"], "DENY")

    def test_literal_push_allowed(self):
        for command in ("git push -u origin feat/x", "echo $HOME && git push origin feat/x"):
            with self.subTest(command=command):
                self.assertEqual(run(command)["result"], "ALLOW")


class HiddenCommandTest(unittest.TestCase):
    def test_hidden_push_denied(self):
        for command in (
            "bash -c 'echo ${0@P}' '\\044(git push origin main)'",
            "cat '\\044(git push origin main)'; echo ${_@P}",
            "echo ${x@P}",
            "echo ${x@E}",
            "${G@P} push origin main",
            "git push origin ${b:-main}",
            "git push origin ${a[0]}",
            "bash -c 'git push origin \"$0\"' main",
            "bash -c 'git status' _ x",
        ):
            with self.subTest(command=command):
                self.assertEqual(run(command)["result"], "DENY")

    def test_plain_expansions_allowed(self):
        for command in (
            "echo ${HOME} && git push origin feat/x",
            "git push -u origin feat/x",
            "bash -lc 'make test' 2>&1 | tail -5",
            "echo ${x:-y}",
            "bash -lc 'pytest -q' > /tmp/log",
        ):
            with self.subTest(command=command):
                self.assertEqual(run(command)["result"], "ALLOW")


PUSH_GLOBS = ['touch HEAD:main; git push origin HEAD:ma?n', 'git push origin HEAD:ma[i]n', 'git push origin HEAD:{main,feat/x}', 'git push origin HEAD:{ma,}in', "git push origin 'HEAD:ma*'", 'git push {origin,} HEAD:feat/x']
CONFIG_REDIRECTS = [
    "git -c include.path=/tmp/c p origin",
    "git -c includeIf.gitdir:/repo/.path=/tmp/c p origin",
    "git -c IncludeIf.onbranch:main.path=/tmp/c p origin",
    "git --config-env=include.path=E p origin",
    "git config include.path /tmp/c",
    "git config --add includeif.gitdir:/x/.path /tmp/c",
    "GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=include.path GIT_CONFIG_VALUE_0=/tmp/c git p origin",
    "GIT_CONFIG_PARAMETERS=\"'include.path'='/tmp/c'\" git p origin",
    "git config alias.ship 'push origin HEAD:main'; git ship", "git config --global alias.ship 'push origin HEAD:main'", "git config --local --add alias.ship 'push origin HEAD:main'", "git config set alias.ship 'push origin HEAD:main'", "git config Alias.Ship 'push origin HEAD:main'", "git -c alias.ship='push origin HEAD:main' ship", "git -calias.ship='push origin HEAD:main' ship", 'git --config-env=alias.ship=SHIP ship', 'git --config-env alias.ship=SHIP ship', 'git config remote.origin.push HEAD:refs/heads/main && git push', 'git config remote.origin.url https://github.com/o/fork.git', 'git config remote.origin.pushurl https://github.com/o/fork.git', 'git config remote.pushDefault fork', 'git config branch.feat/x.merge refs/heads/main', 'git config branch.feat/x.remote upstream', 'git config push.default current', 'git config url.https://github.com/o/fork.git.insteadOf https://github.com/o/r.git', 'git config --unset alias.ship', "git -C repo config alias.ship 'push origin HEAD:main'", 'git -c remote.origin.push=HEAD:refs/heads/main push', 'git -c push.default=upstream push', "GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=alias.ship GIT_CONFIG_VALUE_0='push origin HEAD:main' git ship", 'export GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=remote.origin.push GIT_CONFIG_VALUE_0=HEAD:main; git push', 'GIT_CONFIG_PARAMETERS="\'alias.ship\'=\'push origin HEAD:main\'" git ship', 'GIT_CONFIG_GLOBAL=./evil.gitconfig git push origin feat/x', 'env GIT_CONFIG_SYSTEM=./evil git push origin feat/x']
EVERYDAY = ['git push -u origin feat/x', 'git push origin HEAD:feat/x', 'git config user.email bot@example.com', 'git config --get remote.origin.url', "git config --get-regexp '^alias\\.'", 'git config --list', 'git config get remote.origin.url', 'git config list --show-origin', 'git -c core.pager=cat log', 'git -c user.name=bot commit -m x', 'git config core.hooksPath .githooks', "rg 'alias.ship' docs"]


class PushRedirectTest(unittest.TestCase):
    def test_glob_and_brace_refspecs_denied(self):
        for command in PUSH_GLOBS:
            with self.subTest(command=command):
                self.assertEqual(run(command)["result"], "DENY")

    def test_config_redirects_denied(self):
        for command in CONFIG_REDIRECTS:
            with self.subTest(command=command):
                self.assertEqual(run(command)["result"], "DENY")

    def test_everyday_git_allowed(self):
        for command in EVERYDAY:
            with self.subTest(command=command):
                self.assertEqual(run(command)["result"], "ALLOW", run(command).get("reason"))


class QuotedNameTest(unittest.TestCase):
    def test_ansi_c_quoted_names_denied(self):
        for command in (
            "$'git' push origin main",
            "$'\\x67it' push origin main",
            '$"git" push origin main',
            "git $'push' origin main",
            "git $'\\x70ush' origin main",
            "gh pr $'merge' 5",
            "$'gh' pr merge 5",
            "git $'\\x70ush' origin main $'it\\'s'",
        ):
            with self.subTest(command=command):
                self.assertEqual(run(command)["result"], "DENY")

    def test_ansi_c_quoting_elsewhere_allowed(self):
        for command in ("echo $'a\\tb' && git push -u origin feat/x", "printf $'x\\n'", "git commit -m $'line\\nmore'"):
            with self.subTest(command=command):
                self.assertEqual(run(command)["result"], "ALLOW")


class MidWordQuotingTest(unittest.TestCase):
    def test_mid_word_quoting_denied(self):
        for command in (
            "g$'it' push origin main",
            "git p$'ush' origin main",
            "g$'h' pr merge 1",
            "gh pr m$'erge' 1",
            "g$'\\x69t' push origin main",
            "g$\"it\" push origin main",
            "git push origin m$'ain'",
            "git push origin HEAD:$'\\x6d'ain",
        ):
            with self.subTest(command=command):
                self.assertEqual(run(command)["result"], "DENY")

    def test_ansi_c_in_arguments_allowed(self):
        for command in ("git commit -m $'a\\nb'", "printf $'a\\tb'", "rg $'\\t' src", "git push -u origin feat/x"):
            with self.subTest(command=command):
                self.assertEqual(run(command)["result"], "ALLOW")


class AnsiCOpaqueWordTest(unittest.TestCase):
    def test_opaque_words_denied(self):
        for command in (
            "$'git\\0' push origin main",
            "git push origin $'main\\0x'",
            "git $'push\\0' origin main",
            "echo $'\\c'; git push origin main #'",
            "echo $'\\c'; git push origin main",
            "gh pr merge $'1\\0'",
            "$'gh\\x00' pr merge 1",
        ):
            with self.subTest(command=command):
                self.assertEqual(run(command)["result"], "DENY")


class HeredocTest(unittest.TestCase):
    def test_statements_after_a_heredoc_are_seen(self):
        for command in (
            "cat <<E\nit's\nE\ngit push origin main #'",
            "cat <<E\n$'\nE\ngit push origin main\n#'",
            "cat <<-E\n\tit's\n\tE\ngit push origin main",
            "cat <<A <<'B'\nit's\nA\n\"\nB\ngit push origin main",
            "cat <<E\nno end",
            "cat <<E\nit's",
        ):
            with self.subTest(command=command):
                self.assertEqual(run(command)["result"], "DENY")

    def test_heredoc_commit_form_allowed(self):
        for command in (
            "git commit -m \"$(cat <<'EOF'\nfix: it's done\n\nMore text.\nEOF\n)\"",
            "cat <<'EOF' > notes.md\nIt's fine\nEOF\ngit push -u origin feat/x",
        ):
            with self.subTest(command=command):
                self.assertEqual(run(command)["result"], "ALLOW")


class HeredocInQuotedSubstitutionTest(unittest.TestCase):
    def test_hidden_push_denied(self):
        for command in (
            "git commit -m \"$(cat <<'EOF'\nx\"\nEOF\n)\"\ngit push origin main\n#\"",
            "git commit -m \"$(cat <<EOF\nx\"\nEOF\n)\"\ngit push origin main\n#\"",
            "git commit -m \"$(cat <<'EOF'\nx\"\n)\"",
        ):
            with self.subTest(command=command):
                self.assertEqual(run(command)["result"], "DENY")

    def test_commit_and_pr_forms_allowed(self):
        for command in (
            "git commit -m \"$(cat <<'EOF'\nfix: it's done\n\nMore text.\nEOF\n)\"",
            "gh pr create --title \"fix: x\" --body \"$(cat <<'EOF'\n## Summary\n- it's \"quoted\"\n\n## Testing\n- make test\nEOF\n)\"",
            "git commit -m \"$(cat <<'EOF'\nfix: x\nEOF\n)\" && git push -u origin feat/x",
        ):
            with self.subTest(command=command):
                self.assertEqual(run(command)["result"], "ALLOW", run(command).get("reason"))


class CommentAfterSubstitutionTest(unittest.TestCase):
    def test_hidden_push_denied(self):
        for command in ("echo $(true)#; git push origin main", "echo $((1))#; git push origin main", "(true)# c\ngit push origin main"):
            with self.subTest(command=command):
                self.assertEqual(run(command)["result"], "DENY")

    def test_real_comment_allowed(self):
        self.assertEqual(run("(make test)# run tests\ngit push -u origin feat/x")["result"], "ALLOW")


class GhFlagsBeforeVerbTest(unittest.TestCase):
    def test_pr_merge_with_flags_between_denied(self):
        for command in (
            "gh pr -R o/r merge 5",
            "gh pr --repo=o/r merge 5",
            "gh pr --repo o/r merge 5",
            "gh --repo o/r pr --repo o/r merge 5",
            "gh -R o/r pr -R o/r merge",
            "gh pr -Ro/r merge 5",
            "gh api -X PUT 'repos/o/r/pulls/5/merge?merge_method=squash'",
            "gh alias set pm pr merge",
        ):
            with self.subTest(command=command):
                self.assertEqual(run(command)["result"], "DENY")

    def test_other_pr_verbs_allowed(self):
        for command in ("gh pr -R o/r view 5", "gh pr --repo=o/r diff 5", "gh pr view merge", "gh pr list --search merge"):
            with self.subTest(command=command):
                self.assertEqual(run(command)["result"], "ALLOW")
