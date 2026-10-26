import asyncio
import unittest
from unittest import mock

from agent_stack_policies import unattended

from . import assert_not_ask, tool_call_event

EVENT = tool_call_event("Bash", {"command": "ls"})
PATCH = mock.patch.object(unattended, "ALLOWED_HANDLER_PREFIXES", ("tests.fake_policies.",))


def run(policy, event=EVENT):
    return asyncio.run(policy(event, {}))


@PATCH
class UnattendedTest(unittest.TestCase):
    def test_wrapper_turns_ask_into_deny(self):
        decision = run(unattended.deny_on_ask(handler="tests.fake_policies.asker", arguments={"reason": "why"}))
        self.assertEqual(decision["result"], "DENY")
        self.assertEqual(decision["reason"], "agent-stack: unattended: why")

    def test_wrapper_normalises_enum_like_verdict(self):
        decision = run(unattended.deny_on_ask(handler="tests.fake_policies.enum_asker"))
        self.assertEqual(decision["result"], "DENY")
        self.assertIn("enum ask", decision["reason"])

    def test_wrapper_passes_allow_and_deny(self):
        allowed = run(unattended.deny_on_ask(handler="tests.fake_policies.allower"))
        self.assertEqual(allowed, {"result": "ALLOW", "reason": ""})
        denied = run(unattended.deny_on_ask(handler="tests.fake_policies.denier"))
        self.assertEqual(denied, {"result": "DENY", "reason": "no"})
        self.assertIsNone(run(unattended.deny_on_ask(handler="tests.fake_policies.abstainer")))

    def test_wrapper_rejects_foreign_handler(self):
        with self.assertRaises(ValueError):
            unattended.deny_on_ask(handler="os.getcwd")

    def test_wrapper_rejects_runtime_counter_builtins(self):
        with mock.patch.object(unattended, "ALLOWED_HANDLER_PREFIXES", ("omnigent.policies.builtins.",)):
            for handler in sorted(unattended.RUNTIME_COUNTER_HANDLERS):
                with self.subTest(handler=handler), self.assertRaisesRegex(ValueError, "runtime counters"):
                    unattended.deny_on_ask(handler=handler)

    def test_wrapper_two_arg_evaluator(self):
        policy = unattended.deny_on_ask(handler="tests.fake_policies.two_arg", arguments={"marker": "m"})
        decision = run(policy)
        self.assertEqual(decision["result"], "DENY")
        self.assertIn("m:['marker']", decision["reason"])

    def test_wrapper_awaits_async(self):
        decision = run(unattended.deny_on_ask(handler="tests.fake_policies.async_asker"))
        self.assertEqual(decision["result"], "DENY")
        self.assertIn("late", decision["reason"])

    def test_wrapper_denies_unrecognised_verdict(self):
        for name in ("odd_result", "non_dict"):
            decision = run(unattended.deny_on_ask(handler=f"tests.fake_policies.{name}"))
            self.assertEqual(decision, {"result": "DENY", "reason": "agent-stack: unattended: unrecognised verdict"})

    def test_wrapper_enum_name_fallback(self):
        decision = run(unattended.deny_on_ask(handler="tests.fake_policies.name_enum"))
        self.assertIn("by name", decision["reason"])

    def test_wrapper_output_is_never_ask(self):
        for name in ("asker", "enum_asker", "allower", "denier", "abstainer", "async_asker"):
            assert_not_ask(self, run(unattended.deny_on_ask(handler=f"tests.fake_policies.{name}")))
