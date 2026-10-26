import json
from collections.abc import Collection

Event = dict[str, object]
Decision = dict[str, object]

TOOL_CALL = "tool_call"
AGENT_START_PROBE = "sys_agent_start"


def allow(**extra) -> Decision:
    return {"result": "ALLOW", "reason": "", **extra}


def deny(reason: str) -> Decision:
    return {"result": "DENY", "reason": f"agent-stack: {reason}"}


def session_state(event: Event) -> dict:
    state = event.get("session_state")
    return state if isinstance(state, dict) else {}


def _first(data: dict, keys: tuple[str, ...]):
    for key in keys:
        if key in data:
            return data[key]
    return None


def tool_name(event: Event) -> str | None:
    if event.get("type") != TOOL_CALL:
        return None
    data = event.get("data")
    name = _first(data, ("name", "tool_name")) if isinstance(data, dict) else None
    if not isinstance(name, str):
        name = event.get("target")
    if not isinstance(name, str) or name == AGENT_START_PROBE:
        return None
    return name


def tool_call(event: Event, names: Collection[str]) -> dict | None:
    """Callers must test `is None`: `{}` is a valid match with no arguments."""
    name = tool_name(event)
    if name is None or name not in names:
        return None
    arguments = _first(event["data"], ("arguments", "input", "args"))
    if arguments is None:
        return {}
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments)
        except ValueError:
            return {"_unparseable": True}
    if not isinstance(arguments, dict):
        return {"_unparseable": True}
    return arguments


def is_response(event: Event) -> bool:
    return event.get("type") == "response"


def response_text(event: Event) -> str:
    data = event.get("data")
    if isinstance(data, dict):
        data = data.get("text", data.get("content"))
    if isinstance(data, str):
        return data
    if isinstance(data, list):
        parts = [b.get("text") for b in data if isinstance(b, dict) and b.get("type") == "text"]
        return "\n".join(p for p in parts if isinstance(p, str))
    return ""


def turn_final(event: Event) -> bool | None:
    context = event.get("context")
    value = context.get("turn_final") if isinstance(context, dict) else None
    return value if isinstance(value, bool) else None
