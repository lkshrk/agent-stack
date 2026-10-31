import io
import json
import os
import tempfile
import unittest
import urllib.error

from agent_stack_hermes.postrun import MAX_PAGES, Activity, GitHubError, GitHubReader, PullHead, agent_actors, violations

API = "https://api.github.com"
REPO = "acme/app"
HEAD = PullHead("feat/x", "acme/app", "main", "acme/app", "main")


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


def entry(ref, actor, ts=1000.0):
    return Activity(ref, ts, actor)


class Lookup:
    def __init__(self, *entries):
        self.entries = entries
        self.asked = []

    def __call__(self, ref):
        self.asked.append(ref)
        return [e for e in self.entries if e.ref == ref]


class Releases:
    def __init__(self, **authors):
        self.authors = authors
        self.asked = []

    def __call__(self, tag):
        self.asked.append(tag)
        return self.authors.get(tag)


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
        self.assertEqual(self.lookup.asked, [])

    def test_head_branch_creation_allowed(self):
        before = {k: v for k, v in self.base.items() if k != "refs/heads/feat/x"}
        self.assertEqual(self.check(self.base, before=before), [])

    def test_other_agent_branch_move_allowed(self):
        after = {**self.base, "refs/heads/polly/x": "z", "refs/heads/feat/y": "q"}
        self.assertEqual(self.check(after), [])
        self.assertEqual(self.lookup.asked, [])

    def test_owner_merge_to_main_allowed(self):
        lookup = Lookup(entry("refs/heads/main", "lkshrk"))
        self.assertEqual(self.check({**self.base, "refs/heads/main": "m"}, lookup=lookup), [])
        self.assertEqual(lookup.asked, ["refs/heads/main"])

    def test_agent_moves_main_is_violation(self):
        lookup = Lookup(entry("refs/heads/main", "lkshrk"), entry("refs/heads/main", AGENT))
        out = self.check({**self.base, "refs/heads/main": "m"}, lookup=lookup)
        self.assertEqual(out, ["ref moved by agent: refs/heads/main"])

    def test_agent_login_compare_case_insensitive(self):
        lookup = Lookup(entry("refs/heads/main", "H-Cloud-NPA[bot]"))
        out = self.check({**self.base, "refs/heads/main": "m"}, lookup=lookup)
        self.assertEqual(out, ["ref moved by agent: refs/heads/main"])

    def test_release_tag_by_non_agent_allowed(self):
        releases = Releases(v2="github-actions[bot]")
        self.assertEqual(self.check({**self.base, "refs/tags/v2": "t2"}, releases=releases), [])
        self.assertEqual((releases.asked, self.lookup.asked), (["v2"], []))

    def test_agent_release_on_moved_tag_is_violation(self):
        out = self.check({**self.base, "refs/tags/v1": "t2"}, releases=Releases(v1="H-Cloud-NPA[bot]"))
        self.assertEqual(out, ["ref moved by agent: refs/tags/v1"])

    def test_tag_without_release_is_unattributed(self):
        lookup = Lookup(entry("refs/tags/v2", "github-actions[bot]"))
        out = self.check({**self.base, "refs/tags/v2": "t2"}, lookup=lookup)
        self.assertEqual(out, ["ref moved (unattributed): refs/tags/v2"])
        self.assertEqual((self.releases.asked, lookup.asked), (["v2"], []))

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
            self.check(after), ["ref moved (unattributed): refs/heads/main", "ref moved (unattributed): refs/tags/v1"]
        )

    def test_null_actor_is_unattributed(self):
        lookup = Lookup(entry("refs/heads/main", "lkshrk"), entry("refs/heads/main", ""))
        out = self.check({**self.base, "refs/heads/main": "m"}, lookup=lookup)
        self.assertEqual(out, ["ref moved (unattributed): refs/heads/main"])

    def test_base_branch_is_protected(self):
        pull = PullHead("feat/x", "acme/app", "release", "acme/app", "main")
        before = {**self.base, "refs/heads/release": "r"}
        out = self.check({**before, "refs/heads/release": "r2"}, before=before, pull=pull)
        self.assertEqual(out, ["ref moved (unattributed): refs/heads/release"])

    def test_no_agent_actors_protected_move_is_violation(self):
        lookup = Lookup(entry("refs/heads/main", "lkshrk"))
        for agents in (frozenset(), frozenset({""})):
            with self.subTest(agents=agents):
                after = {**self.base, "refs/heads/main": "m", "refs/tags/v2": "t2"}
                out = self.check(after, lookup=lookup, agents=agents, releases=Releases(v2="github-actions[bot]"))
                self.assertEqual(out, ["ref moved: refs/heads/main", "ref moved: refs/tags/v2"])

    def test_no_pr_protects_given_branches(self):
        after = {**self.base, "refs/heads/main": "m", "refs/heads/feat/x": "c"}
        out = self.check(after, pull=None, branches=("main",))
        self.assertEqual(out, ["ref moved (unattributed): refs/heads/main"])

    def test_no_change_no_pr_passes(self):
        self.assertEqual(self.check(dict(self.base), pull=None, branches=("main",)), [])

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
        lookup = Lookup(entry("refs/heads/main", AGENT))
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

    def item(self, ts, login="lkshrk", ref="refs/heads/main"):
        return {"id": 1, "node_id": "n", "before": "a", "after": "b", "ref": ref, "timestamp": ts,
                "activity_type": "push", "actor": None if login is None else {"login": login}}

    def test_entries_in_window_returned(self):
        nxt = f'<{API}/repositories/1/activity?ref=refs%2Fheads%2Fmain&per_page=100&after=c1>; rel="next"'
        r = self.reader(
            [
                jr([self.item("2023-11-14T22:20:00Z", "h-cloud-npa[bot]")], {"Link": nxt}),
                jr([self.item("2023-11-14T22:13:21Z"), self.item("2023-11-14T22:13:19Z", "old")]),
            ]
        )
        got = r.activity(REPO, "refs/heads/main", self.SINCE)
        self.assertEqual(
            got,
            [Activity("refs/heads/main", 1_700_000_400.0, "h-cloud-npa[bot]"),
             Activity("refs/heads/main", 1_700_000_001.0, "lkshrk")],
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
        self.assertEqual(r.activity(REPO, "refs/heads/main", self.SINCE), [Activity("refs/heads/main", 1_700_000_001.0, "")])

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
