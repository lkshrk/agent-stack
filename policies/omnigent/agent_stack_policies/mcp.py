import json
import sys

from .result import KINDS, extract_result, validate_result

PROTOCOL_VERSION = "2025-06-18"
TOOL = {
    "name": "validate_result",
    "description": (
        "Validate an agent result against its contract. Pass the full response text; "
        "the last fenced json block is checked. Returns 'valid' or one error per line."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "kind": {"type": "string", "enum": list(KINDS)},
            "text": {"type": "string", "description": "Response text holding the fenced json result block."},
        },
        "required": ["kind", "text"],
        "additionalProperties": False,
    },
    "outputSchema": {
        "type": "object",
        "properties": {
            "valid": {"type": "boolean"},
            "errors": {"type": "array", "items": {"type": "string"}},
            "has_block": {"type": "boolean"},
        },
        "required": ["valid", "errors", "has_block"],
    },
}


class RpcError(Exception):
    def __init__(self, code: int, message: str):
        super().__init__(message)
        self.code = code


def _check(kind: str, text: str) -> dict:
    result = extract_result(text)
    if result is None:
        errors = ["no ```json result block found"]
    else:
        errors = validate_result(kind, result)
    report = {"valid": not errors, "errors": errors, "has_block": result is not None}
    return {
        "content": [{"type": "text", "text": "\n".join(errors) or "valid"}],
        "structuredContent": report,
        "isError": False,
    }


def _call(params: dict) -> dict:
    if params.get("name") != TOOL["name"]:
        raise RpcError(-32602, f"unknown tool {params.get('name')!r}")
    args = params.get("arguments")
    args = args if isinstance(args, dict) else {}
    kind, text = args.get("kind"), args.get("text")
    if not isinstance(kind, str) or not isinstance(text, str):
        return {"content": [{"type": "text", "text": "kind and text must be strings"}], "isError": True}
    return _check(kind, text)


def _handle(method: object, params: dict) -> dict:
    if method == "initialize":
        version = params.get("protocolVersion")
        return {
            "protocolVersion": version if isinstance(version, str) else PROTOCOL_VERSION,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": "agent-stack-result", "version": "1"},
        }
    if method == "ping":
        return {}
    if method == "tools/list":
        return {"tools": [TOOL]}
    if method == "tools/call":
        return _call(params)
    raise RpcError(-32601, f"method {method!r} not found")


def respond(line: str) -> dict | None:
    try:
        message = json.loads(line)
    except (ValueError, RecursionError):
        return {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "parse error"}}
    if not isinstance(message, dict) or "method" not in message or "id" not in message:
        return None
    params = message.get("params")
    try:
        result = _handle(message["method"], params if isinstance(params, dict) else {})
    except RpcError as exc:
        return {"jsonrpc": "2.0", "id": message["id"], "error": {"code": exc.code, "message": str(exc)}}
    except Exception as exc:
        error = {"code": -32603, "message": f"internal error: {type(exc).__name__}"}
        return {"jsonrpc": "2.0", "id": message["id"], "error": error}
    return {"jsonrpc": "2.0", "id": message["id"], "result": result}


def main() -> int:
    for line in sys.stdin:
        if not line.strip():
            continue
        reply = respond(line)
        if reply is not None:
            sys.stdout.write(json.dumps(reply) + "\n")
            sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
