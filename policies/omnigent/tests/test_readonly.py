import unittest

from agent_stack_policies.readonly import git_verbs, read_only_shell

from . import assert_not_ask, tool_call_event

EVENTS = [
    tool_call_event("bash", {"command": "rg -n foo src"}),
    tool_call_event("bash", {"command": "echo x > plan.md"}),
    tool_call_event("bash", {"command": 'echo "unterminated'}),
    tool_call_event("bash", {"command": {"x": 1}}),
    tool_call_event("bash", {}),
    tool_call_event("Read", {"file_path": "a"}),
    {"type": "tool_call", "target": "bash", "data": {"name": "bash", "arguments": "{bad"}},
    {"type": "other"},
]

DENIED = [
    "echo x > plan.md",
    "echo x >> plan.md",
    "rg x >| out",
    "ls &> out",
    "ls >& out",
    "cat > a.md <<'EOF'\nx\nEOF",
    "rg x | tee out.txt",
    "sed -i s/a/b/ a.py",
    "awk '{print > \"o\"}' a",
    "perl -pi -e s/a/b/ a.py",
    "python3 -c 'open(\"a\", \"w\")'",
    "rm a",
    "mv a b",
    "cp a b",
    "touch a",
    "mkdir d",
    "ln -s a b",
    "chmod +x a",
    "truncate -s0 a",
    "dd if=a of=b",
    "timeout 5 rm a",
    "ls | xargs rm",
    "find . -name '*.pyc' -delete",
    "find . -fprint out",
    "find . -exec rm {} +",
    "find . -exec cat {} \\; -exec rm {} \\;",
    "sort -o out a",
    "sort --output=out a",
    "uniq a out",
    "rg --pre ./x foo",
    "git commit -m x",
    "git add a",
    "git push origin feat/x",
    "git checkout -- a",
    "git stash",
    "git apply p.diff",
    "git -C repo reset --hard",
    "git -c core.pager=x log",
    "git diff --output=x",
    "git log --ext-diff",
    "git branch -D x",
    "git grep -O foo",
    "git clone https://x.test/r.git",
    "bash -c 'echo x > a'",
    "eval rg x",
    "echo $(rm a)",
    'echo "$(touch a)"',
    "rg x && rm a",
    "make plan",
    "curl -o out https://x.test",
    'echo "unterminated',
    "GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=core.fsmonitor GIT_CONFIG_VALUE_0=touch git status",
    "GIT_EXTERNAL_DIFF=bash git diff HEAD~1",
    "env FOO=1 git status",
    "env -u HOME GIT_DIR=x git log",
    "X=1 rg foo",
    "export GIT_EXTERNAL_DIFF=bash",
    "sort -S 1 --compress-program=./x a",
    "sort --comp=./x a",
    "sort --outp=out a",
    "sort -uo out a",
    "cat <> plan.md",
    "cat 1<>plan.md",
    "rg -n 'a > b' src",
    "rg --hostname-bin=./x foo",
    "git grep --open-files-in-p=vim foo",
    "git grep -nO foo",
    "git --config-env=core.pager=X log",
    "git --git-dir=x log",
    "sed 'w out' a",
    "sed -n '1p;w out' a",
    "sed 'e rm a' a",
    "sed --in-place s/a/b/ a",
    "sed -n -e 1p -e 'w o' a",
    "bash --rcfile x -c 'rg x'",
    "bash -i -c 'rg x'",
    "bash -lc 'rm a'",
    "bash scripts/x.sh",
    "ls | xargs sh -c 'rm a'",
    "less a.py",
    "man git",
    "gh pr view 3",
    "uniq -f 1 a out",
    "rg $'x\\0' src",
    "$'rg\\0' x",
    "echo $'\\cA'",
    "rg $'\\x80' src",
    "$'\\x74ouch' x",
    "t$'ouch' x",
    'r$"m" x',
    "bash -c 'echo ${0@P}' '\\044(touch /tmp/p)'",
    'bash -c \'echo "${1@P}"\' _ \'\\044(touch /tmp/p)\'',
    "bash -c 'echo ${0@P}' '\\140touch /tmp/p\\140'",
    "cat '\\044(touch p)'; echo ${_@P}",
    'echo "${PWD@P}"',
    'cd pr-dir && echo "${PWD@P}"',
    'cd \'$(touch pwn-pwd)\' && echo "${PWD@P}"',
    'bash -lc \'bash -c "echo \\${0@P}" "\\\\044(touch /tmp/p)"\'',
    'echo ${x@E}',
    'echo ${x@Q}',
    'echo ${x@A}',
    "bash -c 'rg x' _ a",
    "printf -v y '%s%s' 'a[$' '(touch /tmp/p)]'; echo ${x:y}",
    "printf -v PATH '%s' ./bin; rg x",
    "printf -v HOME '%s' ./evil; git log -p -1",
    "printf -v GIT_DIR '%s' x; git log",
    'printf -vPATH %s ./bin',
    'printf -- -v x',
    'read x',
    'read -r x <<< y',
    'mapfile a',
    'readarray a',
    'declare x=1',
    'typeset x=1',
    'local x=1',
    'export PATH=./bin',
    'readonly x=1',
    'let x=1',
    'getopts ab x',
    'echo $((1+1))',
    '((x=1))',
    '(( y ))',
    'echo $[1+1]',
    'echo ${x:y}',
    'echo ${x:1:2}',
    'echo ${a[y]}',
    'echo ${#a[y]}',
    'echo ${!x}',
    'echo ${!x[@]}',
    '[[ -v x ]]',
    '[[ -n a ]]',
    'test -v a[y]',
    '[ -v x ]',
    "cat <(touch /tmp/p)",
    "rg x <(touch /tmp/p)",
    "cat <(git push origin feat/x)",
    "diff <(rg a a)<(rm b)",
    "bash -lc 'cat <(git push origin feat/x)'",
    "npx -p evil-pkg cat f",
    "npx cat f",
    "uvx --from httpie rg x",
    "uv run cat f",
    "uv tool run rg x",
    "mise exec -- cat f",
    "mise x -- rg x",
    "pnpm exec cat f",
    "pnpm dlx cat f",
    "script -qc 'cat f' src/a.py",
    "flock /tmp/newfile cat f",
    "time -o src/a.py cat f",
    "/usr/bin/time -o src/a.py cat f",
    "echo -o/tmp/x | xargs sort f",
    "echo --pre=sh | xargs rg x",
    "bash -lc 'echo --output=/tmp/x | xargs git log -1'",
    "printf 'x; touch /tmp/p' | xargs -I{} bash -c 'cat {}'",
    "find . -exec bash -c 'cat {}' \\;",
    "find . -name '*.py' -exec sh -c 'rg x {}' +",
    "./tools/cat f",
    "/tmp/x/git status",
    "scripts/rg x",
    "timeout 30 rg foo",
    "nice rg x",
    "nohup rg x",
    "env rg x",
    "stdbuf -oL rg x",
    "setsid rg x",
    "parallel rg x ::: a b",
    "watch rg x",
    "ionice -c3 rg x",
    "sudo rg x",
    "ls >/dev/null",
    "rg x >/dev/null 2>&1",
    "rg x >&2",
    "rg x 1>&2",
    "rg x 2>>/dev/null",
    "rg x 2>out",
    "bash -lc 'rg x' > out",
    "bash -lc 'rg x > out'",
]

ALLOWED = [
    "rg -n foo src",
    "grep -rn foo src",
    "cat a.py",
    "head -n 40 a.py",
    "tail -n 20 a.py",
    "wc -l a.py",
    "ls -la docs",
    "find . -name '*.py'",
    "find . -name '*.py' -exec wc -l {} +",
    "jq .children plan.json",
    "diff a b",
    "rg x 2>/dev/null",
    "rg x 2> /dev/null | head",
    "rg x 2>&1 | head -20",
    "echo ok",
    "rg foo | sort | uniq -c",
    "git log --oneline -5 -- backend",
    "git diff abc..def -- a.py",
    "git show HEAD:a.py",
    "git status --porcelain",
    "git rev-parse HEAD",
    "git ls-files",
    "git grep -n foo",
    "git blame a.py",
    "git merge-base HEAD origin/main",
    "git branch --show-current",
    "git --no-pager log -1",
    "git -C ~/knowledge log -1",
    "sed -n 1,80p a.py",
    "$'rg' x",
    "$'\\x72g' x src",
    '$"rg" x',
    "rg $'\\t' src",
    "bash -lc 'rg x' 2>&1",
    "cd src && rg -n ${HOME} .",
    "printf '%s\\n' a b",
    "echo ${HOME}",
    "echo $HOME",
    "cat ${HOME}/notes.md",
    "test -f a.py",
    "[ -d src ]",
    "jq '.files[] | .path' pr.json",
    "jq '.a[0]' pr.json",
    "git diff abc...def -- src",
    "rg -n 'a[0-9]' src",
    "rg x 2>/dev/null | head",
    "rg x 2>&1 | head -20",
    "bash -lc 'rg x 2>/dev/null'",
    "bash -lc 'rg -n x src 2>&1 | head -20'",
    "uniq -c a 2>/dev/null",
    "command rg x",
    "find . -name '*.py' -exec wc -l {} +",
    "/bin/bash -lc 'rg x'",
    "sed -n '$p' a.py",
    "sed -n -e 10,20p a.py",
    "bash -lc 'rg -n foo src'",
    "bash -euo pipefail -c 'rg foo | head'",
    "sh -c 'git log -1'",
    "cd sub && rg foo",
    "uniq -f 1 a",
    "uniq -c -s 2 a",
    "sort -k 2,2 -t , a",
    "",
]


def run(command, **kwargs):
    return read_only_shell(**kwargs)(tool_call_event("bash", {"command": command}))


class ReadOnlyShellTest(unittest.TestCase):
    def test_denied(self):
        for command in DENIED:
            with self.subTest(command=command):
                decision = run(command)
                self.assertEqual(decision["result"], "DENY", decision.get("reason"))
                self.assertTrue(decision["reason"].startswith("agent-stack: "))

    def test_allowed(self):
        for command in ALLOWED:
            with self.subTest(command=command):
                decision = run(command)
                self.assertEqual(decision["result"], "ALLOW", decision.get("reason"))

    def test_gh_reads_with_extra_gh(self):
        for command in ("gh pr view 3 --json files", "gh pr diff 3", "gh issue view 1 --repo o/r", "gh api repos/o/r/pulls/3/files"):
            with self.subTest(command=command):
                self.assertEqual(run(command, extra_commands=["gh"])["result"], "ALLOW")
        for command in ("gh pr create --title x", "gh pr review 1 --approve", "gh pr comment 1 -b x",
                        "gh api -X POST repos/o/r/issues", "gh api repos/o/r/issues -f title=x", "gh extension install x/y",
                        "gh pr checkout 3", "gh"):
            with self.subTest(command=command):
                self.assertEqual(run(command, extra_commands=["gh"])["result"], "DENY")

    def test_exact_commands(self):
        clone = "git clone --depth 50 https://github.com/lkshrk/knowledge.git ~/knowledge"
        pull = "git -C ~/knowledge pull --ff-only"
        policy = read_only_shell(exact_commands=[clone, pull])
        for command, expected in (
            (clone, "ALLOW"),
            (pull, "ALLOW"),
            ("git clone --depth 50 https://github.com/x/evil.git ~/knowledge", "DENY"),
            ("git -C ~/knowledge pull", "DENY"),
            (pull + " && rm a", "DENY"),
            ("GIT_DIR=x " + pull, "DENY"),
            (clone + " > out", "DENY"),
        ):
            with self.subTest(command=command):
                self.assertEqual(policy(tool_call_event("bash", {"command": command}))["result"], expected)

    def test_extra_commands(self):
        self.assertEqual(run("yq . a.yaml")["result"], "DENY")
        self.assertEqual(run("yq . a.yaml", extra_commands=["yq"])["result"], "ALLOW")

    def test_reason_names_the_command(self):
        self.assertIn("rm", run("rm a")["reason"])
        self.assertIn("git commit", run("git commit -m x")["reason"])
        self.assertIn("plan.md", run("echo x > plan.md")["reason"])

    def test_shapes(self):
        policy = read_only_shell()
        self.assertEqual(policy(tool_call_event("shell", {"command": ["rg", "x"]}))["result"], "ALLOW")
        self.assertEqual(policy(tool_call_event("shell", {"command": ["bash", "-lc", "rm a"]}))["result"], "DENY")
        self.assertEqual(policy(tool_call_event("shell", {"command": ["bash", "-lc", "rg -n x src"]}))["result"], "ALLOW")
        self.assertEqual(policy(tool_call_event("bash", {"command": {"x": 1}}))["result"], "DENY")
        event = {"type": "tool_call", "target": "bash", "data": {"name": "bash", "arguments": "{bad"}}
        self.assertEqual(policy(event)["result"], "DENY")

    def test_other_tools_abstain(self):
        self.assertIsNone(read_only_shell()(tool_call_event("Read", {"file_path": "a"})))
        self.assertIsNone(read_only_shell()({"type": "other"}))

    def test_never_asks(self):
        policy = read_only_shell()
        for event in EVENTS:
            assert_not_ask(self, policy(event))


GIT_DENIED = [
    "git commit -m x",
    "git push origin feat/x",
    "git merge origin/main",
    "git rebase origin/main",
    "git reset --hard HEAD~1",
    "git checkout -- a.py",
    "git switch main",
    "git restore a.py",
    "git add a.py",
    "git rm a.py",
    "git mv a b",
    "git stash",
    "git cherry-pick abc",
    "git revert abc",
    "git tag v1",
    "git am p.mbox",
    "git apply p.diff",
    "git worktree move a b",
    "git -C .worktrees/x commit -m x",
    "cat <(git push origin feat/x)",
    "cat <(git -C ../wt commit -am x)",
    "echo --output=/tmp/x | xargs git log -1",
    "echo main | xargs git push origin",
    "cat <<'E'\n'\nE\ngit commit -am x #'",
    "cat <<E\nunterminated",
    "git log $'\\0'",
    "$'git\\0' commit -am x",
    "echo $'\\c'; git commit -am x",
    "$'git' push origin feat/x",
    "$'\\x67it' commit -am x",
    '$"git" commit -am x',
    "$G commit -am x",
    "$'\\x67it' commit -m $'it\\'s'",
    'echo "x; git commit -am y',
    'echo "$(git commit -am x"',
    'make test "unterminated',
    'echo "unterminated',
    "echo $(git commit -am x",
    "echo ${x@P}; git status",
    "bash -c 'git status' _ x",
    "printf -v HOME %s ./evil; git diff",
    "HOME=./evil; git diff",
    "export HOME=./evil; git log -1",
    "read x; git log",
    "env HOME=./evil git diff",
    "HOME=./evil git diff",
    "declare -x GIT_DIR=x; git status",
    "make test && git push origin feat/x",
    "bash -lc 'git commit -am x'",
    "echo $(git push)",
    'echo "unterminated git',
]
GIT_ALLOWED = [
    "CI=1 make test",
    "echo ${HOME} && git status",
    "bash -lc 'make test' 2>&1 | tail -5",
    "export FOO=1; make test",
    "printf -v x %s y",
    "git worktree add .worktrees/t1 -b polly/t1",
    "git worktree remove .worktrees/t1",
    "git worktree list",
    "git fetch origin",
    "git -C .worktrees/t1 diff abc...def",
    "git status --porcelain",
    "git log --oneline -5",
    "gh pr diff 3 > .polly/diffs/t1.diff",
    "make test",
    "pnpm test 2>&1 | tail -20",
]


class GitVerbsTest(unittest.TestCase):
    def setUp(self):
        self.policy = git_verbs(extra_verbs=["fetch", "worktree"])

    def test_denied(self):
        for command in GIT_DENIED:
            with self.subTest(command=command):
                decision = self.policy(tool_call_event("bash", {"command": command}))
                self.assertEqual(decision["result"], "DENY", decision.get("reason"))
                self.assertTrue(decision["reason"].startswith("agent-stack: "))

    def test_allowed(self):
        for command in GIT_ALLOWED:
            with self.subTest(command=command):
                decision = self.policy(tool_call_event("bash", {"command": command}))
                self.assertEqual(decision["result"], "ALLOW", decision.get("reason"))

    def test_shapes(self):
        self.assertIsNone(self.policy(tool_call_event("Read", {"file_path": "a"})))
        self.assertEqual(self.policy(tool_call_event("bash", {"command": {"x": 1}}))["result"], "DENY")
        self.assertEqual(git_verbs()(tool_call_event("bash", {"command": "git fetch"}))["result"], "DENY")

    def test_never_asks(self):
        for event in EVENTS:
            assert_not_ask(self, self.policy(event))
