import base64
import hashlib
import importlib.machinery
import importlib.util
import io
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import traceback
import unittest
import unittest.mock
from pathlib import Path
from urllib.parse import unquote

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
REGISTRY = ROOT / "config" / "public-contributions.yaml"
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
            "has_issues": False,
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
        self.workflow_lists = 0
        self.late_workflows: dict[int, list[dict]] = {}
        self.workflow_permissions = {"default_workflow_permissions": "write", "can_approve_pull_request_reviews": True}
        self.fail: tuple[str, str] | None = None
        self.fail_answer: tuple[int, dict] = (500, {"message": "Server Error"})
        self.redirects: dict[tuple[str, str], tuple[int, str]] = {}
        routes = [
            ("GET", r"/user", self.user),
            ("GET", API, lambda m, q, b: (200, self.repo)),
            ("PATCH", API, self.update_repo),
            ("GET", API + r"/labels", lambda m, q, b: self.page(self.labels, q)),
            ("POST", API + r"/labels", self.create_label),
            ("PATCH", API + r"/labels/([^/]+)", self.update_label),
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
                return self.fail_answer
            if (method, pattern) in self.redirects:
                status, location = self.redirects[(method, pattern)]
                return status, {"message": "Moved"}, {"Location": location}
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

    def update_repo(self, match, query, body):
        self.repo.update(body)
        return 200, self.repo

    def create_label(self, match, query, body):
        if any(label["name"].lower() == body["name"].lower() for label in self.labels):
            return 422, {"message": "Validation Failed", "errors": [{"code": "already_exists"}]}
        self.labels.append(dict(body))
        return 201, dict(body)

    def update_label(self, match, query, body):
        label = next((label for label in self.labels if label["name"] == unquote(match[1])), None)
        if label is None:
            return 404, {"message": "Not Found"}
        label.update({key: value for key, value in body.items() if key != "new_name"}, name=body.get("new_name", label["name"]))
        return 200, dict(label)

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
        if query.get("page", ["1"]) == ["1"]:
            self.workflow_lists += 1
            self.workflows += self.late_workflows.pop(self.workflow_lists, [])
        if self.hide_ci_lists:
            self.hide_ci_lists -= 1
            return self.page([w for w in self.workflows if w["path"] != CI_PATH], query, "workflows")
        return self.page(self.workflows, query, "workflows")

    def toggle_workflow(self, match, query, body):
        workflow = next(w for w in self.workflows if w.get("id") == int(match[1]))
        workflow["state"] = "active" if match[2] == "enable" else "disabled_manually"
        return 204, None

    def put_workflow_permissions(self, match, query, body):
        self.workflow_permissions = dict(body)
        return 204, None


TEST_IDENTITY = {
    "GIT_AUTHOR_NAME": "fork-setup-test",
    "GIT_AUTHOR_EMAIL": "fork-setup-test@example.invalid",
    "GIT_COMMITTER_NAME": "fork-setup-test",
    "GIT_COMMITTER_EMAIL": "fork-setup-test@example.invalid",
}


def git_env() -> dict[str, str]:
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    return {
        **env,
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CEILING_DIRECTORIES": str(Path(tempfile.gettempdir()).resolve()),
        **TEST_IDENTITY,
    }


def git(directory: Path, *args: str, check: bool = True) -> str:
    command = ["git", "-c", "commit.gpgsign=false", "-c", "tag.gpgsign=false", *args]
    return subprocess.run(command, cwd=directory, check=check, capture_output=True, text=True, env=git_env()).stdout


def _index_digest() -> str:
    path = git(ROOT, "rev-parse", "--git-path", "index", check=False).strip()
    try:
        return hashlib.sha256((ROOT / path).read_bytes()).hexdigest() if path else ""
    except OSError:
        return ""


def _config_digest() -> str:
    path = git(ROOT, "rev-parse", "--git-path", "config", check=False).strip()
    if not path:
        return ""
    listing = git(ROOT, "config", "--file", str(ROOT / path), "--list", check=False)
    kept = [line for line in listing.splitlines() if not line.startswith(("branch.", "remote."))]
    return hashlib.sha256("\n".join(kept).encode()).hexdigest()


def repository_state() -> tuple[str, ...]:
    return (
        git(ROOT, "symbolic-ref", "-q", "HEAD", check=False),
        git(ROOT, "rev-parse", "HEAD", check=False),
        git(ROOT, "for-each-ref", "refs/tags", "--format=%(objectname) %(refname)", check=False),
        git(ROOT, "config", "--get", "core.bare", check=False),
        _config_digest(),
        _index_digest(),
    )


_REPOSITORY_AT_IMPORT = repository_state()


def _require_unchanged(when: str) -> None:
    if repository_state() != _REPOSITORY_AT_IMPORT:
        raise AssertionError(f"the real repository at {ROOT} changed {when}: HEAD, a tag, core.bare, the config or the index")


def setUpModule():
    _require_unchanged("before the setup tests ran")


def tearDownModule():
    _require_unchanged("during the setup tests")


def init_repository(root: Path) -> None:
    if subprocess.run(["git", "rev-parse", "--git-dir"], cwd=root, capture_output=True, env=git_env()).returncode == 0:
        raise RuntimeError(f"{root} already resolves to a git repository; refusing to run git init there")
    git(root, "init", "-q", "-b", "main")
    if Path(git(root, "rev-parse", "--absolute-git-dir").strip()).resolve() != (root / ".git").resolve():
        raise RuntimeError("test checkout escaped its temporary directory")


def checkout(tagged: bool = True) -> tempfile.TemporaryDirectory:
    directory = tempfile.TemporaryDirectory()
    root = Path(directory.name)
    init_repository(root)
    (root / "README.md").write_text("x\n")
    (root / "config").mkdir()
    (root / "config" / "public-contributions.yaml").write_text(REGISTRY.read_text())
    git(root, "add", "README.md", "config")
    git(root, "commit", "-q", "-m", "init")
    if tagged:
        git(root, "tag", "-a", "v1.2.3", "-m", "v1.2.3")
    return directory


def step(method: str, path: str) -> str:
    suffix = path.removeprefix("/repos/" + FORK)
    if method == "PATCH" and suffix == "":
        return "issues"
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
        self.checkout = checkout()
        self.addCleanup(self.checkout.cleanup)
        self.root = Path(self.checkout.name)

    def run_setup(self, *args: str, api_url: str | None = None) -> tuple[int, str, str]:
        stdout, stderr = io.StringIO(), io.StringIO()

        def confirm(prompt: str) -> str:
            self.prompts.append((prompt, len(self.gh.writes())))
            return self.answer

        code = setup.main(
            list(args) or ["omnigent"],
            api_url=api_url or self.gh.url,
            token=TOKEN,
            confirm=confirm,
            stdout=stdout,
            stderr=stderr,
            sleep=lambda s: None,
            checkout=self.root,
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
        self.assertEqual(self.steps(), ["issues", "labels", "rulesets", "commit", "enable-ci", "disable", "actions", "permissions"])
        self.assertEqual(len(self.prompts), 1)
        writes_before_prompt = self.gh.writes()[: self.prompts[0][1]]
        self.assertEqual(self.steps(writes_before_prompt), ["issues", "labels", "rulesets"])
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

    def test_enables_issues_on_a_fork_that_has_them_off(self):
        code, out, err = self.run_setup()

        self.assertEqual(code, 0, err)
        self.assertIs(self.fork.repo["has_issues"], True)
        self.assertIn(("PATCH", f"/repos/{FORK}", {"has_issues": True}), self.gh.writes())
        self.assertIn("enabled issues", out)

    def test_leaves_issues_alone_when_they_are_on(self):
        self.fork.repo["has_issues"] = True

        code, out, err = self.run_setup()

        self.assertEqual(code, 0, err)
        self.assertNotIn(f"/repos/{FORK}", [path for _, path, _ in self.gh.writes()])
        self.assertNotIn("enabled issues", out)

    def test_check_flags_disabled_issues_alone_and_writes_nothing(self):
        self.run_setup()
        self.fork.repo["has_issues"] = False
        written = len(self.gh.writes())

        code, out, err = self.run_setup("omnigent", "--check")

        self.assertEqual(code, 1)
        self.assertEqual(self.drift_lines(out), ["drift: issues are disabled"])
        self.assertEqual(len(self.gh.writes()), written)

    def test_every_upstream_workflow_is_disabled_before_actions_are_enabled(self):
        self.run_setup()
        paths = [path for _, path, _ in self.gh.writes()]
        actions = paths.index(f"/repos/{FORK}/actions/permissions")
        disables = [i for i, path in enumerate(paths) if path.endswith("/disable")]
        self.assertEqual(len(disables), 2)
        self.assertLess(max(disables), actions)

    def test_a_failed_workflow_disable_stops_before_actions_are_enabled(self):
        self.fork.fail = ("PUT", API + r"/actions/workflows/(\d+)/(enable|disable)")
        self.fork.ci_state = "active"

        code, out, err = self.run_setup()

        self.assertEqual(code, 1)
        self.assertIn("disable", err)
        self.assertIs(self.fork.actions["enabled"], False)
        self.assertNotIn(("PUT", f"/repos/{FORK}/actions/permissions"), [(m, p) for m, p, _ in self.gh.writes()])

    def test_a_run_that_failed_to_enable_actions_finishes_on_the_next_run(self):
        self.fork.fail = ("PUT", API + r"/actions/permissions")
        self.assertEqual(self.run_setup()[0], 1)
        self.assertIs(self.fork.actions["enabled"], False)
        self.fork.fail = None
        first = len(self.gh.writes())

        code, out, err = self.run_setup()

        self.assertEqual(code, 0, err)
        self.assertIs(self.fork.actions["enabled"], True)
        self.assertEqual([p for _, p, _ in self.gh.writes()[first:] if p.endswith("/disable")], [])

    def test_a_workflow_github_lists_late_is_disabled_before_actions_are_enabled(self):
        self.fork.late_workflows[2] = [{"id": 30, "name": "deploy", "path": ".github/workflows/deploy.yml", "state": "active"}]

        code, out, err = self.run_setup()

        self.assertEqual(code, 0, err)
        self.assertEqual({w["path"]: w["state"] for w in self.fork.workflows}[".github/workflows/deploy.yml"], "disabled_manually")
        paths = [p for _, p, _ in self.gh.writes()]
        self.assertLess(paths.index(f"/repos/{FORK}/actions/workflows/30/disable"), paths.index(f"/repos/{FORK}/actions/permissions"))
        self.assertEqual(len([p for p in paths if p.endswith("/disable")]), 3)

    def test_workflows_are_listed_once_when_actions_are_already_on(self):
        self.fork.actions["enabled"] = True

        code, out, err = self.run_setup()

        self.assertEqual(code, 0, err)
        self.assertEqual(self.fork.workflow_lists, 1)

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
        for expected in ("issues are disabled", "upstream-ready", "ruleset", CI_PATH, "Actions", "ci.yml", "release.yml", "workflow permissions"):
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
        self.assertEqual(self.steps(), ["issues", "labels", "rulesets"])
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

    def test_refuses_an_untagged_head_before_calling_github(self):
        untagged = checkout(tagged=False)
        self.addCleanup(untagged.cleanup)
        self.root = Path(untagged.name)
        code, out, err = self.run_setup()
        self.assertEqual(code, 1)
        self.assertIn("release tag", err)
        self.assertEqual(self.gh.requests, [])

    def test_refuses_a_head_one_commit_past_the_tag(self):
        (self.root / "README.md").write_text("y\n")
        git(self.root, "commit", "-q", "-am", "next")
        code, out, err = self.run_setup()
        self.assertEqual(code, 1)
        self.assertIn("release tag", err)
        self.assertEqual(self.gh.requests, [])

    def test_refuses_a_dirty_tree_before_calling_github(self):
        for dirty in ("modified", "untracked"):
            with self.subTest(dirty=dirty):
                fresh = checkout()
                self.addCleanup(fresh.cleanup)
                self.root = Path(fresh.name)
                if dirty == "modified":
                    (self.root / "README.md").write_text("changed\n")
                else:
                    (self.root / "yaml.py").write_text("raise SystemExit\n")
                code, out, err = self.run_setup()
                self.assertEqual(code, 1)
                self.assertIn("not clean", err)
        self.assertEqual(self.gh.requests, [])

    def test_refuses_a_directory_that_is_not_a_checkout(self):
        with tempfile.TemporaryDirectory() as directory:
            self.root = Path(directory)
            code, out, err = self.run_setup()
        self.assertEqual(code, 1)
        self.assertIn("release tag", err)
        self.assertEqual(self.gh.requests, [])

    def test_inherited_git_variables_cannot_point_the_release_check_elsewhere(self):
        untagged = checkout(tagged=False)
        self.addCleanup(untagged.cleanup)
        with unittest.mock.patch.dict(os.environ, {"GIT_DIR": str(self.root / ".git"), "GIT_WORK_TREE": str(self.root)}):
            self.root = Path(untagged.name)
            code, out, err = self.run_setup()
        self.assertEqual(code, 1)
        self.assertIn("is not checked out at a release tag", err)
        self.assertEqual(self.gh.requests, [])

    def test_check_only_warns_about_an_untagged_or_dirty_checkout(self):
        self.run_setup()
        untagged = checkout(tagged=False)
        self.addCleanup(untagged.cleanup)
        self.root = Path(untagged.name)
        (self.root / "README.md").write_text("changed\n")

        code, out, err = self.run_setup("omnigent", "--check")

        self.assertEqual(code, 0, err)
        self.assertIn("warning", err)
        self.assertIn("release tag", err)
        self.assertIn("not clean", err)

    def test_dynamic_workflows_are_reported_but_never_disabled(self):
        self.fork.workflows.append({"id": 30, "name": "Dependabot Updates", "path": "dynamic/dependabot/dependabot-updates", "state": "active"})

        code, out, err = self.run_setup()

        self.assertEqual(code, 0, err)
        self.assertNotIn(f"/repos/{FORK}/actions/workflows/30/disable", [path for _, path, _ in self.gh.writes()])
        self.assertIn("dynamic/dependabot/dependabot-updates", out)
        self.assertEqual(next(w for w in self.fork.workflows if w["id"] == 30)["state"], "active")

        code, out, err = self.run_setup("omnigent", "--check")
        self.assertEqual(code, 0, err)
        self.assertNotIn("drift", out)
        self.assertIn("info: ", out)
        self.assertIn("dynamic/dependabot/dependabot-updates", out)

    def drift_lines(self, out: str) -> list[str]:
        return [line for line in out.splitlines() if line.startswith("drift: ")]

    def ruleset_named(self, name: str) -> dict:
        return next(r for r in self.fork.rulesets.values() if r["name"] == name)

    def commit_and_tag(self, message: str, tag: str) -> None:
        git(self.root, "add", "-A")
        git(self.root, "commit", "-q", "-m", message)
        git(self.root, "tag", "-a", tag, "-m", tag)

    def test_a_301_is_refused_without_following_it(self):
        with FakeGitHub("elsewhere-token") as elsewhere:
            self.fork.redirects[("GET", API)] = (301, elsewhere.url + "/repos/someone/omnigent")
            code, out, err = self.run_setup()
            self.assertEqual(elsewhere.requests, [])
        self.assertEqual(code, 1)
        self.assertIn("301", err)
        self.assertEqual(self.gh.writes(), [])

    def test_a_307_on_a_write_is_refused_and_never_replayed(self):
        with FakeGitHub("elsewhere-token") as elsewhere:
            self.fork.redirects[("PUT", API + r"/contents/(.+)")] = (307, elsewhere.url + "/repos/someone/omnigent/contents/" + CI_PATH)
            code, out, err = self.run_setup()
            self.assertEqual(elsewhere.requests, [])
        self.assertEqual(code, 1)
        self.assertIn("307", err)
        self.assertEqual(len([w for w in self.gh.writes() if w[0] == "PUT" and "/contents/" in w[1]]), 1)
        self.assertNotIn(CI_PATH, self.fork.files)
        self.assertEqual(self.steps(), ["issues", "labels", "rulesets", "commit"])

    def test_control_characters_in_the_fork_ci_are_escaped_in_the_diff(self):
        self.fork.files[CI_PATH] = ("name: contribution-ci\x1b[2K\rlooks: fine\x07\n", "old")
        self.answer = "n"

        code, out, err = self.run_setup()

        self.assertEqual(code, 1)
        for raw in ("\x1b", "\r", "\x07"):
            self.assertNotIn(raw, out)
        self.assertIn("\\x1b[2K\\rlooks: fine\\x07", out)

    def test_a_network_error_never_shows_the_token(self):
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        code, out, err = self.run_setup(api_url=f"http://127.0.0.1:{port}")
        self.assertEqual(code, 1)
        self.assertIn("cannot talk to GitHub", err)

        with self.assertRaises(setup.SetupError) as raised:
            setup.GitHub(f"http://127.0.0.1:{port}", TOKEN, read_only=False).request("GET", "/user")
        self.assertNotIn(TOKEN, "".join(traceback.format_exception(raised.exception)))

    def test_a_planted_module_never_runs_before_the_release_check(self):
        with tempfile.TemporaryDirectory() as directory:
            copy = Path(directory) / "agent-stack"
            for part in ("scripts", "templates", "policies/omnigent/agent_stack_policies", "config"):
                shutil.copytree(ROOT / part, copy / part, ignore=shutil.ignore_patterns("__pycache__"))
            init_repository(copy)
            git(copy, "add", "-A")
            git(copy, "commit", "-q", "-m", "release")
            git(copy, "tag", "-a", "v9.9.9", "-m", "v9.9.9")
            marker = Path(directory) / "planted-ran"
            planted = f"open({str(marker)!r}, 'w').write('ran')\nraise SystemExit(7)\n"
            (copy / "templates" / "yaml.py").write_text(planted)
            (copy / "scripts" / "json.py").write_text(planted)
            script = copy / "scripts" / "agent-stack-fork-setup"
            for command in ([str(script), "omnigent"], [sys.executable, "-I", str(script), "omnigent"]):
                with self.subTest(command=command[0]):
                    result = subprocess.run(command, cwd=copy, capture_output=True, text=True, env=git_env(), timeout=60)
                    self.assertEqual(result.returncode, 1, result.stderr)
                    self.assertIn("not clean", result.stderr)
                    self.assertFalse(marker.exists())
            (copy / "scripts" / "os.py").write_text(planted)
            result = subprocess.run([sys.executable, str(script), "omnigent"], cwd=copy, capture_output=True, text=True, env=git_env(), timeout=60)
            self.assertEqual(result.returncode, 1, result.stderr)
            self.assertIn("not clean", result.stderr)
            self.assertFalse(marker.exists())

    def test_untracked_files_count_even_when_the_status_config_hides_them(self):
        git(self.root, "config", "status.showUntrackedFiles", "no")
        (self.root / "stray.txt").write_text("x\n")
        code, out, err = self.run_setup()
        self.assertEqual(code, 1)
        self.assertIn("not clean", err)
        self.assertEqual(self.gh.requests, [])

    def test_refuses_a_checkout_below_the_repository_top_level(self):
        (self.root / "sub").mkdir()
        (self.root / "sub" / "file").write_text("x\n")
        self.commit_and_tag("sub", "v1.2.4")
        self.root = self.root / "sub"
        code, out, err = self.run_setup()
        self.assertEqual(code, 1)
        self.assertIn("top level", err)
        self.assertEqual(self.gh.requests, [])

    def test_a_registry_path_is_refused_without_check(self):
        code, out, err = self.run_setup("omnigent", "--registry", str(REGISTRY))
        self.assertEqual(code, 1)
        self.assertIn("--registry", err)
        self.assertEqual(self.gh.requests, [])

    def test_check_accepts_a_registry_path(self):
        self.run_setup()
        code, out, err = self.run_setup("omnigent", "--check", "--registry", str(REGISTRY))
        self.assertEqual(code, 0, err)

    def test_a_normal_run_reads_the_registry_of_the_checkout(self):
        registry = self.root / "config" / "public-contributions.yaml"
        registry.write_text(registry.read_text().replace("default_branch: main", "default_branch: trunk"))
        self.commit_and_tag("trunk", "v1.2.4")
        code, out, err = self.run_setup()
        self.assertEqual(code, 1)
        self.assertIn("trunk", err)
        self.assertEqual(self.gh.writes(), [])

    def test_each_ruleset_field_alone_is_drift_and_is_restored(self):
        cases = {
            "enforcement": lambda r: r.update(enforcement="evaluate"),
            "target": lambda r: r.update(target="tag"),
            "include": lambda r: r["conditions"]["ref_name"].update(include=["refs/heads/main", "refs/heads/other"]),
            "exclude": lambda r: r["conditions"]["ref_name"].update(exclude=["refs/heads/main"]),
        }
        self.run_setup()
        for field, change in cases.items():
            with self.subTest(field=field):
                ruleset = self.ruleset_named("contribution-default-branch")
                restored = json.loads(json.dumps(ruleset))
                change(ruleset)
                code, out, err = self.run_setup("omnigent", "--check")
                self.assertEqual(code, 1)
                self.assertEqual(self.drift_lines(out), ["drift: ruleset contribution-default-branch differs from the setup"])
                self.assertEqual(self.run_setup()[0], 0)
                self.assertEqual(self.ruleset_named("contribution-default-branch"), restored)

    def test_check_flags_changed_ci_content_alone(self):
        self.run_setup()
        text, sha = self.fork.files[CI_PATH]
        self.fork.files[CI_PATH] = (text + "# extra\n", sha)
        code, out, err = self.run_setup("omnigent", "--check")
        self.assertEqual(code, 1)
        self.assertEqual(self.drift_lines(out), [f"drift: {CI_PATH} on main differs from the rendered workflow"])

    def test_check_flags_an_inactive_ci_workflow_alone(self):
        self.run_setup()
        next(w for w in self.fork.workflows if w["path"] == CI_PATH)["state"] = "disabled_manually"
        code, out, err = self.run_setup("omnigent", "--check")
        self.assertEqual(code, 1)
        self.assertEqual(self.drift_lines(out), [f"drift: {CI_PATH} is not an active workflow"])

    def test_refuses_a_repository_whose_fork_flag_is_off_even_with_the_right_parent(self):
        self.fork.repo["fork"] = False
        code, out, err = self.run_setup()
        self.assertEqual(code, 1)
        self.assertIn("not a fork", err)
        self.assertEqual(self.gh.writes(), [])

    def test_a_label_differing_in_case_or_colour_converges_in_one_run(self):
        self.fork.labels.append({"name": "Approved-Upstream", "color": "ffffff", "description": "old"})
        self.fork.labels.append({"name": "upstream-issue", "color": "000000", "description": "old"})
        code, out, err = self.run_setup("omnigent", "--check")
        self.assertIn("drift: label approved-upstream", out)
        self.assertIn("drift: label upstream-issue", out)

        self.assertEqual(self.run_setup()[0], 0)

        matching = [label for label in self.fork.labels if label["name"].casefold() == "approved-upstream"]
        self.assertEqual([label["name"] for label in matching], ["approved-upstream"])
        self.assertEqual(matching[0]["color"], setup.LABELS["approved-upstream"][0])
        self.assertIn(("PATCH", f"/repos/{FORK}/labels/Approved-Upstream"), [(m, p) for m, p, _ in self.gh.writes()])
        self.assertEqual(next(label for label in self.fork.labels if label["name"] == "upstream-issue")["color"], setup.LABELS["upstream-issue"][0])
        self.assertNotIn("approved-upstream", [b["name"] for m, p, b in self.gh.writes() if m == "POST" and p.endswith("/labels")])
        written = len(self.gh.writes())
        self.assertEqual(self.run_setup("omnigent", "--check")[0], 0)
        self.assertEqual(self.run_setup()[0], 0)
        self.assertEqual(len(self.gh.writes()), written)

    def test_malformed_answers_refuse_cleanly(self):
        cases = {
            "user is a list": lambda fork: self.gh.route("GET", r"/user", lambda m, q, b: (200, ["x"], {"X-OAuth-Scopes": "workflow"})),
            "repo is a string": lambda fork: self.gh.route("GET", API, lambda m, q, b: (200, "x")),
            "label is a string": lambda fork: setattr(fork, "labels", ["oops"]),
            "workflow without id": lambda fork: fork.workflows.append({"name": "x", "path": ".github/workflows/x.yml", "state": "active"}),
            "workflow id is a string": lambda fork: fork.workflows.append({"id": "7", "path": ".github/workflows/x.yml", "state": "active"}),
            "ruleset detail is a list": lambda fork: (
                fork.rulesets.update({100: {"id": 100, "name": "contribution-default-branch", "target": "branch", "enforcement": "active"}}),
                self.gh.route("GET", API + r"/rulesets/(\d+)", lambda m, q, b: (200, ["x"])),
            ),
            "permissions are a list": lambda fork: self.gh.route("GET", API + r"/actions/permissions", lambda m, q, b: (200, ["x"])),
            "ruleset id is a string": lambda fork: self.gh.route(
                "GET", API + r"/rulesets", lambda m, q, b: (200, [{"id": "100", "name": "contribution-default-branch", "target": "branch", "enforcement": "active"}])
            ),
        }
        ids_only_matter_when_writing = {"workflow without id", "workflow id is a string"}
        for name, break_it in cases.items():
            with self.subTest(case=name):
                self.gh.routes.clear()
                self.fork = FakeFork(self.gh)
                break_it(self.fork)
                for args in (["omnigent"],) if name in ids_only_matter_when_writing else (["omnigent"], ["omnigent", "--check"]):
                    code, out, err = self.run_setup(*args)
                    self.assertEqual(code, 1, err)
                    self.assertTrue(err.startswith("agent-stack-fork-setup: unexpected answer from GitHub"), err)
                    self.assertNotIn("Traceback", err)

    def test_an_internal_error_is_a_clean_refusal(self):
        with unittest.mock.patch.object(setup.Setup, "verify", side_effect=KeyError("boom")):
            code, out, err = self.run_setup()
        self.assertEqual(code, 1)
        self.assertEqual(err, "agent-stack-fork-setup: stopped on an internal error (KeyError)\n")

    def test_a_non_ascii_lookalike_label_is_neither_drift_nor_renamed(self):
        self.fork.labels.append({"name": "approved-up\u017ftream", "color": "1d76db", "description": "x"})

        self.assertEqual(self.run_setup()[0], 0)

        self.assertNotIn("PATCH", [method for method, path, _ in self.gh.writes() if "/labels" in path])
        self.assertIn("approved-upstream", [b["name"] for m, p, b in self.gh.writes() if m == "POST" and p.endswith("/labels")])
        self.assertIn("approved-up\u017ftream", [label["name"] for label in self.fork.labels])
        code, out, err = self.run_setup("omnigent", "--check")
        self.assertEqual(code, 0, out)
        self.assertNotIn("drift: label", out)

    def test_an_exact_label_wins_over_a_case_variant_in_the_same_listing(self):
        self.run_setup()
        written = len(self.gh.writes())
        self.fork.labels.append({"name": "APPROVED-UPSTREAM", "color": "ffffff", "description": "x"})
        code, out, err = self.run_setup("omnigent", "--check")
        self.assertNotIn("drift: label", out)
        self.assertEqual(self.run_setup()[0], 0)
        self.assertEqual(len(self.gh.writes()), written)

    def test_a_missing_pyyaml_is_named(self):
        with unittest.mock.patch.dict(sys.modules):
            for name in ("agent_stack_policies.public_contributions", "render_contribution_fork"):
                sys.modules.pop(name, None)
            sys.modules["yaml"] = None
            code, out, err = self.run_setup()
        self.assertEqual(code, 1)
        self.assertIn("PyYAML is required", err)
        for part in ("isolated mode", "user site", "python-yaml"):
            self.assertIn(part, err)
        self.assertNotIn("internal error", err)
        self.assertEqual(self.gh.requests, [])

    def test_github_text_in_drift_lines_is_escaped(self):
        self.run_setup()
        self.fork.workflows.append({"id": 40, "name": "x", "path": ".github/workflows/evil\x1b[2K\r.yml", "state": "active"})
        code, out, err = self.run_setup("omnigent", "--check")
        self.assertEqual(code, 1)
        self.assertNotIn("\x1b", out)
        self.assertNotIn("\r", out)
        self.assertIn("evil\\x1b[2K\\r.yml", out)

    def test_github_error_messages_are_escaped(self):
        self.fork.fail = ("GET", r"/user")
        self.fork.fail_answer = (422, {"message": "bad\x1b[2K\rname\nmore"})
        with self.assertRaises(setup.SetupError) as raised:
            setup.GitHub(self.gh.url, TOKEN, read_only=False).request("GET", "/user")
        message = str(raised.exception)
        for raw in ("\x1b", "\r", "\n"):
            self.assertNotIn(raw, message)
        self.assertIn("bad\\x1b[2K\\rname", message)

    def test_every_request_carries_the_token_only_in_the_authorization_header(self):
        self.run_setup()
        for request in self.gh.requests:
            self.assertEqual(request["auth"], f"Bearer {TOKEN}")
            self.assertNotIn(TOKEN, request["path"] + request["query"])


if __name__ == "__main__":
    unittest.main()
