import asyncio
import importlib
import inspect
import unittest

from agent_stack_policies import POLICY_REGISTRY

from . import assert_not_ask, test_loop, test_merge, test_paths, test_result, test_spawn, test_tools, test_vcs

EVENTS_BY_MODULE = {
    "agent_stack_policies.spawn": test_spawn.EVENTS,
    "agent_stack_policies.paths": test_paths.EVENTS,
    "agent_stack_policies.tools": test_tools.EVENTS,
    "agent_stack_policies.merge": test_merge.EVENTS,
    "agent_stack_policies.unattended": test_tools.EVENTS,
    "agent_stack_policies.loop": test_loop.EVENTS,
    "agent_stack_policies.result": test_result.EVENTS,
    "agent_stack_policies.vcs": test_vcs.EVENTS,
}
DEFAULT_PARAMS = {
    "agent_stack_policies.spawn": {"limit": 3},
    "agent_stack_policies.paths": {"deny": [".github/workflows/**"], "allow_only": ["docs/**"]},
    "agent_stack_policies.tools": {"patterns": ["mcp__*linear_app-*"]},
    "agent_stack_policies.merge": {},
    "agent_stack_policies.unattended": {
        "handler": "agent_stack_policies.tools.deny_tools",
        "arguments": {"patterns": ["mcp__*linear_app-*"]},
    },
    "agent_stack_policies.result": {"kind": "planner"},
    "agent_stack_policies.loop": {},
    "agent_stack_policies.vcs": {"allow_paths": ["docs/**"], "deny": ["AGENTS.md"]},
}


def resolve(handler):
    module_name, _, attr = handler.rpartition(".")
    return importlib.import_module(module_name), getattr(importlib.import_module(module_name), attr)


class RegistryTest(unittest.TestCase):
    def test_registry_handlers_resolve(self):
        self.assertTrue(POLICY_REGISTRY)
        for entry in POLICY_REGISTRY:
            _, factory = resolve(entry["handler"])
            self.assertTrue(callable(factory), entry["handler"])
            self.assertEqual(entry["kind"], "factory")
            self.assertEqual(entry["params_schema"]["type"], "object")

    def test_registry_schema_lists_required(self):
        for entry in POLICY_REGISTRY:
            _, factory = resolve(entry["handler"])
            required = {
                name
                for name, p in inspect.signature(factory).parameters.items()
                if p.kind is inspect.Parameter.KEYWORD_ONLY and p.default is inspect.Parameter.empty
            }
            schema = entry["params_schema"]
            self.assertTrue(required <= set(schema.get("required", [])), entry["handler"])
            self.assertTrue(required <= set(schema["properties"]), entry["handler"])

    def test_no_policy_returns_ask(self):
        for entry in POLICY_REGISTRY:
            module, factory = resolve(entry["handler"])
            policy = factory(**DEFAULT_PARAMS[module.__name__])
            for event in EVENTS_BY_MODULE[module.__name__]:
                decision = policy(event)
                if inspect.isawaitable(decision):
                    decision = asyncio.run(decision)
                assert_not_ask(self, decision)
