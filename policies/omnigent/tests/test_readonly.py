import unittest

from agent_stack_policies.readonly import read_only_shell

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
    "sed -n 1,80p a.py",
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
    "bash -c 'rg x'",
    "bash -c 'echo x > a'",
    "eval rg x",
    "echo $(rm a)",
    'echo "$(touch a)"',
    "rg x && rm a",
    "make plan",
    "curl -o out https://x.test",
    'echo "unterminated',
]

ALLOWED = [
    "rg -n foo src",
    "rg -n 'a > b' src",
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
    "ls >/dev/null 2>&1",
    "echo ok",
    "rg -l foo | xargs wc -l",
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
    "timeout 30 rg foo",
    "X=1 rg foo",
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
