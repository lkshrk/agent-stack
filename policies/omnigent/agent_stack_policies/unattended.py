import importlib
import inspect
import json
import shlex
from collections.abc import Awaitable, Callable

from ._event import TOOL_CALL, Decision, Event, deny

ALLOWED_HANDLER_PREFIXES = ("omnigent.policies.builtins.", "agent_stack_policies.")
# The engine accepts their reserved `_policy_*` counter writes only from the unwrapped builtin.
RUNTIME_COUNTER_HANDLERS = frozenset(
    {
        "omnigent.policies.builtins.orchestration.spawn_bounds",
        "omnigent.policies.builtins.safety.max_tool_calls_per_session",
        "omnigent.policies.builtins.safety.detect_loop",
    }
)


KNOWN_VERDICTS = ("ALLOW", "DENY", "ASK")


def _verdict(decision: object) -> str | None:
    if not isinstance(decision, dict):
        return None
    value = decision.get("result")
    if not isinstance(value, str):
        inner = getattr(value, "value", None)
        value = inner if isinstance(inner, str) else getattr(value, "name", None)
    if not isinstance(value, str) or value.upper() not in KNOWN_VERDICTS:
        return None
    return value.upper()


def _takes_config(evaluator: Callable) -> bool:
    try:
        params = inspect.signature(evaluator).parameters.values()
    except (TypeError, ValueError):
        return False
    positional = (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)
    return sum(p.kind in positional for p in params) >= 2 or any(
        p.kind is inspect.Parameter.VAR_POSITIONAL for p in params
    )


def _with_string_command(event: Event) -> Event:
    """The event with a list-form shell `command` joined into one string, for handlers that only read strings."""
    data = event.get("data")
    if event.get("type") != TOOL_CALL or not isinstance(data, dict):
        return event
    key = next((k for k in ("arguments", "input", "args") if k in data), None)
    arguments = data.get(key)
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments)
        except ValueError:
            return event
    if not isinstance(arguments, dict):
        return event
    command = arguments.get("command")
    if not isinstance(command, list) or not all(isinstance(part, str) for part in command):
        return event
    return {**event, "data": {**data, key: {**arguments, "command": shlex.join(command)}}}


def deny_on_ask(
    *, handler: str, arguments: dict | None = None, join_list_commands: bool = False
) -> Callable[[Event, dict], Awaitable[Decision | None]]:
    if not handler.startswith(ALLOWED_HANDLER_PREFIXES):
        raise ValueError(f"handler {handler!r} is outside the allowed prefixes")
    if handler in RUNTIME_COUNTER_HANDLERS:
        raise ValueError(f"handler {handler!r} writes runtime counters and cannot be wrapped")
    module_name, _, attr = handler.rpartition(".")
    factory = getattr(importlib.import_module(module_name), attr)
    inner = factory(**arguments) if arguments else factory()
    wants_config = _takes_config(inner)
    config = dict(arguments or {})

    async def policy(event: Event, _config: dict | None = None) -> Decision | None:
        if join_list_commands:
            event = _with_string_command(event)
        decision = inner(event, config) if wants_config else inner(event)
        if inspect.isawaitable(decision):
            decision = await decision
        if decision is None:
            return None
        verdict = _verdict(decision)
        if verdict is None:
            return deny("unattended: unrecognised verdict")
        if verdict == "ASK":
            return deny(f"unattended: {decision.get('reason', '')}")
        return decision

    return policy
