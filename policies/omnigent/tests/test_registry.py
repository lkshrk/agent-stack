import asyncio
import importlib
import inspect
import unittest

from agent_stack_policies import POLICY_REGISTRY

from . import HAS_YAML, assert_not_ask, test_agents, test_infra_cluster, test_infra_vcs, test_loop, test_merge, test_paths, test_process_secrets, test_readonly, test_result, test_secret_files, test_spawn, test_tools, test_vcs

NEEDS_YAML = {"agent_stack_policies.public_contributions"}
ENTRIES = [entry for entry in POLICY_REGISTRY if HAS_YAML or entry["handler"].rpartition(".")[0] not in NEEDS_YAML]

EVENTS_BY_MODULE = {
    "agent_stack_policies.agents": test_agents.EVENTS,
    "agent_stack_policies.spawn": test_spawn.EVENTS,
    "agent_stack_policies.paths": test_paths.EVENTS,
    "agent_stack_policies.tools": test_tools.EVENTS,
    "agent_stack_policies.merge": test_merge.EVENTS,
    "agent_stack_policies.unattended": test_tools.EVENTS,
    "agent_stack_policies.loop": test_loop.EVENTS,
    "agent_stack_policies.result": test_result.EVENTS,
    "agent_stack_policies.vcs": test_vcs.EVENTS,
    "agent_stack_policies.infra_vcs": test_infra_vcs.EVENTS,
    "agent_stack_policies.infra_cluster": test_infra_cluster.EVENTS,
    "agent_stack_policies.readonly": test_readonly.EVENTS,
    "agent_stack_policies.secret_files": test_secret_files.EVENTS,
    "agent_stack_policies.process_secrets": test_process_secrets.EVENTS,
}
if HAS_YAML:
    from . import test_public_contribution_policy

    EVENTS_BY_MODULE["agent_stack_policies.public_contributions"] = test_public_contribution_policy.EVENTS
DEFAULT_PARAMS = {
    "agent_stack_policies.agents": {},
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
    "agent_stack_policies.infra_vcs": {},
    "agent_stack_policies.infra_cluster": {},
    "agent_stack_policies.readonly": {},
    "agent_stack_policies.process_secrets": {},
    "agent_stack_policies.secret_files": {"secret_dir": "/agent-secrets", "token_file": "/agent-secrets/github/review"},
    "agent_stack_policies.public_contributions": {},
}


def resolve(handler):
    module_name, _, attr = handler.rpartition(".")
    return importlib.import_module(module_name), getattr(importlib.import_module(module_name), attr)


class RegistryTest(unittest.TestCase):
    def test_registry_handlers_resolve(self):
        self.assertTrue(ENTRIES)
        for entry in ENTRIES:
            _, factory = resolve(entry["handler"])
            self.assertTrue(callable(factory), entry["handler"])
            self.assertEqual(entry["kind"], "factory")
            self.assertEqual(entry["params_schema"]["type"], "object")

    def test_registry_schema_lists_required(self):
        for entry in ENTRIES:
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
        for entry in ENTRIES:
            module, factory = resolve(entry["handler"])
            policy = factory(**DEFAULT_PARAMS[module.__name__])
            for event in EVENTS_BY_MODULE[module.__name__]:
                decision = policy(event)
                if inspect.isawaitable(decision):
                    decision = asyncio.run(decision)
                assert_not_ask(self, decision)
