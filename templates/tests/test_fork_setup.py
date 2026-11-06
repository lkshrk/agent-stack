import base64
import importlib.machinery
import importlib.util
import io
import unittest
from pathlib import Path

from .fake_github import FakeGitHub

ROOT = Path(__file__).resolve().parents[2]
_loader = importlib.machinery.SourceFileLoader("fork_setup", str(ROOT / "scripts" / "agent-stack-fork-setup"))
_spec = importlib.util.spec_from_loader("fork_setup", _loader)
assert _spec
setup = importlib.util.module_from_spec(_spec)
_loader.exec_module(setup)

TOKEN = "gho_forksetupsecret0123456789"
FORK = "lkshrk/omnigent"
CI_PATH = ".github/workflows/contribution-ci.yml"
RENDERED = (ROOT / "templates" / "contribution-fork" / CI_PATH).read_text()
API = r"/repos/lkshrk/omnigent"


class FakeFork:
    def __init__(self, gh: FakeGitHub):
        self.login = "lkshrk"
        self.scopes: str | None = "gist, read:org, repo, workflow"
        self.repo = {
            "full_name": FORK,
            "owner": {"login": "lkshrk"},
            "fork": True,
            "parent": {"full_name": "omnigent-ai/omnigent"},
            "default_branch": "main",
        }
        self.labels = [{"name": "bug"}, {"name": "enhancement"}]
        self.rulesets: dict[int, dict] = {}
        self.files: dict[str, tuple[str, str]] = {}
        self.actions = {"enabled": False, "allowed_actions": "all"}
        self.workflows = [
            {"id": 11, "name": "ci", "path": ".github/workflows/ci.yml", "state": "active"},
            {"id": 12, "name": "release", "path": ".github/workflows/release.yml", "state": "active"},
            {"id": 13, "name": "stale", "path": ".github/workflows/stale.yml", "state": "disabled_manually"},
        ]
        self.ci_state = "disabled_manually"
        self.hide_ci_lists = 0
        self.workflow_permissions = {"default_workflow_permissions": "write", "can_approve_pull_request_reviews": True}
        self.fail: tuple[str, str] | None = None
        routes = [
            ("GET", r"/user", self.user),
            ("GET", API, lambda m, q, b: (200, self.repo)),
            ("GET", API + r"/labels", lambda m, q, b: self.page(self.labels, q)),
            ("POST", API + r"/labels", self.create_label),
            ("GET", API + r"/rulesets", self.list_rulesets),
            ("POST", API + r"/rulesets", self.create_ruleset),
            ("GET", API + r"/rulesets/(\d+)", lambda m, q, b: (200, self.rulesets[int(m[1])])),
            ("PUT", API + r"/rulesets/(\d+)", self.update_ruleset),
            ("GET", API + r"/contents/(.+)", self.get_file),
            ("PUT", API + r"/contents/(.+)", self.put_file),
            ("GET", API + r"/actions/permissions", lambda m, q, b: (200, self.actions)),
            ("PUT", API + r"/actions/permissions", self.put_actions),
            ("GET", API + r"/actions/workflows", self.list_workflows),
            ("PUT", API + r"/actions/workflows/(\d+)/(enable|disable)", self.toggle_workflow),
            ("GET", API + r"/actions/permissions/workflow", lambda m, q, b: (200, self.workflow_permissions)),
            ("PUT", API + r"/actions/permissions/workflow", self.put_workflow_permissions),
        ]
        for method, pattern, handler in routes:
            gh.route(method, pattern, self.guard(method, pattern, handler))

    def guard(self, method, pattern, handler):
        def run(match, query, body):
            if self.fail == (method, pattern):
                return 500, {"message": "Server Error"}
            return handler(match, query, body)

        return run

    def user(self, match, query, body):
        return 200, {"login": self.login}, ({"X-OAuth-Scopes": self.scopes} if self.scopes is not None else {})

    @staticmethod
    def page(items, query, key=None):
        per_page = int(query.get("per_page", ["30"])[0])
        page = int(query.get("page", ["1"])[0])
        chunk = items[(page - 1) * per_page : page * per_page]
        return 200, ({"total_count": len(items), key: chunk} if key else chunk)

    def create_label(self, match, query, body):
        if any(label["name"].lower() == body["name"].lower() for label in self.labels):
            return 422, {"message": "Validation Failed", "errors": [{"code": "already_exists"}]}
        self.labels.append(dict(body))
        return 201, dict(body)

    def list_rulesets(self, match, query, body):
        summaries = [{"id": i, "name": r["name"], "target": r["target"], "enforcement": r["enforcement"]} for i, r in self.rulesets.items()]
        return self.page(summaries, query)

    def create_ruleset(self, match, query, body):
        ruleset_id = 100 + len(self.rulesets)
        self.rulesets[ruleset_id] = {"id": ruleset_id, **body}
        return 201, self.rulesets[ruleset_id]

    def update_ruleset(self, match, query, body):
        ruleset_id = int(match[1])
        self.rulesets[ruleset_id] = {"id": ruleset_id, **body}
        return 200, self.rulesets[ruleset_id]

    def get_file(self, match, query, body):
        if query.get("ref") != ["main"] or match[1] not in self.files:
            return 404, {"message": "Not Found"}
        text, sha = self.files[match[1]]
        encoded = base64.encodebytes(text.encode()).decode()
        return 200, {"type": "file", "encoding": "base64", "content": encoded, "sha": sha, "path": match[1]}

    def put_file(self, match, query, body):
        path = match[1]
        if body.get("branch") != "main" or body.get("sha") != (self.files[path][1] if path in self.files else None):
            return 409, {"message": "sha mismatch"}
        sha = f"blob{len(self.files) + 1}"
        self.files[path] = (base64.b64decode(body["content"]).decode(), sha)
        if path.startswith(".github/workflows/") and not any(w["path"] == path for w in self.workflows):
            self.workflows.append({"id": 20, "name": "contribution-ci", "path": path, "state": self.ci_state})
        return 200, {"content": {"sha": sha}}

    def put_actions(self, match, query, body):
        self.actions = {**self.actions, **body}
        return 204, None

    def list_workflows(self, match, query, body):
        if self.hide_ci_lists:
            self.hide_ci_lists -= 1
            return self.page([w for w in self.workflows if w["path"] != CI_PATH], query, "workflows")
        return self.page(self.workflows, query, "workflows")

    def toggle_workflow(self, match, query, body):
        workflow = next(w for w in self.workflows if w["id"] == int(match[1]))
        workflow["state"] = "active" if match[2] == "enable" else "disabled_manually"
        return 204, None

    def put_workflow_permissions(self, match, query, body):
        self.workflow_permissions = dict(body)
        return 204, None


def step(method: str, path: str) -> str:
    suffix = path.removeprefix("/repos/" + FORK)
    if suffix == "/labels":
        return "labels"
    if suffix.startswith("/rulesets"):
        return "rulesets"
    if suffix.startswith("/contents/"):
        return "commit"
    if suffix == "/actions/permissions":
        return "actions"
    if suffix.endswith("/enable"):
        return "enable-ci"
    if suffix.endswith("/disable"):
        return "disable"
    if suffix == "/actions/permissions/workflow":
        return "permissions"
    return f"{method} {path}"


class ForkSetupTest(unittest.TestCase):
    def setUp(self):
        self.gh = FakeGitHub(TOKEN)
        self.gh.__enter__()
        self.addCleanup(self.gh.__exit__)
        self.fork = FakeFork(self.gh)
        self.prompts: list[tuple[str, int]] = []
        self.answer = "y"

    def run_setup(self, *args: str) -> tuple[int, str, str]:
        stdout, stderr = io.StringIO(), io.StringIO()

        def confirm(prompt: str) -> str:
            self.prompts.append((prompt, len(self.gh.writes())))
            return self.answer

        code = setup.main(
            list(args) or ["omnigent"], api_url=self.gh.url, token=TOKEN, confirm=confirm, stdout=stdout, stderr=stderr, sleep=lambda s: None
        )
        out, err = stdout.getvalue(), stderr.getvalue()
        self.assertNotIn(TOKEN, out + err)
        return code, out, err

    def steps(self, writes=None) -> list[str]:
        collapsed: list[str] = []
        for method, path, _ in self.gh.writes() if writes is None else writes:
            name = step(method, path)
            if not collapsed or collapsed[-1] != name:
                collapsed.append(name)
        return collapsed

    def test_runs_every_step_in_order_on_a_fresh_fork(self):
        code, out, err = self.run_setup()

        self.assertEqual(code, 0, err)
        self.assertEqual(self.steps(), ["labels", "rulesets", "commit", "actions", "enable-ci", "disable", "permissions"])
        self.assertEqual(len(self.prompts), 1)
        writes_before_prompt = self.gh.writes()[: self.prompts[0][1]]
        self.assertEqual(self.steps(writes_before_prompt), ["labels", "rulesets"])
        self.assertIn("+name: contribution-ci", out)

    def test_creates_the_labels_the_approver_reads(self):
        self.run_setup()
        names = {label["name"] for label in self.fork.labels}
        self.assertLessEqual({"upstream-ready", "approved-upstream", "upstream-issue", "upstream-comment"}, names)

    def test_rulesets_protect_the_default_branch_and_upstream_branches_with_only_admins_bypassing(self):
        self.run_setup()
        rulesets = list(self.fork.rulesets.values())
        self.assertEqual(sorted(r["conditions"]["ref_name"]["include"][0] for r in rulesets), ["refs/heads/main", "refs/heads/upstream/**"])
        for ruleset in rulesets:
            self.assertEqual(ruleset["target"], "branch")
            self.assertEqual(ruleset["enforcement"], "active")
            self.assertEqual(ruleset["bypass_actors"], [{"actor_id": 5, "actor_type": "RepositoryRole", "bypass_mode": "always"}])
            self.assertEqual(sorted(rule["type"] for rule in ruleset["rules"]), ["creation", "deletion", "non_fast_forward", "update"])

    def test_commits_the_rendered_ci_and_configures_actions(self):
        self.run_setup()
        self.assertEqual(self.fork.files[CI_PATH][0], RENDERED)
        self.assertTrue(self.fork.actions["enabled"])
        states = {w["path"]: w["state"] for w in self.fork.workflows}
        self.assertEqual(states.pop(CI_PATH), "active")
        self.assertEqual(set(states.values()), {"disabled_manually"})
        self.assertEqual(self.fork.workflow_permissions, {"default_workflow_permissions": "read", "can_approve_pull_request_reviews": False})

    def test_only_active_other_workflows_are_disabled(self):
        self.run_setup()
        disabled = [path for method, path, _ in self.gh.writes() if path.endswith("/disable")]
        self.assertEqual(disabled, [f"/repos/{FORK}/actions/workflows/11/disable", f"/repos/{FORK}/actions/workflows/12/disable"])

    def test_second_run_writes_nothing_and_asks_nothing(self):
        self.assertEqual(self.run_setup()[0], 0)
        first = len(self.gh.writes())

        code, out, err = self.run_setup()

        self.assertEqual(code, 0, err)
        self.assertEqual(self.gh.writes()[first:], [])
        self.assertEqual(len(self.prompts), 1)

    def test_check_reports_drift_on_a_fresh_fork_and_writes_nothing(self):
        code, out, err = self.run_setup("omnigent", "--check")

        self.assertEqual(code, 1)
        self.assertEqual(self.gh.writes(), [])
        self.assertEqual(self.prompts, [])
        for expected in ("upstream-ready", "ruleset", CI_PATH, "Actions", "ci.yml", "release.yml", "workflow permissions"):
            self.assertIn(expected, out)
        self.assertNotIn("stale.yml", out)

    def test_the_check_client_refuses_writes_before_sending_them(self):
        gh = setup.GitHub(self.gh.url, TOKEN, read_only=True)
        with self.assertRaises(setup.SetupError):
            gh.request("PUT", f"/repos/{FORK}/actions/permissions", {"enabled": True})
        self.assertEqual(self.gh.requests, [])

    def test_check_passes_after_setup_and_flags_later_drift(self):
        self.run_setup()
        written = len(self.gh.writes())
        self.assertEqual(self.run_setup("omnigent", "--check")[0], 0)

        self.fork.workflows[0]["state"] = "active"
        self.fork.workflow_permissions["can_approve_pull_request_reviews"] = True
        next(iter(self.fork.rulesets.values()))["bypass_actors"].append({"actor_id": 4, "actor_type": "RepositoryRole", "bypass_mode": "always"})
        code, out, err = self.run_setup("omnigent", "--check")

        self.assertEqual(code, 1)
        self.assertIn("ci.yml", out)
        self.assertIn("workflow permissions", out)
        self.assertIn("ruleset", out)
        self.assertEqual(len(self.gh.writes()), written)

    def test_a_weakened_ruleset_is_restored(self):
        self.run_setup()
        ruleset_id, ruleset = next(iter(self.fork.rulesets.items()))
        ruleset["enforcement"] = "disabled"
        ruleset["rules"] = [{"type": "deletion"}]

        self.assertEqual(self.run_setup()[0], 0)

        self.assertEqual(self.fork.rulesets[ruleset_id]["enforcement"], "active")
        self.assertEqual(len(self.fork.rulesets[ruleset_id]["rules"]), 4)
        self.assertEqual(len(self.fork.rulesets), 2)

    def test_an_update_rule_that_allows_fetch_and_merge_is_drift(self):
        self.run_setup()
        ruleset = next(iter(self.fork.rulesets.values()))
        ruleset["rules"] = [
            {"type": "update", "parameters": {"update_allows_fetch_and_merge": True}} if rule["type"] == "update" else rule for rule in ruleset["rules"]
        ]
        self.assertEqual(self.run_setup("omnigent", "--check")[0], 1)

    def test_a_changed_ci_file_is_shown_and_replaced_after_confirmation(self):
        self.fork.files[CI_PATH] = ("name: contribution-ci\non: push\n", "old")

        code, out, err = self.run_setup()

        self.assertEqual(code, 0, err)
        self.assertIn("-on: push", out)
        self.assertEqual(self.fork.files[CI_PATH][0], RENDERED)
        put = next(body for method, path, body in self.gh.writes() if method == "PUT" and "/contents/" in path)
        self.assertEqual(put["sha"], "old")
        self.assertEqual(put["branch"], "main")

    def test_declining_the_ci_commit_stops_before_it(self):
        self.answer = "n"

        code, out, err = self.run_setup()

        self.assertEqual(code, 1)
        self.assertEqual(self.steps(), ["labels", "rulesets"])
        self.assertNotIn(CI_PATH, self.fork.files)

    def test_waits_for_github_to_list_the_new_ci_workflow(self):
        self.fork.hide_ci_lists = 2
        code, out, err = self.run_setup()
        self.assertEqual(code, 0, err)
        self.assertEqual({w["path"]: w["state"] for w in self.fork.workflows}[CI_PATH], "active")

    def test_a_ci_workflow_github_never_lists_fails_the_run(self):
        self.fork.hide_ci_lists = 1000
        code, out, err = self.run_setup()
        self.assertEqual(code, 1)
        self.assertIn("run the setup again", err)
        self.assertNotIn("permissions", self.steps())

    def test_refuses_a_fork_of_another_owner(self):
        self.fork.repo["owner"] = {"login": "someone"}
        for args in (["omnigent"], ["omnigent", "--check"]):
            code, out, err = self.run_setup(*args)
            self.assertEqual(code, 1)
            self.assertIn("owner", err)
        self.assertEqual(self.gh.writes(), [])

    def test_refuses_a_token_of_another_user(self):
        self.fork.login = "someone"
        code, out, err = self.run_setup()
        self.assertEqual(code, 1)
        self.assertIn("lkshrk", err)
        self.assertEqual(self.gh.writes(), [])

    def test_refuses_a_wrong_parent_or_a_repository_that_is_not_a_fork(self):
        for repo in ({"parent": {"full_name": "someone/omnigent"}}, {"fork": False, "parent": None}):
            with self.subTest(repo=repo):
                self.fork.repo.update(repo)
                code, out, err = self.run_setup()
                self.assertEqual(code, 1)
                self.assertIn("omnigent-ai/omnigent", err)
        self.assertEqual(self.gh.writes(), [])

    def test_refuses_a_default_branch_other_than_the_registry_one(self):
        self.fork.repo["default_branch"] = "trunk"
        code, out, err = self.run_setup()
        self.assertEqual(code, 1)
        self.assertIn("main", err)
        self.assertEqual(self.gh.writes(), [])

    def test_refuses_an_unregistered_repository_without_calling_github(self):
        code, out, err = self.run_setup("missing")
        self.assertEqual(code, 1)
        self.assertIn("missing", err)
        self.assertEqual(self.gh.requests, [])

    def test_refuses_a_token_without_the_workflow_scope(self):
        for scopes in ("repo, read:org", "repo, workflowx", None):
            with self.subTest(scopes=scopes):
                self.fork.scopes = scopes
                for args in (["omnigent"], ["omnigent", "--check"]):
                    code, out, err = self.run_setup(*args)
                    self.assertEqual(code, 1)
                    self.assertIn("gh auth refresh -s workflow", err)
        self.assertEqual(self.gh.writes(), [])

    def test_api_errors_name_the_request_but_never_the_token(self):
        self.fork.fail = ("PUT", API + r"/actions/permissions/workflow")
        code, out, err = self.run_setup()
        self.assertEqual(code, 1)
        self.assertIn("500", err)
        self.assertIn("/actions/permissions/workflow", err)

    def test_a_list_beyond_the_page_cap_refuses(self):
        self.fork.labels = [{"name": f"label-{n}"} for n in range(setup.PAGE_SIZE * setup.MAX_PAGES)]
        code, out, err = self.run_setup()
        self.assertEqual(code, 1)
        self.assertIn("labels", err)
        self.assertEqual(self.gh.writes(), [])

    def test_every_request_carries_the_token_only_in_the_authorization_header(self):
        self.run_setup()
        for request in self.gh.requests:
            self.assertEqual(request["auth"], f"Bearer {TOKEN}")
            self.assertNotIn(TOKEN, request["path"] + request["query"])


if __name__ == "__main__":
    unittest.main()
