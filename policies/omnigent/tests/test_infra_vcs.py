import unittest

from agent_stack_policies.infra_vcs import infra_vcs

from . import tool_call_event

EVENTS = [
    tool_call_event("bash", {"command": "git push origin HEAD:renovate/x"}),
    tool_call_event("bash", {"command": "git push origin HEAD:main"}),
    tool_call_event("bash", {"command": "gh pr merge 7"}),
    tool_call_event("bash", {"command": 'echo "unterminated'}),
    tool_call_event("bash", {}),
    tool_call_event("Read", {"file_path": "a"}),
    {"type": "tool_call", "target": "bash", "data": {"name": "bash", "arguments": "{bad"}},
    {"type": "other"},
]


def verdict(command, tool="Bash"):
    return infra_vcs()(tool_call_event(tool, {"command": command}))


class InfraVcsTest(unittest.TestCase):
    def assertVerdict(self, command, expected):
        for form in (command, ["bash", "-lc", command]):
            with self.subTest(form=form):
                decision = verdict(form)
                self.assertIsNotNone(decision)
                self.assertEqual(decision["result"], expected, (form, decision.get("reason")))
                if expected == "DENY":
                    self.assertTrue(decision["reason"].startswith("agent-stack: "))

    def test_other_tools_abstain(self):
        self.assertIsNone(infra_vcs()(tool_call_event("Read", {"file_path": "a"})))
        self.assertIsNone(infra_vcs()({"type": "other"}))

    def test_renovate_branch_push_allowed(self):
        for command in ("git push origin HEAD:renovate/litellm-1.x", "git push origin HEAD:renovate/ghcr.io-foo-bar-2.x"):
            self.assertVerdict(command, "ALLOW")

    def test_other_pushes_denied(self):
        for command in (
            "git push",
            "git push origin",
            "git push origin HEAD:main",
            "git push origin HEAD:feat/x",
            "git push origin renovate/x",
            "git push origin HEAD:refs/heads/renovate/x",
            "git push origin HEAD:refs/tags/renovate/x",
            "git push origin +HEAD:renovate/x",
            "git push origin :renovate/x",
            "git push --force origin HEAD:renovate/x",
            "git push -f origin HEAD:renovate/x",
            "git push --force-with-lease origin HEAD:renovate/x",
            "git push origin HEAD:renovate/x --tags",
            "git push --delete origin renovate/x",
            "git push -u origin HEAD:renovate/x",
            "git push fork HEAD:renovate/x",
            "git push origin HEAD:renovate/",
            "git push origin HEAD:renovate/../main",
            "git push origin HEAD:renovate/x HEAD:main",
            "git -C ../other push origin HEAD:renovate/x",
            "git -c push.default=current push origin HEAD:renovate/x",
            "env git push origin HEAD:renovate/x",
            "xargs git push origin",
            "cd ~/knowledge && git push origin HEAD:main",
            "git push origin HEAD:renovate/$BRANCH",
        ):
            self.assertVerdict(command, "DENY")

    def test_everyday_git_allowed(self):
        for command in (
            "git status",
            "git fetch origin",
            "git switch -c renovate/x origin/renovate/x",
            "git checkout renovate/x",
            "git add kubernetes/apps/ai/litellm/app/helmrelease.yaml",
            'git commit -m "fix(litellm): set the new env var"',
            "git log --oneline -5",
            "git diff origin/renovate/x..HEAD",
            "git -C ~/knowledge status",
            "git rev-parse HEAD",
            "git log --grep push",
            "git remote -v",
            "git remote get-url origin",
            "git remote show origin",
            "git remote --verbose",
            "git remote -v show origin",
        ):
            self.assertVerdict(command, "ALLOW")

    def test_remote_changes_denied(self):
        for command in (
            "git remote set-url origin https://github.com/lkshrk/other.git",
            "git remote add x https://github.com/lkshrk/other.git",
            "git remote rename origin x",
            "git remote remove origin",
            "git remote -v set-url origin https://github.com/lkshrk/other.git",
            "git remote -v add x https://github.com/lkshrk/other.git",
            "git remote --verbose rename origin x",
            "git remote --foo show origin",
        ):
            self.assertVerdict(command, "DENY")

    def test_gh_reads_and_bookkeeping_allowed(self):
        for command in (
            "gh pr list -R lkshrk/h-cloud --state open --json number,title,author",
            "gh pr view 7 -R lkshrk/h-cloud --json files,headRefName,headRefOid",
            "gh pr diff 7 -R lkshrk/h-cloud",
            "gh pr checks 7 -R lkshrk/h-cloud",
            "gh pr comment 7 -R lkshrk/h-cloud --body-file /tmp/c.md",
            'gh pr close 7 -R lkshrk/h-cloud --comment "Superseded by #8."',
            "gh api repos/lkshrk/h-cloud/pulls/7/files",
            "gh api repos/cilium/cilium/releases --paginate",
            "gh release view v1.19.0 -R cilium/cilium",
            "gh release list -R cilium/cilium",
            "gh issue list -R cilium/cilium --search 'netkit'",
            "gh issue view 123 -R cilium/cilium",
            "gh search issues --repo cilium/cilium netkit",
            "gh run list -R lkshrk/h-cloud --branch renovate/x",
            "gh run view 1 -R lkshrk/h-cloud --log-failed",
            "gh repo view lkshrk/h-cloud",
        ):
            self.assertVerdict(command, "ALLOW")

    def test_gh_mutations_denied(self):
        for command in (
            "gh pr merge 7",
            "gh pr create --title x",
            "gh pr edit 7 --add-label x",
            "gh pr review 7 --approve",
            "gh pr ready 7",
            "gh pr reopen 7",
            "gh pr close 7 --delete-branch",
            "gh pr close 7 -d",
            "gh pr close 7 -dc x",
            "gh pr close 7 -cd x",
            "gh api -X PATCH repos/lkshrk/h-cloud/git/refs/heads/main -f sha=abc",
            "gh api -X DELETE repos/lkshrk/h-cloud/git/refs/tags/v1",
            "gh api graphql -f query='mutation { x }'",
            "gh workflow run flate.yaml",
            "gh release create v1",
            "gh repo delete lkshrk/h-cloud",
            "gh secret set X",
            "gh issue create --title x",
            "gh auth token",
            "gh alias set m 'pr merge'",
        ):
            self.assertVerdict(command, "DENY")

    def test_unparseable_and_bad_arguments_denied(self):
        self.assertEqual(verdict('echo "unterminated')["result"], "DENY")
        bad = {"type": "tool_call", "target": "bash", "data": {"name": "bash", "arguments": "{bad"}}
        self.assertEqual(infra_vcs()(bad)["result"], "DENY")
        self.assertEqual(infra_vcs()(tool_call_event("bash", {"command": 5}))["result"], "DENY")
