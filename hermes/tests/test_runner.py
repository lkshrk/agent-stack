import copy
import http.client
import json
import unittest
from pathlib import Path

from agent_stack_hermes import runner
from agent_stack_hermes.kanban_port import CardView
from agent_stack_hermes.omnigent import OmnigentError, StreamEvent
from agent_stack_hermes.postrun import GitHubError, PullHead
from agent_stack_hermes.runspec import LinearLink, RunSpec, render_card_body

DATA = Path(__file__).parent / "testdata" / "omnigent"
POLLY = json.loads((DATA / "snapshot_polly.json").read_text())
T0 = POLLY["updated_at"] + 60
REPO = "example-org/example-repo"
PR = "https://github.com/example-org/example-repo/pull/42"
BRANCH = "agent/example-1-add-widget"
BEFORE = {"refs/heads/main": "a" * 40}
AFTER = {"refs/heads/main": "a" * 40, f"refs/heads/{BRANCH}": "b" * 40}
ENV = {"HERMES_KANBAN_TASK": "t_1", "HERMES_KANBAN_RUN_ID": "7"}
DONE_EVENT = StreamEvent("message", None)
RECORDED = (runner.REFS_NOTE + json.dumps(BEFORE), runner.SESSION_NOTE + "conv_old")


def card_body(bundle="polly"):
    return render_card_body(LinearLink("iss", "EX-1", "lsess", "root"), RunSpec(bundle, REPO, "main", "do it"), "text")


def snapshot(result=None, **over):
    snap = copy.deepcopy(POLLY)
    if result is not None:
        snap["items"][-1]["data"]["content"][0]["text"] = "```json\n" + json.dumps(result) + "\n```"
    snap.update(over)
    return snap


def running(**over):
    return snapshot(**{"status": "running", "active_response_id": "resp_1", "updated_at": T0, **over})


class Clock:
    def __init__(self, t=T0):
        self.t = t

    def __call__(self):
        return self.t


class FakeKanban:
    def __init__(self, log, body, comments=(), ok=True):
        self.log, self.body, self.comments, self.ok = log, body, list(comments), ok

    def card(self, task_id):
        return CardView(task_id, "running", self.body, 7, tuple(self.comments))

    def note(self, task_id, body):
        self.comments.append(body)
        self.log.append(("note", body))

    def heartbeat(self, task_id, run_id, note):
        self.log.append(("heartbeat", run_id))
        return True

    def complete(self, task_id, run_id, summary, metadata):
        self.log.append(("complete", run_id, summary, metadata))
        return self.ok

    def block(self, task_id, run_id, kind, reason):
        self.log.append(("block", run_id, kind, reason))
        return self.ok


class FakeOmnigent:
    def __init__(self, log, clock, snap, events=(DONE_EVENT,), step=0, prs=(), fail=None):
        self.log, self.clock, self.snap, self.events, self.step = log, clock, snap, events, step
        self.prs, self.fail = list(prs), fail or {}

    def _call(self, name, *args):
        self.log.append((name, *args))
        if name in self.fail:
            raise self.fail[name]

    def create_session(self, body):
        self._call("create", body)
        return "conv_new"

    def share(self, session_id, user_id, level=1):
        self._call("share", session_id, user_id, level)

    def stream(self, session_id, read_timeout):
        self._call("stream", session_id)
        for event in self.events:
            self.clock.t += self.step
            if isinstance(event, Exception):
                raise event
            yield event

    def snapshot(self, session_id):
        self._call("snapshot", session_id)
        return copy.deepcopy(self.snap)

    def pull_requests(self, session_id):
        self._call("pull_requests", session_id)
        return self.prs

    def interrupt(self, session_id):
        self._call("interrupt", session_id)


class FakeGitHub:
    def __init__(self, log, refs=(BEFORE, AFTER), files=(), fail=None):
        self.log, self.refs_seq, self.files, self.fail = log, list(refs), list(files), fail or {}

    def _call(self, name, *args):
        self.log.append((name, *args))
        if name in self.fail:
            raise self.fail[name]

    def refs(self, repo):
        self._call("refs", repo)
        return dict(self.refs_seq.pop(0))

    def pull(self, pr_url):
        self._call("pull", pr_url)
        return PullHead(BRANCH, REPO, "main", REPO, "main")

    def changed_files(self, repo, base, head):
        self._call("changed_files", repo, base, head)
        return self.files


class RunnerTest(unittest.TestCase):
    def setUp(self):
        self.log = []
        self.clock = Clock()

    def run_slice(self, snap, comments=RECORDED, bundle="polly", body=None, ok=True, env=ENV, refs=None,
                  gh_fail=None, files=(), slice_seconds=runner.SLICE_SECONDS, **omni):
        self.kanban = FakeKanban(self.log, card_body(bundle) if body is None else body, comments, ok)
        self.omni = FakeOmnigent(self.log, self.clock, snap, **omni)
        if refs is None:
            refs = (AFTER,) if comments else (BEFORE, AFTER)
        self.gh = FakeGitHub(self.log, refs, files, gh_fail)
        return runner.run_slice(env, self.kanban, self.omni, self.gh, now=self.clock, slice_seconds=slice_seconds)

    def calls(self, name):
        return [c for c in self.log if c[0] == name]

    def terminal_writes(self):
        return self.calls("complete") + self.calls("block")

    def assert_blocked(self, out, kind, reason_prefix):
        self.assertEqual(out["state"], "blocked")
        self.assertFalse(out["call_again"])
        [block] = self.calls("block")
        self.assertEqual(block[1], 7)
        self.assertEqual(block[2], kind)
        self.assertTrue(block[3].startswith(reason_prefix), block[3])
        self.assertFalse(self.calls("complete"))

    def test_first_slice_records_refs_then_session(self):
        out = self.run_slice(running(), comments=())
        names = [c[0] if c[0] != "note" else c[1].split(":")[0] for c in self.log]
        self.assertLess(names.index("refs-before"), names.index("create"))
        self.assertLess(names.index("create"), names.index("omnigent-session"))
        self.assertLess(names.index("omnigent-session"), names.index("stream"))
        notes = [c[1] for c in self.calls("note")]
        self.assertEqual(notes, [runner.REFS_NOTE + json.dumps(BEFORE, sort_keys=True, separators=(",", ":")),
                                 runner.SESSION_NOTE + "conv_new"])
        [create] = self.calls("create")
        self.assertEqual(create[1]["labels"]["agent-stack/card"], "t_1")
        self.assertEqual(out["session_id"], "conv_new")
        self.assertFalse(self.calls("share"))

    def test_share_follows_session_note_and_failure_is_ignored(self):
        env = {**ENV, "OMNIGENT_SHARE_WITH": "owner@example.test"}
        out = self.run_slice(running(), comments=(), env=env, fail={"share": OmnigentError(403)})
        names = [c[0] if c[0] != "note" else c[1].split(":")[0] for c in self.log]
        self.assertEqual(names.index("share"), names.index("omnigent-session") + 1)
        self.assertEqual(self.calls("share")[0][1:], ("conv_new", "owner@example.test", 1))
        self.assertTrue(out["call_again"])
        self.assertFalse(self.terminal_writes())

    def test_retry_resumes_recorded_session(self):
        out = self.run_slice(running())
        self.assertFalse(self.calls("create"))
        self.assertFalse(self.calls("note"))
        self.assertEqual(self.calls("stream")[0][1], "conv_old")
        self.assertEqual(out["session_id"], "conv_old")

    def test_running_returns_call_again(self):
        out = self.run_slice(running())
        self.assertEqual(out["state"], "running")
        self.assertTrue(out["call_again"])
        self.assertFalse(self.terminal_writes())

    def test_startup_pending_idle_is_not_terminal(self):
        labels = {**POLLY["labels"], "omnigent.startup": "pending"}
        out = self.run_slice(snapshot(updated_at=T0, labels=labels, sandbox_status={"state": "launching"}))
        self.assertTrue(out["call_again"])
        self.assertFalse(self.terminal_writes())

    def test_stream_end_reconciles_from_snapshot(self):
        out = self.run_slice(running(), events=(DONE_EVENT, StreamEvent("session.status", {"status": "idle"})))
        self.assertTrue(out["call_again"])
        self.assertEqual(len(self.calls("snapshot")), 1)
        self.assertFalse(self.terminal_writes())

    def test_stream_error_reconciles_from_snapshot(self):
        out = self.run_slice(running(), events=(OmnigentError(0, "reset"),))
        self.assertTrue(out["call_again"])
        self.assertEqual(len(self.calls("snapshot")), 1)
        self.assertFalse(self.terminal_writes())

    def test_heartbeat_every_15_minutes(self):
        events = [StreamEvent("session.heartbeat", {"server_time": None})] * 60
        snap = running(updated_at=T0 + 2400)
        out = self.run_slice(snap, events=events, step=60, slice_seconds=2400)
        self.assertEqual(len(self.calls("heartbeat")), 3)
        self.assertTrue(out["call_again"])

    def test_stall_interrupts_and_blocks_transient(self):
        out = self.run_slice(running(updated_at=T0 - runner.STALL_SECONDS - 1))
        self.assertEqual(self.calls("interrupt"), [("interrupt", "conv_old")])
        self.assert_blocked(out, "transient", "BLOCKED{environment}: stalled")

    def test_runtime_cap_blocks(self):
        out = self.run_slice(running(created_at=T0 - 7201), bundle="planner")
        self.assertEqual(self.calls("interrupt"), [("interrupt", "conv_old")])
        self.assert_blocked(out, "transient", "BLOCKED{environment}: runtime cap")

    def test_done_completes_with_evidence(self):
        out = self.run_slice(snapshot())
        [complete] = self.calls("complete")
        _, run_id, summary, metadata = complete
        self.assertEqual(run_id, 7)
        self.assertTrue(summary)
        self.assertEqual(set(metadata), {"omnigent_session_id", "bundle", "pr_url", "result", "gaps", "doc_gaps", "post_run"})
        self.assertEqual(metadata["omnigent_session_id"], "conv_old")
        self.assertEqual(metadata["bundle"], "polly")
        self.assertEqual(metadata["pr_url"], PR)
        self.assertEqual(metadata["result"]["status"], "DONE")
        self.assertEqual((metadata["gaps"], metadata["doc_gaps"], metadata["post_run"]), ([], [], "ok"))
        self.assertEqual(self.calls("pull"), [("pull", PR)])
        self.assertFalse(self.calls("pull_requests"))
        self.assertFalse(self.calls("block"))
        self.assertEqual((out["state"], out["call_again"]), ("completed", False))

    def test_pr_falls_back_to_pull_requests_listing(self):
        result = {"status": "BLOCKED", "pr": None, "branch": None, "gaps": [], "assumptions": [],
                  "blocked": {"class": "dependency", "question": "Waiting on EX-2."}}
        out = self.run_slice(snapshot(result), prs=[{"url": PR}])
        self.assertEqual(self.calls("pull"), [("pull", PR)])
        self.assert_blocked(out, "dependency", "BLOCKED{dependency}: Waiting on EX-2.")

    def test_blocked_decision_maps_needs_input(self):
        result = {"status": "BLOCKED", "pr": None, "branch": None, "gaps": [], "assumptions": [],
                  "blocked": {"class": "decision", "question": "Which API?"}}
        out = self.run_slice(snapshot(result), refs=(BEFORE,))
        self.assertEqual(self.calls("pull_requests"), [("pull_requests", "conv_old")])
        self.assertFalse(self.calls("pull"))
        self.assert_blocked(out, "needs_input", "BLOCKED{decision}: Which API?")

    def test_post_run_violation_blocks_permission(self):
        moved = {**AFTER, "refs/heads/main": "c" * 40}
        out = self.run_slice(snapshot(), refs=(moved,))
        self.assertTrue(out["reason"].startswith("post-run check: "), out["reason"])
        self.assert_blocked(out, "needs_input", "BLOCKED{permission}: post-run check: ref moved: refs/heads/main")

    def test_documenter_checks_changed_files(self):
        result = {"status": "DONE", "mode": "delta", "changed_files": ["src/app.py"], "stale": [], "pr": PR}
        out = self.run_slice(snapshot(result), bundle="documenter", files=["src/app.py"])
        self.assertEqual(self.calls("changed_files"), [("changed_files", REPO, "main", BRANCH)])
        self.assert_blocked(out, "needs_input", "BLOCKED{permission}: post-run check: non-doc file: src/app.py")

    def test_github_error_blocks_environment(self):
        out = self.run_slice(snapshot(), gh_fail={"pull": GitHubError("HTTP 500", 500)})
        self.assert_blocked(out, "transient", "BLOCKED{environment}: ")
        self.assertIn("HTTP 500", self.calls("block")[0][3])

    def test_untyped_error_blocks_environment(self):
        out = self.run_slice(running(), fail={"snapshot": http.client.IncompleteRead(b"")})
        self.assert_blocked(out, "transient", "BLOCKED{environment}: ")

    def test_create_failure_blocks_environment(self):
        out = self.run_slice(running(), comments=(), fail={"create": OmnigentError(500, "boom")})
        self.assertEqual([c[1].split(":")[0] for c in self.calls("note")], ["refs-before"])
        self.assert_blocked(out, "transient", "BLOCKED{environment}: ")

    def test_unparseable_refs_note_blocks_environment(self):
        comments = (runner.REFS_NOTE + "{not json", runner.SESSION_NOTE + "conv_old")
        out = self.run_slice(snapshot(), comments=comments)
        self.assert_blocked(out, "transient", "BLOCKED{environment}: ")

    def test_stale_run_does_not_retry(self):
        out = self.run_slice(snapshot(), ok=False)
        self.assertEqual(len(self.calls("complete")), 1)
        self.assertFalse(self.calls("block"))
        self.assertFalse(out["call_again"])
        self.assertNotEqual(out["state"], "completed")

    def test_invalid_spec_blocks_decision(self):
        for body in ("plain text", "agent-stack-link: null\nagent-stack-run: {\"bundle\": \"nope\"}\n"):
            with self.subTest(body=body):
                self.log.clear()
                out = self.run_slice(running(), body=body)
                self.assert_blocked(out, "needs_input", "BLOCKED{decision}: card has no valid run spec")
                self.assertFalse(self.calls("stream") + self.calls("create") + self.calls("refs"))

    def test_missing_kanban_env_writes_nothing(self):
        out = self.run_slice(running(), env={})
        self.assertEqual((out["state"], out["call_again"], out["session_id"]), ("blocked", False, None))
        self.assertFalse(self.log)


if __name__ == "__main__":
    unittest.main()
