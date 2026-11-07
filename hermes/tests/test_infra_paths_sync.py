import unittest

from agent_stack_hermes import postrun


class InfraPathsSyncTest(unittest.TestCase):
    def test_sandbox_and_post_run_share_the_protected_paths(self):
        from agent_stack_policies import infra_paths

        self.assertEqual(infra_paths.PROTECTED_PATHS, postrun.PROTECTED_PATHS)
        self.assertEqual(infra_paths.AGENT_ALLOWED_ROOT, postrun.AGENT_ALLOWED_ROOT)
        self.assertEqual(infra_paths.AGENT_ALLOWED_MIN_SEGMENTS, postrun.AGENT_ALLOWED_MIN_SEGMENTS)
        self.assertEqual(infra_paths.RENOVATE, postrun.RENOVATE)
        self.assertEqual(infra_paths.RENOVATE_PREFIX, postrun.RENOVATE_PREFIX)
        self.assertEqual(infra_paths.MAX_UNTRUSTED_PUSHES, postrun.MAX_UNTRUSTED_PUSHES)
        self.assertEqual(infra_paths.MAX_PUSH_FILES, postrun.MAX_PUSH_FILES)
        self.assertEqual(infra_paths.MAX_AGENT_MERGES, postrun.MAX_AGENT_MERGES)
        self.assertEqual(infra_paths.COMPARE_FILE_CAP, postrun.COMPARE_FILE_CAP)
        self.assertEqual(infra_paths.MAX_CONTENT_BYTES, postrun.MAX_CONTENT_BYTES)
        self.assertEqual(infra_paths.REWRITE_BASE_STATUSES, postrun.REWRITE_BASE_STATUSES)
