from collections.abc import Callable
from fnmatch import fnmatchcase

from ._event import Decision, Event, deny, tool_name


_GH_API_SHORT_VALUES = frozenset("FHXfpqt")
_GH_API_SHORT_FLAGS = frozenset("i")
_GH_API_LONG_VALUES = frozenset(
    {"--method", "--field", "--raw-field", "--input", "--header", "--preview", "--jq", "--template", "--hostname", "--cache"}
)
_GH_API_BODY = frozenset({"f", "F", "--field", "--raw-field", "--input"})


def _gh_api_options(args: list[str]) -> list[tuple[str, str]] | None:
    """(option, value) pairs of `gh api` args as pflag reads them, or None for a form that can't be read."""
    options: list[tuple[str, str]] = []
    index = 0
    while index < len(args):
        arg = args[index]
        index += 1
        if arg == "--":
            break
        if arg.startswith("--"):
            name, equals, value = arg.partition("=")
            if name in _GH_API_LONG_VALUES:
                if not equals:
                    if index >= len(args):
                        return None
                    value, index = args[index], index + 1
                options.append((name, value))
            else:
                options.append((name, ""))
            continue
        if not arg.startswith("-") or arg == "-":
            continue
        letters = arg[1:]
        while letters:
            letter, letters = letters[0], letters[1:]
            if letter in _GH_API_SHORT_FLAGS:
                continue
            if letter not in _GH_API_SHORT_VALUES:
                return None
            value = letters.removeprefix("=")
            if not letters:
                if index >= len(args):
                    return None
                value, index = args[index], index + 1
            options.append((letter, value))
            break
    return options


def gh_api_mutates(args: list[str]) -> bool:
    """Whether `gh api` sends anything but a GET: the last -X/--method wins, and any body field counts as a write."""
    options = _gh_api_options(args)
    if options is None:
        return True
    if any(name in _GH_API_BODY for name, _ in options):
        return True
    methods = [value for name, value in options if name in ("X", "--method")]
    return bool(methods) and methods[-1].upper() != "GET"


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
