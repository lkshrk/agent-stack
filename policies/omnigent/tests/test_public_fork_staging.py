import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

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

    def test_denies_push_when_pushed_commits_cannot_be_listed(self):
        git(self.tree, "update-ref", "-d", "refs/remotes/fork/main")
        git(self.tree, "update-ref", "-d", "refs/remotes/origin/main")
        self.assertDenied(["git", "push", "fork", "HEAD:feat/x"])

    def test_allows_fork_pull_request_and_issue_writes(self):
        for command in (
            ["gh", "pr", "create", "--repo", FORK, "--base", "main", "--head", "feat/x", "--title", "fix(web): a", "--body", "b"],
            ["gh", "pr", "create", "-R", FORK, "--head", "feat/x", "--draft", "--title", "t", "--body", "b"],
            ["gh", "pr", "create", f"--repo={FORK}", "--head=feat/x", "--title", "t", "--body", "b"],
            ["gh", "pr", "edit", "7", "--repo", FORK, "--add-label", "upstream-ready"],
            ["gh", "pr", "edit", "7", "--repo", FORK, "--title", "fix(web): a", "--body", "-starts with a dash"],
            ["gh", "issue", "create", "--repo", FORK, "--title", "bug", "--body", "b", "--label", "upstream-issue"],
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

    def test_raw_and_wrapper_forms_of_fork_writes_fail_closed(self):
        for command in (
            "git push fork HEAD:feat/x",
            f"gh pr create --repo {FORK} --head feat/x --title t --body b",
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
            ["git", "config", "--list"],
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
