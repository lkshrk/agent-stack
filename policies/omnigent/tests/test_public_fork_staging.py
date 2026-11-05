import os
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from . import require_yaml

require_yaml()

from agent_stack_policies import contribution_vcs
from agent_stack_policies.public_contributions import prepare_operation

from . import tool_call_event
from .fork_checkout import FORK, FORK_URL, UPSTREAM_URL, commit, fork_checkout, git

UPSTREAM = "omnigent-ai/omnigent"


class ForkStagingTest(unittest.TestCase):
    def setUp(self):
        context = fork_checkout()
        self.tree = context.__enter__()
        self.addCleanup(context.__exit__, None, None, None)

    def evaluate(self, command, cwd=None):
        return prepare_operation()(tool_call_event("Bash", {"command": command, "cwd": str(cwd or self.tree)}))

    def assertAllowed(self, command, cwd=None):
        decision = self.evaluate(command, cwd)
        self.assertIsNotNone(decision, command)
        self.assertEqual(decision["result"], "ALLOW", decision)

    def assertDenied(self, command, cwd=None):
        decision = self.evaluate(command, cwd)
        self.assertIsNotNone(decision, command)
        self.assertEqual(decision["result"], "DENY", decision)
        self.assertTrue(decision["reason"].startswith("agent-stack: "), decision)

    def test_allows_explicit_feature_push_to_the_fork_remote(self):
        for command in (
            ["git", "push", "fork", "HEAD:feat/x"],
            ["git", "push", "-u", "fork", "HEAD:fix/web-a"],
            ["git", "push", "--set-upstream", "fork", "HEAD:refs/heads/feat/x"],
        ):
            with self.subTest(command=command):
                self.assertAllowed(command)
        self.assertAllowed(["git", "push", "fork", "HEAD:feat/x"], cwd=self.tree / "web")

    def test_allows_any_remote_name_whose_push_url_is_the_fork(self):
        git(self.tree, "remote", "set-url", "origin", FORK_URL)
        git(self.tree, "remote", "add", "upstream", UPSTREAM_URL)
        self.assertAllowed(["git", "push", "origin", "HEAD:feat/x"])
        git(self.tree, "remote", "set-url", "origin", f"https://github.com/{FORK}")
        self.assertAllowed(["git", "push", "origin", "HEAD:feat/x"])

    def test_allows_follow_tags_and_mirror_set_to_false(self):
        git(self.tree, "config", "push.followTags", "false")
        git(self.tree, "config", "remote.fork.mirror", "false")
        self.assertAllowed(["git", "push", "fork", "HEAD:feat/x"])

    def test_denies_push_to_upstream_or_unresolved_remotes(self):
        for command in (
            ["git", "push", "origin", "HEAD:feat/x"],
            ["git", "push", "missing", "HEAD:feat/x"],
            ["git", "push", FORK_URL, "HEAD:feat/x"],
            ["git", "push", UPSTREAM_URL, "HEAD:feat/x"],
        ):
            with self.subTest(command=command):
                self.assertDenied(command)

    def test_denies_fork_remote_rewritten_to_another_destination(self):
        cases = (
            ("pushurl", ["remote.fork.pushurl", UPSTREAM_URL]),
            ("extra pushurl", ["--add", "remote.fork.pushurl", UPSTREAM_URL]),
            ("pushInsteadOf", ["url.https://github.com/omnigent-ai/.pushInsteadOf", "https://github.com/lkshrk/"]),
            ("ssh url", ["remote.fork.url", f"git@github.com:{FORK}.git"]),
            ("credential url", ["remote.fork.url", f"https://token@github.com/{FORK}.git"]),
            ("other host", ["remote.fork.url", f"https://gitlab.com/{FORK}.git"]),
            ("remote helper", ["remote.fork.vcs", "ext"]),
            ("mirror remote", ["remote.fork.mirror", "true"]),
            ("follow tags", ["push.followTags", "yes"]),
            ("unreadable follow tags", ["push.followTags", "maybe"]),
        )
        for label, config in cases:
            with self.subTest(case=label):
                with fork_checkout() as tree:
                    if label == "extra pushurl":
                        git(tree, "config", "remote.fork.pushurl", FORK_URL)
                    git(tree, "config", *config)
                    self.assertDenied(["git", "push", "fork", "HEAD:feat/x"], cwd=tree)

    def test_denies_protected_tag_force_delete_and_mirror_pushes(self):
        for command in (
            ["git", "push", "fork", "HEAD:main"],
            ["git", "push", "fork", "HEAD:master"],
            ["git", "push", "fork", "HEAD:refs/heads/main"],
            ["git", "push", "fork", "HEAD:refs/tags/v1.0.0"],
            ["git", "push", "fork", "HEAD:tags/v1"],
            ["git", "push", "fork", "+HEAD:feat/x"],
            ["git", "push", "fork", "HEAD:+feat/x"],
            ["git", "push", "--force", "fork", "HEAD:feat/x"],
            ["git", "push", "-f", "fork", "HEAD:feat/x"],
            ["git", "push", "--force-with-lease", "fork", "HEAD:feat/x"],
            ["git", "push", "--tags", "fork", "HEAD:feat/x"],
            ["git", "push", "--mirror", "fork"],
            ["git", "push", "--all", "fork"],
            ["git", "push", "--delete", "fork", "feat/x"],
            ["git", "push", "-d", "fork", "feat/x"],
            ["git", "push", "fork", ":feat/x"],
            ["git", "push", "fork", "HEAD:feat/x", "HEAD:feat/y"],
            ["git", "push", "fork", "feat/x"],
            ["git", "push", "fork", "HEAD"],
            ["git", "push", "fork"],
            ["git", "push"],
            ["git", "push", "fork", "HEAD:feat/x:y"],
            ["git", "push", "fork", "HEAD:feat/../main"],
            ["git", "push", "fork", "HEAD:-x"],
            ["git", "push", "--no-verify", "fork", "HEAD:feat/x"],
            ["git", "push", "-o", "ci.skip", "fork", "HEAD:feat/x"],
            ["git", "push", "--repo=fork", "HEAD:feat/x"],
            ["git", "-c", "remote.fork.pushurl=" + UPSTREAM_URL, "push", "fork", "HEAD:feat/x"],
            ["git", "-C", str(self.tree), "push", "fork", "HEAD:feat/x"],
        ):
            with self.subTest(command=command):
                self.assertDenied(command)

    def test_denies_pushes_that_touch_workflows(self):
        commit(self.tree, ".github/workflows/release.yml", "on: push\n", "ci: add release")
        commit(self.tree, "web/b.ts", "export const b = 1\n", "feat(web): add b")
        self.assertDenied(["git", "push", "fork", "HEAD:feat/x"])

    def test_allows_upstream_based_branch_when_fork_main_carries_the_template(self):
        upstream_head = git(self.tree, "rev-parse", "HEAD~1").strip()
        git(self.tree, "switch", "-q", "-c", "fork-main", upstream_head)
        commit(self.tree, ".github/workflows/publish-upstream.yml", "on: pull_request_review\n", "ci: add publish")
        commit(self.tree, "publish/gate.py", "print()\n", "ci: add gate")
        fork_main = git(self.tree, "rev-parse", "HEAD").strip()
        git(self.tree, "switch", "-q", "-")
        with patch.object(contribution_vcs, "fork_default_head", lambda repository: fork_main):
            self.assertAllowed(["git", "push", "fork", "HEAD:fix/web-a"])

    def test_denies_pushes_to_the_upstream_publish_branches(self):
        for command in (
            ["git", "push", "fork", "HEAD:upstream/pr-1"],
            ["git", "push", "fork", "HEAD:refs/heads/upstream/pr-1"],
            ["git", "push", "fork", "HEAD:Upstream/pr-1"],
        ):
            with self.subTest(command=command):
                self.assertDenied(command)

    def test_denies_push_when_the_fork_head_is_unknown_or_not_fetched(self):
        with patch.object(contribution_vcs, "fork_default_head", lambda repository: None):
            self.assertDenied(["git", "push", "fork", "HEAD:feat/x"])
        with patch.object(contribution_vcs, "fork_default_head", lambda repository: "0" * 40):
            self.assertDenied(["git", "push", "fork", "HEAD:feat/x"])

    def test_allows_fork_pull_request_and_issue_writes(self):
        for command in (
            ["gh", "pr", "create", "--repo", FORK, "--base", "main", "--head", "feat/x", "--title", "fix(web): a", "--body", "b"],
            ["gh", "pr", "create", "-R", FORK, "--head", "feat/x", "--draft", "--title", "t", "--body", "b"],
            ["gh", "pr", "create", f"--repo={FORK}", "--head=feat/x", "--title", "t", "--body", "b"],
            ["gh", "pr", "edit", "7", "--repo", FORK, "--add-label", "upstream-ready"],
            ["gh", "pr", "edit", "7", "--repo", FORK, "--title", "fix(web): a", "--body", "-starts with a dash"],
            ["gh", "issue", "create", "--repo", FORK, "--title", "bug", "--body", "b", "--label", "upstream-issue"],
            ["gh", "issue", "create", "--repo", FORK, "--title", "Comment on #42", "--body", "b", "--label", "upstream-comment"],
            ["gh", "issue", "edit", "4", "-R", FORK, "--body", "b"],
        ):
            with self.subTest(command=command):
                self.assertAllowed(command)

    def test_denies_gh_writes_without_the_fork_repo(self):
        for command in (
            ["gh", "pr", "create", "--head", "feat/x", "--title", "t", "--body", "b"],
            ["gh", "pr", "create", "--repo", UPSTREAM, "--head", "feat/x", "--title", "t", "--body", "b"],
            ["gh", "pr", "create", "--repo", f"github.com/{FORK}", "--head", "feat/x", "--title", "t"],
            ["gh", "pr", "create", "--repo", FORK, "--repo", UPSTREAM, "--head", "feat/x", "--title", "t"],
            ["gh", "pr", "create", "--repo", FORK, "-R", UPSTREAM, "--head", "feat/x", "--title", "t"],
            ["gh", "pr", "create", "--repo", FORK, "--title", "t", "--body", "b"],
            ["gh", "pr", "create", "--repo", FORK, "--head", "feat/x", "--body-file", "/etc/passwd"],
            ["gh", "pr", "create", "--repo", FORK, "--head", "feat/x", "-F", "notes.md"],
            ["gh", "pr", "create", "--repo"],
            ["gh", "pr", "edit", "7", "--repo", FORK, "--", "--title"],
            ["gh", "pr", "edit", "7", "--add-label", "upstream-ready"],
            ["gh", "pr", "edit", "feat/x", "--repo", FORK, "--title", "t"],
            ["gh", "pr", "edit", f"https://github.com/{UPSTREAM}/pull/7", "--repo", FORK, "--title", "t"],
            ["gh", "pr", "edit", "7", "8", "--repo", FORK, "--title", "t"],
            ["gh", "pr", "edit", "--repo", FORK, "--title", "t"],
            ["gh", "pr", "create", "--repo", FORK, "--head", "feat/x", "extra"],
            ["gh", "pr", "create", "--repo", FORK, "-dR", UPSTREAM, "--head", "feat/x"],
            ["gh", "pr", "create", f"-R{UPSTREAM}", "--repo", FORK, "--head", "feat/x"],
            ["gh", "pr", "create", "--repo", FORK, "--head", "feat/x", "--web"],
            ["gh", "pr", "create", "--repo", FORK, "--head", "feat/x", "--draft=false"],
            ["gh", "pr", "create", "--repo", FORK, "--head", "feat/x", "--head", "other"],
            ["gh", "pr", "edit", "7", "--title", "--repo", FORK],
            ["gh", "pr", "edit", "7", "--repo", UPSTREAM, "--title", "t"],
            ["gh", "issue", "create", "--title", "bug"],
            ["gh", "issue", "create", "--repo", UPSTREAM, "--title", "bug"],
            ["gh", "issue", "edit", "4", "--repo", FORK, "--add-label", "approved-upstream"],
            ["gh", "issue", "edit", "4", "--repo", FORK, "--add-label", "bug,approved-upstream"],
            ["gh", "issue", "create", "--repo", FORK, "--title", "b", "--label=approved-upstream"],
            ["gh", "issue", "create", "--repo", FORK, "--title", "b", "-l", "approved-upstream"],
            ["gh", "issue", "edit", "4", "--repo", FORK, "--add-label", "Approved-Upstream"],
            ["gh", "issue", "edit", "4", "--repo", FORK, "--add-label", "upstream-comment, approved-upstream"],
            ["gh", "issue", "edit", "4", "--repo", FORK, "--add-label", "upstream-comment", "--add-label", "approved-upstream"],
            ["gh", "pr", "edit", "7", "--repo", FORK, "--add-label", "approved-upstream"],
            ["gh", "pr", "create", "--repo", FORK, "--head", "feat/x", "--title", "t", "--label", "approved-upstream"],
            ["gh", "api", "-X", "POST", f"repos/{FORK}/issues/4/labels", "-f", "labels[]=approved-upstream"],
            ["gh", "api", f"repos/{FORK}/issues/4/labels", "-f", "labels[]=approved-upstream"],
        ):
            with self.subTest(command=command):
                self.assertDenied(command)

    def test_denies_other_gh_writes_even_on_the_fork(self):
        for command in (
            ["gh", "pr", "merge", "7", "--repo", FORK],
            ["gh", "pr", "comment", "7", "--repo", FORK, "--body", "x"],
            ["gh", "pr", "close", "7", "--repo", FORK],
            ["gh", "pr", "review", "7", "--repo", FORK, "--approve"],
            ["gh", "pr", "ready", "7", "--repo", FORK],
            ["gh", "issue", "comment", "4", "--repo", FORK, "--body", "x"],
            ["gh", "issue", "close", "4", "--repo", FORK],
            ["gh", "release", "create", "v1.0.0", "--repo", FORK],
            ["gh", "release", "list", "--repo", FORK],
            ["gh", "workflow", "run", "ci.yml", "--repo", FORK],
            ["gh", "workflow", "enable", "ci.yml", "--repo", FORK],
            ["gh", "label", "create", "approved-upstream", "--repo", FORK],
            ["gh", "repo", "edit", FORK, "--default-branch", "x"],
            ["gh", "repo", "sync", FORK],
            ["gh", "secret", "set", "X", "--repo", FORK],
            ["gh", "api", "-X", "POST", f"repos/{FORK}/pulls"],
            ["gh", "api", f"repos/{FORK}/issues", "-f", "title=x"],
            ["gh", "api", "graphql", "-f", "query=mutation{x}"],
            ["gh"],
        ):
            with self.subTest(command=command):
                self.assertDenied(command)

    def test_leaves_gh_reads_to_the_other_policies(self):
        for command in (
            ["gh", "pr", "view", "7", "--repo", FORK],
            ["gh", "pr", "checks", "7", "--repo", FORK],
            ["gh", "pr", "list", "--repo", UPSTREAM],
            ["gh", "issue", "view", "4", "--repo", FORK],
            ["gh", "run", "list", "--repo", FORK],
            ["gh", "api", f"repos/{FORK}/pulls"],
        ):
            with self.subTest(command=command):
                self.assertIsNone(self.evaluate(command), command)

    def test_allows_one_literal_simple_command_string(self):
        git(self.tree, "remote", "set-url", "origin", FORK_URL)
        for command in (
            'git commit -m "fix(web): x"',
            "git commit -s -m 'fix(web): x' -m 'Body with * and [brackets] and $HOME, literally.'",
            "git push origin HEAD:fix/x",
            "git push -u origin HEAD:fix/x",
            "git status",
            'git add "web/a.ts"',
            f'gh pr create --repo {FORK} --base main --head fix/x --title "fix(web): x" --body "What changed.\n\nTested with pnpm test."',
            f"gh pr edit 7 --repo={FORK} --add-label upstream-ready",
            "git commit -m 'fix(web): it'\"'\"'s fixed'",
        ):
            with self.subTest(command=command):
                self.assertAllowed(command)

    def test_literal_strings_go_through_the_structured_rules(self):
        for command in (
            "git push origin HEAD:fix/x",
            "git push fork HEAD:main",
            "git config core.hooksPath /tmp/hooks",
            "git tag v1.0.0",
            "git remote set-url fork https://github.com/omnigent-ai/omnigent.git",
            "gh pr create --repo omnigent-ai/omnigent --head fix/x --title t",
            "gh pr merge 7 --repo lkshrk/omnigent",
        ):
            with self.subTest(command=command):
                self.assertDenied(command)

    def test_denies_strings_that_are_not_one_literal_simple_command(self):
        for command in (
            "git push fork HEAD:x && echo",
            "git push fork HEAD:x; git push origin HEAD:main",
            "git push fork HEAD:x || true",
            "git status | cat",
            "git status &",
            "git push fork HEAD:x\ngit push origin HEAD:main",
            'git commit -m "$(id)"',
            "git commit -m $(id)",
            "git commit -m `id`",
            'git commit -m "`id`"',
            'git commit -m "$HOME"',
            "git commit -m $HOME",
            'git commit -m "a\\"b"',
            "git commit -m a\\ b",
            "git -c core.hooksPath=/tmp push fork HEAD:x",
            "env git push fork HEAD:x",
            "GIT_DIR=/tmp/x git push fork HEAD:x",
            "command git status",
            "bash -c 'git status'",
            "git log > /tmp/out",
            "git log 2>/dev/null",
            "git add *.ts",
            "git add web/?.ts",
            "git add web/{a,b}.ts",
            "git add ~/x",
            "git status # comment",
            "cat <<EOF\nx\nEOF",
            "git commit -F - <<EOF\nx\nEOF",
            "(git status)",
            "{ git status; }",
            "git commit -m 'unterminated",
            "/usr/bin/git status",
            "ls",
            "pnpm test",
            "",
        ):
            with self.subTest(command=command):
                self.assertDenied(command)

    def test_raw_and_wrapper_forms_of_fork_writes_fail_closed(self):
        for command in (
            ["bash", "-lc", "git push fork HEAD:feat/x"],
            ["env", "git", "push", "fork", "HEAD:feat/x"],
            ["command", "gh", "pr", "create", "--repo", FORK, "--head", "feat/x", "--title", "t"],
            ["/usr/bin/git", "push", "fork", "HEAD:feat/x"],
            ["git", "push", "fork", "HEAD:feat/x", ";", "git", "push", "origin", "HEAD:main"],
        ):
            with self.subTest(command=command):
                self.assertDenied(command)

    def test_allows_local_git_work_in_the_registered_tree(self):
        (self.tree / "web" / "a.ts").write_text("export const a = 2\n")
        for command in (
            ["git", "status"],
            ["git", "--no-pager", "diff"],
            ["git", "log", "--oneline", "-5"],
            ["git", "show", "HEAD"],
            ["git", "add", "web/a.ts"],
            ["git", "add", "-A"],
            ["git", "commit", "-m", "fix(web): change a again"],
            ["git", "commit", "-s", "-m", "fix(web): a", "-m", "body"],
            ["git", "commit", "-a", "-m", "fix(web): a"],
            ["git", "restore", "web/a.ts"],
            ["git", "restore", "--staged", "web/a.ts"],
            ["git", "switch", "-c", "feat/x"],
            ["git", "switch", "-c", "feat/x", "origin/main"],
            ["git", "checkout", "-b", "fix/web-a"],
            ["git", "rebase", "origin/main"],
            ["git", "rebase", "--continue"],
            ["git", "fetch", "origin"],
            ["git", "fetch", "fork", "main"],
            ["git", "branch", "--show-current"],
            ["git", "config", "--get", "user.name"],
            ["git", "remote", "-v"],
            ["git", "remote", "get-url", "fork"],
        ):
            with self.subTest(command=command):
                self.assertAllowed(command)
        self.assertAllowed(["git", "add", "a.ts"], cwd=self.tree / "web")

    def test_denies_config_remote_tag_and_ref_writes(self):
        for command in (
            ["git", "config", "core.hooksPath", "/tmp/hooks"],
            ["git", "config", "--local", "credential.helper", "store"],
            ["git", "config", "url.https://github.com/omnigent-ai/.pushInsteadOf", "https://github.com/lkshrk/"],
            ["git", "config", "remote.fork.pushurl", UPSTREAM_URL],
            ["git", "config", "--unset", "remote.fork.url"],
            ["git", "config", "--add", "push.followTags", "true"],
            ["git", "config", "--global", "user.name", "x"],
            ["git", "config", "--get-all", "x", "--add"],
            ["git", "remote", "add", "evil", UPSTREAM_URL],
            ["git", "remote", "set-url", "fork", UPSTREAM_URL],
            ["git", "remote", "set-url", "--push", "fork", UPSTREAM_URL],
            ["git", "remote", "rename", "fork", "x"],
            ["git", "remote", "remove", "fork"],
            ["git", "tag", "v1.0.0"],
            ["git", "tag", "-a", "v1.0.0", "-m", "x"],
            ["git", "update-ref", "refs/remotes/fork/x", "HEAD"],
            ["git", "submodule", "add", UPSTREAM_URL],
            ["git", "worktree", "add", "/tmp/other"],
            ["git", "branch", "feat/x"],
            ["git", "branch", "-D", "main"],
            ["git", "switch", "-c", "main"],
            ["git", "checkout", "-b", "master"],
            ["git", "switch", "main"],
            ["git", "checkout", "main"],
            ["git", "checkout", "--", "web/a.ts"],
            ["git", "switch", "-c", "feat/x", "--track", "origin/main", "extra"],
            ["git", "rebase", "-i", "origin/main"],
            ["git", "rebase", "--exec", "make", "origin/main"],
            ["git", "rebase", "-x", "make", "origin/main"],
            ["git", "fetch", "--upload-pack=touch /tmp/x", "origin"],
            ["git", "-c", "core.hooksPath=/tmp/hooks", "commit", "-m", "x"],
            ["/usr/bin/git", "commit", "-m", "x"],
            ["/usr/bin/git", "config", "core.hooksPath", "/tmp/hooks"],
            ["GIT_CONFIG_COUNT=1", "git", "commit", "-m", "x"],
            ["env", "git", "commit", "-m", "x"],
        ):
            with self.subTest(command=command):
                self.assertDenied(command)

    def test_denies_staging_or_committing_workflow_changes(self):
        (self.tree / ".github" / "workflows" / "ci.yml").write_text("on: pull_request\n")
        (self.tree / ".github" / "workflows" / "new.yml").write_text("on: push\n")
        for command in (
            ["git", "add", ".github/workflows/ci.yml"],
            ["git", "add", "-A"],
            ["git", "add", "."],
            ["git", "commit", "-a", "-m", "x"],
        ):
            with self.subTest(command=command):
                self.assertDenied(command)
        self.assertDenied(["git", "add", "../.github"], cwd=self.tree / "web")
        git(self.tree, "add", ".github/workflows/new.yml")
        self.assertDenied(["git", "commit", "-m", "ci: new"])

    def test_policy_git_calls_ignore_planted_executable_config(self):
        marker = self.tree.parent / "marker"
        script = self.tree.parent / "planted"
        script.write_text(f'#!/bin/sh\necho "$0 $*" >> {marker}\ncat\n')
        script.chmod(0o755)
        (self.tree / ".gitattributes").write_text("*.ts filter=planted diff=planted\n")
        for key in (
            "core.fsmonitor",
            "core.hooksPath",
            "core.pager",
            "diff.external",
            "diff.planted.textconv",
            "diff.planted.command",
            "filter.planted.clean",
            "filter.planted.smudge",
            "filter.planted.process",
            "gpg.program",
        ):
            git(self.tree, "config", key, str(script))
        git(self.tree, "config", "filter.planted.required", "true")
        git(self.tree, "config", "log.showSignature", "true")
        (self.tree / "web" / "a.ts").write_text("export const a = 3\n")
        self.assertAllowed(["git", "add", "web/a.ts"])
        self.assertAllowed(["git", "commit", "-m", "fix(web): a"])
        self.assertAllowed('git commit -a -m "fix(web): a"')
        self.assertAllowed(["git", "push", "fork", "HEAD:feat/x"])
        self.assertFalse(marker.exists(), marker.read_text() if marker.exists() else "")

    def test_denies_when_a_filter_driver_name_cannot_be_overridden(self):
        marker = self.tree.parent / "marker"
        script = self.tree.parent / "planted"
        script.write_text(f'#!/bin/sh\necho "$0 $*" >> {marker}\ncat\n')
        script.chmod(0o755)
        (self.tree / ".gitattributes").write_text('*.ts filter="a=b"\n')
        git(self.tree, "config", "filter.a=b.clean", str(script))
        (self.tree / "web" / "a.ts").write_text("export const a = 4\n")
        self.assertDenied(["git", "add", "web/a.ts"])
        self.assertDenied(["git", "commit", "-a", "-m", "fix(web): a"])
        self.assertFalse(marker.exists())

    def test_a_policy_git_timeout_is_a_denial(self):
        def expire(*args, **kwargs):
            raise subprocess.TimeoutExpired(args[0], kwargs["timeout"])

        with patch.object(contribution_vcs.subprocess, "run", side_effect=expire):
            self.assertDenied(["git", "push", "fork", "HEAD:feat/x"])
            self.assertDenied(["git", "add", "web/a.ts"])
            self.assertDenied(["git", "commit", "-m", "fix(web): a"])

    def test_policy_git_calls_use_a_minimal_environment_with_a_timeout(self):
        calls = []
        real_run = subprocess.run

        def record(*args, **kwargs):
            calls.append(kwargs)
            return real_run(*args, **kwargs)

        secrets = {"GH_TOKEN": "x", "GITHUB_TOKEN": "x", "GIT_ASKPASS": "/bin/true", "SSH_AUTH_SOCK": "/tmp/s", "AWS_SECRET_ACCESS_KEY": "x"}
        with patch.dict(os.environ, secrets), patch.object(contribution_vcs.subprocess, "run", side_effect=record):
            self.assertAllowed(["git", "push", "fork", "HEAD:feat/x"])
            self.assertAllowed(["git", "commit", "-m", "fix(web): a"])
        self.assertTrue(calls)
        for kwargs in calls:
            self.assertFalse(set(secrets) & set(kwargs["env"]), sorted(set(secrets) & set(kwargs["env"])))
            self.assertEqual(kwargs["env"]["GIT_TERMINAL_PROMPT"], "0")
            self.assertGreater(kwargs["timeout"], 0)

    def test_optional_argument_options_never_swallow_the_next_option(self):
        for verb in ("diff", "log", "show", "rev-parse", "status"):
            for first in ("--unified", "--pretty", "--format", "--stat", "--color", "--word-diff", "--abbrev", "--short", "--abbrev-ref", "--decorate", "-U", "-u", "--untracked-files"):
                for danger in ("--output=/tmp/pwn", "--ext-diff", "--textconv", "--upload-pack=x"):
                    self.assertDenied(["git", verb, first, danger])

    def test_everyday_read_options_stay_allowed(self):
        for command in (
            ["git", "status", "-sb"],
            ["git", "status", "--porcelain", "-uall"],
            ["git", "log", "-n5"],
            ["git", "log", "--stat=200"],
            ["git", "diff", "-U3"],
            ["git", "diff", "--color=never"],
            ["git", "diff", "--word-diff=color"],
            ["git", "diff", "--unified", "HEAD"],
            ["git", "log", "--pretty", "--oneline"],
            ["git", "commit", "-am", "x"],
        ):
            self.assertAllowed(command)

    def test_review_probes_fail_closed(self):
        for command in (
            ["git", "rebase", "--exe", "git push origin HEAD:main", "HEAD~1"],
            ["git", "rebase", "-kx", "git push origin HEAD:main", "HEAD~1"],
            ["git", "rebase", "--inter", "HEAD~1"],
            ["git", "rebase", "-i", "HEAD~1"],
            ["git", "fetch", "--upload-pac=touch /tmp/pwn;:", "."],
            ["git", "fetch", "--upload-pack=touch /tmp/pwn", "origin"],
            ["git", "fetch", "--exe=x", "origin"],
            ["git", "push", "--receive-pa=x", "fork", "HEAD:feat/x"],
            ["git", "diff", "--outp=/tmp/out"],
            ["git", "log", "--format=x", "--outp=.git/x"],
            ["git", "show", "--outp=/tmp/x"],
            ["git", "fetch", ".", "HEAD:refs/remotes/origin/forged"],
            ["git", "fetch", "origin", "main:refs/remotes/origin/forged"],
            ["git", "fetch", "origin", "+main"],
            ["git", "fetch", "--set-upstream", "origin", "main"],
            ["git", "fetch", "ext::sh -c touch% /tmp/ext"],
            ["git", "fetch", "https://github.com/omnigent-ai/omnigent.git"],
            ["git", "push", "fork", "HEAD:HEAD"],
            ["git", "push", "fork", "HEAD:refs/heads/refs/tags/v1"],
            ["git", "push", "fork", "HEAD:refs/tags/v1"],
            ["git", "push", "fork", "HEAD:Main"],
            ["git", "push", "fork", "HEAD:MASTER"],
            ["git", "push", "fork", "HEAD:refs/heads/HEAD"],
            ["git", "switch", "-c", "HEAD"],
            ["git", "switch", "-c", "Main"],
            ["git", "config", "--get-regexp", "."],
            ["git", "config", "--get", "http.https://github.com/.extraheader"],
            ["git", "config", "--get", "credential.helper"],
            ["git", "config", "--list"],
            ["gh", "api", "-X", "GET", "-X", "POST", "repos/omnigent-ai/omnigent/issues", "-f", "title=x"],
            ["gh", "api", "--method=GET", "--method=POST", "repos/omnigent-ai/omnigent/issues"],
            ["gh", "api", "-X", "GET", "graphql", "-f", "query={viewer{login}}"],
            ["gh", "api", "user"],
            ["gh", "api", "repos/omnigent-ai/omnigent/contents/../../../graphql"],
            ["gh", "api", "-H", "X-HTTP-Method-Override: POST", "repos/omnigent-ai/omnigent/issues"],
            ["gh", "pr", "edit", "1", "--repo", "lkshrk/omnigent", "--add-label", "Approved-Upstream"],
            ["gh", "issue", "edit", "1", "--repo", "lkshrk/omnigent", "--add-label", "APPROVED-UPSTREAM"],
            ["gh", "issue", "create", "--repo", "lkshrk/omnigent", "--title", "t", "--label", " approved-UPSTREAM "],
            ["gh", "auth", "status", "--show-token"],
            ["gh", "auth", "status", "-t"],
            ["gh", "auth", "token"],
            ["gh", "pr", "-R", "omnigent-ai/omnigent", "create", "--head", "x"],
            ["git", "st"],
            ["git", "commit-tree", "HEAD^{tree}", "-m", "x"],
            ["git", "reset", "--hard", "FETCH_HEAD"],
            ["git", "cherry-pick", "FETCH_HEAD"],
            ["git", "merge", "FETCH_HEAD"],
            ["git", "stash"],
            ["git", "am", "x.patch"],
            ["git", "update-index", "--add", "x"],
            ["git", "read-tree", "FETCH_HEAD"],
            ["git", "gc"],
            ["git", "pull", "--upload-pac=x", "."],
            ["git", "bisect", "run", "sh"],
            ["git", "difftool", "-x", "sh"],
            ["git", "grep", "-O", "sh"],
            ["git", "grep", "--open-files-in-pager=sh", "x"],
            ["git", "maintenance", "run"],
            ["git", "archive", "--remote=x", "HEAD"],
            ["git", "send-email", "x"],
            ["git", "request-pull", "x"],
            ["git", "credential", "fill"],
            ["git", "branch", "--list", "-D"],
            ["git", "commit", "-m", "x", "-F", "/etc/passwd"],
            ["git", "commit"],
            ["git", "add", "-p"],
            ["git", "add", ".git/hooks/pre-commit"],
            ["git", "restore", "--source=HEAD", ".git/config"],
            ["git"],
            "git rebase --exe 'git push origin HEAD:main' HEAD~1",
            "git push fork HEAD:feat/x\x00",
            "git push fork 'HEAD:feat/x\x00'",
            "git push fork HEAD:feat/x\r",
            "git commit -m 'a\rb'",
            "git push fork HEAD:feat/x # c",
            "git -c x=y push",
            "git push fork HEAD:feat/x\v",
        ):
            with self.subTest(command=command):
                self.assertDenied(command)

    def test_review_probe_controls_stay_allowed(self):
        for command in (
            ["git", "status", "--porcelain"],
            ["git", "remote", "get-url", "fork"],
            ["git", "commit", "--amend", "--no-edit"],
            ["git", "switch", "-c", "x", "FETCH_HEAD"],
            ["git", "fetch", "fork", "main"],
            ["git", "fetch", "--prune", "origin"],
            ["git", "log", "-5", "--format=%h %s"],
            ["git", "log", "-n", "3", "--oneline", "--", "web"],
            ["git", "diff", "--stat", "origin/main...HEAD"],
            ["git", "rev-parse", "--short=7", "HEAD"],
            ["git", "rebase", "--onto", "origin/main", "HEAD~1"],
            ["git", "config", "--get", "user.name"],
            ["gh", "api", "repos/lkshrk/omnigent/pulls", "--jq", ".[].number"],
            ["gh", "api", "-X", "GET", "repos/lkshrk/omnigent/actions/runs?branch=feat/x"],
            "git commit -m 'a\nb'",
            f"gh pr create --repo {FORK} --head x --title \"a\"\"b\"",
            "git commit -m x y",
        ):
            with self.subTest(command=command):
                decision = self.evaluate(command)
                self.assertNotEqual((decision or {}).get("result"), "DENY", decision)

    def test_forged_remote_refs_do_not_hide_workflow_commits(self):
        commit(self.tree, ".github/workflows/evil.yml", "on: push\n", "ci: evil")
        git(self.tree, "update-ref", "refs/remotes/origin/forged", "HEAD")
        git(self.tree, "update-ref", "refs/remotes/fork/forged", "HEAD")
        self.assertDenied(["git", "push", "fork", "HEAD:feat/x"])

    def test_shell_paths_into_git_dir_are_denied_in_the_tree(self):
        for command in (
            ["cp", "/tmp/hook", ".git/hooks/pre-commit"],
            ["tee", ".git/config"],
            ["ln", "-s", "/tmp/x", "web/../.git/hooks/post-checkout"],
            ["chmod", "+x", f"{self.tree}/.git/hooks/pre-commit"],
            ["python3", "script.py", "--out=.git/config"],
        ):
            with self.subTest(command=command):
                self.assertDenied(command)
        self.assertIsNone(self.evaluate(["ls", ".github"]))

    def test_registered_context_cannot_be_escaped_with_cd_or_home_expansion(self):
        home = self.tree.parent.parent
        for command in (
            "cd ~/work/omnigent && git push origin HEAD:feat/x",
            "cd $HOME/work/omnigent && git push origin HEAD:feat/x",
            "cd ${HOME}/work/omnigent && git push origin HEAD:feat/x",
            "cd work/omnigent; git push origin HEAD:feat/x",
            "cd work && cd omnigent && git push origin HEAD:feat/x",
            "pushd work/omnigent && git push origin HEAD:feat/x",
            "CDPATH=~/work cd omnigent && git push origin HEAD:feat/x",
            "git -C ~/work/omnigent push fork HEAD:feat/x",
            f"cd {home}/work/omni* && git push origin HEAD:main",
            f"cd {home}/w*/o*gent && git push origin HEAD:main",
            "cat ~/work/omnigent/.git/config",
            "bash -c 'cd ~/work/omnigent && git push origin HEAD:main'",
            ["bash", "-c", "cd ~/work/omnigent && git push origin HEAD:main"],
            ["bash", "-c", "cd work/omnigent && git push origin HEAD:main"],
            ["git", "-C", "work/omnigent", "push", "fork", "HEAD:feat/x"],
            ["cp", "/tmp/hook", "work/omnigent/.git/hooks/pre-commit"],
        ):
            with self.subTest(command=command):
                self.assertDenied(command, cwd=home)
        own = home / "work" / "agent-stack"
        own.mkdir()
        for command in (
            "cd ~/work/agent-stack && git status",
            ["git", "push", "origin", "HEAD:feat/x"],
            "git status",
        ):
            with self.subTest(control=command):
                self.assertIsNone(self.evaluate(command, cwd=own))
        self.assertIsNone(self.evaluate("gh auth status", cwd=own))
        self.assertDenied("gh auth status --show-token", cwd=own)
        self.assertDenied(["gh", "auth", "token"], cwd=own)
        self.assertDenied("gh auth status -t 2>&1 | cat", cwd=own)

    def test_quoted_and_exported_scoping_escapes_are_denied(self):
        home = self.tree.parent.parent
        for command in (
            "cd ~/work/omni''gent && git push origin HEAD:main",
            'cd ~/work/omni"gent" && git push origin HEAD:main',
            "cd ~/work/omni\\gent && git push origin HEAD:main",
            "cd ~/work/omnigen[t] && git push origin HEAD:main",
            "cd ~/work && cd omn?gent && git push origin HEAD:main",
            "export CDPATH=~/work; cd omnigent && git push origin HEAD:main",
            "declare -x CDPATH=~/work; cd omnigent && git push origin HEAD:main",
            "typeset CDPATH=~/work; cd omnigent && git push origin HEAD:main",
            "CDPATH=~/work; cd omnigent && git push origin HEAD:main",
            "CDPATH=~/work cd omnigent && git push origin HEAD:main",
            "cd work; cd omnigent; git push origin HEAD:main",
            "(cd ~/work/omnigent && git push origin HEAD:main)",
            "cd $'~/work/omni\\x67ent' && git push origin HEAD:main",
            "cd ~/work/./omnigent/ && git push origin HEAD:main",
            "pushd ~/work >/dev/null; pushd omnigent; git push origin HEAD:main",
            "pushd ~/work 2>/dev/null; pushd omnigent >/dev/null; git push origin HEAD:main",
            "env -C ~/work/omnigent git push origin HEAD:main",
            "cd ~/work/omnige\\\nnt && git push origin HEAD:main",
        ):
            with self.subTest(command=command):
                self.assertDenied(command, cwd=home)

    def test_scoping_has_no_false_positives_in_own_repositories(self):
        home = self.tree.parent.parent
        own = self.tree.parent / "agent-stack"
        (own / "policies" / "omnigent").mkdir(parents=True)
        for command, cwd in (
            ("cd policies/omnigent && python3 -m unittest", own),
            ("grep -rn omnigent policies", own),
            ("docker build -t omnigent .", own),
            (["git", "commit", "-m", "fix(omnigent): x"], own),
            (["git", "push", "origin", "HEAD:feat/omnigent"], own),
            ("git log --oneline -- policies/omnigent", own),
            ("ls ~/work", home),
            ("rg 'omni*' src", own),
            ("ls policies/*", own),
        ):
            with self.subTest(command=command):
                self.assertIsNone(self.evaluate(command, cwd=cwd))

    def test_persistent_global_git_config_writes_are_denied_everywhere(self):
        home = self.tree.parent.parent
        own = self.tree.parent / "agent-stack"
        own.mkdir(exist_ok=True)
        denied = (
            "git config --global core.fsmonitor 'sh /tmp/x'",
            ["git", "config", "--global", "core.fsmonitor", "sh /tmp/x"],
            "git config set --global core.hooksPath /tmp/h",
            "git config --system core.pager /tmp/x",
            "git config --worktree core.hooksPath /tmp/h",
            "git config --global --edit",
            "git config --global -e",
            "git config edit --global",
            "git config --global --add trailer.x.cmd 'sh /tmp/x'",
            "git config --global --replace-all core.fsmonitor /tmp/x",
            "git config --global --unset core.fsmonitor",
            "git config --global --unset-all core.fsmonitor",
            "git config --global --rename-section a b",
            "git config --global --remove-section core",
            "git config unset --global core.fsmonitor",
            "git config rename-section --global a b",
            "git config remove-section --global core",
            "git config --file ~/.gitconfig core.fsmonitor /tmp/x",
            f"git config -f {home}/.config/git/config core.hooksPath /tmp/h",
            f"git config -f{home}/.gitconfig core.hooksPath /tmp/h",
            f"git config -zf {home}/.gitconfig core.hooksPath /tmp/h",
            f"git config --file={home}/.gitconfig core.fsmonitor /tmp/x",
            "git config --file $HOME/.config/git/attributes x y",
            "git config --fil ~/.gitconfig core.fsmonitor /tmp/x",
            "git config --glob core.fsmonitor /tmp/x",
            "git config --gl core.fsmonitor /tmp/x",
            "git config --sys core.pager /tmp/x",
            "git config --work core.hooksPath /tmp/h",
            "git config --global --ad trailer.x.cmd /tmp/x",
            "git config --global --rep core.fsmonitor /tmp/x",
            "git config --global --unset-a core.fsmonitor",
            "git config --global --edi",
            "git config --global -ze",
            "git config --global --type bool core.fsmonitor /tmp/x",
            "git config set --global --value=/tmp/x core.fsmonitor",
            "GIT_CONFIG=~/.gitconfig git config core.fsmonitor /tmp/x",
            "env GIT_CONFIG=$HOME/.gitconfig git config core.fsmonitor /tmp/x",
            "export GIT_CONFIG=~/.gitconfig; git config core.fsmonitor /tmp/x",
            "GIT_CONFIG_GLOBAL=/tmp/g git config --global core.fsmonitor /tmp/x",
            "git -c core.pager=cat config --global core.fsmonitor /tmp/x",
            "git -C /tmp config --global core.fsmonitor /tmp/x",
            "git --no-pager config --global core.fsmonitor /tmp/x",
            "cd /tmp && git config --global core.fsmonitor /tmp/x",
            "bash -c 'git config --global core.fsmonitor /tmp/x'",
            "env git config --global core.fsmonitor /tmp/x",
            "command git config --global core.fsmonitor /tmp/x",
        )
        for cwd in (own, home):
            for command in denied:
                with self.subTest(cwd=cwd, command=command):
                    self.assertDenied(command, cwd=cwd)
        for command in (
            "git config --global --get user.name",
            "git config --global --get-all user.name",
            "git config --global --list",
            "git config --global -l",
            "git config get --global user.name",
            "git config list --global",
            "git config --global user.name",
            "git config --global --type bool core.bare",
            "git config --glob --get-regexp user",
            f"git config -f {home}/.gitconfig --get user.name",
            "GIT_CONFIG=~/.gitconfig git config --get user.name",
            "git config --list",
            "git config user.email x@example.invalid",
            "git config --local core.autocrlf false",
            "git -c core.pager=cat log -1",
        ):
            with self.subTest(control=command):
                self.assertIsNone(self.evaluate(command, cwd=own))

    def test_own_repositories_keep_their_behaviour(self):
        own = self.tree.parent / "agent-stack"
        own.mkdir()
        for command in (
            ["git", "push", "origin", "HEAD:feat/x"],
            ["gh", "pr", "create", "--title", "feat: x", "--body", "b"],
            ["gh", "pr", "merge", "7"],
        ):
            with self.subTest(command=command):
                self.assertIsNone(self.evaluate(command, cwd=own), command)


if __name__ == "__main__":
    unittest.main()
