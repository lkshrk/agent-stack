import asyncio
import os
import stat
import subprocess
import sys
import unittest
from pathlib import Path

from agent_stack_policies import POLICY_REGISTRY

from .test_bundles import BUNDLES, IN_IMAGE, agent_policies, server_policies

INSTALL_ROOT = Path("/opt/agent-stack/policies")
PACKAGE = "agent_stack_policies"


def bundle_dirs() -> list[Path]:
    return sorted(p.parent for p in BUNDLES.rglob("config.yaml"))


def walk_specs(spec):
    yield spec
    for child in spec.sub_agents:
        yield from walk_specs(child)


@unittest.skipUnless(IN_IMAGE, "needs the Omnigent image (AGENT_STACK_IN_IMAGE=1)")
class InImageTest(unittest.TestCase):
    def test_installed_import_path(self):
        env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
        out = subprocess.run(
            [sys.executable, "-c", f"import {PACKAGE}; print({PACKAGE}.__file__)"],
            cwd="/",
            env=env,
            capture_output=True,
            text=True,
            check=True,
        )
        self.assertEqual(Path(out.stdout.strip()).parent, INSTALL_ROOT / PACKAGE)
        for path in [INSTALL_ROOT, *INSTALL_ROOT.rglob("*")]:
            mode = path.stat().st_mode
            self.assertTrue(mode & stat.S_IROTH, path)
            if path.is_dir():
                self.assertTrue(mode & stat.S_IXOTH, path)

    def test_registry_loads_in_omnigent(self):
        from omnigent.policies.registry import get_registry, load_registry

        load_registry(extra_modules=[PACKAGE])
        handlers = {entry.handler for entry in get_registry()}
        for entry in POLICY_REGISTRY:
            self.assertIn(entry["handler"], handlers)

    def test_handler_allowlist_accepts_bundle_arguments(self):
        from omnigent.policies.registry import (
            function_policy_handler_allowed,
            load_registry,
            validate_factory_params,
        )
        from omnigent.spec import load

        load_registry(extra_modules=[PACKAGE])
        specs = server_policies()
        for config in sorted(BUNDLES.glob("*/config.yaml")):
            for spec in walk_specs(load(config.parent, expand_env=False)):
                specs.extend(agent_policies(spec))
        self.assertTrue(specs)
        for policy in specs:
            ref = policy.function
            with self.subTest(policy=policy.name, path=ref.path):
                self.assertTrue(function_policy_handler_allowed(ref.path, ref.arguments))
                self.assertIsNone(validate_factory_params(ref.path, ref.arguments))

    def test_phase_names_reach_policies(self):
        from omnigent.policies.function import resolve_function_policy
        from omnigent.policies.types import EvaluationContext
        from omnigent.runtime.policies.engine import _dispatch_policy
        from omnigent.spec.types import FunctionPolicySpec, FunctionRef, Phase

        def run(path, arguments, **ctx):
            spec = FunctionPolicySpec(
                name="probe", on=None, function=FunctionRef(path=path, arguments=arguments)
            )
            result = asyncio.run(_dispatch_policy(resolve_function_policy(spec), EvaluationContext(**ctx), {}))
            return result.action.name

        dispatch = {
            "phase": Phase.TOOL_CALL,
            "content": {"name": "sys_session_send", "arguments": {}},
            "tool_name": "sys_session_send",
        }
        spawn = f"{PACKAGE}.spawn.max_dispatches_per_session"
        self.assertEqual(run(spawn, {"limit": 2}, session_state={"agent_stack_dispatches": 1}, **dispatch), "ALLOW")
        self.assertEqual(run(spawn, {"limit": 2}, session_state={"agent_stack_dispatches": 2}, **dispatch), "DENY")

        contract = f"{PACKAGE}.result.result_contract"
        final = {"phase": Phase.RESPONSE, "content": "All done.", "turn_final": True}
        self.assertEqual(run(contract, {"kind": "planner"}, **final), "DENY")
        self.assertEqual(run(contract, {"kind": "planner"}, **{**final, "turn_final": False}), "ALLOW")

    def test_every_bundle_loads(self):
        from omnigent.spec import load

        dirs = bundle_dirs()
        self.assertTrue(dirs)
        for agent_dir in dirs:
            with self.subTest(agent=str(agent_dir.relative_to(BUNDLES))):
                load(agent_dir, expand_env=False)
