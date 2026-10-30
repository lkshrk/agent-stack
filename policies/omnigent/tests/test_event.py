import unittest

from agent_stack_policies._event import session_state, tool_call, tool_name


def event(data):
    return {"type": "tool_call", "data": data}


class ToolCallTest(unittest.TestCase):
    def test_json_string_arguments_parsed(self):
        result = tool_call(event({"name": "Write", "arguments": '{"path": "a"}'}), {"Write"})
        self.assertEqual(result, {"path": "a"})

    def test_invalid_json_unparseable(self):
        result = tool_call(event({"name": "Write", "arguments": "{bad"}), {"Write"})
        self.assertEqual(result, {"_unparseable": True})

    def test_non_dict_arguments_unparseable(self):
        for arguments in ('["a"]', "3", [1], 7):
            result = tool_call(event({"name": "Write", "arguments": arguments}), {"Write"})
            self.assertEqual(result, {"_unparseable": True}, arguments)

    def test_alias_keys(self):
        for name_key in ("name", "tool_name"):
            for args_key in ("arguments", "input", "args"):
                result = tool_call(event({name_key: "Write", args_key: {"path": "a"}}), {"Write"})
                self.assertEqual(result, {"path": "a"}, (name_key, args_key))

    def test_no_arguments_is_empty_dict_not_none(self):
        result = tool_call(event({"name": "Write"}), {"Write"})
        self.assertEqual(result, {})
        self.assertIsNotNone(result)

    def test_non_matching_returns_none(self):
        self.assertIsNone(tool_call(event({"name": "Read"}), {"Write"}))
        self.assertIsNone(tool_call({"type": "other", "data": {"name": "Write"}}, {"Write"}))
        self.assertIsNone(tool_call({"type": "tool_call", "data": "x"}, {"Write"}))

    def test_name_falls_back_to_target(self):
        self.assertEqual(tool_name({"type": "tool_call", "target": "Write", "data": {}}), "Write")
        self.assertEqual(tool_name({"type": "tool_call", "target": "X", "data": {"name": "Write"}}), "Write")

    def test_agent_start_probe_is_ignored(self):
        probe = {"type": "tool_call", "target": "sys_agent_start", "data": {"name": "sys_agent_start", "arguments": {}}}
        self.assertIsNone(tool_name(probe))
        self.assertIsNone(tool_call(probe, {"sys_agent_start"}))

    def test_session_state_is_top_level(self):
        self.assertEqual(session_state({"context": {"session_state": {"k": 1}}}), {})
        self.assertEqual(session_state({"session_state": {"k": 1}}), {"k": 1})
