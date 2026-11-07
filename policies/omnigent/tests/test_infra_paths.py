import unittest

from agent_stack_policies.infra_paths import PROTECTED_PATHS, agent_path_allowed, protected_path


class InfraPathsTest(unittest.TestCase):
    def test_protected_paths(self):
        self.assertEqual(len(PROTECTED_PATHS), 14)
        for path in (
            "kubernetes/apps/security/kyverno/app/helmrelease.yaml",
            "kubernetes/apps/omnigent-sandboxes/x/app/a.yaml",
            "kubernetes/apps/ai/hermes/app/helmrelease.yaml",
            "kubernetes/apps/ai/contribution-approver/app/a.yaml",
            "kubernetes/apps/actions-runner-system/arc/app/helmrelease.yaml",
            "kubernetes/apps/flux-system/flux-operator/app/helmrelease.yaml",
            "kubernetes/apps/ai/omnigent/app/helmrelease.yaml",
            "kubernetes/apps/system-upgrade/tuppr/app/plan.yaml",
            "kubernetes/apps/kube-system/cilium/app/helm/values.yaml",
            "kubernetes/apps/ai/litellm/ks.yaml",
            "kubernetes/apps/ai/litellm/app/secret.sops.yaml",
            "kubernetes/flux/cluster/ks.yaml",
            ".github/workflows/tag.yaml",
            "talos/talconfig.yaml",
        ):
            with self.subTest(path):
                self.assertTrue(protected_path(path))
                self.assertFalse(agent_path_allowed(path))

    def test_allowed_only_inside_an_app_directory(self):
        for path in (
            "kubernetes/apps/ai/litellm/app/helmrelease.yaml",
            "kubernetes/apps/ai/hermes-webui/app/x.yaml",
            "kubernetes/apps/securityx/a/app.yaml",
            "kubernetes/apps/ai/litellm/app/ks.yaml.md",
        ):
            with self.subTest(path):
                self.assertTrue(agent_path_allowed(path))
        for path in (
            "kubernetes/apps/ai/kustomization.yaml",
            "kubernetes/apps/kustomization.yaml",
            "kubernetes/components/common/x.yaml",
            "README.md",
            "kubernetes/apps/ai//x.yaml",
            "kubernetes/apps/ai/../security/x/y.yaml",
            "kubernetes/apps/ai/./litellm/x.yaml",
        ):
            with self.subTest(path):
                self.assertFalse(agent_path_allowed(path))
