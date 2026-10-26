import json
from collections.abc import Collection

Event = dict[str, object]
Decision = dict[str, object]

TOOL_CALL = "tool_call"


def allow(**extra) -> Decision:
    return {"result": "ALLOW", "reason": "", **extra}


def deny(reason: str) -> Decision:
    return {"result": "DENY", "reason": f"agent-stack: {reason}"}


def session_state(event: Event) -> dict:
    context = event.get("context")
    state = context.get("session_state") if isinstance(context, dict) else None
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
    if not isinstance(data, dict):
        return None
    name = _first(data, ("name", "tool_name"))
    return name if isinstance(name, str) else None


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
