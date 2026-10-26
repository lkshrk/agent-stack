import hashlib
import json
from collections.abc import Callable

from ._event import Decision, Event, allow, deny, session_state, tool_call, tool_name

LOOP_STATE_KEY = "agent_stack_loop_recent"


def _call_hash(name: str, arguments: dict) -> str:
    blob = json.dumps({"t": name, "a": arguments}, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode()).hexdigest()


def detect_loop(*, window: int = 10, threshold: int = 3) -> Callable[[Event], Decision | None]:
    if threshold < 2 or window < threshold:
        raise ValueError("need 2 <= threshold <= window")

    def policy(event: Event) -> Decision | None:
        name = tool_name(event)
        if name is None:
            return None
        current = _call_hash(name, tool_call(event, {name}))
        stored = session_state(event).get(LOOP_STATE_KEY)
        history = [h for h in stored if isinstance(h, str)] if isinstance(stored, list) else []
        recent = [*history, current][-window:]
        count = recent.count(current)
        if count >= threshold:
            return deny(
                f"loop: {name} called with identical arguments {count} times in the last {len(recent)} calls"
            )
        return allow(state_updates=[{"key": LOOP_STATE_KEY, "action": "set", "value": recent}])

    return policy
