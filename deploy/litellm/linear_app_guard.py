import asyncio
import logging
import os
import re
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class Issue:
    id: str
    identifier: str
    parent_id: str | None
    creator_id: str | None
    labels: tuple[str, ...]
    child_count: int


@dataclass(frozen=True)
class Policy:
    owner_id: str
    app_user_id: str
    max_depth: int = 2
    max_children: int = 8
    walk_cap: int = 10

    def __post_init__(self):
        if not self.owner_id or not self.app_user_id or self.owner_id == self.app_user_id:
            raise ValueError("owner_id and app_user_id must be non-empty and different")


class Lookup(Protocol):
    async def issue(self, ref: str) -> Issue | None: ...


READ_TOOLS = frozenset(
    {
        "get_issue",
        "list_issues",
        "list_comments",
        "get_project",
        "list_projects",
        "list_teams",
        "get_team",
        "list_issue_labels",
        "list_issue_statuses",
        "get_issue_status",
        "list_users",
        "get_user",
        "list_milestones",
        "get_milestone",
        "list_cycles",
        "get_document",
        "list_documents",
    }
)

logger = logging.getLogger("LiteLLM Proxy")

LOOKUP_TIMEOUT = 5.0
GATE_RANK = {"gate:pr": 0, "gate:split": 1, "gate:plan": 2}
OWNER_ISSUE_KEYS = frozenset({"id", "addLabels", "links", "blocks", "blockedBy", "relatedTo"})
FORBIDDEN_UPDATE_KEYS = ("state", "team", "duplicateOf", "template", "labels")
ASSIGN_KEYS = ("delegate", "delegateId", "assignee", "assigneeId")
RELATION_KEYS = ("blocks", "blockedBy", "relatedTo", "removeBlocks", "removeBlockedBy", "removeRelatedTo")
MAX_RELATIONS = 10
UPLOAD_CONTENT_TYPES = frozenset({"image/png", "image/jpeg", "image/webp"})
MAX_UPLOAD_BYTES = 10 * 1024 * 1024
MAX_UPLOAD_FILENAME = 200


UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


def _norm(name: str) -> str:
    return name.strip().casefold()


def _label_names(key: str, value) -> tuple[list[str] | None, str | None]:
    if not isinstance(value, list) or not all(isinstance(name, str) for name in value):
        return None, f"{key} must be a list of names"
    names = [_norm(name) for name in value]
    if any(UUID.fullmatch(name) for name in names):
        return None, "label ids not allowed, use names"
    return names, None


def _reserved_label(names: list[str]) -> str | None:
    return next((name for name in names if name == "epic" or name.startswith("chain:")), None)


def _label_error(args: dict, target: Issue, owner_issue: bool) -> str | None:
    if "removeLabels" in args:
        return "label removal not allowed"
    if "addLabels" not in args:
        return None
    names, error = _label_names("addLabels", args["addLabels"])
    if error:
        return error
    if owner_issue:
        if any(not name.startswith("gate:") for name in names):
            return "owner issue: only gate labels allowed"
    elif reserved := _reserved_label(names):
        return f"label {reserved} not allowed"
    gates = [name for name in names if name.startswith("gate:")]
    if any(name not in GATE_RANK for name in gates):
        return "unknown gate label"
    current = max((GATE_RANK[n] for n in map(_norm, target.labels) if n in GATE_RANK), default=GATE_RANK["gate:plan"])
    if any(GATE_RANK[name] < current for name in gates):
        return "gate cannot be lowered"
    return None


def _create_label_error(args: dict) -> str | None:
    for key in ("labels", "addLabels"):
        if key not in args:
            continue
        names, error = _label_names(key, args[key])
        if error:
            return error
        if any(name.startswith("gate:") for name in names):
            return "gate labels on create are owner-only"
        if reserved := _reserved_label(names):
            return f"label {reserved} not allowed"
    return None


def _upload_error(args: dict) -> str | None:
    content_type = args.get("contentType")
    if not isinstance(content_type, str) or content_type not in UPLOAD_CONTENT_TYPES:
        return "content type not allowed"
    size = args.get("size")
    if type(size) is not int or not 1 <= size <= MAX_UPLOAD_BYTES:
        return "upload size not allowed"
    name = args.get("filename")
    if not isinstance(name, str) or not name.strip() or len(name) > MAX_UPLOAD_FILENAME or any(c in name for c in "/\\\0"):
        return "filename not allowed"
    return None


class _Denied(Exception):
    pass


class _MemoLookup:
    def __init__(self, lookup: Lookup):
        self._lookup = lookup
        self._tasks: dict[str, asyncio.Task] = {}

    async def issue(self, ref: str) -> Issue | None:
        task = self._tasks.get(ref)
        if task is None:
            task = self._tasks[ref] = asyncio.ensure_future(self._lookup.issue(ref))
        found = await asyncio.shield(task)
        if found is not None:
            self._tasks.setdefault(found.id, task)
            self._tasks.setdefault(found.identifier, task)
        return found

    def close(self) -> None:
        for task in self._tasks.values():
            if not task.done():
                task.cancel()
            elif not task.cancelled():
                task.exception()


async def _walk(
    start: Issue, lookup: Lookup, policy: Policy, moving: str | None = None
) -> tuple[Issue | None, int, str | None]:
    seen = {start.id}
    node = start
    while node.parent_id:
        if len(seen) >= policy.walk_cap:
            return None, len(seen), "parent chain too long"
        node = await lookup.issue(node.parent_id)
        if node is None:
            return None, len(seen), "parent chain broken"
        if node.id in seen or node.id == moving:
            return None, len(seen), "parent chain loops"
        seen.add(node.id)
    return node, len(seen), None


async def _check_parent(
    parent: Issue, lookup: Lookup, policy: Policy, moving: str | None = None
) -> tuple[str | None, Issue | None]:
    root, depth, error = await _walk(parent, lookup, policy, moving)
    if error:
        return error, None
    if root.creator_id != policy.owner_id:
        return "root issue is not owner-created", None
    if depth > policy.max_depth:
        return "sub-issue depth exceeds limit", None
    if parent.child_count >= policy.max_children:
        return "parent has too many children", None
    return None, root


def _relation_refs(args: dict) -> tuple[set[str], str | None]:
    refs: set[str] = set()
    for key in RELATION_KEYS:
        if key not in args:
            continue
        value = args[key]
        if not isinstance(value, list) or not all(isinstance(ref, str) and ref for ref in value):
            return refs, f"{key} must be a list of issue ids"
        refs.update(value)
    if len(refs) > MAX_RELATIONS:
        return refs, "too many relations"
    return refs, None


async def _relations_error(refs: set[str], root: Issue, lookup: Lookup, policy: Policy) -> str | None:
    async def check(ref: str) -> None:
        found = await lookup.issue(ref)
        if found is None:
            raise _Denied("relation target not found")
        target_root, _, error = await _walk(found, lookup, policy)
        if error:
            raise _Denied(f"relation target: {error}")
        if target_root.id != root.id:
            raise _Denied("relation target outside the issue's root")

    try:
        async with asyncio.TaskGroup() as group:
            for ref in sorted(refs):
                group.create_task(check(ref))
    except ExceptionGroup as group:
        denied = group.subgroup(_Denied)
        if denied is None:
            raise
        return str(denied.exceptions[0])
    return None


async def _create(args: dict, lookup: Lookup, policy: Policy) -> str | None:
    ref = args.get("parentId")
    if not isinstance(ref, str) or not ref:
        return "parent required"
    for key in ("template", "state", "duplicateOf"):
        if key in args:
            return f"{key} not allowed"
    error = _create_label_error(args)
    if error:
        return error
    refs, error = _relation_refs(args)
    if error:
        return error
    parent = await lookup.issue(ref)
    if parent is None:
        return "parent not found"
    error, root = await _check_parent(parent, lookup, policy)
    if error or not refs:
        return error
    return await _relations_error(refs, root, lookup, policy)


async def _update(args: dict, lookup: Lookup, policy: Policy) -> str | None:
    target = await lookup.issue(args["id"])
    if target is None:
        return "target issue not found"
    owner_issue = target.creator_id != policy.app_user_id
    if owner_issue:
        extra = set(args) - OWNER_ISSUE_KEYS
        if extra:
            return f"owner issue: {sorted(extra)[0]} not allowed"
    for key in FORBIDDEN_UPDATE_KEYS:
        if key in args:
            return f"{key} not allowed"
    error = _label_error(args, target, owner_issue)
    if error:
        return error
    refs, error = _relation_refs(args)
    if error:
        return error
    root = None
    if "parentId" in args:
        ref = args["parentId"]
        if not isinstance(ref, str) or not ref:
            return "detaching parent not allowed"
        parent = await lookup.issue(ref)
        if parent is None:
            return "parent not found"
        if parent.id == target.id:
            return "parent chain loops"
        if parent.id != target.parent_id:
            if target.child_count > 0:
                return "moving an issue with children not allowed"
            error, root = await _check_parent(parent, lookup, policy, moving=target.id)
            if error:
                return error
    if not refs:
        return None
    if root is None:
        root, _, error = await _walk(target, lookup, policy)
        if error:
            return error
        if root.creator_id != policy.owner_id:
            return "root issue is not owner-created"
    return await _relations_error(refs, root, lookup, policy)


async def _save_issue(args: dict, lookup: Lookup, policy: Policy) -> str | None:
    for key in ASSIGN_KEYS:
        if key in args:
            return f"{key} not allowed"
    if "id" in args and (not isinstance(args["id"], str) or not args["id"]):
        return "invalid id"
    memo = _MemoLookup(lookup)
    try:
        return await (_update if "id" in args else _create)(args, memo, policy)
    finally:
        memo.close()


async def decide(tool: str, args: dict, lookup: Lookup, policy: Policy, timeout: float = LOOKUP_TIMEOUT) -> str | None:
    if tool in READ_TOOLS or tool == "save_comment":
        return None
    if tool == "prepare_file_upload":
        return _upload_error(args) if args else None
    if tool != "save_issue":
        return f"tool {tool} not allowed"
    # LiteLLM's tools/list scan sends no arguments; Linear rejects an argumentless save_issue.
    if not args:
        return None
    try:
        return await asyncio.wait_for(_save_issue(args, lookup, policy), timeout)
    except Exception:
        logger.warning("linear-app guard: lookup failed", exc_info=True)
        return "lookup failed"


try:
    import httpx
    from fastapi import HTTPException
    from litellm.integrations.custom_guardrail import CustomGuardrail
except ImportError:
    CustomGuardrail = None

GRAPHQL_URL = "https://api.linear.app/graphql"
GRAPHQL_TIMEOUT = 4.0
ISSUE_QUERY = (
    "query($id:String!){ issue(id:$id){ id identifier parent{ id } creator{ id } "
    "labels{ nodes{ name } } children(first:50){ nodes{ id } } } }"
)
SERVER_NAME = "linear_app"


class GraphQLLookup:
    def __init__(self, token: str, client: "httpx.AsyncClient"):
        self._token = token
        self._client = client

    async def issue(self, ref: str) -> Issue | None:
        response = await self._client.post(
            GRAPHQL_URL,
            json={"query": ISSUE_QUERY, "variables": {"id": ref}},
            headers={"Authorization": self._token},
            timeout=GRAPHQL_TIMEOUT,
        )
        response.raise_for_status()
        body = response.json()
        if body.get("errors"):
            raise RuntimeError(f"linear graphql errors: {body['errors']}")
        node = body["data"]["issue"]
        if node is None:
            return None
        return Issue(
            id=node["id"],
            identifier=node["identifier"],
            parent_id=(node["parent"] or {}).get("id"),
            creator_id=(node["creator"] or {}).get("id"),
            labels=tuple(label["name"] for label in node["labels"]["nodes"]),
            child_count=len(node["children"]["nodes"]),
        )


if CustomGuardrail is not None:

    class LinearAppGuard(CustomGuardrail):
        def __init__(self, lookup: Lookup | None = None, policy: Policy | None = None, **kwargs):
            super().__init__(**kwargs)
            self._lookup = lookup
            self._policy = policy

        def _resolve(self) -> tuple[Lookup, Policy]:
            if self._policy is None:
                self._policy = Policy(os.environ["LINEAR_OWNER_ID"], os.environ["LINEAR_APP_USER_ID"])
            if self._lookup is None:
                self._lookup = GraphQLLookup(os.environ["LINEAR_API_KEY"], httpx.AsyncClient())
            return self._lookup, self._policy

        async def apply_guardrail(self, inputs, request_data: dict, input_type, logging_obj=None):
            server = request_data.get("mcp_server_name")
            if not server:
                raise HTTPException(status_code=403, detail="linear-app guard: server unknown")
            if server != SERVER_NAME:
                return inputs
            try:
                lookup, policy = self._resolve()
            except Exception:
                logger.warning("linear-app guard: not configured", exc_info=True)
                raise HTTPException(status_code=403, detail="linear-app guard: not configured")
            reason = await decide(request_data.get("mcp_tool_name") or "", request_data.get("mcp_arguments") or {}, lookup, policy)
            if reason:
                raise HTTPException(status_code=403, detail=f"linear-app guard: {reason}")
            return inputs
