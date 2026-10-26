import importlib
import inspect
import unittest

from agent_stack_policies import POLICY_REGISTRY

from . import assert_not_ask, test_spawn

EVENTS_BY_MODULE = {"agent_stack_policies.spawn": test_spawn.EVENTS}
DEFAULT_PARAMS = {"agent_stack_policies.spawn": {"limit": 3}}


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
                assert_not_ask(self, policy(event))
