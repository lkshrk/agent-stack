import unittest

from . import require_yaml

require_yaml()

from agent_stack_policies.manifests import ALLOWED_KINDS, HELMRELEASE_FIXED_FIELDS, POD_FIELDS, check_change

APP = "kubernetes/apps/ai/litellm/app/"

DEPLOYMENT = """\
apiVersion: apps/v1
kind: Deployment
metadata:
  name: web
spec:
  template:
    spec:
      containers:
        - name: web
          image: ghcr.io/acme/web:1.0.0
          env:
            - name: LOG_LEVEL
              value: info
            - name: TOKEN
              valueFrom:
                secretKeyRef:
                  name: web-secret
                  key: token
"""

HELMRELEASE = """\
apiVersion: helm.toolkit.fluxcd.io/v2
kind: HelmRelease
metadata:
  name: web
spec:
  interval: 1h
  chartRef:
    kind: OCIRepository
    name: web
  values:
    resources:
      limits:
        memory: 512Mi
    env:
      LOG_LEVEL: info
    ingress:
      host: web.example.test
    rbac:
      create: false
"""

KUSTOMIZATION = """\
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
resources:
  - ./helmrelease.yaml
  - ./service.yaml
"""

SERVICE = """\
apiVersion: v1
kind: Service
metadata:
  name: web
spec:
  ports:
    - port: 80
      targetPort: 8080
"""

CONFIGMAP = """\
apiVersion: v1
kind: ConfigMap
metadata:
  name: web-config
data:
  a: "1"
"""

BINDING = """\
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRoleBinding
metadata:
  name: web
roleRef:
  apiGroup: rbac.authorization.k8s.io
  kind: ClusterRole
  name: view
subjects:
  - kind: ServiceAccount
    name: web
    namespace: ai
"""


def added(text, name="extra.yaml"):
    return check_change(APP + name, None, text)


def changed(before, after, name="deployment.yaml"):
    return check_change(APP + name, before, after)


class BypassTest(unittest.TestCase):
    def assert_rejected(self, errors, contains=""):
        self.assertTrue(errors, "expected a violation")
        self.assertTrue(any(contains in e for e in errors), errors)

    def test_cluster_role_in_every_spelling(self):
        payloads = (
            '{"apiVersion":"rbac.authorization.k8s.io/v1","kind":"ClusterRole","metadata":{"name":"x"},"rules":[]}',
            "{apiVersion: rbac.authorization.k8s.io/v1, kind: ClusterRole, metadata: {name: x}}",
            "{kind: ClusterRole}",
            '"apiVersion": "rbac.authorization.k8s.io/v1"\n"kind": "ClusterRole"\nmetadata: {name: x}\n',
            "apiVersion: rbac.authorization.k8s.io/v1\nkind : ClusterRole\nmetadata: {name: x}\n",
            'apiVersion: rbac.authorization.k8s.io/v1\nkind: "Cluster\\x52ole"\nmetadata: {name: x}\n',
            "apiVersion: rbac.authorization.k8s.io/v1\nkind:\n  ClusterRole\nmetadata: {name: x}\n",
            "apiVersion: v1\nkind: ConfigMap\nmetadata: {name: a}\n---\napiVersion: rbac.authorization.k8s.io/v1\n"
            "kind: ClusterRole\nmetadata: {name: x}\n",
        )
        for payload in payloads:
            with self.subTest(payload[:40]):
                self.assert_rejected(added(payload), "not an allowed kind")
        for name in ("role.json", "role.yml"):
            self.assert_rejected(added(payloads[0], name), "not an allowed kind")

    def test_list_document_is_rejected(self):
        self.assert_rejected(added("- apiVersion: rbac.authorization.k8s.io/v1\n  kind: ClusterRole\n"), "mapping")

    def test_yaml_tags_and_directives_are_rejected(self):
        for payload in ("apiVersion: v1\nkind: !!str ClusterRole\n", "%TAG ! tag:x,2000:\n---\nkind: !a ConfigMap\n",
                        "apiVersion: v1\nkind: ConfigMap\ndata: !!binary aGk=\n"):
            self.assert_rejected(added(payload), "tags")

    def test_line_breaks_and_encoding(self):
        for sep, name in (("\r", "CR"), ("\x85", "NEL"), (" ", "LS"), (" ", "PS")):
            payload = SERVICE.replace("\n", sep, 3)
            self.assert_rejected(added(payload), name)
        self.assert_rejected(added(SERVICE.replace("\n", "\r\n")), "CR")
        self.assert_rejected(added(b"apiVersion: v1\nkind: \xffService\n"), "UTF-8")
        self.assert_rejected(check_change(APP + "README.md", None, "line\rbreak"), "CR")

    def test_parse_errors_duplicates_and_aliases(self):
        self.assert_rejected(added("apiVersion: v1\nkind: [Service\n"), "does not parse")
        self.assert_rejected(added("apiVersion: v1\nkind: Service\nkind: ClusterRole\nmetadata: {name: x}\n"), "parse")
        laughs = "a: &a [x, x, x, x, x, x, x, x, x, x]\n" + "".join(
            f"{chr(98 + i)}: &{chr(98 + i)} [{', '.join([f'*{chr(97 + i)}'] * 10)}]\n" for i in range(6))
        self.assert_rejected(added("apiVersion: v1\nkind: ConfigMap\nmetadata: {name: l}\ndata:\n  " +
                                   laughs.replace("\n", "\n  ")), "expands")
        self.assert_rejected(added("apiVersion: v1\nkind: ConfigMap\nmetadata: {name: r}\nloop: &l [*l]\n"), "through aliases")
        self.assert_rejected(added(SERVICE + "---\n" + SERVICE), "twice")

    def test_flux_kinds_in_an_app_file(self):
        for api, kind in (("kustomize.toolkit.fluxcd.io/v1", "Kustomization"), ("source.toolkit.fluxcd.io/v1", "GitRepository"),
                          ("source.toolkit.fluxcd.io/v1", "OCIRepository"), ("source.toolkit.fluxcd.io/v1", "HelmRepository")):
            payload = f"apiVersion: {api}\nkind: {kind}\nmetadata: {{name: x}}\nspec: {{}}\n"
            self.assert_rejected(added(payload), f"{api} {kind}, which is not an allowed kind")

    def test_kustomization_changes(self):
        bad = {
            "../../security": KUSTOMIZATION + "  - ../../security\n",
            "a URL": KUSTOMIZATION + "  - https://example.test/x.yaml\n",
            "a subdirectory": KUSTOMIZATION + "  - ./sub/x.yaml\n",
            "patches": KUSTOMIZATION + "patches:\n  - path: p.yaml\n",
            "namespace": KUSTOMIZATION + "namespace: kube-system\n",
            "components": KUSTOMIZATION + "components:\n  - ../../components/x\n",
            "secretGenerator": KUSTOMIZATION + "secretGenerator:\n  - name: x\n    literals: [a=b]\n",
            "a removed resource": KUSTOMIZATION.replace("  - ./service.yaml\n", ""),
        }
        for name, after in bad.items():
            with self.subTest(name):
                self.assertTrue(check_change(APP + "kustomization.yaml", KUSTOMIZATION, after), name)
        bare = "resources:\n  - ./helmrelease.yaml\n"
        self.assertTrue(check_change(APP + "kustomization.yaml", bare, bare + "  - ../x.yaml\n"))

    def test_edits_to_existing_rbac(self):
        for old, new in (("name: view", "name: cluster-admin"), ("name: web\n    namespace", "name: other\n    namespace")):
            after = BINDING.replace(old, new)
            self.assert_rejected(check_change(APP + "rbac.yaml", BINDING, after), "not an allowed kind")
        self.assert_rejected(check_change(APP + "rbac.yaml", BINDING, BINDING + "  - kind: User\n    name: x\n"))
        self.assert_rejected(check_change(APP + "rbac.yaml", BINDING, None), "removes")

    def test_new_secret_references(self):
        after = DEPLOYMENT.replace("name: web-secret", "name: other-secret")
        self.assert_rejected(changed(DEPLOYMENT, after), "secretKeyRef")
        for addition in ("          envFrom:\n            - secretRef:\n                name: db\n",
                         "      volumes:\n        - name: s\n          secret:\n            secretName: db\n",
                         "      imagePullSecrets:\n        - name: regcred\n"):
            self.assert_rejected(changed(DEPLOYMENT, DEPLOYMENT + addition))

    def test_pod_fields(self):
        for addition in ("      serviceAccountName: admin\n", "      serviceAccount: admin\n",
                         "      automountServiceAccountToken: true\n", "      hostNetwork: true\n", "      hostPID: true\n",
                         "      hostIPC: true\n",
                         "      volumes:\n        - name: h\n          hostPath:\n            path: /\n",
                         "          securityContext:\n            privileged: true\n",
                         "          securityContext:\n            runAsUser: 0\n",
                         "          securityContext:\n            capabilities:\n              add: [SYS_ADMIN]\n"):
            with self.subTest(addition.strip()[:30]):
                self.assertTrue(changed(DEPLOYMENT, DEPLOYMENT + addition))
        cron = ("apiVersion: batch/v1\nkind: CronJob\nmetadata: {name: c}\nspec:\n  jobTemplate:\n    spec:\n      template:\n"
                "        spec:\n          containers: [{name: c, image: x}]\n")
        self.assertTrue(changed(cron, cron + "          hostNetwork: true\n", "cronjob.yaml"))

    def test_new_document_may_not_set_a_namespace(self):
        self.assert_rejected(added(CONFIGMAP.replace("name: web-config", "name: web-config\n  namespace: kube-system")),
                             "metadata.namespace")
        moved = SERVICE.replace("name: web", "name: web\n  namespace: security")
        self.assert_rejected(changed(SERVICE, moved, "service.yaml"), "metadata.namespace")

    def test_helmrelease_rules(self):
        bad = {
            "chartRef": HELMRELEASE.replace("name: web\n  values", "name: evil\n  values"),
            "chart": HELMRELEASE.replace("  chartRef:\n    kind: OCIRepository\n    name: web\n",
                                         "  chart:\n    spec:\n      chart: evil\n"),
            "rbac": HELMRELEASE.replace("create: false", "create: true"),
            "extraObjects": HELMRELEASE + "    extraObjects:\n      - apiVersion: v1\n        kind: Namespace\n",
            "serviceAccount": HELMRELEASE + "    serviceAccount:\n      name: admin\n",
            "hostNetwork": HELMRELEASE + "    hostNetwork: true\n",
            "postRenderers": HELMRELEASE + "  postRenderers:\n    - kustomize: {patches: []}\n",
            "targetNamespace": HELMRELEASE + "  targetNamespace: kube-system\n",
            "valuesFrom": HELMRELEASE + "  valuesFrom:\n    - kind: Secret\n      name: x\n",
            "secret value": HELMRELEASE + "    existingSecret: other\n",
        }
        for name, after in bad.items():
            with self.subTest(name):
                self.assertTrue(check_change(APP + "helmrelease.yaml", HELMRELEASE, after), name)
        with_chart = HELMRELEASE.replace("  chartRef:\n    kind: OCIRepository\n    name: web\n",
                                         "  chart:\n    spec:\n      chart: web\n      version: 1.0.0\n")
        self.assertTrue(check_change(APP + "helmrelease.yaml", with_chart, with_chart.replace("chart: web", "chart: evil")))
        self.assertTrue(added(HELMRELEASE, "new-helmrelease.yaml"))

    def test_disallowed_extensions(self):
        for name in ("script.sh", "Dockerfile", "values.toml", "x.yaml.sh"):
            self.assertTrue(check_change(APP + name, None, "x"), name)


class LegitimateFixTest(unittest.TestCase):
    def test_image_tag_and_env_change(self):
        after = DEPLOYMENT.replace("web:1.0.0", "web:1.0.1").replace("value: info", "value: debug")
        self.assertEqual(changed(DEPLOYMENT, after), [])
        extra_env = DEPLOYMENT.replace("          env:\n", "          env:\n            - name: NEW\n              value: x\n")
        self.assertEqual(changed(DEPLOYMENT, extra_env), [])

    def test_helmrelease_values_change(self):
        after = (HELMRELEASE.replace("memory: 512Mi", "memory: 1Gi").replace("LOG_LEVEL: info", "LOG_LEVEL: debug")
                 .replace("web.example.test", "app.example.test"))
        self.assertEqual(check_change(APP + "helmrelease.yaml", HELMRELEASE, after), [])

    def test_configmap_key_added(self):
        self.assertEqual(check_change(APP + "configmap.yaml", CONFIGMAP, CONFIGMAP + '  b: "2"\n'), [])

    def test_local_resource_added_to_kustomization(self):
        after = KUSTOMIZATION + "  - ./configmap.yaml\n  - pdb.yml\n"
        self.assertEqual(check_change(APP + "kustomization.yaml", KUSTOMIZATION, after), [])
        images = KUSTOMIZATION + "images:\n  - name: web\n    newTag: 1.0.1\n"
        self.assertEqual(check_change(APP + "kustomization.yaml", KUSTOMIZATION, images), [])

    def test_service_port_change(self):
        self.assertEqual(check_change(APP + "service.yaml", SERVICE, SERVICE.replace("port: 80", "port: 8080")), [])

    def test_new_allowed_documents_and_markdown(self):
        self.assertEqual(added(CONFIGMAP), [])
        pdb = "apiVersion: policy/v1\nkind: PodDisruptionBudget\nmetadata: {name: web}\nspec: {maxUnavailable: 1}\n"
        self.assertEqual(added(pdb, "pdb.yaml"), [])
        self.assertEqual(check_change(APP + "README.md", "old", "new notes"), [])
        self.assertEqual(check_change(APP + "service.json", None, '{"apiVersion": "v1", "kind": "Service", '
                                                                  '"metadata": {"name": "web"}}'), [])

    def test_anchors_and_aliases_as_used_in_h_cloud(self):
        before = DEPLOYMENT.replace("          image: ghcr.io/acme/web:1.0.0\n",
                                    "          image: ghcr.io/acme/web:1.0.0\n          livenessProbe: &probe\n"
                                    "            httpGet: {path: /healthz, port: 8080}\n          readinessProbe: *probe\n")
        after = before.replace("web:1.0.0", "web:1.0.1").replace("/healthz", "/ready")
        self.assertEqual(changed(before, after), [])
        sneaky = before.replace("livenessProbe: &probe", "securityContext: &probe").replace(
            "httpGet: {path: /healthz, port: 8080}", "privileged: true")
        self.assertTrue(changed(before, sneaky))

    def test_unchanged_non_allowlisted_document_may_stay(self):
        before = BINDING + "---\n" + CONFIGMAP
        self.assertEqual(check_change(APP + "mixed.yaml", before, BINDING + "---\n" + CONFIGMAP + '  b: "2"\n'), [])

    def test_constants_are_named(self):
        self.assertIn(("apps/v1", "Deployment"), ALLOWED_KINDS)
        self.assertNotIn(("rbac.authorization.k8s.io/v1", "ClusterRole"), ALLOWED_KINDS)
        self.assertIn("hostNetwork", POD_FIELDS)
        self.assertIn("postRenderers", HELMRELEASE_FIXED_FIELDS)


POD = """\
apiVersion: apps/v1
kind: Deployment
metadata:
  name: pod
spec:
  template:
    spec:
      securityContext:
        runAsUser: 1000
      containers:
        - name: a
          image: ghcr.io/acme/a:1.0.0
          envFrom:
            - secretRef:
                name: a-secret
        - name: b
          image: ghcr.io/acme/b:1.0.0
"""

VALUES_CM = """\
apiVersion: v1
kind: ConfigMap
metadata:
  name: web-image-values
data:
  values.yaml: |
    image:
      tag: 1.0.0
    rbac:
      create: false
  notes.txt: just text
"""

PVC = """\
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: data
spec:
  accessModes: [ReadWriteOnce]
  storageClassName: ceph-block
  resources:
    requests:
      storage: 1Gi
"""


class ReviewSixTest(unittest.TestCase):
    def rejected(self, before, after, name="deployment.yaml", contains="", previous=None):
        errors = check_change(APP + name, before, after, previous=previous)
        self.assertTrue(errors, "expected a violation")
        self.assertTrue(any(contains in e for e in errors), errors)

    def test_rename_across_directories_counts_as_added(self):
        other = "kubernetes/apps/ai/other/app/deployment.yaml"
        self.rejected(DEPLOYMENT, DEPLOYMENT, previous=other, contains="secretKeyRef")
        self.assertEqual(check_change(APP + "deployment.yaml", DEPLOYMENT, DEPLOYMENT, previous=APP + "old.yaml"), [])

    def test_secret_like_keys_and_volume_sources(self):
        additions = (
            "      volumes:\n        - name: s\n          secret:\n            secretName: other\n",
            "          env:\n            - name: X\n              value: x\n          bearerTokenSecret: {name: t}\n",
            "      clientSecret: abc\n",
            "      basicAuth: {username: {name: u}}\n",
            "      authorization: {credentials: {name: c}}\n",
            "      oauth2: {clientId: {secret: {name: c}}}\n",
            "      volumes:\n        - name: c\n          csi: {driver: secrets-store.csi.k8s.io}\n",
            "      volumes:\n        - name: f\n          flexVolume: {driver: x}\n",
            "      volumes:\n        - name: n\n          nfs: {server: 10.0.0.1, path: /}\n",
            "      volumes:\n        - name: i\n          iscsi: {targetPortal: x, iqn: y, lun: 0}\n",
            "      volumes:\n        - name: p\n          projected:\n            sources:\n              - serviceAccountToken:"
            " {path: token}\n",
        )
        for addition in additions:
            with self.subTest(addition.strip()[:40]):
                self.rejected(POD, POD + addition)

    def test_more_pod_fields(self):
        additions = (
            "      shareProcessNamespace: true\n", "      hostUsers: false\n", "      nodeName: node-1\n",
            "      priorityClassName: system-cluster-critical\n", "      hostAliases: [{ip: 1.2.3.4, hostnames: [x]}]\n",
            "      securityContext:\n        sysctls: [{name: net.ipv4.ip_forward, value: '1'}]\n",
            "          securityContext: {allowPrivilegeEscalation: true}\n",
            "          securityContext: {runAsNonRoot: false}\n", "          securityContext: {procMount: Unmasked}\n",
            "          securityContext: {seccompProfile: {type: Unconfined}}\n",
            "          securityContext: {appArmorProfile: {type: Unconfined}}\n",
            "          ports: [{containerPort: 80, hostPort: 80}]\n",
        )
        for addition in additions:
            with self.subTest(addition.strip()[:40]):
                self.rejected(POD, POD + addition)

    def test_user_and_group_ids_must_be_plain_positive_ints(self):
        for value in ("0", "'1000'", "1000.0", "true", "-1", "null"):
            for field in ("runAsUser", "runAsGroup", "fsGroup"):
                with self.subTest(field=field, value=value):
                    after = POD.replace("runAsUser: 1000", f"runAsUser: 1000\n        {field}: {value}"
                                        if field != "runAsUser" else f"runAsUser: {value}")
                    self.rejected(POD, after, contains=field)
        ok = POD.replace("runAsUser: 1000", "runAsUser: 1001\n        runAsGroup: 1001\n        fsGroup: 2000")
        self.assertEqual(changed(POD, ok), [])

    def test_copying_an_existing_secret_ref_to_another_container_is_caught(self):
        after = POD + "          envFrom:\n            - secretRef:\n                name: a-secret\n"
        self.rejected(POD, after, contains="secretRef")
        reordered = POD.replace("        - name: a\n", "        - name: z\n          image: z:1\n        - name: a\n")
        self.assertEqual(changed(POD, reordered), [])

    def test_configmap_values_follow_the_values_rules(self):
        for old, new in (("create: false", "create: true"), ("tag: 1.0.0", "tag: 1.0.0\n    rawResources: {x: {}}"),
                         ("tag: 1.0.0", "tag: 1.0.0\n    serviceAccount: {name: admin}"),
                         ("tag: 1.0.0", "tag: 1.0.0\n    persistence: {h: {type: hostPath, hostPath: /}}"),
                         ("tag: 1.0.0", "tag: 1.0.0\n    env: [{valueFrom: {secretKeyRef: {name: s, key: k}}}]")):
            with self.subTest(new.strip()[-30:]):
                self.rejected(VALUES_CM, VALUES_CM.replace(old, new), "configmap.yaml")
        bumped = VALUES_CM.replace("tag: 1.0.0", "tag: 1.0.1").replace("just text", '"plain: not: yaml: {"')
        self.assertEqual(check_change(APP + "configmap.yaml", VALUES_CM, bumped), [])
        tagged = VALUES_CM.replace("tag: 1.0.0", "tag: !!str 1.0.0")
        self.rejected(VALUES_CM, tagged, "configmap.yaml", "data.values.yaml")

    def test_configmap_binary_data_is_checked_too(self):
        import base64

        def binary(text):
            encoded = base64.b64encode(text.encode()).decode()
            return f"apiVersion: v1\nkind: ConfigMap\nmetadata: {{name: b}}\nbinaryData:\n  values.yaml: {encoded}\n"
        self.rejected(binary("rbac: {create: false}\n"), binary("rbac: {create: true}\n"), "configmap.yaml")
        self.assertEqual(check_change(APP + "configmap.yaml", binary("a: 1\n"), binary("a: 2\n")), [])

    def test_more_sensitive_values(self):
        release = HELMRELEASE.replace("rbac:\n      create: false", "controllers: {}")
        for key in ("rawResources", "extraResources", "additionalResources", "extraManifests", "extraDeploy"):
            with self.subTest(key):
                self.rejected(release, release + f"    {key}: {{x: {{}}}}\n", "helmrelease.yaml", key)

    def test_pvc_binding_fields(self):
        for after in (PVC + "  volumeName: pv-secret\n", PVC + "  dataSource: {kind: PersistentVolumeClaim, name: x}\n",
                      PVC + "  dataSourceRef: {kind: VolumeSnapshot, name: x}\n",
                      PVC.replace("ceph-block", "openebs-hostpath")):
            self.rejected(PVC, after, "pvc.yaml")
        self.assertEqual(check_change(APP + "pvc.yaml", PVC, PVC.replace("1Gi", "2Gi")), [])

    def test_removing_a_kustomization_is_a_violation(self):
        self.rejected(KUSTOMIZATION, None, "kustomization.yaml", "removes")

    def test_merge_keys_are_rejected(self):
        payload = "apiVersion: v1\nkind: ConfigMap\nmetadata: {name: m}\n<<: {kind: ClusterRole}\n"
        self.assertTrue(any("merge keys" in e for e in added(payload)), added(payload))

    def test_deep_nesting_is_an_error_not_a_crash(self):
        self.assertTrue(added("a: " + "[" * 20000 + "]" * 20000 + "\n"))
        self.assertTrue(added("{" * 5000 + "}" * 5000))

    def test_line_separator_constants_are_escaped_in_the_source(self):
        from pathlib import Path

        import agent_stack_policies.manifests as manifests

        source = Path(manifests.__file__).read_text(encoding="utf-8")
        for char in (" ", " ", "\x85"):
            self.assertNotIn(char, source)


TWO = """\
apiVersion: apps/v1
kind: Deployment
metadata:
  name: two
spec:
  template:
    spec:
      containers:
        - name: a
          image: ghcr.io/acme/a:1.0.0
          securityContext:
            privileged: true
            runAsUser: 0
          env:
            - name: TOKEN
              valueFrom:
                secretKeyRef: {name: s, key: k}
        - name: b
          image: ghcr.io/acme/b:1.0.0
      volumes:
        - name: host
          hostPath: {path: /var/lib/a}
        - name: scratch
          emptyDir: {}
"""


def swap(text, old_block, new_block):
    assert text.count(old_block) == 1, old_block
    return text.replace(old_block, new_block)


class ReviewSevenTest(unittest.TestCase):
    def rejected(self, before, after, name="deployment.yaml", contains=""):
        errors = check_change(APP + name, before, after)
        self.assertTrue(errors, "expected a violation")
        self.assertTrue(any(contains in e for e in errors), errors)

    def cm(self, value):
        body = "\n".join("    " + line for line in value.splitlines())
        return f"apiVersion: v1\nkind: ConfigMap\nmetadata: {{name: v}}\ndata:\n  values.yaml: |\n{body}\n"

    def test_timestamp_next_to_rbac_is_not_hidden(self):
        before = self.cm("d: 2001-01-01\nrbac: {create: false}")
        self.rejected(before, self.cm("d: 2001-99-99\nrbac: {create: true}"), "configmap.yaml", "rbac")
        self.rejected(self.cm("a: 1"), self.cm("d: 2001-99-99\nrbac: {create: true}"), "configmap.yaml", "rbac")
        main = "apiVersion: v1\nkind: ConfigMap\nmetadata: {name: t}\ndata:\n  when: 2001-99-99\n"
        self.assertEqual(added(main), [])

    def test_errors_other_than_not_yaml_are_violations(self):
        chain = "a0: &a0 x\n" + "".join(f"a{i}: &a{i} [*a{i - 1}]\n" for i in range(1, 300))
        self.rejected(self.cm("a: 1"), self.cm(chain), "configmap.yaml", "data.values.yaml")
        self.rejected(self.cm("a: 1"), self.cm("loop: &l [*l]"), "configmap.yaml", "data.values.yaml")
        laughs = "a: &a [x, x, x, x, x, x, x, x, x, x]\n" + "".join(
            f"{chr(98 + i)}: &{chr(98 + i)} [{', '.join([f'*{chr(97 + i)}'] * 10)}]\n" for i in range(6))
        self.rejected(self.cm("a: 1"), self.cm(laughs), "configmap.yaml", "expands")
        self.rejected(self.cm("a: 1"), self.cm("a: *undefined"), "configmap.yaml", "data.values.yaml")
        self.assertEqual(check_change(APP + "configmap.yaml", self.cm("a: 1"), self.cm("a: [unclosed")), [])
        self.assertEqual(check_change(APP + "configmap.yaml", self.cm("a: 1"), self.cm("just: some: text")), [])

    def test_top_level_list_in_data_follows_the_rules(self):
        self.rejected(self.cm("a: 1"), self.cm("- securityContext: {privileged: true}"), "configmap.yaml", "privileged")
        self.assertEqual(check_change(APP + "configmap.yaml", self.cm("- a: 1"), self.cm("- a: 2\n- b: 3")), [])

    def test_moving_sensitive_fields_between_containers_is_caught(self):
        moves = {
            "privileged": ("            privileged: true\n", "", "          image: ghcr.io/acme/b:1.0.0\n",
                           "          image: ghcr.io/acme/b:1.0.0\n          securityContext: {privileged: true}\n"),
            "runAsUser": ("            runAsUser: 0\n", "", "          image: ghcr.io/acme/b:1.0.0\n",
                          "          image: ghcr.io/acme/b:1.0.0\n          securityContext: {runAsUser: 0}\n"),
            "secretKeyRef": ("          env:\n            - name: TOKEN\n              valueFrom:\n"
                             "                secretKeyRef: {name: s, key: k}\n", "",
                             "          image: ghcr.io/acme/b:1.0.0\n",
                             "          image: ghcr.io/acme/b:1.0.0\n          env:\n            - name: TOKEN\n"
                             "              valueFrom:\n                secretKeyRef: {name: s, key: k}\n"),
            "hostPath": ("          hostPath: {path: /var/lib/a}\n", "          emptyDir: {}\n",
                         "        - name: scratch\n          emptyDir: {}\n",
                         "        - name: scratch\n          hostPath: {path: /var/lib/a}\n"),
        }
        for field, (old1, new1, old2, new2) in moves.items():
            with self.subTest(field):
                self.rejected(TWO, swap(swap(TWO, old1, new1), old2, new2), contains=field)

    def test_reordering_and_inserting_named_entries_passes(self):
        a = TWO[TWO.index("        - name: a\n"):TWO.index("        - name: b\n")]
        b = TWO[TWO.index("        - name: b\n"):TWO.index("      volumes:\n")]
        self.assertEqual(changed(TWO, swap(TWO, a + b, b + a)), [])
        inserted = swap(TWO, "          env:\n", "          env:\n            - name: NEW\n              value: x\n")
        self.assertEqual(changed(TWO, inserted), [])
        volume = swap(TWO, "      volumes:\n", "      volumes:\n        - name: cache\n          emptyDir: {}\n")
        self.assertEqual(changed(TWO, volume), [])

    def test_alias_tricks(self):
        scalar = POD.replace("runAsUser: 1000", "runAsUser: *uid").replace("metadata:\n", "uid: &uid 0\nmetadata:\n", 1)
        self.rejected(POD, scalar, contains="runAsUser")
        key = POD.replace("  name: pod\n", "  name: pod\n  label: &k privileged\n").replace(
            "        runAsUser: 1000\n", "        runAsUser: 1000\n      podSecurity: {*k : true}\n")
        self.rejected(POD, key, contains="privileged")
        shared = POD.replace("        - name: b\n          image: ghcr.io/acme/b:1.0.0\n",
                             "        - name: b\n          image: ghcr.io/acme/b:1.0.0\n"
                             "          securityContext: &sc {readOnlyRootFilesystem: true}\n"
                             "        - name: c\n          image: ghcr.io/acme/c:1.0.0\n          securityContext: *sc\n")
        self.rejected(shared, shared.replace("{readOnlyRootFilesystem: true}", "{privileged: true}"), contains="privileged")
        self.assertEqual(changed(shared, shared.replace("c:1.0.0", "c:1.0.1")), [])

    def test_kustomization_constants(self):
        from agent_stack_policies.manifests import KUSTOMIZATION_FILES

        self.assertEqual(KUSTOMIZATION_FILES, ("kustomization.yaml", "kustomization.yml"))


if __name__ == "__main__":
    unittest.main()
