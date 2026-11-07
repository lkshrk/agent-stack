from collections.abc import Callable

from ._event import Decision, Event, allow, tool_call
from ._event import deny as _deny
from ._shell import OpaqueCommand, group_token, resolve, shell_command, shell_shape_problem, walk
from .paths import SHELL_TOOLS

_KUBE_VALUES = frozenset({
    "-n", "--namespace", "--context", "--kubeconfig", "-s", "--server", "--cluster", "--user", "--as", "--as-group",
    "--as-uid", "--token", "--request-timeout", "-v", "--v", "--cache-dir", "--certificate-authority",
    "--client-certificate", "--client-key", "--tls-server-name",
})
_KUBECTL = frozenset({"get", "describe", "logs", "events", "wait", "auth", "top", "version", "api-resources", "explain"})
_KUBECTL_AUTH = frozenset({"whoami", "can-i"})
_FLUX_VALUES = frozenset({"-n", "--namespace", "--context", "--kubeconfig", "--timeout", "--cluster", "--user"})
_FLUX = frozenset({"reconcile", "get", "logs", "trace", "tree", "diff", "stats", "version", "check"})
_FLUX_RECONCILE_DENIED = ("--force", "--reset")
_HELM_VALUES = frozenset({
    "-n", "--namespace", "--kube-context", "--kubeconfig", "--registry-config", "--repository-config",
    "--repository-cache", "--burst-limit", "--qps", "--kube-apiserver", "--kube-token", "--kube-as-user",
    "--kube-as-group", "--kube-ca-file", "--kube-tls-server-name",
})
_HELM = frozenset({"template", "show", "pull", "search", "history", "status", "get", "list", "ls", "version"})
_HELM_GROUPS = {"repo": frozenset({"add", "update"}), "dependency": frozenset({"build"}), "dep": frozenset({"build"})}
_GROUPING = frozenset({"(", ")", "{", "}", "$"})

KUBECTL_FORM = (
    "kubectl is read-only here: get, describe, logs, events, wait, auth whoami|can-i, top, version, api-resources, explain"
)
FLUX_FORM = "flux allows only reconcile (without --force or --reset), get, logs, trace, tree, diff, stats, version and check"
HELM_FORM = (
    "helm allows only template, show, pull, search, history, status, get, list, version, repo add|update and "
    "dependency build"
)


def _words(rest: list[str], values: frozenset[str]) -> list[str]:
    """The positional words of a command line, skipping its known value options; an unknown option counts as a flag."""
    out, i = [], 0
    while i < len(rest):
        arg = rest[i]
        if arg == "--":
            out.extend(rest[i + 1 :])
            break
        if arg.startswith("-") and arg != "-":
            i += 2 if arg in values else 1
            continue
        out.append(arg)
        i += 1
    return out


def _first_word(rest: list[str], values: frozenset[str]) -> tuple[str | None, list[str]]:
    i = 0
    while i < len(rest) and rest[i].startswith("-"):
        i += 2 if rest[i] in values else 1
    if i >= len(rest):
        return None, []
    return rest[i], rest[i + 1 :]


def _kubectl(rest: list[str]) -> str | None:
    verb, args = _first_word(rest, _KUBE_VALUES)
    if verb is None:
        return None
    if verb not in _KUBECTL:
        return KUBECTL_FORM
    if verb == "auth":
        sub = _words(args, _KUBE_VALUES)[:1]
        if sub and sub[0] not in _KUBECTL_AUTH:
            return KUBECTL_FORM
    return None


def _flux(rest: list[str]) -> str | None:
    verb, args = _first_word(rest, _FLUX_VALUES)
    if verb is None:
        return None
    if verb not in _FLUX:
        return FLUX_FORM
    if verb == "reconcile" and any(a.startswith(_FLUX_RECONCILE_DENIED) for a in args):
        return FLUX_FORM
    return None


def _helm(rest: list[str]) -> str | None:
    verb, args = _first_word(rest, _HELM_VALUES)
    if verb is None or verb in _HELM:
        return None
    group = _HELM_GROUPS.get(verb)
    sub = _words(args, _HELM_VALUES)[:1]
    if group is None or not sub or sub[0] not in group:
        return HELM_FORM
    return None


_CHECKS = {"kubectl": _kubectl, "flux": _flux, "helm": _helm}


def _segments(tokens: list[str]) -> list[list[str]]:
    segments, current = [], []
    for token in tokens:
        if token in _GROUPING or group_token(token):
            segments.append(current)
            current = []
        else:
            current.append(token)
    segments.append(current)
    return [s for s in segments if s]


def infra_cluster() -> Callable[[Event], Decision | None]:
    def policy(event: Event) -> Decision | None:
        arguments = tool_call(event, SHELL_TOOLS)
        if arguments is None:
            return None
        if arguments.get("_unparseable"):
            return _deny("unparseable tool arguments")
        command = shell_command(arguments)
        if command is None:
            return _deny("shell command is not a string")
        try:
            statements = walk(command)
        except OpaqueCommand as error:
            return _deny(str(error))
        except ValueError:
            return _deny("unparseable shell command; simplify it")
        reason = shell_shape_problem(statements)
        if reason:
            return _deny(reason)
        for tokens in statements:
            for segment in _segments(tokens):
                argv = resolve(segment).argv
                check = _CHECKS.get(argv[0].rsplit("/", 1)[-1]) if argv else None
                reason = check(argv[1:]) if check else None
                if reason:
                    return _deny(reason)
        return allow()

    return policy
