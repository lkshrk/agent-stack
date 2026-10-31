import hashlib
import json
import re
from dataclasses import asdict, dataclass

BUNDLES = ("planner", "security-review", "documenter", "polly")
RESULT_KIND = {"planner": "planner", "security-review": "security", "documenter": "documenter", "polly": "polly"}
MODELS = {
    "planner": "anthropic/claude-fable",
    "security-review": "openai/gpt-astra",
    "documenter": "anthropic-auto",
    "polly": "anthropic-auto",
}
LAUNCH_ARGS = {"security-review": ["--profile", "security-review"]}
MAX_RUNTIME = {"planner": 7200, "security-review": 3600, "documenter": 7200, "polly": 21600}
LINK_PREFIX = "agent-stack-link: "
RUN_PREFIX = "agent-stack-run: "
MAX_ERRORS_SHOWN = 5
NO_RESULT_BLOCK = "no result block"

_REPO = re.compile(r"[A-Za-z0-9-]+/[A-Za-z0-9._-]+")
_REF_BAD = re.compile(r"[\s#]")


@dataclass(frozen=True)
class LinearLink:
    issue_id: str
    identifier: str
    session_id: str
    root_issue_id: str


@dataclass(frozen=True)
class RunSpec:
    bundle: str
    repo: str
    ref: str
    input: str

    def __post_init__(self):
        if self.bundle not in BUNDLES:
            raise ValueError(f"unknown bundle {self.bundle!r}")
        if not isinstance(self.repo, str) or not _REPO.fullmatch(self.repo):
            raise ValueError(f"invalid repo {self.repo!r}")
        if not isinstance(self.ref, str) or not self.ref or _REF_BAD.search(self.ref):
            raise ValueError(f"invalid ref {self.ref!r}")
        if not isinstance(self.input, str):
            raise ValueError("input must be a string")


@dataclass(frozen=True)
class Outcome:
    done: bool
    block_class: str | None
    reason: str
    result: dict | None


def render_card_body(link: LinearLink | None, spec: RunSpec | None, text: str) -> str:
    def dump(value):
        return "null" if value is None else json.dumps(asdict(value), separators=(",", ":"))

    return f"{LINK_PREFIX}{dump(link)}\n{RUN_PREFIX}{dump(spec)}\n{text}"


def _load(line: str, prefix: str, cls):
    try:
        data = json.loads(line[len(prefix):])
        if data is None:
            return None
        if not isinstance(data, dict):
            raise ValueError("not an object")
        return cls(**data)
    except (ValueError, TypeError) as exc:
        raise ValueError(f"malformed {prefix.strip(': ')} line: {exc}") from exc


def parse_card_body(body: str) -> tuple[LinearLink | None, RunSpec | None]:
    lines = body.split("\n", 2)
    if len(lines) < 2 or not lines[0].startswith(LINK_PREFIX) or not lines[1].startswith(RUN_PREFIX):
        return None, None
    return _load(lines[0], LINK_PREFIX, LinearLink), _load(lines[1], RUN_PREFIX, RunSpec)


def builtin_agent_id(name: str) -> str:
    return hashlib.sha256(f"builtin:{name}".encode()).hexdigest()[:32]


def session_request(spec: RunSpec, card_id: str, link: LinearLink | None) -> dict:
    identifier = link.identifier if link else ""
    request = {
        "agent_id": builtin_agent_id(spec.bundle),
        "host_type": "managed",
        "workspace": f"https://github.com/{spec.repo}#{spec.ref}",
        "labels": {"agent-stack/card": card_id, "agent-stack/linear-issue": identifier},
        "title": f"{spec.bundle}: {identifier or card_id}",
        "initial_input": {
            "type": "message",
            "data": {"role": "user", "content": [{"type": "input_text", "text": spec.input}]},
        },
        "model_override": MODELS[spec.bundle],
    }
    if spec.bundle in LAUNCH_ARGS:
        request["terminal_launch_args"] = list(LAUNCH_ARGS[spec.bundle])
    return request


def _items(snapshot: dict) -> list:
    items = snapshot.get("items") if isinstance(snapshot, dict) else None
    if isinstance(items, dict):
        items = items.get("data")
    return items if isinstance(items, list) else []


def _assistant_texts(snapshot: dict) -> list[str]:
    texts = []
    for item in _items(snapshot):
        if not isinstance(item, dict) or item.get("type") != "message":
            continue
        body = item["data"] if isinstance(item.get("data"), dict) else item
        if body.get("role") != "assistant":
            continue
        content = body.get("content")
        if not isinstance(content, list):
            continue
        parts = [b["text"] for b in content if isinstance(b, dict) and isinstance(b.get("text"), str)]
        if parts:
            texts.append("\n".join(parts))
    return texts


def result_text(bundle: str, snapshot: dict) -> str | None:
    from agent_stack_policies.result import extract_result, validate_result

    texts = _assistant_texts(snapshot)
    if not texts:
        return None
    if bundle == "planner":
        kind = RESULT_KIND[bundle]
        for text in texts:
            result = extract_result(text)
            if result is not None and not validate_result(kind, result):
                return text
        for text in reversed(texts):
            if extract_result(text) is not None:
                return text
    return texts[-1]


def _block(reason: str, block_class: str = "environment", result: dict | None = None) -> Outcome:
    return Outcome(False, block_class, reason, result)


def evaluate(bundle: str, snapshot: dict) -> Outcome:
    from agent_stack_policies.result import extract_result, validate_result

    if bundle not in RESULT_KIND:
        return _block(f"unknown bundle {bundle!r}")
    if not isinstance(snapshot, dict):
        return _block("unreadable session snapshot")
    if snapshot.get("status") == "failed":
        error = snapshot.get("last_task_error")
        detail = error.get("message") if isinstance(error, dict) else None
        return _block(f"session failed: {detail}" if detail else "session failed")
    text = result_text(bundle, snapshot)
    result = extract_result(text) if text else None
    if result is None:
        return _block(NO_RESULT_BLOCK)
    errors = validate_result(RESULT_KIND[bundle], result)
    if errors:
        return _block("invalid result: " + "; ".join(errors[:MAX_ERRORS_SHOWN]), result=result)
    if result.get("status") == "BLOCKED":
        blocked = result["blocked"]
        return Outcome(False, blocked["class"], blocked["question"], result)
    return Outcome(True, None, str(result["status"]), result)
