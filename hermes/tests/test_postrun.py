import io
import json
import os
import tempfile
import unittest
import urllib.error

from pathlib import Path

from agent_stack_hermes.postrun import (
    MAX_PAGES,
    NULL_SHA,
    COMPARE_FILE_CAP,
    Activity,
    Compare,
    Content,
    FileChange,
    GitHubError,
    GitHubReader,
    MAX_AGENT_MERGES,
    MAX_UNTRUSTED_PUSHES,
    MAX_PUSH_FILES,
    PROTECTED_PATHS,
    PULLS_RETRY_SECONDS,
    InfraReads,
    MergedPull,
    PullHead,
    WorkflowRun,
    agent_actors,
    token_files,
    violations,
)

API = "https://api.github.com"
REPO = "acme/app"
HEAD = PullHead("feat/x", "acme/app", "main", "acme/app", "main")
RECORDED = Path(__file__).parent / "testdata" / "github"


def recorded(name):
    return json.loads((RECORDED / name).read_text())


class FakeResponse(io.BytesIO):
    def __init__(self, body=b"", status=200, headers=None):
        super().__init__(body)
        self.status = status
        self.headers = headers or {}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def jr(obj, headers=None, status=200):
    return FakeResponse(json.dumps(obj).encode(), status, headers)


class FakeOpener:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []
        self.timeouts = []

    def __call__(self, request, timeout=None):
        self.requests.append(request)
        self.timeouts.append(timeout)
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class ReaderCase(unittest.TestCase):
    def setUp(self):
        fd, self.token_file = tempfile.mkstemp()
        os.write(fd, b"tok1\n")
        os.close(fd)
        self.addCleanup(os.unlink, self.token_file)

    def reader(self, responses):
        self.opener = FakeOpener(responses)
        self.sleeps = []
        return GitHubReader(self.token_file, opener=self.opener, sleep=self.sleeps.append)


AGENT = "h-cloud-npa[bot]"
AGENTS = frozenset({AGENT})


MAIN = "refs/heads/main"


def entry(ref, actor, before, after, ts=1000.0):
    return Activity(ref, ts, actor, before, after)


class Lookup:
    def __init__(self, *entries):
        self.entries = entries
        self.asked = []

    def __call__(self, ref):
        self.asked.append(ref)
        return [e for e in self.entries if e.ref == ref]


RENOVATE = "renovate-master[bot]"


def renovate_pull(sha="m", **over):
    fields = {"number": 7, "merged": True, "merge_commit_sha": sha, "base_ref": "main", "base_repo": REPO,
              "head_ref": "renovate/x-1.x", "head_repo": REPO, "author": RENOVATE, "merged_by": AGENT,
              "head_sha": "h", "base_sha": "b"}
    return MergedPull(**{**fields, **over})


def merge(before, after, actor=AGENT, kind="pr_merge"):
    return Activity(MAIN, 1000.0, actor, before, after, kind)


class Releases:
    def __init__(self, **authors):
        self.authors = authors
        self.asked = []

    def __call__(self, tag):
        self.asked.append(tag)
        return self.authors.get(tag)


HEAD_REF = "refs/heads/renovate/x-1.x"
APP = "kubernetes/apps/ai/litellm/app/helmrelease.yaml"


def push(before, after, actor=AGENT, kind="push"):
    return Activity(HEAD_REF, 1000.0, actor, before, after, kind)


def change(filename=APP, previous=""):
    return FileChange(filename, previous)


def tag_run(sha="c1", branch="main", status="completed", conclusion="success", updated=2000.0, event="schedule",
            repo=REPO):
    return WorkflowRun(sha, branch, status, conclusion, updated, event, repo)


RELEASE = """apiVersion: helm.toolkit.fluxcd.io/v2
kind: HelmRelease
metadata:
  name: litellm
spec:
  chartRef: {kind: OCIRepository, name: litellm}
  values:
    image: {tag: 1.0.0}
"""


RENOVATE_ONLY = [push(NULL_SHA, "h", RENOVATE, "branch_creation")]


class FakeInfra:
    def __init__(self, pulls=None, pushes=None, changes=None, tag_commits=None, runs=None, trusted=None, since=1000.0,
                 contents=None, statuses=None):
        self.pulls, self.changes = pulls or {}, changes or {}
        self.contents, self.statuses = contents or {}, statuses or {}
        self.pushes = RENOVATE_ONLY if pushes is None else pushes
        self.tag_commits, self.runs = tag_commits or {}, runs or {}
        self.trusted = frozenset({RENOVATE}) if trusted is None else frozenset(trusted)
        self.since = since
        self.asked = []

    def reads(self):
        return InfraReads(self.merged, self.head_pushes, self.push_changes, self.file_content, self.tag_commit,
                          self.tag_runs, self.trusted, self.since)

    def merged(self, sha):
        self.asked.append(("merged", sha))
        return list(self.pulls.get(sha, ()))

    def head_pushes(self, ref, head_sha):
        self.asked.append(("pushes", ref, head_sha))
        return None if self.pushes == "untraceable" else list(self.pushes)

    def push_changes(self, base, head):
        self.asked.append(("compare", base, head))
        if (base, head) not in self.changes:
            raise AssertionError(f"unexpected compare {base}...{head}")
        return Compare(self.statuses.get((base, head), "ahead"), list(self.changes[(base, head)]))

    def file_content(self, path, ref):
        self.asked.append(("content", path, ref))
        found = self.contents.get((path, ref))
        if isinstance(found, Content):
            return found
        return Content("absent") if found is None else Content("present", found.encode() if isinstance(found, str) else found)

    def tag_commit(self, sha):
        self.asked.append(("tag", sha))
        return self.tag_commits.get(sha)

    def tag_runs(self, commit):
        self.asked.append(("runs", commit))
        return list(self.runs.get(commit, ()))


class ViolationsTest(unittest.TestCase):
    base = {"refs/heads/main": "a", "refs/heads/feat/x": "b", "refs/tags/v1": "t"}

    def check(self, after, before=None, pull=HEAD, files=(), bundle="coder", lookup=None, agents=AGENTS, branches=(),
              releases=None):
        self.lookup = lookup or Lookup()
        self.releases = releases or Releases()
        before = self.base if before is None else before
        return violations(bundle, REPO, before, after, pull, list(files), self.lookup, self.releases, agents, branches)

    def test_head_branch_move_allowed(self):
        self.assertEqual(self.check({**self.base, "refs/heads/feat/x": "c"}), [])
        self.assertEqual(self.lookup.asked, [MAIN])

    def test_head_branch_creation_allowed(self):
        before = {k: v for k, v in self.base.items() if k != "refs/heads/feat/x"}
        self.assertEqual(self.check(self.base, before=before), [])

    def test_other_agent_branch_move_allowed(self):
        after = {**self.base, "refs/heads/polly/x": "z", "refs/heads/feat/y": "q"}
        self.assertEqual(self.check(after), [])
        self.assertEqual(self.lookup.asked, [MAIN])

    def test_owner_merge_to_main_allowed(self):
        lookup = Lookup(entry(MAIN, "lkshrk", "a", "m"))
        self.assertEqual(self.check({**self.base, MAIN: "m"}, lookup=lookup), [])
        self.assertEqual(lookup.asked, [MAIN])

    def test_owner_chain_of_pushes_allowed(self):
        lookup = Lookup(entry(MAIN, "lkshrk", "a", "m1"), entry(MAIN, "renovate[bot]", "m1", "m2"))
        self.assertEqual(self.check({**self.base, MAIN: "m2"}, lookup=lookup), [])

    def test_entries_before_the_baseline_are_skipped(self):
        lookup = Lookup(entry(MAIN, "lkshrk", "p", "a"), entry(MAIN, "lkshrk", "a", "m"))
        self.assertEqual(self.check({**self.base, MAIN: "m"}, lookup=lookup), [])

    def test_owner_entry_not_reaching_current_sha_is_unattributed(self):
        lookup = Lookup(entry(MAIN, "lkshrk", "a", "x"))
        out = self.check({**self.base, MAIN: "y"}, lookup=lookup)
        self.assertEqual(out, ["ref moved (unattributed): refs/heads/main"])

    def test_chain_gap_is_unattributed(self):
        lookup = Lookup(entry(MAIN, "lkshrk", "a", "x"), entry(MAIN, "lkshrk", "z", "m"))
        out = self.check({**self.base, MAIN: "m"}, lookup=lookup)
        self.assertEqual(out, ["ref moved (unattributed): refs/heads/main"])

    def test_chain_not_from_baseline_is_unattributed(self):
        lookup = Lookup(entry(MAIN, "lkshrk", "q", "m"))
        out = self.check({**self.base, MAIN: "m"}, lookup=lookup)
        self.assertEqual(out, ["ref moved (unattributed): refs/heads/main"])

    def test_created_and_deleted_protected_branch_chain_on_null_sha(self):
        before = {k: v for k, v in self.base.items() if k != MAIN}
        lookup = Lookup(entry(MAIN, "lkshrk", NULL_SHA, "a"))
        self.assertEqual(self.check(self.base, before=before, lookup=lookup), [])
        lookup = Lookup(entry(MAIN, "lkshrk", "a", NULL_SHA))
        self.assertEqual(self.check(before, lookup=lookup), [])
        out = self.check(before, lookup=Lookup(entry(MAIN, "lkshrk", "a", "x")))
        self.assertEqual(out, ["ref moved (unattributed): refs/heads/main"])

    def test_agent_moves_main_is_violation(self):
        lookup = Lookup(entry(MAIN, "lkshrk", "a", "x"), entry(MAIN, AGENT, "x", "m"))
        out = self.check({**self.base, MAIN: "m"}, lookup=lookup)
        self.assertEqual(out, ["ref moved by agent: refs/heads/main"])

    def test_agent_login_compare_case_insensitive(self):
        lookup = Lookup(entry(MAIN, "H-Cloud-NPA[bot]", "a", "m"))
        out = self.check({**self.base, MAIN: "m"}, lookup=lookup)
        self.assertEqual(out, ["ref moved by agent: refs/heads/main"])

    def test_agent_push_and_restore_is_violation(self):
        lookup = Lookup(entry(MAIN, AGENT, "a", "x"), entry(MAIN, AGENT, "x", "a"))
        self.assertEqual(self.check(dict(self.base), lookup=lookup), ["ref moved by agent: refs/heads/main"])

    def test_non_agent_entries_with_unchanged_sha_allowed(self):
        lookup = Lookup(entry(MAIN, "lkshrk", "a", "x"), entry(MAIN, "lkshrk", "x", "a"))
        self.assertEqual(self.check(dict(self.base), lookup=lookup), [])

    def test_null_actor_with_unchanged_sha_is_unattributed(self):
        lookup = Lookup(entry(MAIN, "", "a", "x"), entry(MAIN, "lkshrk", "x", "a"))
        self.assertEqual(self.check(dict(self.base), lookup=lookup), ["ref moved (unattributed): refs/heads/main"])

    def test_release_tag_by_non_agent_allowed(self):
        releases = Releases(v2="github-actions[bot]")
        self.assertEqual(self.check({**self.base, "refs/tags/v2": "t2"}, releases=releases), [])
        self.assertEqual((releases.asked, self.lookup.asked), (["v2"], [MAIN]))

    def test_agent_release_on_created_tag_is_violation(self):
        out = self.check({**self.base, "refs/tags/v2": "t2"}, releases=Releases(v2="H-Cloud-NPA[bot]"))
        self.assertEqual(out, ["ref moved by agent: refs/tags/v2"])

    def test_moved_tag_is_violation_despite_release(self):
        releases = Releases(v1="github-actions[bot]")
        for agents in (AGENTS, frozenset()):
            with self.subTest(agents=agents):
                out = self.check({**self.base, "refs/tags/v1": "t2"}, releases=releases, agents=agents)
                self.assertEqual(out, ["tag moved: refs/tags/v1"])
        self.assertEqual(releases.asked, [])

    def test_tag_without_release_is_unattributed(self):
        lookup = Lookup(entry("refs/tags/2026.11.0", "github-actions[bot]", NULL_SHA, "t2"))
        out = self.check({**self.base, "refs/tags/2026.11.0": "t2"}, lookup=lookup)
        self.assertEqual(out, ["ref moved (unattributed): refs/tags/2026.11.0"])
        self.assertEqual((self.releases.asked, lookup.asked), (["2026.11.0"], [MAIN]))

    def test_non_infra_ignores_infra_reads_for_tags(self):
        reads = FakeInfra(tag_commits={"t2": "c1"}, runs={"c1": [tag_run()]})
        for bundle in ("polly", "documenter", "planner"):
            self.lookup = Lookup()
            out = violations(bundle, REPO, self.base, {**self.base, "refs/tags/2026.11.0": "t2"}, HEAD, [], self.lookup,
                             Releases(), AGENTS, (), infra=reads.reads(), default="main")
            self.assertEqual(out, ["ref moved (unattributed): refs/tags/2026.11.0"], bundle)
        self.assertEqual((reads.asked, self.lookup.asked), ([], [MAIN]))

    def test_release_without_author_is_unattributed(self):
        out = self.check({**self.base, "refs/tags/v2": "t2"}, releases=Releases(v2=""))
        self.assertEqual(out, ["ref moved (unattributed): refs/tags/v2"])

    def test_deleted_tag_is_unattributed(self):
        releases = Releases(v1="github-actions[bot]")
        out = self.check({k: v for k, v in self.base.items() if k != "refs/tags/v1"}, releases=releases)
        self.assertEqual(out, ["ref moved (unattributed): refs/tags/v1"])
        self.assertEqual(releases.asked, [])

    def test_nested_tag_name_passed_to_release_lookup(self):
        releases = Releases(**{"release/1.0": "github-actions[bot]"})
        self.assertEqual(self.check({**self.base, "refs/tags/release/1.0": "r"}, releases=releases), [])
        self.assertEqual(releases.asked, ["release/1.0"])

    def test_protected_move_without_activity_is_unattributed(self):
        after = {k: v for k, v in self.base.items() if k != "refs/heads/main"} | {"refs/tags/v1": "t2"}
        self.assertEqual(
            self.check(after), ["ref moved (unattributed): refs/heads/main", "tag moved: refs/tags/v1"]
        )

    def test_null_actor_is_unattributed(self):
        lookup = Lookup(entry(MAIN, "", "a", "m"))
        out = self.check({**self.base, MAIN: "m"}, lookup=lookup)
        self.assertEqual(out, ["ref moved (unattributed): refs/heads/main"])

    def test_base_branch_is_protected(self):
        pull = PullHead("feat/x", "acme/app", "release", "acme/app", "main")
        before = {**self.base, "refs/heads/release": "r"}
        out = self.check({**before, "refs/heads/release": "r2"}, before=before, pull=pull)
        self.assertEqual(out, ["ref moved (unattributed): refs/heads/release"])
        self.assertEqual(self.lookup.asked, [MAIN, "refs/heads/release"])

    def test_given_branches_are_protected_with_pr(self):
        before = {**self.base, "refs/heads/release/1.x": "r"}
        out = self.check({**before, "refs/heads/release/1.x": "r2"}, before=before, branches=("release/1.x",))
        self.assertEqual(out, ["ref moved (unattributed): refs/heads/release/1.x"])

    def test_no_agent_actors_protected_move_is_violation(self):
        lookup = Lookup(entry(MAIN, "lkshrk", "a", "m"))
        after = {**self.base, MAIN: "m", "refs/tags/v2": "t2"}
        out = self.check(after, lookup=lookup, agents=frozenset(), releases=Releases(v2="github-actions[bot]"))
        self.assertEqual(out, ["ref moved: refs/heads/main", "ref moved: refs/tags/v2"])
        self.assertEqual((lookup.asked, self.releases.asked), ([], []))

    def test_no_pr_protects_given_branches(self):
        after = {**self.base, MAIN: "m", "refs/heads/feat/x": "c"}
        out = self.check(after, pull=None, branches=("main",))
        self.assertEqual(out, ["ref moved (unattributed): refs/heads/main"])

    def test_no_change_no_pr_passes(self):
        self.assertEqual(self.check(dict(self.base), pull=None, branches=("main",)), [])
        self.assertEqual(self.lookup.asked, [MAIN])

    def test_fork_head_is_violation(self):
        pull = PullHead("feat/x", "evil/app", "main", "acme/app", "main")
        self.assertEqual(self.check(self.base, pull=pull), [f"PR head is not in {REPO}"])

    def test_deleted_fork_head_is_violation(self):
        pull = PullHead("feat/x", "", "main", "acme/app", "main")
        self.assertEqual(self.check(self.base, pull=pull), [f"PR head is not in {REPO}"])

    def test_repo_compare_case_insensitive(self):
        pull = PullHead("feat/x", "Acme/App", "main", "ACME/app", "main")
        self.assertEqual(self.check(self.base, pull=pull), [])

    def test_protected_head_branch_is_violation(self):
        pull = PullHead("main", "acme/app", "release", "acme/app", "main")
        lookup = Lookup(entry(MAIN, AGENT, "a", "z"))
        self.assertEqual(
            self.check({**self.base, "refs/heads/main": "z"}, pull=pull, lookup=lookup),
            ["ref moved by agent: refs/heads/main", "PR head is a protected branch: main"],
        )

    def test_head_equal_base_is_violation(self):
        pull = PullHead("dev", "acme/app", "dev", "acme/app", "main")
        before = {**self.base, "refs/heads/dev": "1"}
        self.assertEqual(
            self.check({**before, "refs/heads/dev": "2"}, before=before, pull=pull),
            ["ref moved (unattributed): refs/heads/dev", "PR head is a protected branch: dev"],
        )

    def test_base_repo_elsewhere_is_violation(self):
        pull = PullHead("feat/x", "acme/app", "main", "other/app", "main")
        self.assertEqual(self.check(self.base, pull=pull), [f"PR is not in {REPO}"])

    def test_documenter_code_file_is_violation(self):
        out = self.check(self.base, bundle="documenter", files=["README.md", "src/app.py"])
        self.assertEqual(out, ["non-doc file: src/app.py"])

    def test_documenter_docs_only_passes(self):
        self.assertEqual(self.check(self.base, bundle="documenter", files=["README.md", "docs/a.md"]), [])

    def test_non_documenter_ignores_files(self):
        self.assertEqual(self.check(self.base, files=["src/app.py"]), [])


class InfraViolationsTest(unittest.TestCase):
    base = {MAIN: "a", "refs/heads/renovate/x-1.x": "r"}
    PR7 = "https://github.com/acme/app/pull/7"

    def check(self, after, entries, fake=None, bundle="infra", agents=AGENTS, pulls=None, default="main"):
        self.fake = fake or FakeInfra()
        self.observed = []
        lookup = Lookup(*entries)
        return violations(bundle, REPO, self.base, after, None, [], lookup, Releases(), agents, ("main",),
                          infra=self.fake.reads(), default=default, pulls=pulls or {}, observed=self.observed)

    def landed_ok(self):
        return FakeInfra(pulls={"m": [renovate_pull()]})

    def assert_agent_violation(self, out, contains=""):
        self.assertEqual(len(out), 1, out)
        self.assertTrue(out[0].startswith("ref moved by agent: refs/heads/main ("), out)
        self.assertIn(contains, out[0])

    def agent_push(self, changes, actor=AGENT, kind="push", trusted=None, contents=None, status="ahead"):
        pushes = [push(NULL_SHA, "r1", RENOVATE, "branch_creation"), push("r1", "h", actor, kind)]
        base = "b" if kind == "force_push" else "r1"
        return FakeInfra(pulls={"m": [renovate_pull()]}, pushes=pushes, changes={(base, "h"): changes}, trusted=trusted,
                         contents={(path, base if ref == "base" else ref): text for (path, ref), text in (contents or {}).items()},
                         statuses={(base, "h"): status})

    def landed(self, fake):
        return self.check({MAIN: "m"}, [merge("a", "m")], fake)

    def test_agent_landed_renovate_pr_allowed_and_observed(self):
        self.assertEqual(self.landed(self.landed_ok()), [])
        self.assertEqual(self.fake.asked, [("merged", "m"), ("pushes", "renovate/x-1.x", "h")])
        self.assertEqual(self.observed, [{"pr": self.PR7, "merge_commit_sha": "m", "agent_pushes": []}])

    def test_chain_of_agent_merges_and_owner_push_allowed(self):
        entries = [merge("a", "m1"), entry(MAIN, "lkshrk", "m1", "o"), merge("o", "m2")]
        fake = FakeInfra(pulls={"m1": [renovate_pull("m1", number=1)],
                                "m2": [renovate_pull("m2", number=2, merged_by="H-Cloud-NPA[bot]")]})
        self.assertEqual(self.check({MAIN: "m2"}, entries, fake), [])
        self.assertEqual([o["pr"] for o in self.observed],
                         ["https://github.com/acme/app/pull/1", "https://github.com/acme/app/pull/2"])

    def test_agent_push_is_violation(self):
        for kind in ("push", "force_push", "merge_queue_merge", ""):
            out = self.check({MAIN: "m"}, [merge("a", "m", kind=kind)], self.landed_ok())
            self.assert_agent_violation(out)
            self.assertEqual(self.fake.asked, [])

    def test_merge_not_matching_rules_is_violation(self):
        bad = {
            "unmerged": renovate_pull(merged=False),
            "other merge commit": renovate_pull(merge_commit_sha="x"),
            "other base": renovate_pull(base_ref="release"),
            "fork head": renovate_pull(head_repo="evil/app"),
            "deleted fork": renovate_pull(head_repo=""),
            "other base repo": renovate_pull(base_repo="other/app"),
            "non-renovate head": renovate_pull(head_ref="feat/renovate/x"),
            "human author": renovate_pull(author="lkshrk"),
            "other renovate": renovate_pull(author="renovate[bot]"),
            "merged by owner": renovate_pull(merged_by="lkshrk"),
            "merged by nobody": renovate_pull(merged_by=""),
        }
        for name, pull in bad.items():
            with self.subTest(name):
                self.assert_agent_violation(self.landed(FakeInfra(pulls={"m": [pull]})))
                self.assertEqual(self.observed, [])

    def test_not_exactly_one_pr_is_violation(self):
        for pulls in ([], [renovate_pull(), renovate_pull()]):
            self.assert_agent_violation(self.landed(FakeInfra(pulls={"m": pulls})))

    def test_bad_second_agent_entry_after_a_good_one_is_violation(self):
        entries = [merge("a", "m1"), merge("m1", "m2", kind="push")]
        fake = FakeInfra(pulls={"m1": [renovate_pull("m1")], "m2": [renovate_pull("m2")]})
        self.assert_agent_violation(self.check({MAIN: "m2"}, entries, fake), "push activity")
        entries = [merge("a", "m1"), merge("m1", "m2")]
        fake = FakeInfra(pulls={"m1": [renovate_pull("m1")], "m2": [renovate_pull("m2", author="lkshrk")]})
        self.assert_agent_violation(self.check({MAIN: "m2"}, entries, fake), "author")

    def test_too_many_agent_merges_is_violation(self):
        entries = [merge(f"s{i}", f"s{i + 1}") for i in range(MAX_AGENT_MERGES + 1)]
        base = {MAIN: "s0"}
        out = violations("infra", REPO, base, {MAIN: f"s{MAX_AGENT_MERGES + 1}"}, None, [], Lookup(*entries), Releases(),
                         AGENTS, ("main",), infra=FakeInfra().reads(), default="main")
        self.assertEqual(out, [f"ref moved by agent: refs/heads/main (more than {MAX_AGENT_MERGES} agent merges)"])

    def test_agent_push_inside_an_app_passes_and_is_reported(self):
        fake = self.agent_push([change()])
        self.assertEqual(self.landed(fake), [])
        self.assertEqual(fake.asked, [("merged", "m"), ("pushes", "renovate/x-1.x", "h"), ("compare", "r1", "h"),
                                      ("content", APP, "r1"), ("content", APP, "h")])
        self.assertEqual(self.observed[0]["agent_pushes"], [{"before": "r1", "after": "h", "files": [APP]}])

    def test_never_paths_are_violations(self):
        never = (
            "kubernetes/apps/security/kyverno/policies/agent-github-tokens/mutatingpolicy.yaml",
            "kubernetes/apps/omnigent-sandboxes/runners/app/runner-prepull.yaml",
            "kubernetes/apps/ai/hermes/app/helmrelease.yaml",
            "kubernetes/apps/ai/contribution-approver/app/helmrelease.yaml",
            "kubernetes/apps/actions-runner-system/arc/app/helmrelease.yaml",
            "kubernetes/apps/flux-system/flux-operator/app/helmrelease.yaml",
            "kubernetes/apps/ai/omnigent/app/helmrelease.yaml",
            "kubernetes/apps/system-upgrade/tuppr/app/plan.yaml",
            "kubernetes/apps/kube-system/cilium/app/helm/values.yaml",
            "kubernetes/apps/ai/litellm/ks.yaml",
            "kubernetes/apps/ai/litellm/app/ks.yaml",
            "kubernetes/apps/ai/litellm/app/secret.sops.yaml",
            "kubernetes/flux/cluster/ks.yaml",
            ".github/workflows/tag.yaml",
            "talos/talconfig.yaml",
        )
        self.assertEqual(len(PROTECTED_PATHS), 14)
        for path in never:
            with self.subTest(path):
                out = self.landed(self.agent_push([change(), change(path)]))
                self.assert_agent_violation(out, f"{self.PR7}: agent push r1..h changes {path} outside the allowed app paths")
                self.assertEqual(self.observed, [])

    def test_paths_outside_an_app_dir_are_violations(self):
        for path in ("kubernetes/apps/ai/kustomization.yaml", "kubernetes/apps/ai/namespace.yaml",
                     "kubernetes/apps/kustomization.yaml", "kubernetes/components/common/x.yaml", "README.md",
                     "renovate.json5", "kubernetes/apps/ai//x.yaml", "kubernetes/apps/ai/../security/x/y.yaml"):
            out = self.landed(self.agent_push([change(path)]))
            self.assert_agent_violation(out, "outside the allowed app paths")

    def test_paths_inside_other_apps_pass(self):
        for path in ("kubernetes/apps/securityx/a/app.yaml", "kubernetes/apps/ai/hermes-webui/app/x.yaml",
                     "kubernetes/apps/ai/omnigent-x/app/a.yaml", "kubernetes/apps/ai/litellm/app/notks.yaml",
                     "kubernetes/apps/ai/litellm/app/ks.yaml.md"):
            self.assertEqual(self.landed(self.agent_push([change(path)])), [], path)

    def test_rename_into_or_out_of_disallowed_path_is_violation(self):
        for filename, previous in ((APP, "talos/x.yaml"), ("docs/x.yaml", APP)):
            out = self.landed(self.agent_push([change(filename, previous=previous)]))
            self.assert_agent_violation(out, "outside the allowed app paths")

    def test_content_is_checked_by_the_manifest_checker(self):
        bumped = RELEASE.replace("tag: 1.0.0", "tag: 1.0.1")
        fake = self.agent_push([change()], contents={(APP, "base"): RELEASE, (APP, "h"): bumped})
        self.assertEqual(self.landed(fake), [])
        role = "{apiVersion: rbac.authorization.k8s.io/v1, kind: ClusterRole, metadata: {name: x}, rules: []}"
        fake = self.agent_push([change()], contents={(APP, "base"): RELEASE, (APP, "h"): RELEASE + "---\n" + role})
        self.assert_agent_violation(self.landed(fake), "ClusterRole x: adds a rbac.authorization.k8s.io/v1 ClusterRole")
        moved = RELEASE.replace("name: litellm}", "name: evil}")
        fake = self.agent_push([change()], contents={(APP, "base"): RELEASE, (APP, "h"): moved})
        self.assert_agent_violation(self.landed(fake), "changes spec.chartRef")

    def test_renamed_file_is_compared_with_its_old_path(self):
        old = "kubernetes/apps/ai/litellm/app/old.yaml"
        fake = self.agent_push([change(previous=old)], contents={(old, "base"): RELEASE, (APP, "h"): RELEASE})
        self.assertEqual(self.landed(fake), [])
        self.assertIn(("content", old, "r1"), fake.asked)

    def test_rename_across_directories_counts_as_added(self):
        old = "kubernetes/apps/ai/other/app/helmrelease.yaml"
        fake = self.agent_push([change(previous=old)], contents={(old, "base"): RELEASE, (APP, "h"): RELEASE})
        self.assert_agent_violation(self.landed(fake), "changes spec.chartRef")

    def test_unreadable_content_is_violation(self):
        for state in ("larger than 1 MB", "not a file"):
            fake = self.agent_push([change()], contents={(APP, "h"): Content(state)})
            self.assert_agent_violation(self.landed(fake), f"{APP} at h is {state}")

    def test_too_many_files_in_a_push_is_violation(self):
        many = [change(f"kubernetes/apps/ai/litellm/app/f{i}.yaml") for i in range(MAX_PUSH_FILES + 1)]
        fake = self.agent_push(many)
        self.assert_agent_violation(self.landed(fake), f"changes more than {MAX_PUSH_FILES} files")
        self.assertNotIn("content", [a[0] for a in fake.asked])

    def test_rewritten_push_must_be_ahead_of_the_pr_base(self):
        for status in ("behind", "identical"):
            fake = self.agent_push([change()], kind="force_push", status=status)
            self.assert_agent_violation(self.landed(fake), f"is not ahead of the PR base ({status})")
        self.assertEqual(self.landed(self.agent_push([change()], kind="force_push", status="diverged")), [])
        self.assertEqual(self.landed(self.agent_push([change()], status="behind")), [])

    def test_null_actor_and_unlisted_actors_are_scanned(self):
        for actor in ("", "github-actions[bot]", "lkshrk"):
            with self.subTest(actor):
                out = self.landed(self.agent_push([change(".github/x.yaml")], actor=actor))
                self.assert_agent_violation(out, "push r1..h changes .github/x.yaml")

    def test_listed_owner_push_is_trusted(self):
        fake = self.agent_push([change(".github/x.yaml")], actor="LKSHRK", trusted={RENOVATE, "lkshrk"})
        self.assertEqual(self.landed(fake), [])
        self.assertNotIn("compare", [a[0] for a in fake.asked])

    def test_an_agent_listed_as_trusted_is_still_scanned(self):
        fake = self.agent_push([change(".github/x.yaml")], trusted={RENOVATE, AGENT})
        self.assert_agent_violation(self.landed(fake), "push r1..h changes .github/x.yaml")

    def test_agent_force_push_compares_from_the_base(self):
        fake = self.agent_push([change("talos/x.yaml")], kind="force_push")
        self.assert_agent_violation(self.landed(fake), "push r1..h")
        self.assertIn(("compare", "b", "h"), fake.asked)

    def test_agent_branch_creation_compares_from_the_base(self):
        pushes = [push(NULL_SHA, "h", AGENT, "branch_creation")]
        fake = FakeInfra(pulls={"m": [renovate_pull()]}, pushes=pushes, changes={("b", "h"): [change()]})
        self.assertEqual(self.landed(fake), [])
        self.assertEqual(self.observed[0]["agent_pushes"], [{"before": NULL_SHA, "after": "h", "files": [APP]}])

    def test_renovate_pushes_are_trusted(self):
        pushes = [push(NULL_SHA, "r1", RENOVATE, "branch_creation"), push("r1", "h", "Renovate-Master[bot]", "force_push")]
        fake = FakeInfra(pulls={"m": [renovate_pull()]}, pushes=pushes)
        self.assertEqual(self.landed(fake), [])
        self.assertNotIn("compare", [a[0] for a in fake.asked])

    def test_too_many_untrusted_pushes_is_violation(self):
        pushes = [push(NULL_SHA, "p0", RENOVATE, "branch_creation")]
        pushes += [push(f"p{i}", f"p{i + 1}") for i in range(MAX_UNTRUSTED_PUSHES)]
        pushes.append(push(f"p{MAX_UNTRUSTED_PUSHES}", "h"))
        fake = FakeInfra(pulls={"m": [renovate_pull()]}, pushes=pushes)
        self.assert_agent_violation(self.landed(fake), f"more than {MAX_UNTRUSTED_PUSHES} untrusted pushes")
        self.assertNotIn("compare", [a[0] for a in fake.asked])

    def test_untraceable_head_history_is_violation(self):
        fake = FakeInfra(pulls={"m": [renovate_pull()]}, pushes="untraceable")
        self.assert_agent_violation(self.landed(fake), "head branch history")

    def test_head_history_with_a_gap_is_violation(self):
        gaps = (
            [push(NULL_SHA, "r1", RENOVATE, "branch_creation"), push("x", "h", RENOVATE)],
            [push("r0", "r1", RENOVATE), push("r1", "h", RENOVATE)],
            [push(NULL_SHA, "r1", RENOVATE, "branch_creation"), push("r1", "r2", RENOVATE)],
            [],
        )
        for pushes in gaps:
            fake = FakeInfra(pulls={"m": [renovate_pull()]}, pushes=pushes)
            self.assert_agent_violation(self.landed(fake), "head branch history of renovate/x-1.x has a gap")
            self.assertNotIn("compare", [a[0] for a in fake.asked])

    def test_broken_chain_after_landed_merge_is_unattributed(self):
        out = self.check({MAIN: "z"}, [merge("a", "m")], self.landed_ok())
        self.assertEqual(out, ["ref moved (unattributed): refs/heads/main"])

    def test_case_insensitive_repo_and_logins(self):
        pull = renovate_pull(base_repo="ACME/app", head_repo="Acme/App", author="Renovate-Master[bot]")
        self.assertEqual(self.check({MAIN: "m"}, [merge("a", "m")], FakeInfra(pulls={"m": [pull]})), [])

    def test_other_protected_branch_still_violation(self):
        base = {**self.base, "refs/heads/release": "q"}
        lookup = Lookup(Activity("refs/heads/release", 1000.0, AGENT, "q", "m", "pr_merge"))
        fake = FakeInfra(pulls={"m": [renovate_pull(base_ref="release")]})
        out = violations("infra", REPO, base, {**base, "refs/heads/release": "m"}, None, [], lookup, Releases(), AGENTS,
                         ("main", "release"), infra=fake.reads(), default="main", pulls={})
        self.assertEqual(out, ["ref moved by agent: refs/heads/release"])
        self.assertEqual(fake.asked, [])

    def test_other_bundles_keep_the_plain_rule(self):
        for bundle in ("polly", "documenter"):
            out = self.check({MAIN: "m"}, [merge("a", "m")], self.landed_ok(), bundle=bundle)
            self.assertEqual(out, ["ref moved by agent: refs/heads/main"], bundle)
            self.assertEqual(self.fake.asked, [])

    def test_missing_reads_or_default_fall_back_to_the_plain_rule(self):
        for infra, default in ((None, "main"), (self.landed_ok().reads(), None)):
            out = violations("infra", REPO, self.base, {MAIN: "m"}, None, [], Lookup(merge("a", "m")), Releases(),
                             AGENTS, ("main",), infra=infra, default=default)
            self.assertEqual(out, ["ref moved by agent: refs/heads/main"])

    def test_no_agent_actors_is_violation(self):
        out = self.check({MAIN: "m"}, [merge("a", "m")], self.landed_ok(), agents=frozenset())
        self.assertEqual(out, ["ref moved: refs/heads/main"])

    def test_every_result_pr_is_checked(self):
        pulls = {
            "https://github.com/acme/app/pull/1": PullHead("renovate/a", REPO, "main", REPO, "main"),
            "https://github.com/acme/app/pull/2": PullHead("renovate/b", "evil/app", "main", REPO, "main"),
            "https://github.com/acme/app/pull/3": PullHead("main", REPO, "main", REPO, "main"),
            "https://github.com/other/app/pull/4": PullHead("renovate/c", "other/app", "main", "other/app", "main"),
        }
        out = self.check(dict(self.base), [], pulls=pulls)
        self.assertEqual(out, [
            f"https://github.com/acme/app/pull/2: PR head is not in {REPO}",
            "https://github.com/acme/app/pull/3: PR head is a protected branch: main",
            f"https://github.com/other/app/pull/4: PR head is not in {REPO}",
            f"https://github.com/other/app/pull/4: PR is not in {REPO}",
        ])


class InfraTagTest(unittest.TestCase):
    base = {MAIN: "a"}
    TAG = "refs/tags/2026.11.0"

    def check(self, name="2026.11.0", sha="t2", fake=None, entries=(), releases=None):
        self.fake = fake or FakeInfra()
        self.lookup = Lookup(*entries)
        return violations("infra", REPO, self.base, {**self.base, f"refs/tags/{name}": sha}, None, [], self.lookup,
                          releases or Releases(), AGENTS, ("main",), infra=self.fake.reads(), default="main")

    def workflow(self, **run):
        return FakeInfra(tag_commits={"t2": "c1"}, runs={"c1": [tag_run(**run)]})

    def test_monthly_tag_from_a_tag_workflow_run_passes(self):
        for name, event in (("2026.11.0", "schedule"), ("2026.1.0", "workflow_dispatch"), ("2027.12.14", "schedule")):
            fake = self.workflow(event=event)
            self.assertEqual(self.check(name, fake=fake), [], name)
            self.assertEqual(fake.asked, [("tag", "t2"), ("runs", "c1")])

    def test_recorded_annotated_tag_and_workflow_run(self):
        [ref] = recorded("matching_refs_tag_h-cloud_2026.11.0.json")
        tag = recorded("tag_h-cloud_2026.11.0.json")
        tag_sha, commit = tag["sha"], tag["object"]["sha"]
        self.assertEqual((ref["object"]["type"], ref["object"]["sha"]), ("tag", tag_sha))
        [run] = recorded("tag_runs_h-cloud_2026.11.0.json")["workflow_runs"]
        self.assertEqual((run["head_sha"], run["path"]), (commit, ".github/workflows/tag.yaml"))
        fake = FakeInfra(tag_commits={tag_sha: commit}, runs={commit: [tag_run(commit, updated=1500.0)]})
        self.assertEqual(self.check(sha=tag_sha, fake=fake), [])
        self.assertEqual(fake.asked, [("tag", tag_sha), ("runs", commit)])
        self.assertEqual(self.check(sha=tag_sha, entries=[entry(self.TAG, "x[bot]", NULL_SHA, tag_sha)]), [])
        out = self.check(sha=tag_sha, entries=[entry(self.TAG, "x[bot]", NULL_SHA, commit)])
        self.assertEqual(out, [f"ref moved (unattributed): {self.TAG}"])

    def test_lightweight_tag_uses_the_ref_sha_as_commit(self):
        fake = FakeInfra(runs={"t2": [tag_run("t2")]})
        self.assertEqual(self.check(fake=fake), [])
        self.assertEqual(fake.asked, [("tag", "t2"), ("runs", "t2")])

    def test_runs_that_do_not_prove_the_tag_are_unattributed(self):
        for run in ({"sha": "other"}, {"branch": "renovate/x"}, {"status": "in_progress"}, {"conclusion": "failure"},
                    {"conclusion": ""}, {"updated": 999.0}, {"event": "push"}, {"event": "pull_request"},
                    {"repo": "evil/app"}):
            with self.subTest(run):
                self.assertEqual(self.check(fake=self.workflow(**run)), [f"ref moved (unattributed): {self.TAG}"])
        self.assertEqual(self.check(fake=FakeInfra(tag_commits={"t2": "c1"})), [f"ref moved (unattributed): {self.TAG}"])

    def test_nested_tag_object_is_unattributed(self):
        fake = FakeInfra(tag_commits={"t2": ""}, runs={"t2": [tag_run("t2")]})
        self.assertEqual(self.check(fake=fake), [f"ref moved (unattributed): {self.TAG}"])

    def test_other_tag_names_are_unattributed(self):
        for name in ("v1.2.3", "2026.11", "2026.111.0", "26.11.0", "2026.11.0-rc1", "２０２６.11.0", "2026.1١.0",
                     "2026.0.0", "2026.13.0", "2026.00.1", "2026.01.0"):
            fake = self.workflow()
            self.assertEqual(self.check(name, fake=fake), [f"ref moved (unattributed): refs/tags/{name}"], name)
            self.assertEqual(fake.asked, [])

    def test_activity_attributes_first(self):
        self.assertEqual(self.check(entries=[entry(self.TAG, "github-actions[bot]", NULL_SHA, "t2")]), [])
        self.assertEqual(self.fake.asked, [])
        out = self.check(entries=[entry(self.TAG, AGENT, NULL_SHA, "t2")], fake=self.workflow())
        self.assertEqual(out, [f"ref moved by agent: {self.TAG}"])
        for actor in ("github-actions[bot]", ""):
            out = self.check(entries=[entry(self.TAG, actor, NULL_SHA, "t9")], fake=self.workflow())
            self.assertEqual(out, [f"ref moved (unattributed): {self.TAG}"], actor)

    def test_release_author_still_wins(self):
        fake = self.workflow()
        self.assertEqual(self.check(fake=fake, releases=Releases(**{"2026.11.0": "lkshrk"})), [])
        self.assertEqual(fake.asked, [])

    def test_moved_tag_is_violation(self):
        before = {**self.base, self.TAG: "t1"}
        fake = self.workflow()
        out = violations("infra", REPO, before, {**before, self.TAG: "t2"}, None, [], Lookup(), Releases(), AGENTS,
                         ("main",), infra=fake.reads(), default="main")
        self.assertEqual(out, [f"tag moved: {self.TAG}"])
        self.assertEqual(fake.asked, [])


class TokenFilesTest(unittest.TestCase):
    def test_parsed(self):
        value = " routivo=/var/run/agent/github/review, LKSHRK=/var/run/agent/github/hcloud-ro ,"
        expected = {"routivo": "/var/run/agent/github/review", "lkshrk": "/var/run/agent/github/hcloud-ro"}
        self.assertEqual(token_files(value), expected)
        for value in (None, "", " , "):
            self.assertEqual(token_files(value), {}, value)

    def test_malformed_raises(self):
        for value in ("routivo", "=/x", "routivo=", "a=/x,A=/y", "a b=/x"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                token_files(value)

    def test_malformed_entry_is_named_by_index_only(self):
        with self.assertRaises(ValueError) as cm:
            token_files("routivo=/var/run/a, s3cr3t-looking-value ")
        self.assertIn("entry 2", str(cm.exception))
        self.assertNotIn("s3cr3t", str(cm.exception))
        with self.assertRaises(ValueError) as cm:
            token_files("Lkshrk=/x,lkshrk=/y")
        self.assertIn("entry 2", str(cm.exception))
        self.assertNotIn("/y", str(cm.exception))


class MultiOwnerTokenTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.files = {}
        for name in ("fallback", "review", "hcloud"):
            path = os.path.join(tmp.name, name)
            with open(path, "w") as fh:
                fh.write(f"tok-{name}\n")
            self.files[name] = path

    def reader(self, responses, fallback=True):
        self.opener = FakeOpener(responses)
        owners = {"routivo": self.files["review"], "lkshrk": self.files["hcloud"]}
        return GitHubReader(self.files["fallback"] if fallback else None, opener=self.opener, token_files=owners)

    def auth(self, i=0):
        return self.opener.requests[i].get_header("Authorization")

    def test_token_chosen_by_owner(self):
        r = self.reader([jr({"default_branch": "main"}) for _ in range(3)])
        for repo, token in (("lkshrk/h-cloud", "tok-hcloud"), ("Routivo/app", "tok-review"), ("acme/app", "tok-fallback")):
            r.default_branch(repo)
            self.assertEqual(self.auth(-1), f"Bearer {token}", repo)

    def test_pull_and_pages_use_the_owner_token(self):
        nxt = f'<{API}/repositories/1/git/matching-refs/heads?page=2>; rel="next"'
        body = {"head": {"ref": "renovate/x", "repo": {"full_name": "lkshrk/h-cloud"}},
                "base": {"ref": "main", "repo": {"full_name": "lkshrk/h-cloud", "default_branch": "main"}}}
        r = self.reader([jr([], {"Link": nxt}), jr([]), jr([]), jr(body)])
        r.refs("lkshrk/h-cloud")
        r.pull("https://github.com/lkshrk/h-cloud/pull/3")
        self.assertEqual({q.get_header("Authorization") for q in self.opener.requests}, {"Bearer tok-hcloud"})

    def test_unlisted_owner_without_fallback_raises_without_retry(self):
        r = self.reader([jr({"default_branch": "main"})], fallback=False)
        with self.assertRaises(GitHubError) as cm:
            r.default_branch("acme/app")
        self.assertNotIn(cm.exception.status, (0, 429))
        self.assertLess(cm.exception.status, 500)
        self.assertFalse(cm.exception.malformed)
        self.assertIn("acme", str(cm.exception))
        self.assertEqual(self.opener.requests, [])
        r.default_branch("lkshrk/h-cloud")
        self.assertEqual(self.auth(), "Bearer tok-hcloud")

    def test_errors_never_carry_the_token(self):
        err = urllib.error.HTTPError(API, 401, "bad", {}, io.BytesIO(b"Bad credentials"))
        with self.assertRaises(GitHubError) as cm:
            self.reader([err]).default_branch("lkshrk/h-cloud")
        self.assertNotIn("tok-", str(cm.exception))


class AgentActorsTest(unittest.TestCase):
    def test_parsed(self):
        self.assertEqual(agent_actors(" a[bot], ,B "), frozenset({"a[bot]", "b"}))

    def test_empty_or_missing(self):
        for value in (None, "", " , "):
            with self.subTest(value=value):
                self.assertEqual(agent_actors(value), frozenset())


class RefsTest(ReaderCase):
    def test_refs_paginates(self):
        nxt = f'<{API}/repositories/1/git/matching-refs/heads?page=2>; rel="next", <{API}/x>; rel="last"'
        r = self.reader(
            [
                jr([{"ref": "refs/heads/a", "object": {"sha": "1"}}], {"Link": nxt}),
                jr([{"ref": "refs/heads/b", "object": {"sha": "3"}}]),
                jr([{"ref": "refs/tags/v1", "object": {"sha": "2"}}]),
            ]
        )
        self.assertEqual(r.refs(REPO), {"refs/heads/a": "1", "refs/heads/b": "3", "refs/tags/v1": "2"})
        urls = [q.full_url for q in self.opener.requests]
        self.assertIn("/repos/acme/app/git/matching-refs/heads", urls[0])
        self.assertIn("page=2", urls[1])
        self.assertIn("/repos/acme/app/git/matching-refs/tags", urls[2])

    def test_only_heads_and_tags_returned(self):
        r = self.reader(
            [
                jr([{"ref": "refs/heads/a", "object": {"sha": "1"}}, {"ref": "refs/heads-x/y", "object": {"sha": "9"}}]),
                jr([{"ref": "refs/tags/v1", "object": {"sha": "2"}}, {"ref": "refs/tags/v2", "object": {"sha": "3"}}]),
            ]
        )
        self.assertEqual(set(r.refs(REPO)), {"refs/heads/a", "refs/tags/v1", "refs/tags/v2"})

    def test_request_headers_and_timeout(self):
        r = self.reader([jr([]), jr([])])
        r.refs(REPO)
        req = self.opener.requests[0]
        self.assertEqual(req.get_header("Authorization"), "Bearer tok1")
        self.assertEqual(req.get_header("Accept"), "application/vnd.github+json")
        self.assertEqual(req.get_header("X-github-api-version"), "2022-11-28")
        self.assertTrue(self.opener.timeouts[0])

    def test_token_file_reread(self):
        r = self.reader([jr([]), jr([]), jr([]), jr([])])
        r.refs(REPO)
        with open(self.token_file, "w") as fh:
            fh.write("tok2")
        r.refs(REPO)
        self.assertEqual(self.opener.requests[2].get_header("Authorization"), "Bearer tok2")

    def test_empty_token_raises(self):
        open(self.token_file, "w").close()
        with self.assertRaises(GitHubError):
            self.reader([jr([])]).refs(REPO)

    def test_missing_token_raises(self):
        r = GitHubReader(self.token_file + ".nope", opener=FakeOpener([jr([])]))
        with self.assertRaises(GitHubError):
            r.refs(REPO)

    def test_next_link_off_host_raises(self):
        link = '<https://evil.test/x>; rel="next"'
        with self.assertRaises(GitHubError):
            self.reader([jr([], {"Link": link})]).refs(REPO)

    def test_http_error_raises_with_status(self):
        err = urllib.error.HTTPError(API, 403, "no", {}, io.BytesIO(b"denied"))
        with self.assertRaises(GitHubError) as cm:
            self.reader([err]).refs(REPO)
        self.assertEqual(cm.exception.status, 403)

    def test_transport_error_raises(self):
        with self.assertRaises(GitHubError):
            self.reader([OSError("boom")]).refs(REPO)

    def test_non_json_raises(self):
        with self.assertRaises(GitHubError):
            self.reader([FakeResponse(b"<html>")]).refs(REPO)

    def test_missing_status_raises(self):
        resp = FakeResponse(b"[]")
        del resp.status
        with self.assertRaises(GitHubError):
            self.reader([resp]).refs(REPO)

    def test_wrong_shape_raises(self):
        for body in ({"a": 1}, [{"ref": "refs/heads/a"}], [{"ref": "refs/heads/a", "object": {}}], ["x"]):
            with self.subTest(body=body), self.assertRaises(GitHubError):
                self.reader([jr(body)]).refs(REPO)


class ActivityTest(ReaderCase):
    SINCE = 1_700_000_000.0

    def item(self, ts, login="lkshrk", ref="refs/heads/main", before="a", after="b"):
        return {"id": 1, "node_id": "n", "before": before, "after": after, "ref": ref, "timestamp": ts,
                "activity_type": "push", "actor": None if login is None else {"login": login}}

    def test_entries_in_window_returned_oldest_first(self):
        nxt = f'<{API}/repositories/1/activity?ref=refs%2Fheads%2Fmain&per_page=100&after=c1>; rel="next"'
        r = self.reader(
            [
                jr([self.item("2023-11-14T22:20:00Z", "h-cloud-npa[bot]", before="b", after="c")], {"Link": nxt}),
                jr([self.item("2023-11-14T22:13:21Z"), self.item("2023-11-14T22:13:19Z", "old")]),
            ]
        )
        got = r.activity(REPO, "refs/heads/main", self.SINCE)
        self.assertEqual(
            got,
            [Activity("refs/heads/main", 1_700_000_001.0, "lkshrk", "a", "b", "push"),
             Activity("refs/heads/main", 1_700_000_400.0, "h-cloud-npa[bot]", "b", "c", "push")],
        )
        first = self.opener.requests[0].full_url
        self.assertIn("/repos/acme/app/activity?", first)
        self.assertIn("ref=refs%2Fheads%2Fmain", first)
        self.assertIn("per_page=100", first)
        self.assertIn("direction=desc", first)
        self.assertIn("after=c1", self.opener.requests[1].full_url)

    def test_stops_paging_once_older_than_window(self):
        nxt = f'<{API}/repositories/1/activity?after=c1>; rel="next"'
        r = self.reader([jr([self.item("2023-11-14T22:00:00Z")], {"Link": nxt})])
        self.assertEqual(r.activity(REPO, "refs/heads/main", self.SINCE), [])
        self.assertEqual(len(self.opener.requests), 1)

    def test_null_actor_and_offset_timestamp(self):
        r = self.reader([jr([self.item("2023-11-15T00:13:21+02:00", None)])])
        self.assertEqual(r.activity(REPO, "refs/heads/main", self.SINCE), [Activity("refs/heads/main", 1_700_000_001.0, "", "a", "b", "push")])

    def test_recorded_pr_merge_keeps_activity_type(self):
        r = self.reader([jr(recorded("activity_h-cloud_pr_merge.json"))])
        [got] = r.activity("lkshrk/h-cloud", "refs/heads/main", self.SINCE)
        self.assertEqual((got.actor, got.activity_type, got.before[:7], got.after[:7]),
                         ("renovate-master[bot]", "pr_merge", "999bff9", "05394a4"))

    def test_missing_activity_type_is_empty(self):
        item = self.item("2023-11-14T22:20:00Z")
        del item["activity_type"]
        got = self.reader([jr([item])]).activity(REPO, "refs/heads/main", self.SINCE)
        self.assertEqual(got[0].activity_type, "")

    def test_other_refs_dropped(self):
        r = self.reader([jr([self.item("2023-11-14T22:20:00Z", ref="refs/heads/main-x")])])
        self.assertEqual(r.activity(REPO, "refs/heads/main", self.SINCE), [])

    def test_error_raises(self):
        err = urllib.error.HTTPError(API, 422, "spam", {}, io.BytesIO(b"x"))
        for item in (err, OSError("reset"), FakeResponse(b"<html>")):
            with self.subTest(item=item), self.assertRaises(GitHubError):
                self.reader([item]).activity(REPO, "refs/heads/main", self.SINCE)

    def test_wrong_shape_raises(self):
        bad = (
            {"a": 1},
            ["x"],
            [{**self.item("2023-11-14T22:20:00Z"), "timestamp": "soon"}],
            [{**self.item("2023-11-14T22:20:00Z"), "timestamp": None}],
            [{**self.item("2023-11-14T22:20:00Z"), "timestamp": "2023-11-14T22:20:00"}],
            [{**self.item("2023-11-14T22:20:00Z"), "actor": "lkshrk"}],
            [{**self.item("2023-11-14T22:20:00Z"), "actor": {"login": 5}}],
            [{**self.item("2023-11-14T22:20:00Z"), "ref": None}],
            [{**self.item("2023-11-14T22:20:00Z"), "before": None}],
            [{**self.item("2023-11-14T22:20:00Z"), "after": ""}],
            [{**self.item("2023-11-14T22:20:00Z"), "activity_type": 3}],
        )
        for body in bad:
            with self.subTest(body=body), self.assertRaises(GitHubError):
                self.reader([jr(body)]).activity(REPO, "refs/heads/main", self.SINCE)

    def test_next_link_off_host_raises(self):
        link = '<https://evil.test/activity?after=c1>; rel="next"'
        with self.assertRaises(GitHubError):
            self.reader([jr([self.item("2023-11-14T22:20:00Z")], {"Link": link})]).activity(REPO, "refs/heads/main", self.SINCE)

    def test_too_many_pages_raises(self):
        link = {"Link": f'<{API}/repositories/1/activity?after=c>; rel="next"'}
        pages = [jr([self.item("2023-11-14T22:20:00Z")], link) for _ in range(MAX_PAGES)]
        with self.assertRaises(GitHubError):
            self.reader(pages).activity(REPO, "refs/heads/main", self.SINCE)


class ReleaseAuthorTest(ReaderCase):
    def test_author_returned(self):
        r = self.reader([jr({"tag_name": "release/1.0", "author": {"login": "github-actions[bot]"}})])
        self.assertEqual(r.release_author(REPO, "release/1.0"), "github-actions[bot]")
        self.assertTrue(self.opener.requests[0].full_url.endswith("/repos/acme/app/releases/tags/release%2F1.0"))

    def test_no_release_is_none(self):
        err = urllib.error.HTTPError(API, 404, "missing", {}, io.BytesIO(b"{}"))
        self.assertIsNone(self.reader([err]).release_author(REPO, "v1"))

    def test_null_author_is_empty(self):
        self.assertEqual(self.reader([jr({"tag_name": "v1", "author": None})]).release_author(REPO, "v1"), "")

    def test_error_raises(self):
        err = urllib.error.HTTPError(API, 403, "no", {}, io.BytesIO(b"x"))
        for item in (err, OSError("reset"), FakeResponse(b"<html>")):
            with self.subTest(item=item), self.assertRaises(GitHubError):
                self.reader([item]).release_author(REPO, "v1")

    def test_wrong_shape_raises(self):
        for body in ([], {"author": "x"}, {"author": {"login": 5}}, {"author": {}}):
            with self.subTest(body=body), self.assertRaises(GitHubError):
                self.reader([jr(body)]).release_author(REPO, "v1")


class DefaultBranchTest(ReaderCase):
    def test_default_branch(self):
        r = self.reader([jr({"full_name": "acme/app", "default_branch": "trunk"})])
        self.assertEqual(r.default_branch(REPO), "trunk")
        self.assertTrue(self.opener.requests[0].full_url.endswith("/repos/acme/app"))

    def test_missing_default_branch_raises(self):
        for body in ({}, [], {"default_branch": ""}):
            with self.subTest(body=body), self.assertRaises(GitHubError):
                self.reader([jr(body)]).default_branch(REPO)


class PullTest(ReaderCase):
    def test_pull_parsed(self):
        body = {"head": {"ref": "feat/x", "repo": {"full_name": "acme/app"}}, "base": {"ref": "main", "repo": {"full_name": "acme/app", "default_branch": "main"}}}
        got = self.reader([jr(body)]).pull("https://github.com/acme/app/pull/7")
        self.assertEqual(got, PullHead("feat/x", "acme/app", "main", "acme/app", "main"))
        self.assertTrue(self.opener.requests[0].full_url.endswith("/repos/acme/app/pulls/7"))

    def test_deleted_fork_repo_null(self):
        body = {"head": {"ref": "feat/x", "repo": None}, "base": {"ref": "main", "repo": {"full_name": "acme/app", "default_branch": "main"}}}
        got = self.reader([jr(body)]).pull("https://github.com/acme/app/pull/7")
        self.assertEqual(got.head_repo, "")

    def test_missing_base_repo_raises(self):
        body = {"head": {"ref": "x", "repo": {"full_name": "acme/app"}}, "base": {"ref": "main"}}
        with self.assertRaises(GitHubError):
            self.reader([jr(body)]).pull("https://github.com/acme/app/pull/7")

    def test_bad_url_raises(self):
        for url in ("https://github.com/acme/../pull/7", "https://github.com/ac_me/app/pull/7", "", "https://example.com/acme/app/pull/7", "https://github.com/acme/app/issues/7", None):
            with self.subTest(url=url), self.assertRaises(GitHubError):
                self.reader([]).pull(url)

    def test_wrong_shape_raises(self):
        with self.assertRaises(GitHubError):
            self.reader([jr({"head": {"ref": "x"}})]).pull("https://github.com/acme/app/pull/7")


class MergedPullsTest(ReaderCase):
    SHA = "05394a48b339dffd02103e1f2aa0113aa095c0be"

    def test_recorded_renovate_merge(self):
        r = self.reader([jr(recorded("commit_pulls_h-cloud_1519.json")), jr(recorded("pull_h-cloud_1519.json"))])
        got = r.merged_pulls("lkshrk/h-cloud", self.SHA)
        self.assertEqual(got, [MergedPull(True, self.SHA, "main", "lkshrk/h-cloud",
                                          "renovate/ghcr.io-lkshrk-agent-stack-omnigent-host-1.x", "lkshrk/h-cloud",
                                          "renovate-master[bot]", "renovate-master[bot]", 1519,
                                          "c9018854bca2115d2b5373058095ce1056ecd3d3",
                                          "999bff9014dec1f2485a9395977fe1bfdd573686")])
        urls = [q.full_url for q in self.opener.requests]
        self.assertIn(f"/repos/lkshrk/h-cloud/commits/{self.SHA}/pulls", urls[0])
        self.assertTrue(urls[1].endswith("/repos/lkshrk/h-cloud/pulls/1519"))
        self.assertEqual(self.sleeps, [])

    def test_several_pulls_skip_the_detail(self):
        listed = recorded("commit_pulls_h-cloud_1519.json")
        r = self.reader([jr(listed + listed)])
        self.assertEqual([p.merged_by for p in r.merged_pulls("lkshrk/h-cloud", self.SHA)], ["", ""])
        self.assertEqual(len(self.opener.requests), 1)

    def test_no_pulls_is_retried_once_after_a_delay(self):
        r = self.reader([jr([]), jr([])])
        self.assertEqual(r.merged_pulls("lkshrk/h-cloud", self.SHA), [])
        self.assertEqual((len(self.opener.requests), self.sleeps), (2, [PULLS_RETRY_SECONDS]))
        listed = recorded("commit_pulls_h-cloud_1519.json")
        r = self.reader([jr([]), jr(listed), jr(recorded("pull_h-cloud_1519.json"))])
        self.assertEqual([p.number for p in r.merged_pulls("lkshrk/h-cloud", self.SHA)], [1519])

    def test_unmerged_and_deleted_fork(self):
        listed = recorded("commit_pulls_h-cloud_1519.json")
        detail = {**recorded("pull_h-cloud_1519.json"), "merged_at": None, "merge_commit_sha": None, "merged_by": None}
        detail["head"] = {**detail["head"], "repo": None}
        [got] = self.reader([jr(listed), jr(detail)]).merged_pulls("lkshrk/h-cloud", self.SHA)
        self.assertEqual((got.merged, got.merge_commit_sha, got.merged_by, got.head_repo), (False, "", "", ""))

    def test_wrong_shape_raises(self):
        listed = recorded("commit_pulls_h-cloud_1519.json")
        detail = recorded("pull_h-cloud_1519.json")
        bad = ({**detail, "base": None}, {**detail, "user": None}, {**detail, "merged_by": "x"}, [],
               {**detail, "merged_at": 5}, {**detail, "number": "1"})
        for body in bad:
            with self.subTest(body=body), self.assertRaises(GitHubError):
                self.reader([jr(listed), jr(body)]).merged_pulls("lkshrk/h-cloud", self.SHA)
        for body in ({}, ["x"], [{"number": "1"}]):
            with self.subTest(body=body), self.assertRaises(GitHubError):
                self.reader([jr(body)]).merged_pulls("lkshrk/h-cloud", self.SHA)

    def test_error_raises(self):
        err = urllib.error.HTTPError(API, 422, "No commit found", {}, io.BytesIO(b"{}"))
        with self.assertRaises(GitHubError):
            self.reader([err]).merged_pulls("lkshrk/h-cloud", self.SHA)


class HeadPushesTest(ReaderCase):
    REF = "refs/heads/renovate/ghcr.io-lkshrk-agent-stack-omnigent-host-1.x"
    NAME = REF.removeprefix("refs/heads/")
    HEAD = "c9018854bca2115d2b5373058095ce1056ecd3d3"

    def item(self, before, after, login=AGENT, kind="push", ts="2026-11-07T13:00:00Z"):
        return {"ref": self.REF, "before": before, "after": after, "timestamp": ts, "activity_type": kind,
                "actor": None if login is None else {"login": login}}

    def test_recorded_history_stops_at_the_pr_branch_creation(self):
        r = self.reader([jr(recorded("activity_head_h-cloud_1519.json"))])
        got = r.head_pushes("lkshrk/h-cloud", self.NAME, self.HEAD)
        self.assertEqual([(a.activity_type, a.actor, a.before, a.after) for a in got],
                         [("branch_creation", "renovate-master[bot]", NULL_SHA, self.HEAD)])
        url = self.opener.requests[0].full_url
        self.assertIn("ref=refs%2Fheads%2Frenovate%2Fghcr.io-lkshrk-agent-stack-omnigent-host-1.x", url)
        self.assertIn("direction=desc", url)

    def test_chained_pushes_oldest_first_across_pages(self):
        nxt = f'<{API}/repositories/1/activity?after=c1>; rel="next"'
        r = self.reader([
            jr([self.item(self.HEAD, NULL_SHA, RENOVATE, "branch_deletion"), self.item("p2", self.HEAD, None)],
               {"Link": nxt}),
            jr([self.item("p1", "p2", AGENT, "force_push"), self.item(NULL_SHA, "p1", RENOVATE, "branch_creation"),
                self.item("old", NULL_SHA, RENOVATE, "branch_deletion")]),
        ])
        got = r.head_pushes(REPO, self.NAME, self.HEAD)
        self.assertEqual([(a.actor, a.before, a.after) for a in got],
                         [(RENOVATE, NULL_SHA, "p1"), (AGENT, "p1", "p2"), ("", "p2", self.HEAD)])
        self.assertEqual(got[0].before, NULL_SHA)
        self.assertTrue(all(a.after == b.before for a, b in zip(got, got[1:])))
        self.assertEqual(got[-1].after, self.HEAD)
        self.assertEqual(len(self.opener.requests), 2)

    def test_gap_in_the_walked_entries_is_returned_for_the_policy_to_reject(self):
        r = self.reader([jr([self.item("p2", self.HEAD), self.item(NULL_SHA, "p1", RENOVATE, "branch_creation")])])
        got = r.head_pushes(REPO, self.NAME, self.HEAD)
        self.assertEqual([(a.before, a.after) for a in got], [(NULL_SHA, "p1"), ("p2", self.HEAD)])

    def test_head_never_seen_or_no_creation_is_untraceable(self):
        r = self.reader([jr([self.item(NULL_SHA, "other", RENOVATE, "branch_creation")])])
        self.assertIsNone(r.head_pushes(REPO, self.NAME, self.HEAD))
        r = self.reader([jr([self.item("p1", self.HEAD)])])
        self.assertIsNone(r.head_pushes(REPO, self.NAME, self.HEAD))

    def test_page_cap_is_untraceable(self):
        link = {"Link": f'<{API}/repositories/1/activity?after=c>; rel="next"'}
        pages = [jr([self.item("p0", "p1")], link) for _ in range(MAX_PAGES)]
        self.assertIsNone(self.reader(pages).head_pushes(REPO, self.NAME, self.HEAD))
        self.assertEqual(len(self.opener.requests), MAX_PAGES)


class PushChangesTest(ReaderCase):
    def test_recorded_compare(self):
        r = self.reader([jr(recorded("compare_h-cloud_1519.json"))])
        got = r.push_changes("lkshrk/h-cloud", "999bff9", "c901885")
        self.assertEqual(got, Compare("ahead", [FileChange("kubernetes/apps/omnigent-sandboxes/runners/app/runner-prepull.yaml")]))
        self.assertIn("/repos/lkshrk/h-cloud/compare/999bff9...c901885", self.opener.requests[0].full_url)

    def test_rename(self):
        body = {"status": "diverged", "files": [{"filename": "b.yaml", "previous_filename": "a.yaml"}]}
        self.assertEqual(self.reader([jr(body)]).push_changes(REPO, "x", "y"),
                         Compare("diverged", [FileChange("b.yaml", "a.yaml")]))

    def test_wrong_shape_or_truncated_raises(self):
        for body in ([], {}, {"status": "ahead", "files": ["x"]}, {"status": "ahead", "files": [{"filename": ""}]},
                     {"files": []}, {"status": "ahead", "files": [{"filename": f"f{i}"} for i in range(COMPARE_FILE_CAP)]}):
            with self.subTest(body=str(body)[:60]), self.assertRaises(GitHubError):
                self.reader([jr(body)]).push_changes(REPO, "x", "y")


class FileContentTest(ReaderCase):
    PATH = "kubernetes/apps/ai/litellm-proxy/app/servicemonitor.yaml"

    def test_recorded_file(self):
        r = self.reader([jr(recorded("contents_h-cloud_servicemonitor.json"))])
        got = r.file_content("lkshrk/h-cloud", self.PATH, "c9018854")
        self.assertEqual(got.state, "present")
        self.assertIn(b"kind: ServiceMonitor", got.data)
        url = self.opener.requests[0].full_url
        self.assertIn(f"/repos/lkshrk/h-cloud/contents/{self.PATH}?ref=c9018854", url)

    def test_absent_large_and_non_files(self):
        err = urllib.error.HTTPError(API, 404, "Not Found", {}, io.BytesIO(b"{}"))
        self.assertEqual(self.reader([err]).file_content(REPO, "a.yaml", "x"), Content("absent"))
        body = recorded("contents_h-cloud_servicemonitor.json")
        cases = (
            ({**body, "size": 2_000_000, "encoding": "none", "content": ""}, "larger than 1 MB"),
            ({**body, "encoding": "none"}, "larger than 1 MB"),
            ({**body, "type": "symlink"}, "not a file"),
            ([{"type": "file", "name": "a"}], "not a file"),
        )
        for payload, state in cases:
            self.assertEqual(self.reader([jr(payload)]).file_content(REPO, "a.yaml", "x").state, state)

    def test_size_mismatch_or_error_raises(self):
        body = recorded("contents_h-cloud_servicemonitor.json")
        err = urllib.error.HTTPError(API, 403, "no", {}, io.BytesIO(b"x"))
        for item in (jr({**body, "size": 1}), err, OSError("reset")):
            with self.subTest(item=item), self.assertRaises(GitHubError):
                self.reader([item]).file_content(REPO, "a.yaml", "x")


class TagCommitTest(ReaderCase):
    SHA = "14300c762a6295307e64914059a0809e1d2c584d"

    def test_recorded_annotated_tag(self):
        r = self.reader([jr(recorded("tag_h-cloud_2026.11.0.json"))])
        self.assertEqual(r.tag_commit("lkshrk/h-cloud", self.SHA), "5a5ee096a0fa04ac3dd7934177b8b80719737dfc")
        self.assertTrue(self.opener.requests[0].full_url.endswith(f"/repos/lkshrk/h-cloud/git/tags/{self.SHA}"))

    def test_lightweight_tag_is_none(self):
        err = urllib.error.HTTPError(API, 404, "Not Found", {}, io.BytesIO(b"{}"))
        self.assertIsNone(self.reader([err]).tag_commit("lkshrk/h-cloud", "5a5ee096a0fa04ac3dd7934177b8b80719737dfc"))

    def test_nested_tag_is_empty(self):
        body = recorded("tag_h-cloud_2026.11.0.json")
        nested = {**body, "object": {**body["object"], "type": "tag"}}
        self.assertEqual(self.reader([jr(nested)]).tag_commit("lkshrk/h-cloud", self.SHA), "")

    def test_error_and_wrong_shape_raise(self):
        err = urllib.error.HTTPError(API, 403, "no", {}, io.BytesIO(b"x"))
        body = recorded("tag_h-cloud_2026.11.0.json")
        for item in (err, OSError("reset"), jr([]), jr({**body, "object": None}),
                     jr({**body, "object": {"type": "commit"}})):
            with self.subTest(item=item), self.assertRaises(GitHubError):
                self.reader([item]).tag_commit("lkshrk/h-cloud", self.SHA)


class TagRunsTest(ReaderCase):
    COMMIT = "5a5ee096a0fa04ac3dd7934177b8b80719737dfc"

    def test_recorded_runs(self):
        r = self.reader([jr(recorded("tag_runs_h-cloud_2026.11.0.json"))])
        [run] = r.tag_runs("lkshrk/h-cloud", self.COMMIT)
        self.assertEqual(run, WorkflowRun(self.COMMIT, "main", "completed", "success", 1_793_505_544.0, "schedule",
                                          "lkshrk/h-cloud"))
        url = self.opener.requests[0].full_url
        self.assertIn("/repos/lkshrk/h-cloud/actions/workflows/tag.yaml/runs?", url)
        self.assertIn(f"head_sha={self.COMMIT}", url)

    def test_null_conclusion_is_empty(self):
        body = recorded("tag_runs_h-cloud_2026.11.0.json")
        body["workflow_runs"][0] = {**body["workflow_runs"][0], "conclusion": None, "status": "in_progress"}
        [run] = self.reader([jr(body)]).tag_runs("lkshrk/h-cloud", self.COMMIT)
        self.assertEqual((run.status, run.conclusion), ("in_progress", ""))

    def test_wrong_shape_raises(self):
        good = recorded("tag_runs_h-cloud_2026.11.0.json")["workflow_runs"][0]
        for body in ([], {}, {"workflow_runs": ["x"]}, {"workflow_runs": [{**good, "updated_at": "soon"}]},
                     {"workflow_runs": [{**good, "head_sha": None}]}):
            with self.subTest(body=str(body)[:60]), self.assertRaises(GitHubError):
                self.reader([jr(body)]).tag_runs("lkshrk/h-cloud", self.COMMIT)


class CompareTest(ReaderCase):
    def test_files_returned(self):
        body = {"files": [{"filename": "a.md"}, {"filename": "b.md", "previous_filename": "src/b.py"}]}
        got = self.reader([jr(body)]).changed_files(REPO, "main", "feat/x")
        self.assertEqual(got, ["a.md", "b.md", "src/b.py"])
        self.assertIn("/repos/acme/app/compare/main...feat%2Fx", self.opener.requests[0].full_url)

    def test_compare_over_300_files_raises(self):
        body = {"files": [{"filename": f"f{i}.md"} for i in range(301)]}
        with self.assertRaises(GitHubError):
            self.reader([jr(body)]).changed_files(REPO, "main", "x")

    def test_compare_at_cap_raises(self):
        body = {"files": [{"filename": f"f{i}.md"} for i in range(300)]}
        with self.assertRaises(GitHubError):
            self.reader([jr(body)]).changed_files(REPO, "main", "x")

    def test_missing_files_raises(self):
        with self.assertRaises(GitHubError):
            self.reader([jr({})]).changed_files(REPO, "main", "x")


if __name__ == "__main__":
    unittest.main()
