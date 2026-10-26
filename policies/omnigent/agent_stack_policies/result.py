import argparse
import json
import re
import sys
from collections.abc import Callable

from ._event import Decision, Event, allow, deny, is_response, response_text, turn_final

KINDS = ("planner", "security", "documenter")
STATUSES = ("DONE", "DONE_WITH_CONCERNS", "BLOCKED")
BLOCK_CLASSES = ("decision", "permission", "dependency", "environment")
RISK_CLASSES = ("low", "medium", "high")
SEVERITIES = ("critical", "high", "medium", "low")
MAX_CHILDREN = 8
MAX_ERRORS_SHOWN = 5

_BLOCK = re.compile(r"```json[ \t]*\r?\n(.*?)```", re.DOTALL)
_EARS = re.compile(r"^WHEN .+ THE SYSTEM SHALL .+")
_PLACEHOLDER = re.compile(r"\[NEEDS CLARIFICATION|\bTBD\b")


def extract_result(text: str) -> dict | None:
    for body in reversed(_BLOCK.findall(text)):
        try:
            parsed = json.loads(body)
        except ValueError:
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


def _text(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _placeholders(value: object, path: str = "result") -> list[str]:
    if isinstance(value, str):
        return [f"{path}: contains a placeholder"] if _PLACEHOLDER.search(value) else []
    if isinstance(value, dict):
        return [e for k, v in value.items() for e in _placeholders(v, f"{path}.{k}")]
    if isinstance(value, list):
        return [e for i, v in enumerate(value) for e in _placeholders(v, f"{path}[{i}]")]
    return []


def _dict(result: dict, key: str, errors: list[str]) -> dict:
    value = result.get(key)
    if not isinstance(value, dict):
        errors.append(f"{key} must be an object")
        return {}
    return value


def _reachable(graph: dict[str, list[str]]) -> dict[str, set[str]]:
    reach: dict[str, set[str]] = {}
    for start in graph:
        seen: set[str] = set()
        stack = list(graph[start])
        while stack:
            node = stack.pop()
            if node in seen:
                continue
            seen.add(node)
            stack.extend(graph.get(node, []))
        reach[start] = seen
    return reach


def _check_children(children: object, errors: list[str]) -> tuple[set[str], list[dict]]:
    if not isinstance(children, list) or not 1 <= len(children) <= MAX_CHILDREN:
        errors.append(f"children must be a list of 1 to {MAX_CHILDREN} items")
        return set(), []
    keys: set[str] = set()
    ids: set[str] = set()
    valid: list[dict] = []
    for i, child in enumerate(children):
        where = f"children[{i}]"
        if not isinstance(child, dict):
            errors.append(f"{where} must be an object")
            continue
        valid.append(child)
        key = child.get("key")
        if not _text(key):
            errors.append(f"{where}.key must be a non-empty string")
        elif key in keys:
            errors.append(f"{where}.key {key!r} is duplicated")
        else:
            keys.add(key)
        for field in ("title", "description", "independent_test"):
            if not _text(child.get(field)):
                errors.append(f"{where}.{field} must be a non-empty string")
        files = child.get("files")
        if not isinstance(files, list) or not files or not all(_text(f) for f in files):
            errors.append(f"{where}.files must be a non-empty list of paths")
        criteria = child.get("criteria")
        if not isinstance(criteria, list) or not criteria:
            errors.append(f"{where}.criteria must be a non-empty list")
            continue
        for j, criterion in enumerate(criteria):
            if not isinstance(criterion, dict) or not _text(criterion.get("id")):
                errors.append(f"{where}.criteria[{j}] needs an id")
                continue
            cid = criterion["id"]
            if cid in ids:
                errors.append(f"criterion id {cid!r} is duplicated")
            ids.add(cid)
            ears = criterion.get("ears")
            if not isinstance(ears, str) or not _EARS.match(ears):
                errors.append(f"criterion {cid!r} is not in EARS form (WHEN ... THE SYSTEM SHALL ...)")
    return ids, valid


def _check_graph(children: list[dict], errors: list[str]) -> None:
    keyed = {c["key"]: c for c in children if _text(c.get("key"))}
    graph: dict[str, list[str]] = {}
    for key, child in keyed.items():
        deps = child.get("blocked_by", [])
        if not isinstance(deps, list) or not all(isinstance(d, str) for d in deps):
            errors.append(f"child {key!r}: blocked_by must be a list of keys")
            deps = []
        unknown = [d for d in deps if d not in keyed]
        if unknown:
            errors.append(f"child {key!r}: blocked_by names unknown keys {unknown}")
        graph[key] = [d for d in deps if d in keyed]
    reach = _reachable(graph)
    cyclic = [k for k in graph if k in reach[k]]
    if cyclic:
        errors.append(f"blocked_by forms a cycle through {sorted(cyclic)}")
        return
    ordered = list(keyed)
    for i, a in enumerate(ordered):
        for b in ordered[i + 1 :]:
            if b in reach[a] or a in reach[b]:
                continue
            fa, fb = keyed[a].get("files"), keyed[b].get("files")
            if isinstance(fa, list) and isinstance(fb, list):
                shared = sorted(set(map(str, fa)) & set(map(str, fb)))
                if shared:
                    errors.append(f"parallel children {a!r} and {b!r} share files {shared}")


def _check_planner(result: dict, errors: list[str]) -> None:
    if not _text(result.get("design")):
        errors.append("design must be a non-empty string")
    risk = _dict(result, "risk", errors)
    if risk.get("class") not in RISK_CLASSES:
        errors.append(f"risk.class must be one of {'|'.join(RISK_CLASSES)}")
    if not isinstance(risk.get("force_gate_plan"), bool):
        errors.append("risk.force_gate_plan must be a boolean")
    ids, children = _check_children(result.get("children"), errors)
    _check_graph(children, errors)
    critic = _dict(result, "critic", errors)
    rounds = critic.get("rounds")
    if not _int(rounds) or not 1 <= rounds <= 3:
        errors.append("critic.rounds must be an integer from 1 to 3")
    coverage = critic.get("coverage")
    if not isinstance(coverage, list):
        errors.append("critic.coverage must be a list")
        coverage = []
    covered = {c.get("criterion") for c in coverage if isinstance(c, dict)}
    missing = sorted(ids - covered)
    if missing:
        errors.append(f"criteria not covered by critic.coverage: {missing}")


def _check_security(result: dict, errors: list[str]) -> None:
    verdict = result.get("verdict")
    if verdict not in ("pass", "fail"):
        errors.append("verdict must be pass or fail")
    findings = result.get("findings")
    if not isinstance(findings, list):
        errors.append("findings must be a list")
        return
    blocking = False
    for i, finding in enumerate(findings):
        where = f"findings[{i}]"
        if not isinstance(finding, dict):
            errors.append(f"{where} must be an object")
            continue
        for field in ("id", "file", "title", "evidence", "fix"):
            if not _text(finding.get(field)):
                errors.append(f"{where}.{field} must be a non-empty string")
        severity = finding.get("severity")
        if severity not in SEVERITIES:
            errors.append(f"{where}.severity must be one of {'|'.join(SEVERITIES)}")
        blocking = blocking or severity in ("critical", "high")
        line = finding.get("line")
        if not _int(line) or line < 1:
            errors.append(f"{where}.line must be an integer >= 1")
    if verdict in ("pass", "fail") and (verdict == "fail") != blocking:
        errors.append("verdict must be fail exactly when a finding is critical or high")


def _check_documenter(result: dict, errors: list[str]) -> None:
    mode = result.get("mode")
    if mode not in ("delta", "audit"):
        errors.append("mode must be delta or audit")
    changed = result.get("changed_files")
    if not isinstance(changed, list):
        errors.append("changed_files must be a list")
        changed = []
    elif mode == "audit" and changed:
        errors.append("changed_files must be empty in audit mode")
    stale = result.get("stale")
    if not isinstance(stale, list):
        errors.append("stale must be a list")
        stale = []
    for i, item in enumerate(stale):
        if not isinstance(item, dict) or not _text(item.get("file")) or not _text(item.get("reason")):
            errors.append(f"stale[{i}] needs file and reason")
    pr = result.get("pr", False)
    if pr is not None and not isinstance(pr, str):
        errors.append("pr must be a string or null")
    elif mode == "delta" and changed and not _text(pr):
        errors.append("pr is required in delta mode with changed files")


_CHECKS = {"planner": _check_planner, "security": _check_security, "documenter": _check_documenter}


def validate_result(kind: str, result: dict) -> list[str]:
    if kind not in _CHECKS:
        return [f"unknown kind {kind!r}"]
    errors = _placeholders(result)
    status = result.get("status")
    if status not in STATUSES:
        errors.append(f"status must be one of {'|'.join(STATUSES)}")
    if status == "BLOCKED":
        blocked = result.get("blocked")
        blocked = blocked if isinstance(blocked, dict) else {}
        if blocked.get("class") not in BLOCK_CLASSES:
            errors.append(f"blocked.class must be one of {'|'.join(BLOCK_CLASSES)}")
        if not _text(blocked.get("question")):
            errors.append("blocked.question must be a non-empty string")
    elif status in STATUSES:
        _CHECKS[kind](result, errors)
    return errors


def result_contract(*, kind: str) -> Callable[[Event], Decision | None]:
    if kind not in KINDS:
        raise ValueError(f"unknown kind {kind!r}")

    def policy(event: Event) -> Decision | None:
        if not is_response(event):
            return None
        final = turn_final(event)
        if final is False:
            return None
        result = extract_result(response_text(event))
        if result is None:
            if final is True:
                return deny(f"final {kind} response has no ```json result block")
            return None
        errors = validate_result(kind, result)
        if errors:
            return deny(f"invalid {kind} result: " + "; ".join(errors[:MAX_ERRORS_SHOWN]))
        return allow()

    return policy


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="agent_stack_policies.result")
    parser.add_argument("kind", choices=KINDS)
    args = parser.parse_args(argv)
    result = extract_result(sys.stdin.read())
    if result is None:
        print("no ```json result block found", file=sys.stderr)
        return 2
    errors = validate_result(args.kind, result)
    for error in errors:
        print(error)
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
