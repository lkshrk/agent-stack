import re
from collections.abc import Callable

from ._event import Decision, Event, allow, deny, session_state, tool_call

DISPATCH_STATE_KEY = "agent_stack_dispatches"
COLLECT_STATE_KEY = "agent_stack_collections"


def awaiting(state: dict) -> bool:
    dispatched, collected = state.get(DISPATCH_STATE_KEY, 0), state.get(COLLECT_STATE_KEY, 0)
    return isinstance(dispatched, int) and isinstance(collected, int) and dispatched > collected


def _reply_pattern(agent: str | None) -> re.Pattern:
    """A completed sub-agent reply as `sys_read_inbox` renders it (runner/tool_dispatch.py `_format_async_task_item`)."""
    name = re.escape(agent) if agent else r"[^\s:\]]+"
    return re.compile(r"\[System: sub-agent task \S+ completed \u2014 " + name + r"(?::[^\n]*?)? returned: ")


def _first_entry_is_reply(text: str, reply: re.Pattern) -> bool:
    """Only the first inbox entry counts: with one round outstanding it is the critic's, and output can't forge it."""
    return reply.match(text.lstrip()) is not None


def _result_text(event: Event) -> str:
    data = event.get("data")
    if isinstance(data, dict):
        data = data.get("result", data.get("output"))
    return data if isinstance(data, str) else ""


def _result_tool(event: Event, names: frozenset[str]) -> bool:
    if event.get("type") != "tool_result":
        return False
    name = event.get("target")
    request = event.get("request_data")
    if not isinstance(name, str) and isinstance(request, dict):
        name = request.get("name")
    return isinstance(name, str) and any(name == n or name.endswith("__" + n) for n in names)


def max_dispatches_per_session(
    *,
    limit: int,
    dispatch_tools: list[str] | None = None,
    collect_tools: list[str] | None = None,
    collect_from: str | None = None,
) -> Callable[[Event], Decision | None]:
    if limit < 1:
        raise ValueError("limit must be at least 1")
    tools = frozenset(dispatch_tools or ["sys_session_send"])
    collectors = frozenset(collect_tools or [])
    reply = _reply_pattern(collect_from)

    def collect(event: Event) -> Decision | None:
        state = session_state(event)
        dispatched, collected = state.get(DISPATCH_STATE_KEY, 0), state.get(COLLECT_STATE_KEY, 0)
        if not isinstance(dispatched, int) or not isinstance(collected, int):
            return None
        if dispatched <= collected or not _first_entry_is_reply(_result_text(event), reply):
            return None
        return allow(state_updates=[{"key": COLLECT_STATE_KEY, "action": "increment", "value": 1}])

    def policy(event: Event) -> Decision | None:
        if collectors and _result_tool(event, collectors):
            return collect(event)
        if tool_call(event, tools) is None:
            return None
        state = session_state(event)
        count = state.get(DISPATCH_STATE_KEY, 0)
        if not isinstance(count, int) or count >= limit:
            return deny(f"dispatch cap of {limit} per session reached")
        if collectors and awaiting(state):
            return deny("an earlier dispatch is still uncollected; read its reply with sys_read_inbox first")
        return allow(
            state_updates=[{"key": DISPATCH_STATE_KEY, "action": "increment", "value": 1}]
        )

    return policy
