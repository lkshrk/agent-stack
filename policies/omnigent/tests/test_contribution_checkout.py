import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent_stack_policies.contribution_checkout import CheckoutError, prepare_checkout
from agent_stack_policies.public_contributions import Registry

from .fork_checkout import FORK_URL, _environment, commit, git

REGISTRY = Path(__file__).resolve().parents[3] / "config" / "public-contributions.yaml"


class PrepareCheckoutTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name).resolve()
        self.env = patch.dict(os.environ, _environment(), clear=True)
        self.env.start()
        self.addCleanup(self.env.stop)
        self.upstream = root / "upstream"
        self.upstream.mkdir()
        git(self.upstream, "init", "-q", "-b", "main")
        commit(self.upstream, "web/a.ts", "export const a = 1\n", "chore: initial")
        git(self.upstream, "branch", "other")
        commit(self.upstream, "web/b.ts", "export const b = 1\n", "chore: second")
        self.upstream_url = self.upstream.as_uri()
        self.workspace = root / "work"
        self.repository = Registry.load(REGISTRY).repository("omnigent")

    def prepare(self):
        return prepare_checkout(self.repository, self.workspace, upstream_url=self.upstream_url, protocol="file")

    def test_clones_the_upstream_default_branch_and_adds_the_fork_remote(self):
        result = self.prepare()

        tree = self.workspace / "omnigent"
        self.assertEqual(result, {"checkout": str(tree), "branch": "main", "remotes": {"origin": self.upstream_url, "fork": FORK_URL}, "state": "cloned"})
        self.assertEqual(git(tree, "branch", "--show-current").strip(), "main")
        self.assertEqual(git(tree, "rev-parse", "HEAD"), git(self.upstream, "rev-parse", "main"))
        self.assertEqual(git(tree, "remote", "get-url", "origin").strip(), self.upstream_url)
        self.assertEqual(git(tree, "remote", "get-url", "--push", "--all", "fork").strip(), FORK_URL)
        self.assertEqual(git(tree, "remote", "get-url", "fork").strip(), FORK_URL)

    def test_a_prepared_checkout_is_reused(self):
        self.prepare()
        tree = self.workspace / "omnigent"
        commit(tree, "web/c.ts", "export const c = 1\n", "fix(web): c")

        result = self.prepare()

        self.assertEqual(result["state"], "existing")
        self.assertIn("fix(web): c", git(tree, "log", "-1", "--format=%s"))

    def test_a_checkout_without_the_fork_remote_gets_it(self):
        self.prepare()
        tree = self.workspace / "omnigent"
        git(tree, "remote", "remove", "fork")

        result = self.prepare()

        self.assertEqual(result["state"], "existing")
        self.assertEqual(git(tree, "remote", "get-url", "fork").strip(), FORK_URL)

    def test_refuses_a_checkout_with_other_remotes(self):
        for remote, url in (("origin", "https://github.com/evil/omnigent.git"), ("fork", "https://github.com/evil/omnigent.git")):
            with self.subTest(remote=remote):
                self.prepare()
                tree = self.workspace / "omnigent"
                git(tree, "remote", "set-url", remote, url)
                with self.assertRaisesRegex(CheckoutError, "remotes"):
                    self.prepare()
                git(tree, "remote", "set-url", remote, self.upstream_url if remote == "origin" else FORK_URL)

    def test_refuses_a_fork_remote_with_a_separate_push_url(self):
        self.prepare()
        tree = self.workspace / "omnigent"
        git(tree, "remote", "set-url", "--push", "fork", "https://github.com/evil/omnigent.git")

        with self.assertRaisesRegex(CheckoutError, "remotes"):
            self.prepare()

    def test_refuses_a_path_that_is_not_a_checkout(self):
        (self.workspace / "omnigent").mkdir(parents=True)
        (self.workspace / "omnigent" / "notes.txt").write_text("x")

        with self.assertRaisesRegex(CheckoutError, "not a git checkout"):
            self.prepare()

    def test_a_failed_clone_leaves_nothing_behind(self):
        with self.assertRaisesRegex(CheckoutError, "clone"):
            prepare_checkout(self.repository, self.workspace, upstream_url=(self.workspace / "missing").as_uri(), protocol="file")
        self.assertFalse((self.workspace / "omnigent").exists())

    def test_the_default_protocol_refuses_local_urls(self):
        with self.assertRaisesRegex(CheckoutError, "clone"):
            prepare_checkout(self.repository, self.workspace, upstream_url=self.upstream_url)


if __name__ == "__main__":
    unittest.main()
