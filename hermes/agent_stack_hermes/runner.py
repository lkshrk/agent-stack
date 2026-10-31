import contextlib
import json
import os
import time
from collections.abc import Mapping
from dataclasses import dataclass, field

from agent_stack_hermes.kanban_port import CardView, HermesKanban, KanbanPort
from agent_stack_hermes.omnigent import MachineCredentials, OmnigentClient, TokenSource
from agent_stack_hermes.postrun import GitHubReader, agent_actors, violations
from agent_stack_hermes.runspec import (
    MAX_RUNTIME,
    NO_RESULT_BLOCK,
    Outcome,
    RunSpec,
    evaluate,
    parse_card_body,
    session_request,
)

SLICE_SECONDS = 300
HEARTBEAT_SECONDS = 900
STALL_SECONDS = 2700
POLL_SECONDS = 15
SESSION_NOTE = "omnigent-session: "
REFS_NOTE = "refs-before: "
CARD_LABEL = "agent-stack/card"
BLOCK_KIND = {"decision": "needs_input", "permission": "needs_input", "dependency": "dependency", "environment": "transient"}
DENIED_TOOLS = frozenset(
    {
        "kanban_complete",
        "kanban_block",
        "kanban_schedule",
        "kanban_request_review",
        "kanban_comment",
        "kanban_create",
        "kanban_unblock",
        "kanban_link",
    }
)
REQUIRED_ENV = ("OMNIGENT_URL", "OMNIGENT_TOKEN_URL", "OMNIGENT_CLIENT_ID", "OMNIGENT_CLIENT_SECRET", "GITHUB_RO_TOKEN_FILE")
TOOL_SCHEMA = {
    "name": "omnigent_run",
    "description": (
        "Run or resume this card's Omnigent session for one bounded slice. Takes no arguments: "
        "the run spec comes from the card. Call again while the result has call_again: true."
    ),
    "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
}
MAX_REASON_CHARS = 300
ACTIVITY_SLACK_SECONDS = 60


@dataclass(frozen=True)
class _Verdict:
    state: str
    reason: str
    block_class: str = "environment"
    metadata: dict = field(default_factory=dict)


_RUNNING = _Verdict("running", "session still running")
_STALE = _Verdict("stale", "stale run: the card is no longer held by this run")


def _result(state: str, session_id: str | None, reason: str, call_again: bool = False) -> dict:
    return {"state": state, "call_again": call_again, "session_id": session_id, "reason": reason}


def _blocked(block_class: str, reason: str) -> _Verdict:
    return _Verdict("blocked", reason, block_class)


def _short(exc: BaseException) -> str:
    return f"{type(exc).__name__}: {exc}"[:MAX_REASON_CHARS]


def _kanban_ids(env: Mapping[str, str]) -> tuple[str, int] | None:
    task_id = env.get("HERMES_KANBAN_TASK")
    try:
        run_id = int(env.get("HERMES_KANBAN_RUN_ID", ""))
    except ValueError:
        return None
    return (task_id, run_id) if task_id else None


class _BlockReason(Exception):
    pass


def _notes(comments: tuple[str, ...], prefix: str) -> list[str]:
    return [c[len(prefix):].strip() for c in comments if c.startswith(prefix) and c[len(prefix):].strip()]


def _last_note(comments: tuple[str, ...], prefix: str) -> str | None:
    notes = _notes(comments, prefix)
    return notes[-1] if notes else None


def _first_note(comments: tuple[str, ...], prefix: str) -> str | None:
    notes = _notes(comments, prefix)
    return notes[0] if notes else None


def _refs_before(comments: tuple[str, ...]) -> tuple[float, dict[str, str]]:
    raw = _first_note(comments, REFS_NOTE)
    if raw is None:
        raise ValueError("no refs-before note on the card")
    note = json.loads(raw)
    refs = note.get("refs") if isinstance(note, dict) else None
    if not isinstance(refs, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in refs.items()):
        raise ValueError("unparseable refs-before note")
    return _epoch(note, "at"), refs


def _epoch(snap: dict, key: str) -> float:
    value = snap.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"snapshot {key} is not a number")
    return float(value)


def _empty(value) -> bool:
    return value is None or value == [] or value == 0


def _child_active(child: dict) -> bool:
    settled = child.get("busy") is False and _empty(child.get("pending_elicitations_count"))
    return not settled or child.get("current_task_status") not in ("completed", "failed")


def _quiet(snap: dict) -> bool:
    labels = snap.get("labels")
    labels = labels if isinstance(labels, dict) else {}
    return (
        snap.get("status") == "idle"
        and _empty(snap.get("pending_inputs"))
        and _empty(snap.get("pending_elicitations"))
        and _empty(snap.get("background_task_count"))
        and _empty(snap.get("background_tasks"))
        and snap.get("active_response_id") is None
        and labels.get("omnigent.startup") != "pending"
        and snap.get("sandbox_status") is None
    )


def _recover(omni, task_id: str) -> str | None:
    matches: set[str] = set()
    for visibility in ("mine", "archived"):
        for s in omni.list_sessions(visibility=visibility):
            if isinstance(s.get("labels"), dict) and s["labels"].get(CARD_LABEL) == task_id:
                session_id = s.get("id")
                if not isinstance(session_id, str) or not session_id:
                    raise ValueError("listed session has no id")
                matches.add(session_id)
    if len(matches) > 1:
        raise _BlockReason("multiple sessions for this card")
    return matches.pop() if matches else None


def _start(env, kanban: KanbanPort, omni, gh, task_id: str, spec: RunSpec, link, comments, now) -> tuple[str, tuple]:
    notes: tuple[str, ...] = ()
    session_id = None
    if _first_note(comments, REFS_NOTE) is None:
        baseline = {"at": now(), "refs": gh.refs(spec.repo)}
        refs_note = REFS_NOTE + json.dumps(baseline, sort_keys=True, separators=(",", ":"))
        kanban.note(task_id, refs_note)
        notes = (refs_note,)
    else:
        session_id = _recover(omni, task_id)
    created = session_id is None
    if created:
        session_id = omni.create_session(session_request(spec, task_id, link))
    session_note = SESSION_NOTE + session_id
    try:
        kanban.note(task_id, session_note)
    except Exception:
        if created:
            with contextlib.suppress(Exception):
                omni.interrupt(session_id)
        raise
    share_with = env.get("OMNIGENT_SHARE_WITH")
    if share_with:
        with contextlib.suppress(Exception):
            omni.share(session_id, share_with, 1)
    return session_id, notes + (session_note,)


class _Heartbeat:
    def __init__(self, kanban: KanbanPort, task_id: str, run_id: int, session_id: str, now):
        self._kanban, self._task_id, self._run_id, self._now = kanban, task_id, run_id, now
        self._note = f"following Omnigent session {session_id}"
        self._last: float | None = None

    def beat(self) -> bool:
        t = self._now()
        if self._last is not None and t - self._last < HEARTBEAT_SECONDS:
            return True
        self._last = t
        return self._kanban.heartbeat(self._task_id, self._run_id, self._note)


def _follow(omni, session_id: str, heartbeat: _Heartbeat, now, deadline: float) -> bool:
    events = iter(omni.stream(session_id, read_timeout=max(1.0, deadline - now())))
    try:
        while True:
            try:
                event = next(events)
            except Exception:
                break
            if now() >= deadline:
                break
            if not heartbeat.beat():
                return False
            if event.data is None:
                break
            if event.name == "session.status" and _event_status(event.data) in ("idle", "failed"):
                break
    finally:
        close = getattr(events, "close", None)
        if close:
            with contextlib.suppress(Exception):
                close()
    return True


def _event_status(data: dict):
    nested = data.get("data")
    return data.get("status") or (nested.get("status") if isinstance(nested, dict) else None)


def _pr_url(result: dict, omni, session_id: str) -> str | None:
    pr = result.get("pr")
    if isinstance(pr, str) and pr:
        return pr
    entries = omni.pull_requests(session_id)
    if not entries:
        return None
    url = entries[0].get("url") if isinstance(entries[0], dict) else None
    if not isinstance(url, str) or not url:
        raise ValueError("pull-requests entry has no url")
    return url


def _judge(spec: RunSpec, session_id: str, outcome: Outcome, comments: tuple[str, ...], omni, gh) -> _Verdict:
    result = outcome.result if isinstance(outcome.result, dict) else {}
    pr_url = _pr_url(result, omni, session_id)
    at, before = _refs_before(comments)
    pull = gh.pull(pr_url) if pr_url else None
    files = gh.changed_files(spec.repo, pull.base_ref, pull.head_ref) if pull and spec.bundle == "documenter" else []
    if pull:
        branches = () if spec.ref == pull.head_ref else (spec.ref,)
    else:
        branches = (spec.ref, gh.default_branch(spec.repo))
    since = at - ACTIVITY_SLACK_SECONDS
    found = violations(spec.bundle, spec.repo, before, gh.refs(spec.repo), pull, files,
                       lambda ref: gh.activity(spec.repo, ref, since), lambda tag: gh.release_author(spec.repo, tag),
                       gh.agents, branches)
    if found:
        return _blocked("permission", "post-run check: " + "; ".join(found))
    if not outcome.done:
        return _blocked(outcome.block_class or "environment", outcome.reason)
    metadata = {
        "omnigent_session_id": session_id,
        "bundle": spec.bundle,
        "pr_url": pr_url,
        "result": outcome.result,
        "gaps": result.get("gaps") or [],
        "doc_gaps": result.get("doc_gaps") or [],
        "post_run": "ok",
    }
    summary = f"{spec.bundle} {outcome.reason}" + (f": {pr_url}" if pr_url else "")
    return _Verdict("completed", summary, metadata=metadata)


def _sandbox_failure(snap: dict) -> str | None:
    sandbox = snap.get("sandbox_status")
    if isinstance(sandbox, dict) and sandbox.get("stage") == "failed":
        return str(sandbox.get("error") or "failed")[:MAX_REASON_CHARS]
    return None


def _reconcile(spec: RunSpec, session_id: str, comments, omni, gh, now) -> _Verdict:
    snap = omni.snapshot(session_id)
    sandbox_error = _sandbox_failure(snap)
    t = now()
    if sandbox_error is not None:
        reason = f"sandbox failed: {sandbox_error}"
    elif snap.get("status") == "failed":
        return _judge(spec, session_id, evaluate(spec.bundle, snap), comments, omni, gh)
    else:
        children, missing = None, False
        if _quiet(snap):
            children = omni.child_sessions(session_id)
            if not any(_child_active(c) for c in children):
                outcome = evaluate(spec.bundle, snap)
                # A planner between critic rounds idles with only a status line.
                missing = outcome.result is None and outcome.reason == NO_RESULT_BLOCK
                if not missing:
                    return _judge(spec, session_id, outcome, comments, omni, gh)
        created = _epoch(snap, "created_at")
        updated = created if snap.get("updated_at") is None else _epoch(snap, "updated_at")
        if t - updated > STALL_SECONDS:
            # A parent waiting on a sub-agent gets no items, so its updated_at stays put.
            children = omni.child_sessions(session_id) if children is None else children
            updated = max([updated, *(_epoch(c, "updated_at") for c in children)])
        if t - updated > STALL_SECONDS:
            reason = NO_RESULT_BLOCK if missing else f"stalled: no session or child session update for {int(t - updated)} s"
        elif t - created > MAX_RUNTIME[spec.bundle]:
            reason = f"runtime cap: {int(t - created)} s exceeds {MAX_RUNTIME[spec.bundle]} s"
        else:
            return _RUNNING
    try:
        omni.interrupt(session_id)
    except Exception as exc:
        reason += f" (interrupt failed: {_short(exc)})"
    return _blocked("environment", reason)


def _close(kanban: KanbanPort, task_id: str, run_id: int, session_id: str | None, verdict: _Verdict) -> dict:
    if verdict is _RUNNING:
        return _result("running", session_id, verdict.reason, call_again=True)
    if verdict is _STALE:
        return _result("running", session_id, verdict.reason)
    if verdict.state == "completed":
        written = kanban.complete(task_id, run_id, verdict.reason, verdict.metadata)
    else:
        block_class = verdict.block_class if verdict.block_class in BLOCK_KIND else "environment"
        written = kanban.block(task_id, run_id, BLOCK_KIND[block_class], f"BLOCKED{{{block_class}}}: {verdict.reason}")
    if not written:
        return _result("running", session_id, _STALE.reason)
    return _result(verdict.state, session_id, verdict.reason)


def run_slice(
    env: Mapping[str, str],
    kanban: KanbanPort,
    omni: OmnigentClient,
    gh: GitHubReader,
    now=time.time,
    slice_seconds: int = SLICE_SECONDS,
    sleep=time.sleep,
) -> dict:
    ids = _kanban_ids(env)
    if ids is None:
        return _result("blocked", None, "not a Kanban worker: HERMES_KANBAN_TASK/HERMES_KANBAN_RUN_ID missing")
    task_id, run_id = ids
    card: CardView = kanban.card(task_id)
    try:
        link, spec = parse_card_body(card.body)
    except ValueError:
        link, spec = None, None
    if spec is None:
        return _close(kanban, task_id, run_id, None, _blocked("decision", "card has no valid run spec"))
    comments = card.comments
    session_id = _last_note(comments, SESSION_NOTE)
    deadline = now() + slice_seconds
    try:
        if session_id is None:
            session_id, notes = _start(env, kanban, omni, gh, task_id, spec, link, comments, now)
            comments = comments + notes
        heartbeat = _Heartbeat(kanban, task_id, run_id, session_id, now)
        if not heartbeat.beat() or not _follow(omni, session_id, heartbeat, now, deadline):
            return _close(kanban, task_id, run_id, session_id, _STALE)
        while True:
            verdict = _reconcile(spec, session_id, comments, omni, gh, now)
            if verdict is not _RUNNING or now() >= deadline:
                break
            sleep(max(0.0, min(POLL_SECONDS, deadline - now())))
            if not heartbeat.beat():
                return _close(kanban, task_id, run_id, session_id, _STALE)
    except Exception as exc:
        verdict = _blocked("environment", str(exc) if isinstance(exc, _BlockReason) else _short(exc))
    return _close(kanban, task_id, run_id, session_id, verdict)


def deny_kanban_terminal(tool_name: str = "", args: dict | None = None, **kw) -> dict | None:
    if tool_name in DENIED_TOOLS:
        return {
            "action": "block",
            "message": f"{tool_name} is not available in the omnigent-runner lane: omnigent_run closes the card.",
        }
    return None


def handle(args: dict | None = None, **kw) -> str:
    env = os.environ
    try:
        missing = [name for name in REQUIRED_ENV if not env.get(name)]
        if missing:
            ids = _kanban_ids(env)
            if ids:
                HermesKanban(claimer=env.get("HERMES_KANBAN_CLAIM_LOCK")).block(
                    *ids, "transient", "BLOCKED{environment}: runner not configured"
                )
            return json.dumps(_result("blocked", None, "runner not configured: missing " + ", ".join(missing)))
        creds = MachineCredentials(env["OMNIGENT_TOKEN_URL"], env["OMNIGENT_CLIENT_ID"], env["OMNIGENT_CLIENT_SECRET"])
        omni = OmnigentClient(env["OMNIGENT_URL"], TokenSource(creds))
        gh = GitHubReader(env["GITHUB_RO_TOKEN_FILE"], agents=agent_actors(env.get("GITHUB_AGENT_ACTORS")))
        kanban = HermesKanban(claimer=env.get("HERMES_KANBAN_CLAIM_LOCK"))
        return json.dumps(run_slice(env, kanban, omni, gh))
    except Exception as exc:
        return json.dumps(_result("running", None, "runner error: " + _short(exc)))
