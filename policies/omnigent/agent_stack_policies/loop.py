import hashlib
import json
from collections.abc import Callable

from ._event import Decision, Event, allow, deny, session_state, tool_call, tool_name

LOOP_STATE_KEY = "agent_stack_loop_recent"


def _call_hash(name: str, arguments: dict) -> str:
    blob = json.dumps({"t": name, "a": arguments}, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode()).hexdigest()


def _streak(history: list[str], current: str) -> int:
    run = 1
    for previous in reversed(history):
        if previous != current:
            break
        run += 1
    return run


def detect_loop(
    *, window: int = 10, threshold: int = 3, exempt_tools: list[str] | None = None
) -> Callable[[Event], Decision | None]:
    if threshold < 2 or window < threshold:
        raise ValueError("need 2 <= threshold <= window")
    exempt = frozenset(["sys_read_inbox"] if exempt_tools is None else exempt_tools)

    def policy(event: Event) -> Decision | None:
        name = tool_name(event)
        if name is None or name in exempt:
            return None
        current = _call_hash(name, tool_call(event, {name}))
        stored = session_state(event).get(LOOP_STATE_KEY)
        history = [h for h in stored if isinstance(h, str)] if isinstance(stored, list) else []
        run = _streak(history, current)
        if run >= threshold:
            return deny(f"loop: {name} called with identical arguments {run} times in a row")
        recent = [*history, current][-window:]
        return allow(state_updates=[{"key": LOOP_STATE_KEY, "action": "set", "value": recent}])

    return policy
