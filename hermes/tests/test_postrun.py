import io
import json
import os
import tempfile
import unittest
import urllib.error

from agent_stack_hermes.postrun import GitHubError, GitHubReader, PullHead, violations

API = "https://api.github.com"
REPO = "acme/app"
HEAD = PullHead("feat/x", "acme/app", "main")


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


class ViolationsTest(unittest.TestCase):
    base = {"refs/heads/main": "a", "refs/heads/feat/x": "b", "refs/tags/v1": "t"}

    def test_head_branch_move_allowed(self):
        after = {**self.base, "refs/heads/feat/x": "c"}
        self.assertEqual(violations("coder", REPO, self.base, after, HEAD, []), [])

    def test_head_branch_creation_allowed(self):
        before = {k: v for k, v in self.base.items() if k != "refs/heads/feat/x"}
        self.assertEqual(violations("coder", REPO, before, self.base, HEAD, []), [])

    def test_second_branch_is_violation(self):
        after = {**self.base, "refs/heads/other": "z"}
        self.assertEqual(violations("coder", REPO, self.base, after, HEAD, []), ["ref moved: refs/heads/other"])

    def test_tag_move_is_violation(self):
        after = {**self.base, "refs/tags/v1": "t2"}
        self.assertEqual(violations("coder", REPO, self.base, after, HEAD, []), ["ref moved: refs/tags/v1"])

    def test_deleted_branch_is_violation(self):
        after = {k: v for k, v in self.base.items() if k != "refs/heads/main"}
        self.assertEqual(violations("coder", REPO, self.base, after, HEAD, []), ["ref moved: refs/heads/main"])

    def test_no_pr_any_move_is_violation(self):
        after = {**self.base, "refs/heads/feat/x": "c"}
        self.assertEqual(violations("coder", REPO, self.base, after, None, []), ["ref moved: refs/heads/feat/x"])

    def test_no_change_no_pr_passes(self):
        self.assertEqual(violations("coder", REPO, self.base, dict(self.base), None, []), [])

    def test_fork_head_is_violation(self):
        pull = PullHead("feat/x", "evil/app", "main")
        self.assertEqual(violations("coder", REPO, self.base, self.base, pull, []), [f"PR head is not in {REPO}"])

    def test_deleted_fork_head_is_violation(self):
        pull = PullHead("feat/x", "", "main")
        self.assertEqual(violations("coder", REPO, self.base, self.base, pull, []), [f"PR head is not in {REPO}"])

    def test_repo_compare_case_insensitive(self):
        pull = PullHead("feat/x", "Acme/App", "main")
        self.assertEqual(violations("coder", REPO, self.base, self.base, pull, []), [])

    def test_documenter_code_file_is_violation(self):
        out = violations("documenter", REPO, self.base, self.base, HEAD, ["README.md", "src/app.py"])
        self.assertEqual(out, ["non-doc file: src/app.py"])

    def test_documenter_docs_only_passes(self):
        self.assertEqual(violations("documenter", REPO, self.base, self.base, HEAD, ["README.md", "docs/a.md"]), [])

    def test_non_documenter_ignores_files(self):
        self.assertEqual(violations("coder", REPO, self.base, self.base, HEAD, ["src/app.py"]), [])


class RefsTest(ReaderCase):
    def test_refs_paginates(self):
        nxt = f'<{API}/repositories/1/git/matching-refs/?page=2>; rel="next", <{API}/x>; rel="last"'
        r = self.reader(
            [
                jr([{"ref": "refs/heads/a", "object": {"sha": "1"}}], {"Link": nxt}),
                jr([{"ref": "refs/tags/v1", "object": {"sha": "2"}}]),
            ]
        )
        self.assertEqual(r.refs(REPO), {"refs/heads/a": "1", "refs/tags/v1": "2"})
        self.assertEqual(len(self.opener.requests), 2)
        self.assertIn("/repos/acme/app/git/matching-refs/", self.opener.requests[0].full_url)
        self.assertIn("page=2", self.opener.requests[1].full_url)

    def test_request_headers_and_timeout(self):
        r = self.reader([jr([])])
        r.refs(REPO)
        req = self.opener.requests[0]
        self.assertEqual(req.get_header("Authorization"), "Bearer tok1")
        self.assertEqual(req.get_header("Accept"), "application/vnd.github+json")
        self.assertEqual(req.get_header("X-github-api-version"), "2022-11-28")
        self.assertTrue(self.opener.timeouts[0])

    def test_token_file_reread(self):
        r = self.reader([jr([]), jr([])])
        r.refs(REPO)
        with open(self.token_file, "w") as fh:
            fh.write("tok2")
        r.refs(REPO)
        self.assertEqual(self.opener.requests[1].get_header("Authorization"), "Bearer tok2")

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

    def test_wrong_shape_raises(self):
        for body in ({"a": 1}, [{"ref": "refs/heads/a"}], [{"ref": "refs/heads/a", "object": {}}], ["x"]):
            with self.subTest(body=body), self.assertRaises(GitHubError):
                self.reader([jr(body)]).refs(REPO)


class PullTest(ReaderCase):
    def test_pull_parsed(self):
        body = {"head": {"ref": "feat/x", "repo": {"full_name": "acme/app"}}, "base": {"ref": "main"}}
        got = self.reader([jr(body)]).pull("https://github.com/acme/app/pull/7")
        self.assertEqual(got, PullHead("feat/x", "acme/app", "main"))
        self.assertTrue(self.opener.requests[0].full_url.endswith("/repos/acme/app/pulls/7"))

    def test_deleted_fork_repo_null(self):
        body = {"head": {"ref": "feat/x", "repo": None}, "base": {"ref": "main"}}
        got = self.reader([jr(body)]).pull("https://github.com/acme/app/pull/7")
        self.assertEqual(got.head_repo, "")

    def test_bad_url_raises(self):
        for url in ("", "https://example.com/acme/app/pull/7", "https://github.com/acme/app/issues/7", None):
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
