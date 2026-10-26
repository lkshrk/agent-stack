from collections.abc import Callable
from fnmatch import fnmatchcase

from ._event import Decision, Event, deny, tool_name


def deny_tools(*, patterns: list[str]) -> Callable[[Event], Decision | None]:
    def policy(event: Event) -> Decision | None:
        name = tool_name(event)
        if name is not None and any(fnmatchcase(name, p) for p in patterns):
            return deny(f"tool {name} is not available to this agent")
        return None

    return policy
