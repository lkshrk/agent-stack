import importlib.util
import json
import os
import unittest
from pathlib import Path
from unittest import mock

from agent_stack_hermes import runner

PLUGIN_DIR = Path(__file__).resolve().parents[1] / "plugins" / "omnigent_runner"


def load_plugin():
    spec = importlib.util.spec_from_file_location("omnigent_runner_plugin", PLUGIN_DIR / "__init__.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeContext:
    def __init__(self):
        self.tools, self.hooks = [], []

    def register_tool(self, name, toolset, schema, handler, **kw):
        self.tools.append((name, toolset, schema, handler))

    def register_hook(self, name, callback):
        self.hooks.append((name, callback))


class RecordingKanban:
    blocks = []

    def __init__(self, claimer=None):
        self.claimer = claimer

    def block(self, task_id, run_id, kind, reason):
        self.blocks.append((task_id, run_id, kind, reason))
        return True


class PluginTest(unittest.TestCase):
    def setUp(self):
        self.ctx = FakeContext()
        load_plugin().register(self.ctx)

    def test_manifest_names_the_plugin(self):
        manifest = (PLUGIN_DIR / "plugin.yaml").read_text()
        self.assertIn("name: omnigent_runner\n", manifest)
        self.assertIn("- pre_tool_call", manifest)
        self.assertIn("- omnigent_run", manifest)

    def test_tool_schema_takes_no_arguments(self):
        [(name, toolset, schema, handler)] = self.ctx.tools
        self.assertEqual((name, toolset), ("omnigent_run", "omnigent_runner"))
        self.assertEqual(schema["name"], "omnigent_run")
        self.assertTrue(schema["description"])
        self.assertEqual(schema["parameters"], {"type": "object", "properties": {}, "additionalProperties": False})
        self.assertIs(handler, runner.handle)

    def test_hook_denies_kanban_terminal_tools(self):
        [(name, hook)] = self.ctx.hooks
        self.assertEqual(name, "pre_tool_call")
        kw = {"task_id": "uuid", "session_id": "s", "tool_call_id": "c", "turn_id": "t", "api_request_id": "r"}
        for tool in sorted(runner.DENIED_TOOLS):
            with self.subTest(tool=tool):
                verdict = hook(tool_name=tool, args={}, **kw)
                self.assertEqual(verdict["action"], "block")
                self.assertTrue(verdict["message"].strip())
        self.assertIsNone(hook(tool_name="omnigent_run", args={}, **kw))

    def test_handler_without_config_reports_error(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            out = json.loads(runner.handle({"bundle": "polly"}, task_id="uuid"))
        self.assertEqual((out["state"], out["call_again"], out["session_id"]), ("blocked", False, None))
        self.assertIn("not configured", out["reason"])

    def test_handler_without_config_blocks_the_card(self):
        RecordingKanban.blocks = []
        env = {"HERMES_KANBAN_TASK": "t_1", "HERMES_KANBAN_RUN_ID": "7", "HERMES_KANBAN_CLAIM_LOCK": "h:1"}
        with mock.patch.dict(os.environ, env, clear=True), mock.patch.object(runner, "HermesKanban", RecordingKanban):
            out = json.loads(runner.handle({}))
        self.assertFalse(out["call_again"])
        self.assertEqual(RecordingKanban.blocks, [("t_1", 7, "transient", "BLOCKED{environment}: runner not configured")])


if __name__ == "__main__":
    unittest.main()
