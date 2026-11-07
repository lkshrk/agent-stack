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
    Activity,
    GitHubError,
    GitHubReader,
    MergedPull,
    PullHead,
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
        return GitHubReader(self.token_file, opener=self.opener)


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


class Merges:
    def __init__(self, **pulls):
        self.pulls = pulls
        self.asked = []

    def __call__(self, sha):
        self.asked.append(sha)
        return list(self.pulls.get(sha, ()))


def renovate_pull(sha="m", **over):
    fields = {"merged": True, "merge_commit_sha": sha, "base_ref": "main", "base_repo": REPO, "head_ref": "renovate/x-1.x",
              "head_repo": REPO, "author": "renovate-master[bot]", "merged_by": AGENT}
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


class Taggers(Releases):
    pass


class ViolationsTest(unittest.TestCase):
    base = {"refs/heads/main": "a", "refs/heads/feat/x": "b", "refs/tags/v1": "t"}

    def check(self, after, before=None, pull=HEAD, files=(), bundle="coder", lookup=None, agents=AGENTS, branches=(),
              releases=None, taggers=None):
        self.lookup = lookup or Lookup()
        self.releases = releases or Releases()
        self.taggers = taggers or Taggers()
        before = self.base if before is None else before
        return violations(bundle, REPO, before, after, pull, list(files), self.lookup, self.releases, agents, branches,
                          tagger=self.taggers)

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

    def test_tag_without_release_or_tagger_is_unattributed(self):
        out = self.check({**self.base, "refs/tags/v2": "t2"})
        self.assertEqual(out, ["ref moved (unattributed): refs/tags/v2"])
        asked = (self.releases.asked, self.lookup.asked, self.taggers.asked)
        self.assertEqual(asked, (["v2"], [MAIN, "refs/tags/v2"], ["t2"]))

    def test_tag_without_release_attributed_by_activity(self):
        lookup = Lookup(entry("refs/tags/v2", "github-actions[bot]", NULL_SHA, "t2"))
        self.assertEqual(self.check({**self.base, "refs/tags/v2": "t2"}, lookup=lookup), [])
        self.assertEqual(self.taggers.asked, [])

    def test_tag_created_by_agent_per_activity_is_violation(self):
        lookup = Lookup(entry("refs/tags/v2", AGENT, NULL_SHA, "t2"))
        out = self.check({**self.base, "refs/tags/v2": "t2"}, lookup=lookup, taggers=Taggers(t2="lkshrk"))
        self.assertEqual(out, ["ref moved by agent: refs/tags/v2"])

    def test_tag_activity_not_reaching_the_tag_is_unattributed(self):
        for actor in ("github-actions[bot]", ""):
            lookup = Lookup(entry("refs/tags/v2", actor, NULL_SHA, "t9"))
            out = self.check({**self.base, "refs/tags/v2": "t2"}, lookup=lookup, taggers=Taggers(t2="lkshrk"))
            self.assertEqual(out, ["ref moved (unattributed): refs/tags/v2"], actor)

    def test_tag_without_release_attributed_by_tagger(self):
        taggers = Taggers(t2="h-cloud-workflow[bot]")
        self.assertEqual(self.check({**self.base, "refs/tags/v2": "t2"}, taggers=taggers), [])
        self.assertEqual(taggers.asked, ["t2"])

    def test_tag_tagged_by_agent_is_violation(self):
        out = self.check({**self.base, "refs/tags/v2": "t2"}, taggers=Taggers(t2="H-Cloud-NPA[bot]"))
        self.assertEqual(out, ["ref moved by agent: refs/tags/v2"])

    def test_moved_tag_is_violation_despite_tagger(self):
        out = self.check({**self.base, "refs/tags/v1": "t2"}, taggers=Taggers(t2="h-cloud-workflow[bot]"))
        self.assertEqual(out, ["tag moved: refs/tags/v1"])
        self.assertEqual(self.taggers.asked, [])

    def test_release_without_author_is_unattributed(self):
        out = self.check({**self.base, "refs/tags/v2": "t2"}, releases=Releases(v2=""))
        self.assertEqual(out, ["ref moved (unattributed): refs/tags/v2"])
        out = self.check({**self.base, "refs/tags/v2": "t2"}, releases=Releases(v2=""), taggers=Taggers(t2="x[bot]"))
        self.assertEqual(out, [])

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

    def check(self, after, entries, merges=None, bundle="infra", agents=AGENTS, pulls=None, default="main"):
        self.merges = merges or Merges()
        lookup = Lookup(*entries)
        return violations(bundle, REPO, self.base, after, None, [], lookup, Releases(), agents, ("main",),
                          tagger=Taggers(), merged=self.merges, default=default, pulls=pulls or {})

    def test_agent_landed_renovate_pr_allowed(self):
        out = self.check({MAIN: "m"}, [merge("a", "m")], Merges(m=[renovate_pull()]))
        self.assertEqual(out, [])
        self.assertEqual(self.merges.asked, ["m"])

    def test_chain_of_agent_merges_and_owner_push_allowed(self):
        entries = [merge("a", "m1"), entry(MAIN, "lkshrk", "m1", "o"), merge("o", "m2")]
        merges = Merges(m1=[renovate_pull("m1")], m2=[renovate_pull("m2", merged_by="H-Cloud-NPA[bot]")])
        self.assertEqual(self.check({MAIN: "m2"}, entries, merges), [])
        self.assertEqual(self.merges.asked, ["m1", "m2"])

    def test_agent_push_is_violation(self):
        for kind in ("push", "force_push", "merge_queue_merge", ""):
            out = self.check({MAIN: "m"}, [merge("a", "m", kind=kind)], Merges(m=[renovate_pull()]))
            self.assertEqual(len(out), 1, kind)
            self.assertTrue(out[0].startswith("ref moved by agent: refs/heads/main ("), out)
            self.assertEqual(self.merges.asked, [])

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
                out = self.check({MAIN: "m"}, [merge("a", "m")], Merges(m=[pull]))
                self.assertEqual(len(out), 1, out)
                self.assertTrue(out[0].startswith("ref moved by agent: refs/heads/main ("), out)

    def test_not_exactly_one_pr_is_violation(self):
        for pulls in ([], [renovate_pull(), renovate_pull()]):
            out = self.check({MAIN: "m"}, [merge("a", "m")], Merges(m=pulls))
            self.assertEqual(len(out), 1, pulls)
            self.assertTrue(out[0].startswith("ref moved by agent: refs/heads/main ("), out)

    def test_broken_chain_after_landed_merge_is_unattributed(self):
        out = self.check({MAIN: "z"}, [merge("a", "m")], Merges(m=[renovate_pull()]))
        self.assertEqual(out, ["ref moved (unattributed): refs/heads/main"])

    def test_case_insensitive_repo_and_logins(self):
        pull = renovate_pull(base_repo="ACME/app", head_repo="Acme/App", author="Renovate-Master[bot]")
        self.assertEqual(self.check({MAIN: "m"}, [merge("a", "m")], Merges(m=[pull])), [])

    def test_other_protected_branch_still_violation(self):
        base = {**self.base, "refs/heads/release": "q"}
        lookup = Lookup(Activity("refs/heads/release", 1000.0, AGENT, "q", "m", "pr_merge"))
        merges = Merges(m=[renovate_pull(base_ref="release")])
        out = violations("infra", REPO, base, {**base, "refs/heads/release": "m"}, None, [], lookup, Releases(), AGENTS,
                         ("main", "release"), tagger=Taggers(), merged=merges, default="main", pulls={})
        self.assertEqual(out, ["ref moved by agent: refs/heads/release"])
        self.assertEqual(merges.asked, [])

    def test_other_bundles_keep_the_plain_rule(self):
        for bundle in ("polly", "documenter"):
            out = self.check({MAIN: "m"}, [merge("a", "m")], Merges(m=[renovate_pull()]), bundle=bundle)
            self.assertEqual(out, ["ref moved by agent: refs/heads/main"], bundle)
            self.assertEqual(self.merges.asked, [])

    def test_no_agent_actors_is_violation(self):
        out = self.check({MAIN: "m"}, [merge("a", "m")], Merges(m=[renovate_pull()]), agents=frozenset())
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
                                          "renovate-master[bot]", "renovate-master[bot]")])
        urls = [q.full_url for q in self.opener.requests]
        self.assertIn(f"/repos/lkshrk/h-cloud/commits/{self.SHA}/pulls", urls[0])
        self.assertTrue(urls[1].endswith("/repos/lkshrk/h-cloud/pulls/1519"))

    def test_several_or_no_pulls_skip_the_detail(self):
        listed = recorded("commit_pulls_h-cloud_1519.json")
        r = self.reader([jr(listed + listed), jr([])])
        self.assertEqual([p.merged_by for p in r.merged_pulls("lkshrk/h-cloud", self.SHA)], ["", ""])
        self.assertEqual(r.merged_pulls("lkshrk/h-cloud", self.SHA), [])
        self.assertEqual(len(self.opener.requests), 2)

    def test_unmerged_and_deleted_fork(self):
        listed = recorded("commit_pulls_h-cloud_1519.json")
        detail = {**recorded("pull_h-cloud_1519.json"), "merged_at": None, "merge_commit_sha": None, "merged_by": None}
        detail["head"] = {**detail["head"], "repo": None}
        [got] = self.reader([jr(listed), jr(detail)]).merged_pulls("lkshrk/h-cloud", self.SHA)
        self.assertEqual((got.merged, got.merge_commit_sha, got.merged_by, got.head_repo), (False, "", "", ""))

    def test_wrong_shape_raises(self):
        listed = recorded("commit_pulls_h-cloud_1519.json")
        detail = recorded("pull_h-cloud_1519.json")
        bad = ({**detail, "base": None}, {**detail, "user": None}, {**detail, "merged_by": "x"}, [], {**detail, "merged_at": 5})
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


class TaggerTest(ReaderCase):
    SHA = "14300c762a6295307e64914059a0809e1d2c584d"

    def test_recorded_annotated_tag(self):
        r = self.reader([jr(recorded("tag_h-cloud_2026.11.0.json"))])
        self.assertEqual(r.tagger("lkshrk/h-cloud", self.SHA), "h-cloud-workflow[bot]")
        self.assertTrue(self.opener.requests[0].full_url.endswith(f"/repos/lkshrk/h-cloud/git/tags/{self.SHA}"))

    def test_lightweight_tag_is_none(self):
        err = urllib.error.HTTPError(API, 404, "Not Found", {}, io.BytesIO(b"{}"))
        self.assertIsNone(self.reader([err]).tagger("lkshrk/h-cloud", "5a5ee096a0fa04ac3dd7934177b8b80719737dfc"))

    def test_missing_tagger_is_empty(self):
        body = {k: v for k, v in recorded("tag_h-cloud_2026.11.0.json").items() if k != "tagger"}
        self.assertEqual(self.reader([jr(body)]).tagger("lkshrk/h-cloud", self.SHA), "")

    def test_error_and_wrong_shape_raise(self):
        err = urllib.error.HTTPError(API, 403, "no", {}, io.BytesIO(b"x"))
        for item in (err, OSError("reset"), jr([]), jr({"tagger": "x"}), jr({"tagger": {"name": 5}})):
            with self.subTest(item=item), self.assertRaises(GitHubError):
                self.reader([item]).tagger("lkshrk/h-cloud", self.SHA)


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
