import unittest

from agent_stack_policies.vcs import docs_vcs

from . import tool_call_event

ALLOW_PATHS = ["**/*.md", "docs/**", "**/README*", "CHANGELOG*", "**/adr/**"]
DENY = [".github/workflows/**", "**/.env", "**/.env.*", "AGENTS.md", "**/CLAUDE.md"]
EXEMPT = ["**/.env.example"]

EVENTS = [
    tool_call_event("bash", {"command": "git add -A"}),
    tool_call_event("bash", {"command": "git add docs/a.md"}),
    tool_call_event("bash", {"command": "gh pr review 7 --approve"}),
    tool_call_event("bash", {"command": 'echo "unterminated'}),
    tool_call_event("bash", {}),
    tool_call_event("Read", {"file_path": "a"}),
    {"type": "tool_call", "target": "bash", "data": {"name": "bash", "arguments": "{bad"}},
    {"type": "other"},
]


def run(command, tool="sys_os_shell"):
    policy = docs_vcs(allow_paths=ALLOW_PATHS, deny=DENY, exempt=EXEMPT)
    return policy(tool_call_event(tool, {"command": command}))


class DocsVcsTest(unittest.TestCase):
    def assertAllowed(self, command):
        decision = run(command)
        self.assertIsNotNone(decision, command)
        self.assertEqual(decision["result"], "ALLOW", (command, decision.get("reason")))

    def assertDenied(self, command, text=None):
        decision = run(command)
        self.assertIsNotNone(decision, command)
        self.assertEqual(decision["result"], "DENY", command)
        self.assertTrue(decision["reason"].startswith("agent-stack: "), decision["reason"])
        if text:
            self.assertIn(text, decision["reason"], command)

    def test_ignores_other_tools_and_commands(self):
        self.assertIsNone(docs_vcs(allow_paths=ALLOW_PATHS)(tool_call_event("Read", {"file_path": "a"})))
        for command in ("rg -n git docs", "ls", "make docs", "sed -i s/a/b/ src/a.py", "echo gh", ""):
            self.assertAllowed(command)

    def test_git_read_only_verbs_allowed(self):
        for command in (
            "git status",
            "git status --porcelain",
            "git diff",
            "git diff --name-only abc123..HEAD",
            "git log --oneline -5 -- docs",
            "git show HEAD:docs/a.md",
            "git rev-parse --abbrev-ref HEAD",
            "git ls-files docs",
            "git branch",
            "git branch --show-current",
            "git branch -a",
            "git remote -v",
            "git fetch origin",
            "git merge-base HEAD origin/main",
            "git log | head -5",
            "cd repo && git status",
        ):
            self.assertAllowed(command)

    def test_git_add_doc_paths_allowed(self):
        for command in (
            "git add docs/setup.md README.md",
            "git add -- docs/setup.md README.md",
            "git add CHANGELOG.md frontend/README",
            "git add docs/adr/0007-queue.md",
            "git add /home/omnigent/work/repo/docs/setup.md",
        ):
            self.assertAllowed(command)

    def test_git_add_outside_docs_denied(self):
        for command in (
            "git add backend/app/main.py",
            "git add docs/a.md backend/app/main.py",
            "git add -A",
            "git add --all",
            "git add -u",
            "git add .",
            "git add docs",
            "git add docs/",
            "git add src",
            "git add 'docs/*.md'",
            "git add ':(glob)**'",
            "git add AGENTS.md",
            "git add docs/../src/a.py",
            "git add -f docs/a.md",
            "git add",
            "git add README.d",
            "git add CHANGELOG.d",
            "git add docs/foo.d",
            "git add docs/v1.2",
            "git add docs/conf.py",
            "git add LICENSE",
            "git add src/img/diagram.png",
            "git add docs/img/diagram.png.d",
        ):
            self.assertDenied(command, "git add")

    def test_git_add_doc_names_and_extensions(self):
        for command in (
            "git add docs/LICENSE",
            "git add docs/NOTICE.txt",
            "git add docs/CONTRIBUTING.rst",
            "git add docs/guide.mdx docs/guide.markdown docs/a.adoc docs/b.txt",
            "git add docs/img/diagram.png docs/img/a.jpg docs/img/b.jpeg docs/img/c.gif",
            "git add docs/img/d.svg docs/img/e.webp docs/arch.drawio docs/img/F.PNG",
            "git add README.rst",
            "git add CHANGELOG",
        ):
            self.assertAllowed(command)
        policy = docs_vcs(allow_paths=ALLOW_PATHS + ["LICENSE"])
        decision = policy(tool_call_event("bash", {"command": "git add LICENSE"}))
        self.assertEqual(decision["result"], "ALLOW")

    def test_read_forms_not_over_denied(self):
        for command in (
            "git --no-pager log -1",
            "git --no-pager diff --name-only abc123..HEAD",
            "git -P show HEAD",
            "GIT_PAGER=cat git log -1",
            "git log -1 2>/dev/null",
            "git log -1 2> /dev/null",
            "git status 2>&1",
            "git status --porcelain 2>&1 | head",
            "gh pr view 7 2>&1",
            "gh pr diff 7 2>/dev/null",
            "git push origin HEAD:feat/x 2>&1",
            "git branch --list 'feat/*'",
            "git branch -l feat/x",
        ):
            self.assertAllowed(command)
        for command in (
            "git --no-pager add -A",
            "git --no-pager push origin HEAD:feat/x",
            "git --no-pager -C . log",
            "GIT_PAGER=less git log",
            "GIT_PAGER=cat GIT_INDEX_FILE=/tmp/i git add docs/a.md",
            "GIT_EDITOR=true git commit",
            "git log > docs/log.md",
            "git log 2>docs/err.md",
            "gh pr diff 7 > /tmp/pr.diff",
            "git log 2>&1>out",
            "git log &>out",
            "git log >&out",
            "git log 2>&1>>out",
            "git log 1>&2>out",
            "git log 2>&1&>out",
            "git log 3>out",
            "git log 1>out",
            "gh pr diff 7 &>out",
            "git push origin HEAD:feat/x &>out",
            "git log >> notes.md",
            "git branch feat/new",
            "git branch -D feat/x",
        ):
            self.assertDenied(command)

    def test_git_commit_message_only(self):
        self.assertAllowed('git commit -m "docs: document ORDERS_QUEUE"')
        self.assertAllowed('git commit -m "docs: x" -m "body"')
        self.assertAllowed('git commit --message="docs: x"')
        for command in (
            'git commit -am "docs: x"',
            'git commit -a -m "docs: x"',
            'git commit --all -m "docs: x"',
            'git commit -m "docs: x" src/a.py',
            'git commit -m "docs: x" -- src/a.py',
            "git commit --amend --no-edit",
            'git commit --no-verify -m "docs: x"',
            'git commit -n -m "docs: x"',
            "git commit -c HEAD",
            "git commit -F msg.txt",
            "git commit",
        ):
            self.assertDenied(command, "git commit")

    def test_git_push_head_to_branch(self):
        self.assertAllowed("git push origin HEAD:feat/x")
        self.assertAllowed("git push origin HEAD:develop")
        self.assertAllowed("git push origin HEAD:refs/heads/feat/x")
        for command in (
            "git push origin HEAD:main",
            "git push origin HEAD:refs/heads/master",
            "git push --no-verify origin HEAD:feat/x",
            "git push --force origin HEAD:feat/x",
            "git push origin +HEAD:feat/x",
            "git push -u origin feat/x",
            "git push origin feat/x",
            "git push upstream HEAD:feat/x",
            "git push origin HEAD:feat/x HEAD:feat/y",
            "git push origin :feat/x",
            "git push origin --delete feat/x",
            "git push",
            "git push origin HEAD:refs/tags/v9",
            "git push origin refs/tags/v9",
            "git push origin HEAD:refs/notes/x",
            "git push origin HEAD:refs/heads/refs/heads/main",
            "git push origin HEAD:refs/remotes/origin/x",
            "git push --tags origin",
            "git push --follow-tags origin HEAD:feat/x",
            "git push --all origin",
            "git push --mirror origin",
            "git push origin HEAD:heads/main",
            "git push origin heads/main",
            "git push origin HEAD:heads/master",
            "git push origin HEAD:heads/feat/x",
            "git push origin HEAD:tags/v1",
            "git push origin HEAD:refs/tags/v1",
            "git push origin HEAD:remotes/origin/x",
            "git push origin HEAD:notes/x",
        ):
            self.assertDenied(command, "git push")

    def test_reviewer_bypass_list_denied(self):
        for command in (
            "git update-index --add backend/app/main.py",
            "git checkout main -- backend/app/main.py",
            "git checkout -- backend/app/main.py",
            "git restore --source=main backend/app/main.py",
            "git restore --staged --source=main backend/app/main.py",
            "git reset main -- backend/app/main.py",
            "git read-tree main",
            "git cherry-pick abc123",
            "git merge origin/main",
            "git revert abc123",
            "git rebase origin/main",
            "git stash pop",
            "git apply x.patch",
            "git am x.patch",
            "git rm src/a.py",
            "git mv src/a.py src/b.py",
            "git commit-tree abc -m x",
            "git update-ref refs/heads/x abc",
            "git config alias.x 'add -A'",
            "git grep -O foo",
            "git branch -D feat/x",
            "git branch new-branch",
            "git remote add evil https://example.test/r.git",
            "git fetch --upload-pack=touch origin",
            "git diff --output=src/a.py",
        ):
            self.assertDenied(command)

    def test_git_global_options_and_env_denied(self):
        for command in (
            "git -C . add backend/app/main.py",
            "git -c alias.x='add -A' x",
            "git --git-dir=.git status",
            "git --work-tree=/tmp add a.md",
            "GIT_INDEX_FILE=/tmp/i git add docs/a.md",
            "GIT_DIR=/tmp/x git status",
            "GIT_INDEX_FILE=/tmp/i python3 x.py",
        ):
            self.assertDenied(command)

    def test_git_and_gh_through_wrappers_denied(self):
        for command in (
            "ls | xargs git add",
            "find . -name '*.py' -exec git add {} +",
            "env git add -A",
            "command git add -A",
            "sudo git add -A",
            "xargs gh pr close",
        ):
            self.assertDenied(command)

    def test_nested_and_substituted_git_denied(self):
        for command in (
            "sh -c 'git add -A'",
            "bash -lc 'git commit -am x'",
            "echo $(git add -A)",
            "echo `git add -A`",
            "git add $(ls)",
            'git commit -m "docs: $(date)"',
        ):
            self.assertDenied(command)

    def test_gh_allowlist(self):
        for command in (
            "gh pr view 7 --repo routivo/routivo-monorepo --json files,headRefName,headRefOid",
            "gh pr diff 7 --repo routivo/routivo-monorepo",
            "gh pr checks 7",
            "gh pr list --head feat/x",
            "gh api repos/o/r/pulls/7/files",
            "gh api -X GET repos/o/r/pulls/7",
            "gh api --method=GET repos/o/r/pulls/7",
        ):
            self.assertAllowed(command)
        for command in (
            "gh pr review 7 --approve",
            "gh pr close 7",
            "gh pr edit 7 --base develop",
            "gh pr comment 7 --body x",
            "gh pr merge 7",
            "gh pr create --title x --body y",
            "gh issue create --title x",
            "gh repo delete o/r",
            "gh api -X PATCH repos/o/r -f default_branch=x",
            "gh api --method PATCH repos/o/r",
            "gh api -XPOST repos/o/r/issues",
            "gh api repos/o/r/issues -f title=x",
            "gh api repos/o/r/issues -F title=x",
            "gh api repos/o/r/issues --field title=x",
            "gh api repos/o/r/issues --raw-field title=x",
            "gh api repos/o/r/issues --input body.json",
            "gh api graphql -f query=x",
            "gh",
        ):
            self.assertDenied(command, "gh")

    def test_reasons_name_the_allowed_form(self):
        self.assertDenied("git add -A", "git add -- <doc paths>")
        self.assertDenied("git commit -am x", "git commit -m <message>")
        self.assertDenied("git push origin feat/x", "git push origin HEAD:<branch>")
        self.assertDenied("gh pr close 7", "gh pr view|diff|checks|list")

    def test_unparseable_fails_closed(self):
        self.assertDenied('echo "unterminated', "unparseable")
        decision = docs_vcs(allow_paths=ALLOW_PATHS)(EVENTS[6])
        self.assertEqual(decision["result"], "DENY")

    def test_without_command_allowed(self):
        decision = docs_vcs(allow_paths=ALLOW_PATHS)(tool_call_event("bash", {}))
        self.assertEqual(decision["result"], "ALLOW")


class SharedResolverTest(unittest.TestCase):
    def test_more_wrappers_denied(self):
        for command in (
            "setsid git add backend/app/main.py",
            "ionice -c 3 git add backend/app/main.py",
            "flock /tmp/l git commit -am x",
            "stdbuf -oL git push origin HEAD:main",
            "timeout 30 git status",
            "nice -n 5 git status",
            "find . -name '*.py' -exec git add {} +",
            "parallel git add ::: a.py b.py",
            "script -qc 'git add -A' /dev/null",
            "su -c 'git add -A' me",
            "env -S 'git add -A'",
            "time git status",
        ):
            decision = run(command)
            self.assertEqual(decision["result"], "DENY", command)

    def test_direct_forms_still_allowed(self):
        for command in ("git status", "GIT_PAGER=cat git log -1", "git add -- docs/a.md"):
            self.assertEqual(run(command)["result"], "ALLOW", command)

    def test_non_string_command_denied(self):
        policy = docs_vcs(allow_paths=ALLOW_PATHS)
        self.assertEqual(policy(tool_call_event("bash", {"command": {"a": 1}}))["result"], "DENY")


class MidWordQuotingTest(unittest.TestCase):
    def test_mid_word_quoting_denied(self):
        for command in ("g$'it' push origin main", "git a$'dd' backend/app/main.py", "g$'h' pr merge 1"):
            with self.subTest(command=command):
                self.assertEqual(run(command)["result"], "DENY")
