import contextlib
import importlib.util
import io
import json
import re
import tempfile
import unittest
from pathlib import Path

from .fake_github import FakeGitHub

TEMPLATE = Path(__file__).resolve().parents[1] / "contribution-fork"
_spec = importlib.util.spec_from_file_location("contribution_gate", TEMPLATE / "publish" / "gate.py")
gate = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gate)

TOKEN = "ghp_faketokenvalue0123456789"
OWNER = "lkshrk"
FORK = "lkshrk/omnigent"
UPSTREAM = "omnigent-ai/omnigent"
REVIEWED = "a" * 40
LATER = "b" * 40
OTHER = "c" * 40
CONFIG = {
    "upstream": UPSTREAM,
    "fork": FORK,
    "owner": OWNER,
    "default_branch": "main",
    "title_format": "conventional-scoped",
    "ci_workflow": "contribution-ci.yml",
}


class Fork:
    """Fork and upstream state behind the fake API, defaulting to a publishable approved PR #7 and an approved issue #9."""

    def __init__(self, fake: FakeGitHub):
        self.pull = {
            "number": 7,
            "state": "open",
            "title": "fix(web): change a",
            "body": "Fixes the a value.\n\nSigned-off-by: Owner <owner@example.invalid>",
            "labels": [{"name": "upstream-ready"}],
            "head": {"sha": REVIEWED, "ref": "fix/web-a", "repo": {"full_name": FORK}},
            "base": {"ref": "main", "repo": {"full_name": FORK}},
        }
        self.review = {"id": 11, "state": "APPROVED", "user": {"login": OWNER}, "commit_id": REVIEWED}
        self.runs = [self.run(REVIEWED, "success")]
        self.compare = {"status": "ahead", "ahead_by": 1, "behind_by": 0, "files": [{"filename": "web/a.ts", "status": "modified"}]}
        self.refs: dict[str, str] = {}
        self.upstream_pulls: list[dict] = []
        self.issue = {
            "number": 9,
            "state": "open",
            "title": "bug(web): sidebar hides archived sessions",
            "body": "Steps to reproduce.",
            "labels": [{"name": "upstream-issue"}, {"name": "approved-upstream"}],
        }
        self.issue_events = [{"event": "labeled", "label": {"name": "approved-upstream"}, "actor": {"login": OWNER}}]
        self.issue_comments: list[dict] = []
        self.upstream_issues: list[dict] = []
        self.upstream_comments: dict[str, list[dict]] = {}
        self.failing: set[str] = set()
        self._install(fake)

    @staticmethod
    def run(sha, conclusion, status="completed"):
        return {"id": 1, "head_sha": sha, "status": status, "conclusion": conclusion, "event": "pull_request", "path": ".github/workflows/contribution-ci.yml"}

    def _install(self, fake: FakeGitHub) -> None:
        def guard(name, handler):
            def wrapped(match, query, body):
                if name in self.failing:
                    return 502, {"message": "Bad Gateway"}
                return handler(match, query, body)
            return wrapped

        def review(match, query, body):
            if match["pr"] != "7" or match["id"] != str(self.review["id"]):
                return 404, {"message": "Not Found"}
            return 200, self.review

        def pull(match, query, body):
            return (200, self.pull) if match["pr"] == "7" else (404, {"message": "Not Found"})

        def runs(match, query, body):
            sha = query.get("head_sha", [""])[0]
            selected = [r for r in self.runs if r["head_sha"] == sha]
            return 200, {"total_count": len(selected), "workflow_runs": selected}

        def compare(match, query, body):
            self.compared = match["spec"]
            return 200, self.compare

        def get_ref(match, query, body):
            sha = self.refs.get(match["branch"])
            return (200, {"ref": f"refs/heads/{match['branch']}", "object": {"sha": sha}}) if sha else (404, {"message": "Not Found"})

        def create_ref(match, query, body):
            self.refs[body["ref"].removeprefix("refs/heads/")] = body["sha"]
            return 201, {"ref": body["ref"], "object": {"sha": body["sha"]}}

        def update_ref(match, query, body):
            self.refs[match["branch"]] = body["sha"]
            return 200, {"ref": f"refs/heads/{match['branch']}", "object": {"sha": body["sha"]}}

        def list_upstream_pulls(match, query, body):
            head = query.get("head", [""])[0]
            state = query.get("state", ["open"])[0]
            return 200, [p for p in self.upstream_pulls if p["head_label"] == head and p["state"] == state]

        def create_upstream_pull(match, query, body):
            number = 100 + len(self.upstream_pulls)
            self.upstream_pulls.append({"number": number, "state": "open", "head_label": body["head"], "html_url": f"https://github.com/{UPSTREAM}/pull/{number}"})
            return 201, self.upstream_pulls[-1]

        def update_upstream_pull(match, query, body):
            found = [p for p in self.upstream_pulls if str(p["number"]) == match["n"]]
            return (200, found[0]) if found else (404, {"message": "Not Found"})

        def issue(match, query, body):
            return (200, self.issue) if match["n"] == "9" else (404, {"message": "Not Found"})

        def page(items):
            def handler(match, query, body):
                number = int(query.get("page", ["1"])[0])
                size = int(query.get("per_page", ["30"])[0])
                return 200, items()[(number - 1) * size : number * size]
            return handler

        def page_by_number(items_by_number):
            def handler(match, query, body):
                return page(lambda: items_by_number.get(match["n"], []))(match, query, body)
            return handler

        def _login(item):
            return (item.get("user") or {}).get("login")

        def create_upstream_issue(match, query, body):
            number = 500 + len(self.upstream_issues)
            self.upstream_issues.append(
                {"number": number, "state": "open", "user": {"login": OWNER}, **body, "html_url": f"https://github.com/{UPSTREAM}/issues/{number}"}
            )
            return 201, self.upstream_issues[-1]

        def list_upstream_issues(match, query, body):
            creator, state = query.get("creator", [None])[0], query.get("state", ["open"])[0]
            selected = [i for i in self.upstream_issues if (creator is None or _login(i) == creator) and (state == "all" or i["state"] == state)]
            return page(lambda: selected)(match, query, body)

        def create_comment(match, query, body):
            self.issue_comments.append({"user": {"login": OWNER}, "body": body["body"]})
            return 201, {"id": 1}

        def create_upstream_comment(match, query, body):
            comments = self.upstream_comments.setdefault(match["n"], [])
            url = f"https://github.com/{UPSTREAM}/issues/{match['n']}#issuecomment-{900 + len(comments)}"
            comments.append({"user": {"login": OWNER}, "body": body["body"], "html_url": url})
            return 201, comments[-1]

        fork, upstream = re.escape(FORK), re.escape(UPSTREAM)
        for method, pattern, name, handler in (
            ("GET", rf"/repos/{fork}/pulls/(?P<pr>\d+)/reviews/(?P<id>\d+)", "review", review),
            ("GET", rf"/repos/{fork}/pulls/(?P<pr>\d+)", "pull", pull),
            ("GET", rf"/repos/{fork}/actions/workflows/contribution-ci\.yml/runs", "runs", runs),
            ("GET", rf"/repos/{fork}/compare/(?P<spec>.+)", "compare", compare),
            ("GET", rf"/repos/{fork}/git/ref/heads/(?P<branch>.+)", "get_ref", get_ref),
            ("POST", rf"/repos/{fork}/git/refs", "create_ref", create_ref),
            ("PATCH", rf"/repos/{fork}/git/refs/heads/(?P<branch>.+)", "update_ref", update_ref),
            ("GET", rf"/repos/{upstream}/pulls", "list_upstream_pulls", list_upstream_pulls),
            ("POST", rf"/repos/{upstream}/pulls", "create_upstream_pull", create_upstream_pull),
            ("PATCH", rf"/repos/{upstream}/pulls/(?P<n>\d+)", "update_upstream_pull", update_upstream_pull),
            ("GET", rf"/repos/{fork}/issues/(?P<n>\d+)", "issue", issue),
            ("GET", rf"/repos/{fork}/issues/(?P<n>\d+)/events", "issue_events", page(lambda: self.issue_events)),
            ("GET", rf"/repos/{fork}/issues/(?P<n>\d+)/comments", "issue_comments", page(lambda: self.issue_comments)),
            ("POST", rf"/repos/{upstream}/issues", "create_upstream_issue", create_upstream_issue),
            ("GET", rf"/repos/{upstream}/issues", "list_upstream_issues", list_upstream_issues),
            ("POST", rf"/repos/{fork}/issues/(?P<n>\d+)/comments", "create_comment", create_comment),
            ("GET", rf"/repos/{upstream}/issues/(?P<n>\d+)/comments", "upstream_comments", page_by_number(self.upstream_comments)),
            ("POST", rf"/repos/{upstream}/issues/(?P<n>\d+)/comments", "create_upstream_comment", create_upstream_comment),
        ):
            fake.route(method, pattern, guard(name, handler))

    def review_event(self, **review):
        snapshot = {**self.pull, "head": {**self.pull["head"]}}
        return {
            "action": "submitted",
            "review": {"id": 11, "state": "approved", "user": {"login": OWNER}, "commit_id": REVIEWED, **review},
            "pull_request": snapshot,
            "repository": {"full_name": FORK, "default_branch": "main"},
            "sender": {"login": review.get("user", {"login": OWNER})["login"]},
        }

    def label_event(self, label="approved-upstream", sender=OWNER):
        return {
            "action": "labeled",
            "label": {"name": label},
            "issue": dict(self.issue),
            "repository": {"full_name": FORK, "default_branch": "main"},
            "sender": {"login": sender},
        }


class GateTest(unittest.TestCase):
    def setUp(self):
        self.fake = FakeGitHub(TOKEN)
        self.fake.__enter__()
        self.addCleanup(self.fake.__exit__, None, None, None)
        self.fork = Fork(self.fake)
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.directory = Path(directory.name)
        self.config = self.directory / "contribution.json"
        self.config.write_text(json.dumps(CONFIG))

    def gate(self, event_name, event, repository=FORK, token=TOKEN):
        path = self.directory / "event.json"
        path.write_text(json.dumps(event))
        environ = {
            "GITHUB_EVENT_NAME": event_name,
            "GITHUB_EVENT_PATH": str(path),
            "GITHUB_REPOSITORY": repository,
            "GITHUB_API_URL": self.fake.url,
        }
        if token is not None:
            environ["UPSTREAM_PUBLISH_TOKEN"] = token
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = gate.main(environ, self.config)
        output = out.getvalue() + err.getvalue()
        self.assertNotIn(TOKEN, output)
        return code, output

    def review(self, **review):
        return self.gate("pull_request_review", self.fork.review_event(**review))

    def assertRefused(self, result, reason=None):
        code, output = result
        self.assertNotEqual(code, 0, output)
        self.assertEqual(self.fake.writes(), [], output)
        if reason:
            self.assertIn(reason, output)

    def upstream_writes(self):
        return [w for w in self.fake.writes() if w[1].startswith(f"/repos/{UPSTREAM}/")]


class PublishPullRequestTest(GateTest):
    def test_owner_approval_publishes_the_reviewed_commit(self):
        code, output = self.review()
        self.assertEqual(code, 0, output)
        self.assertEqual(
            self.fake.writes(),
            [
                ("POST", f"/repos/{FORK}/git/refs", {"ref": "refs/heads/upstream/pr-7", "sha": REVIEWED}),
                (
                    "POST",
                    f"/repos/{UPSTREAM}/pulls",
                    {
                        "title": self.fork.pull["title"],
                        "body": self.fork.pull["body"],
                        "head": "lkshrk:upstream/pr-7",
                        "base": "main",
                        "maintainer_can_modify": False,
                    },
                ),
            ],
        )
        self.assertEqual(self.fork.compared, f"omnigent-ai:main...{REVIEWED}")
        self.assertIn(f"https://github.com/{UPSTREAM}/pull/100", output)

    def test_every_request_carries_the_token_and_the_api_version(self):
        self.review()
        self.assertTrue(self.fake.requests)
        self.assertTrue(all(r["auth"] == f"Bearer {TOKEN}" for r in self.fake.requests))

    def test_a_second_approval_updates_the_branch_and_the_existing_upstream_pull(self):
        self.assertEqual(self.review()[0], 0)
        self.fake.requests.clear()
        self.fork.pull["head"]["sha"] = LATER
        self.fork.pull["title"] = "fix(web): change a properly"
        self.fork.review = {**self.fork.review, "id": 12, "commit_id": LATER}
        self.fork.runs.append(Fork.run(LATER, "success"))
        code, output = self.review(id=12, commit_id=LATER)
        self.assertEqual(code, 0, output)
        self.assertEqual(
            self.fake.writes(),
            [
                ("PATCH", f"/repos/{UPSTREAM}/pulls/100", {"title": "fix(web): change a properly", "body": self.fork.pull["body"]}),
                ("PATCH", f"/repos/{FORK}/git/refs/heads/upstream/pr-7", {"sha": LATER, "force": True}),
            ],
        )
        self.assertEqual(len(self.fork.upstream_pulls), 1)

    def test_a_failed_upstream_edit_leaves_the_published_branch_unmoved(self):
        self.assertEqual(self.review()[0], 0)
        self.fake.requests.clear()
        self.fork.pull["head"]["sha"] = LATER
        self.fork.review = {**self.fork.review, "id": 12, "commit_id": LATER}
        self.fork.runs.append(Fork.run(LATER, "success"))
        self.fork.failing = {"update_upstream_pull"}
        code, output = self.review(id=12, commit_id=LATER)
        self.assertNotEqual(code, 0, output)
        self.assertEqual(self.fork.refs, {"upstream/pr-7": REVIEWED})
        self.assertEqual([w for w in self.fake.writes() if "/git/refs" in w[1]], [])

    def test_a_head_that_moved_after_review_publishes_the_reviewed_commit(self):
        self.fork.pull["head"]["sha"] = LATER
        self.fork.runs.append(Fork.run(LATER, "success"))
        code, output = self.review()
        self.assertEqual(code, 0, output)
        self.assertEqual(self.fork.refs, {"upstream/pr-7": REVIEWED})
        self.assertNotIn(LATER, json.dumps(self.fake.writes()))

    def test_refuses_reviews_by_anyone_but_the_owner(self):
        self.fork.review["user"] = {"login": "npa-bot"}
        self.assertRefused(self.review(user={"login": "npa-bot"}), "owner")

    def test_refuses_when_the_api_review_author_differs_from_the_event(self):
        self.fork.review["user"] = {"login": "npa-bot"}
        self.assertRefused(self.review(), "owner")

    def test_refuses_non_approving_reviews(self):
        for state in ("COMMENTED", "CHANGES_REQUESTED", "DISMISSED", "PENDING"):
            with self.subTest(state=state):
                self.fork.review["state"] = state
                self.assertRefused(self.review(state=state.lower()))
                self.assertRefused(self.review())

    def test_refuses_a_review_whose_commit_is_not_a_full_sha(self):
        self.fork.review["commit_id"] = "abc123"
        self.assertRefused(self.review(commit_id="abc123"))

    def test_refuses_when_the_event_and_api_disagree_on_the_reviewed_commit(self):
        self.fork.runs.append(Fork.run(OTHER, "success"))
        self.assertRefused(self.review(commit_id=OTHER))

    def test_refuses_without_the_ready_label(self):
        self.fork.pull["labels"] = [{"name": "upstream-issue"}]
        self.assertRefused(self.review(), "upstream-ready")

    def test_refuses_red_missing_or_unfinished_ci(self):
        cases = {
            "failure": [Fork.run(REVIEWED, "failure")],
            "cancelled": [Fork.run(REVIEWED, "cancelled")],
            "in progress": [Fork.run(REVIEWED, None, status="in_progress")],
            "missing": [],
            "only the later head": [Fork.run(LATER, "success")],
            "one red among green": [Fork.run(REVIEWED, "success"), Fork.run(REVIEWED, "failure")],
            "another workflow": [{**Fork.run(REVIEWED, "success"), "path": ".github/workflows/other.yml"}],
        }
        for label, runs in cases.items():
            with self.subTest(case=label):
                self.fork.runs = runs
                self.assertRefused(self.review(), "CI")

    def test_refuses_a_base_other_than_the_fork_default_branch(self):
        self.fork.pull["base"] = {"ref": "feat/staging", "repo": {"full_name": FORK}}
        self.assertRefused(self.review(), "base")

    def test_refuses_a_head_from_another_repository(self):
        self.fork.pull["head"]["repo"] = {"full_name": "someone/omnigent"}
        self.assertRefused(self.review())

    def test_refuses_a_head_branch_in_the_publish_namespace(self):
        self.fork.pull["head"]["ref"] = "upstream/pr-3"
        self.assertRefused(self.review())

    def test_refuses_a_closed_pull_request(self):
        self.fork.pull["state"] = "closed"
        self.assertRefused(self.review())

    def test_refuses_a_title_or_body_edited_after_the_review(self):
        event = self.fork.review_event()
        self.fork.pull["title"] = "fix(web): something else"
        self.assertRefused(self.gate("pull_request_review", event), "changed")
        self.fork.pull["title"] = event["pull_request"]["title"]
        self.fork.pull["body"] = "rewritten"
        self.assertRefused(self.gate("pull_request_review", event), "changed")

    def test_refuses_titles_outside_the_upstream_convention(self):
        for title in ("fix: no scope", "Fix the sidebar", "fix(web) missing colon", "fix(web):", ""):
            with self.subTest(title=title):
                self.fork.pull["title"] = title
                self.assertRefused(self.review(), "title")

    def test_refuses_a_commit_that_is_not_ahead_of_upstream(self):
        for compare in (
            {"status": "identical", "ahead_by": 0, "behind_by": 0, "files": []},
            {"status": "behind", "ahead_by": 0, "behind_by": 3, "files": []},
        ):
            with self.subTest(status=compare["status"]):
                self.fork.compare = compare
                self.assertRefused(self.review(), "ahead")

    def test_allows_a_commit_based_on_an_older_upstream_default(self):
        self.fork.compare = {"status": "diverged", "ahead_by": 2, "behind_by": 5, "files": [{"filename": "web/a.ts"}]}
        self.assertEqual(self.review()[0], 0)

    def test_refuses_changes_under_github_or_publish(self):
        for files in (
            [{"filename": ".github/workflows/publish-upstream.yml"}],
            [{"filename": ".github/CODEOWNERS"}],
            [{"filename": "publish/gate.py"}],
            [{"filename": "web/a.ts"}, {"filename": "publish/contribution.json"}],
            [{"filename": "web/moved.yml", "previous_filename": ".github/workflows/ci.yml", "status": "renamed"}],
        ):
            with self.subTest(files=files):
                self.fork.compare = {**self.fork.compare, "files": files}
                self.assertRefused(self.review(), "publish/")

    def test_refuses_a_diff_too_large_to_inspect(self):
        self.fork.compare = {**self.fork.compare, "files": [{"filename": f"web/{n}.ts"} for n in range(300)]}
        self.assertRefused(self.review())

    def test_refuses_more_than_one_open_upstream_pull_for_the_branch(self):
        for number in (100, 101):
            self.fork.upstream_pulls.append({"number": number, "state": "open", "head_label": "lkshrk:upstream/pr-7"})
        self.fork.refs["upstream/pr-7"] = OTHER
        self.assertRefused(self.review(), "more than one")
        self.assertEqual(self.fork.refs, {"upstream/pr-7": OTHER})

    def test_refuses_when_running_outside_the_registered_fork(self):
        self.assertRefused(self.gate("pull_request_review", self.fork.review_event(), repository="someone/omnigent"))

    def test_refuses_without_a_token_before_any_request(self):
        self.assertRefused(self.gate("pull_request_review", self.fork.review_event(), token=None))
        self.assertEqual(self.fake.requests, [])

    def test_fails_closed_on_api_errors_before_any_upstream_write(self):
        for name in ("review", "pull", "runs", "compare", "list_upstream_pulls", "get_ref"):
            with self.subTest(failing=name):
                self.fork.failing = {name}
                self.assertRefused(self.review())
        for name in ("create_ref", "create_upstream_pull"):
            with self.subTest(failing=name):
                self.fork.failing = {name}
                self.fork.refs.clear()
                self.fake.requests.clear()
                code, output = self.review()
                self.assertNotEqual(code, 0, output)
                self.assertNotIn(TOKEN, output)

    def test_refuses_unexpected_events(self):
        self.assertRefused(self.gate("push", {"ref": "refs/heads/main"}))
        self.assertRefused(self.gate("pull_request_review", {"review": {}}))


class PublishIssueTest(GateTest):
    def label(self, **kwargs):
        return self.gate("issues", self.fork.label_event(**kwargs))

    def test_owner_label_files_exactly_one_upstream_issue(self):
        code, output = self.label()
        self.assertEqual(code, 0, output)
        writes = self.fake.writes()
        self.assertEqual(writes[0], ("POST", f"/repos/{UPSTREAM}/issues", {"title": self.fork.issue["title"], "body": self.fork.issue["body"]}))
        self.assertEqual(len(self.upstream_writes()), 1)
        self.assertEqual(writes[1][:2], ("POST", f"/repos/{FORK}/issues/9/comments"))
        self.assertIn(f"https://github.com/{UPSTREAM}/issues/500", writes[1][2]["body"])
        self.assertIn(f"https://github.com/{UPSTREAM}/issues/500", output)

    def test_a_repeated_label_does_not_file_a_second_issue(self):
        self.assertEqual(self.label()[0], 0)
        self.fake.requests.clear()
        code, output = self.label()
        self.assertEqual(code, 0, output)
        self.assertEqual(self.fake.writes(), [])
        self.assertIn(f"https://github.com/{UPSTREAM}/issues/500", output)

    def test_a_marker_comment_by_someone_else_does_not_count(self):
        self.fork.issue_comments.append({"user": {"login": OWNER}, "body": "Looks good, filing it."})
        self.fork.issue_comments.append({"user": {"login": "npa-bot"}, "body": f"{gate.ISSUE_MARKER}\nhttps://github.com/{UPSTREAM}/issues/1"})
        self.assertEqual(self.label()[0], 0)
        self.assertEqual(len(self.upstream_writes()), 1)

    def test_finds_the_owner_marker_beyond_the_first_page(self):
        self.fork.issue_comments.extend({"user": {"login": "npa-bot"}, "body": "noise"} for _ in range(150))
        self.fork.issue_comments.append({"user": {"login": OWNER}, "body": f"{gate.ISSUE_MARKER}\nhttps://github.com/{UPSTREAM}/issues/1"})
        self.assertEqual(self.label()[0], 0)
        self.assertEqual(self.fake.writes(), [])

    def test_refuses_labels_applied_by_anyone_but_the_owner(self):
        self.assertRefused(self.label(sender="npa-bot"), "owner")

    def test_refuses_when_the_latest_approval_label_was_not_applied_by_the_owner(self):
        self.fork.issue_events.append({"event": "unlabeled", "label": {"name": "approved-upstream"}, "actor": {"login": OWNER}})
        self.fork.issue_events.append({"event": "labeled", "label": {"name": "approved-upstream"}, "actor": {"login": "npa-bot"}})
        self.assertRefused(self.label(), "owner")

    def test_refuses_other_labels(self):
        self.assertRefused(self.label(label="upstream-issue"))

    def test_refuses_issues_without_the_upstream_issue_label(self):
        self.fork.issue["labels"] = [{"name": "approved-upstream"}]
        self.assertRefused(self.label(), "upstream-issue")

    def test_refuses_issues_whose_approval_label_was_removed(self):
        event = self.fork.label_event()
        self.fork.issue["labels"] = [{"name": "upstream-issue"}]
        self.assertRefused(self.gate("issues", event))

    def test_refuses_pull_requests_and_closed_issues(self):
        self.fork.issue["pull_request"] = {"url": "x"}
        self.assertRefused(self.label())
        del self.fork.issue["pull_request"]
        self.fork.issue["state"] = "closed"
        self.assertRefused(self.label())

    def test_refuses_a_title_or_body_edited_after_the_label(self):
        event = self.fork.label_event()
        self.fork.issue["body"] = "rewritten after approval"
        self.assertRefused(self.gate("issues", event), "changed")

    def test_fails_closed_on_api_errors(self):
        for name in ("issue", "issue_events", "issue_comments"):
            with self.subTest(failing=name):
                self.fork.failing = {name}
                self.assertRefused(self.label())


if __name__ == "__main__":
    unittest.main()
