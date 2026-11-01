from collections.abc import Callable
from fnmatch import fnmatchcase

from ._event import Decision, Event, deny, tool_name


def gh_api_mutates(args: list[str]) -> bool:
    for index, arg in enumerate(args):
        if arg.startswith(("-f", "-F", "--field", "--raw-field", "--input")):
            return True
        if arg in ("-X", "--method"):
            return index + 1 >= len(args) or args[index + 1].upper() != "GET"
        if arg.startswith("--method="):
            return arg.partition("=")[2].upper() != "GET"
        if arg.startswith("-X") and len(arg) > 2:
            return arg[2:].upper() != "GET"
    return False


def direct_public_write(argv: list[str]) -> bool:
    if argv[:1] == ["git"]:
        command = argv[1] if len(argv) > 1 else ""
        return command in {"push", "tag"} or (command == "remote" and argv[2:] not in ([], ["-v"]))
    if argv[:2] == ["gh", "api"]:
        return gh_api_mutates(argv[2:])
    return argv[:3] in (
        ["gh", "issue", "create"],
        ["gh", "issue", "close"],
        ["gh", "issue", "edit"],
        ["gh", "issue", "comment"],
        ["gh", "pr", "create"],
        ["gh", "pr", "close"],
        ["gh", "pr", "edit"],
        ["gh", "pr", "comment"],
        ["gh", "pr", "review"],
    )


def deny_tools(*, patterns: list[str]) -> Callable[[Event], Decision | None]:
    def policy(event: Event) -> Decision | None:
        name = tool_name(event)
        if name is not None and any(fnmatchcase(name, p) for p in patterns):
            return deny(f"tool {name} is not available to this agent")
        return None

    return policy
