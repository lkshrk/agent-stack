import asyncio
import os
import unittest
from dataclasses import dataclass, field
from pathlib import Path

IN_IMAGE = os.environ.get("AGENT_STACK_IN_IMAGE") == "1"
REPO = Path(__file__).resolve().parents[3]
BUNDLES = REPO / "bundles"
SERVER_CONFIG = REPO / "config" / "omnigent" / "config.yaml"
RANK = {"ALLOW": 0, "ASK": 1, "DENY": 2}


@dataclass(frozen=True)
class Case:
    agent: str
    phase: str
    tool: str | None
    data: object
    expected: str
    session_state: dict = field(default_factory=dict)
    turn_final: bool | None = None


def server_policies() -> list:
    import yaml
    from omnigent.spec import parse_default_policies

    config = yaml.safe_load(SERVER_CONFIG.read_text())
    return parse_default_policies(config.get("policies"), expand_env=False)


def agent_policies(spec) -> list:
    return list(spec.guardrails.policies or []) if spec.guardrails else []


def _fires(policy_spec, case: Case) -> bool:
    if policy_spec.on is None:
        return True
    return any(
        s.phase.value == case.phase and s.tool_name in (None, case.tool) for s in policy_spec.on
    )


def decide(agent_dir: Path, case: Case) -> tuple[str, str | None]:
    from omnigent.policies.function import resolve_function_policy
    from omnigent.policies.types import EvaluationContext
    from omnigent.spec import load
    from omnigent.spec.types import FunctionPolicySpec, Phase

    specs = agent_policies(load(agent_dir, expand_env=False)) + server_policies()
    content = {"name": case.tool, "arguments": case.data} if case.phase == "tool_call" else case.data
    ctx = EvaluationContext(
        phase=Phase(case.phase),
        content=content,
        tool_name=case.tool,
        session_state=dict(case.session_state),
        turn_final=case.turn_final,
    )
    result, reason = "ALLOW", None
    for policy_spec in specs:
        if not isinstance(policy_spec, FunctionPolicySpec):
            raise TypeError(f"{agent_dir}: {policy_spec.name} is not a function policy")
        if not _fires(policy_spec, case):
            continue
        decision = asyncio.run(resolve_function_policy(policy_spec).evaluate(ctx, {}))
        action = decision.action.name
        if RANK[action] > RANK[result]:
            result, reason = action, decision.reason
    return result, reason


def verdict(agent_dir: Path, case: Case) -> str:
    return decide(agent_dir, case)[0]


DISPATCH = {"agent": "claude_code", "title": "fix-x", "args": {"purpose": "implement", "input": "x"}}

CASES: list[Case] = [
    Case("polly", "tool_call", "Write", {"file_path": ".github/workflows/ci.yml", "content": "x"}, "DENY"),
    Case("polly", "tool_call", "Write", {"file_path": "deploy/.env", "content": "x"}, "DENY"),
    Case("polly", "tool_call", "Write", {"file_path": "deploy/.env.example", "content": "x"}, "ALLOW"),
    Case("polly", "tool_call", "Write", {"file_path": "docs/usage.md", "content": "x"}, "ALLOW"),
    Case("polly", "tool_call", "sys_os_shell", {"command": "gh pr merge 3 --squash"}, "DENY"),
    Case("polly", "tool_call", "sys_os_shell", {"command": "git status"}, "ALLOW"),
    Case("polly/agents/codex", "tool_call", "Bash", {"command": "git push origin main"}, "DENY"),
    Case("polly/agents/claude_code", "tool_call", "Edit", {"file_path": "CLAUDE.md"}, "DENY"),
    Case("polly", "tool_call", "sys_os_shell", {"command": "gh pr merge 12"}, "DENY"),
    Case("polly", "tool_call", "sys_os_shell", {"command": "git push origin HEAD:main"}, "DENY"),
    Case("polly", "tool_call", "Write", {"file_path": "docs/notes.md", "content": "x"}, "ALLOW"),
    Case("polly", "tool_call", "sys_session_send", DISPATCH, "DENY", {"agent_stack_dispatches": 24}),
    Case("polly", "tool_call", "sys_session_send", DISPATCH, "ALLOW", {"agent_stack_dispatches": 3}),
    Case("polly", "tool_call", "sys_os_shell", {"command": "ls"}, "DENY", {"_policy_tool_call_count": 800}),
    Case("polly", "tool_call", "sys_add_policy", {"handler": "x"}, "DENY"),
    Case("polly/agents/claude_code", "tool_call", "Bash", {"command": "git push -u origin feat/x"}, "ALLOW"),
    Case("polly/agents/claude_code", "tool_call", "Edit", {"file_path": "backend/.env"}, "DENY"),
    Case("polly/agents/claude_code", "tool_call", "Edit", {"file_path": "backend/.env.example"}, "ALLOW"),
    Case("polly/agents/claude_code", "tool_call", "Bash", {"command": "git push --force origin feat/x"}, "DENY"),
    Case("polly/agents/codex", "tool_call", "Write", {"file_path": "src/a.py", "content": "x"}, "DENY"),
    Case("polly/agents/codex", "tool_call", "Bash", {"command": "rg foo"}, "ALLOW"),
    Case("polly/agents/claude_code", "tool_call", "Bash", {"command": "rm build/a.txt"}, "ALLOW"),
    Case("polly/agents/claude_code", "tool_call", "Bash", {"command": "rm -rf build"}, "ALLOW"),
]


@unittest.skipUnless(IN_IMAGE, "needs the Omnigent image (AGENT_STACK_IN_IMAGE=1)")
class BundleVerdictTest(unittest.TestCase):
    def test_cases(self):
        for case in CASES:
            with self.subTest(case=case):
                self.assertEqual(verdict(BUNDLES / case.agent, case), case.expected)

    def test_polly_loop_denied_not_asked(self):
        from omnigent.policies.builtins.safety import _args_hash

        data = {"command": "make test"}
        repeats = [_args_hash("sys_os_shell", data)] * 2
        state = {"_policy_loop_recent_hashes": repeats}
        case = Case("polly", "tool_call", "sys_os_shell", data, "DENY", state)
        action, reason = decide(BUNDLES / case.agent, case)
        self.assertEqual(action, "DENY")
        self.assertIn("unattended:", reason)

    def test_polly_spawn_disabled(self):
        from omnigent.spec import load

        self.assertIs(load(BUNDLES / "polly", expand_env=False).spawn, False)
