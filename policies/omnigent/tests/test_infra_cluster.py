import unittest

from agent_stack_policies.infra_cluster import infra_cluster

from . import tool_call_event

EVENTS = [
    tool_call_event("bash", {"command": "kubectl get pods -A"}),
    tool_call_event("bash", {"command": "kubectl delete pod x"}),
    tool_call_event("bash", {"command": "helm upgrade x y"}),
    tool_call_event("bash", {"command": 'echo "unterminated'}),
    tool_call_event("bash", {}),
    tool_call_event("Read", {"file_path": "a"}),
    {"type": "tool_call", "target": "bash", "data": {"name": "bash", "arguments": "{bad"}},
    {"type": "other"},
]


class InfraClusterTest(unittest.TestCase):
    def assertVerdict(self, command, expected):
        for form in (command, ["bash", "-lc", command], f"timeout 60 {command}"):
            with self.subTest(form=form):
                decision = infra_cluster()(tool_call_event("Bash", {"command": form}))
                self.assertIsNotNone(decision)
                self.assertEqual(decision["result"], expected, (form, decision.get("reason")))

    def test_other_tools_abstain(self):
        self.assertIsNone(infra_cluster()(tool_call_event("Read", {"file_path": "a"})))

    def test_kubectl_reads_allowed(self):
        for command in (
            "kubectl get pods -A",
            "kubectl -n ai get hr litellm-proxy -o yaml",
            "kubectl --namespace=ai describe deploy litellm-proxy",
            "kubectl logs -n ai deploy/litellm-proxy --since=10m",
            "kubectl events -n ai",
            "kubectl wait hr/litellm-proxy -n ai --for=condition=Ready --timeout=20m",
            "kubectl auth whoami",
            "kubectl auth can-i list pods",
            "kubectl top pods -n ai",
            "kubectl version --client",
            "kubectl api-resources",
            "kubectl explain helmrelease.spec",
            "kubectl get --raw /readyz",
            "/opt/mise/shims/kubectl get ns",
            "kubectl",
        ):
            self.assertVerdict(command, "ALLOW")

    def test_kubectl_writes_denied(self):
        for command in (
            "kubectl apply -f x.yaml",
            "kubectl patch hr x -n ai --type merge -p '{}'",
            "kubectl edit deploy x",
            "kubectl delete pod x",
            "kubectl scale deploy x --replicas 0",
            "kubectl rollout restart deploy x",
            "kubectl exec -it x -- sh",
            "kubectl create job x --image y",
            "kubectl replace -f x.yaml",
            "kubectl label ns x a=b",
            "kubectl annotate hr x a=b",
            "kubectl cp x:/a /tmp/a",
            "kubectl port-forward svc/x 8080",
            "kubectl proxy",
            "kubectl debug x",
            "kubectl run x --image y",
            "kubectl set image deploy/x a=b",
            "kubectl drain node1",
            "kubectl cordon node1",
            "kubectl taint nodes node1 a=b:NoSchedule",
            "kubectl auth reconcile -f rbac.yaml",
            "kubectl config view --raw",
            "kubectl -n ai delete pod x",
            "kubectl --context=x apply -f y",
            "kubectl -l app=x get pods",
            "kubectl foo",
        ):
            self.assertVerdict(command, "DENY")

    def test_flux(self):
        for command in (
            "flux reconcile ks litellm-proxy -n ai --with-source",
            "flux -n ai reconcile hr litellm-proxy",
            "flux get hr -A",
            "flux logs --kind=HelmRelease --name=x -n ai",
            "flux trace deploy/x -n ai",
            "flux tree ks cluster-apps",
            "flux diff ks x --path ./kubernetes/apps/ai/x/app",
            "flux stats",
            "flux version --client",
            "flux check",
        ):
            self.assertVerdict(command, "ALLOW")
        for command in (
            "flux suspend hr x",
            "flux resume hr x",
            "flux delete hr x",
            "flux create source git x",
            "flux install",
            "flux uninstall",
            "flux bootstrap github",
            "flux reconcile hr x --force",
            "flux reconcile hr x --reset",
            "flux export hr x",
        ):
            self.assertVerdict(command, "DENY")

    def test_helm(self):
        for command in (
            "helm template x oci://ghcr.io/a/b --version 1.2.3 -f /tmp/values.yaml",
            "helm show chart oci://ghcr.io/a/b --version 1.2.3",
            "helm show values cilium/cilium --version 1.19.0",
            "helm pull oci://ghcr.io/a/b --version 1.2.3 -d /tmp/chart --untar",
            "helm search repo cilium",
            "helm history x -n ai",
            "helm status x -n ai",
            "helm get values x -n ai",
            "helm list -A",
            "helm version",
            "helm repo add cilium https://helm.cilium.io",
            "helm repo update",
            "helm dependency build /tmp/chart",
            "helm -n ai list",
        ):
            self.assertVerdict(command, "ALLOW")
        for command in (
            "helm install x y",
            "helm upgrade x y",
            "helm rollback x 1",
            "helm uninstall x",
            "helm delete x",
            "helm repo remove x",
            "helm plugin install x",
            "helm registry login ghcr.io",
            "helm push x.tgz oci://ghcr.io/a",
        ):
            self.assertVerdict(command, "DENY")

    def test_other_commands_pass(self):
        for command in ("rg kubectl", "echo kubectl delete pod x", "crane ls ghcr.io/a/b"):
            self.assertVerdict(command, "ALLOW")

    def test_unparseable_denied(self):
        self.assertEqual(infra_cluster()(tool_call_event("Bash", {"command": 'echo "u'}))["result"], "DENY")
