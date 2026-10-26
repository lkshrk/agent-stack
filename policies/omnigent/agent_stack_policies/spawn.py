from collections.abc import Callable

from ._event import Decision, Event, allow, deny, session_state, tool_call

DISPATCH_STATE_KEY = "agent_stack_dispatches"
COLLECT_STATE_KEY = "agent_stack_collections"


def awaiting(state: dict) -> bool:
    dispatched, collected = state.get(DISPATCH_STATE_KEY, 0), state.get(COLLECT_STATE_KEY, 0)
    return isinstance(dispatched, int) and isinstance(collected, int) and dispatched > collected


def max_dispatches_per_session(
    *, limit: int, dispatch_tools: list[str] | None = None, collect_tools: list[str] | None = None
) -> Callable[[Event], Decision | None]:
    if limit < 1:
        raise ValueError("limit must be at least 1")
    tools = frozenset(dispatch_tools or ["sys_session_send"])
    collectors = frozenset(collect_tools or [])

    def policy(event: Event) -> Decision | None:
        if collectors and tool_call(event, collectors) is not None:
            return allow(state_updates=[{"key": COLLECT_STATE_KEY, "action": "increment", "value": 1}])
        if tool_call(event, tools) is None:
            return None
        count = session_state(event).get(DISPATCH_STATE_KEY, 0)
        if not isinstance(count, int) or count >= limit:
            return deny(f"dispatch cap of {limit} per session reached")
        return allow(
            state_updates=[{"key": DISPATCH_STATE_KEY, "action": "increment", "value": 1}]
        )

    return policy
