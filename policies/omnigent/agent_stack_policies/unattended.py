import importlib
import inspect
from collections.abc import Awaitable, Callable

from ._event import Decision, Event, deny

ALLOWED_HANDLER_PREFIXES = ("omnigent.policies.builtins.", "agent_stack_policies.")


def _verdict(decision: object) -> str | None:
    if not isinstance(decision, dict):
        return None
    value = decision.get("result")
    if not isinstance(value, str):
        value = getattr(value, "value", None) or getattr(value, "name", None)
    return value.upper() if isinstance(value, str) else None


def _takes_config(evaluator: Callable) -> bool:
    try:
        params = inspect.signature(evaluator).parameters.values()
    except (TypeError, ValueError):
        return False
    positional = (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)
    return sum(p.kind in positional for p in params) >= 2 or any(
        p.kind is inspect.Parameter.VAR_POSITIONAL for p in params
    )


def deny_on_ask(
    *, handler: str, arguments: dict | None = None
) -> Callable[[Event, dict], Awaitable[Decision | None]]:
    if not handler.startswith(ALLOWED_HANDLER_PREFIXES):
        raise ValueError(f"handler {handler!r} is outside the allowed prefixes")
    module_name, _, attr = handler.rpartition(".")
    factory = getattr(importlib.import_module(module_name), attr)
    inner = factory(**arguments) if arguments else factory()
    wants_config = _takes_config(inner)
    config = dict(arguments or {})

    async def policy(event: Event, _config: dict | None = None) -> Decision | None:
        decision = inner(event, config) if wants_config else inner(event)
        if inspect.isawaitable(decision):
            decision = await decision
        if _verdict(decision) == "ASK":
            return deny(f"unattended: {decision.get('reason', '')}")
        return decision

    return policy
