import json
import subprocess
import sys
import unittest

from .test_result import ROOT, fenced, load

INIT = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "0"}}}
INITIALIZED = {"jsonrpc": "2.0", "method": "notifications/initialized"}


def call(text, kind="planner", rid=3):
    return {"jsonrpc": "2.0", "id": rid, "method": "tools/call", "params": {"name": "validate_result", "arguments": {"kind": kind, "text": text}}}


def session(*messages, raw=""):
    stdin = "".join(json.dumps(m) + "\n" for m in messages) + raw
    proc = subprocess.run(
        [sys.executable, "-m", "agent_stack_policies.mcp"],
        input=stdin, capture_output=True, text=True, cwd=ROOT, timeout=30, check=True,
    )
    return {r.get("id"): r for r in map(json.loads, proc.stdout.splitlines())}


class McpServerTest(unittest.TestCase):
    def test_initialize_and_list(self):
        out = session(INIT, INITIALIZED, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        self.assertEqual(sorted(out), [1, 2])
        init = out[1]["result"]
        self.assertEqual(init["protocolVersion"], "2025-06-18")
        self.assertIn("tools", init["capabilities"])
        (tool,) = out[2]["result"]["tools"]
        self.assertEqual(tool["name"], "validate_result")
        self.assertEqual(sorted(tool["inputSchema"]["required"]), ["kind", "text"])

    def test_valid_plan(self):
        res = session(INIT, call(fenced(load("planner-valid.json"))))[3]["result"]
        self.assertEqual(res["structuredContent"], {"valid": True, "errors": [], "has_block": True})
        self.assertEqual(res["content"], [{"type": "text", "text": "valid"}])
        self.assertFalse(res["isError"])

    def test_invalid_plan(self):
        res = session(INIT, call(fenced(load("planner-invalid-uncovered.json"))))[3]["result"]
        sc = res["structuredContent"]
        self.assertFalse(sc["valid"])
        self.assertTrue(sc["has_block"])
        self.assertTrue(any("not covered" in e for e in sc["errors"]))
        self.assertEqual(res["content"][0]["text"], "\n".join(sc["errors"]))

    def test_no_block(self):
        sc = session(INIT, call("just prose"))[3]["result"]["structuredContent"]
        self.assertEqual(sc["valid"], False)
        self.assertEqual(sc["has_block"], False)
        self.assertTrue(sc["errors"])

    def test_bad_arguments_are_tool_errors(self):
        bad = call(None)
        out = session(INIT, bad, call("x", kind="nope", rid=4))
        self.assertTrue(out[3]["result"]["isError"])
        self.assertFalse(out[4]["result"]["structuredContent"]["valid"])

    def test_protocol_errors(self):
        out = session(
            INIT,
            {"jsonrpc": "2.0", "id": 5, "method": "nope"},
            {"jsonrpc": "2.0", "id": 6, "method": "tools/call", "params": {"name": "other", "arguments": {}}},
            {"jsonrpc": "2.0", "id": 7, "method": "ping"},
            raw="{not json\n",
        )
        self.assertEqual(out[5]["error"]["code"], -32601)
        self.assertEqual(out[6]["error"]["code"], -32602)
        self.assertEqual(out[7]["result"], {})
        self.assertEqual(out[None]["error"]["code"], -32700)
