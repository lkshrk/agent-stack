import json
import posixpath
import re

import yaml

ALLOWED_EXTENSIONS = (".yaml", ".yml", ".json", ".md")
UNPARSED_EXTENSIONS = (".md",)
FORBIDDEN_CHARACTERS = {"\r": "CR", "\x85": "NEL", " ": "LS", " ": "PS"}
KUSTOMIZE = ("kustomize.config.k8s.io/v1beta1", "Kustomization")
KUSTOMIZATION_FILES = ("kustomization.yaml", "kustomization.yml", "Kustomization")
HELMRELEASE = ("helm.toolkit.fluxcd.io/v2", "HelmRelease")
ALLOWED_KINDS = frozenset({
    ("apps/v1", "Deployment"),
    ("apps/v1", "StatefulSet"),
    ("apps/v1", "DaemonSet"),
    ("batch/v1", "Job"),
    ("batch/v1", "CronJob"),
    ("v1", "Service"),
    ("v1", "ConfigMap"),
    ("v1", "PersistentVolumeClaim"),
    ("networking.k8s.io/v1", "Ingress"),
    ("gateway.networking.k8s.io/v1", "HTTPRoute"),
    ("policy/v1", "PodDisruptionBudget"),
    ("autoscaling/v2", "HorizontalPodAutoscaler"),
    ("monitoring.coreos.com/v1", "ServiceMonitor"),
    ("monitoring.coreos.com/v1", "PodMonitor"),
    ("monitoring.coreos.com/v1", "PrometheusRule"),
    HELMRELEASE,
    KUSTOMIZE,
})
POD_FIELDS = frozenset({
    "serviceAccountName",
    "serviceAccount",
    "automountServiceAccountToken",
    "hostPath",
    "hostNetwork",
    "hostPID",
    "hostIPC",
    "privileged",
})
SECRET_FIELDS = frozenset({"secretKeyRef", "secretRef", "secret", "imagePullSecrets"})
HELMRELEASE_FIXED_FIELDS = (
    "chart",
    "chartRef",
    "postRenderers",
    "serviceAccountName",
    "targetNamespace",
    "storageNamespace",
    "kubeConfig",
    "valuesFrom",
)
SENSITIVE_VALUES = re.compile(
    r"rbac|serviceaccount|clusterrole|extraobjects|extramanifests|extradeploy|hostnetwork|hostpath|privileged|secret",
    re.IGNORECASE | re.ASCII,
)
KUSTOMIZATION_MUTABLE = ("images", "resources")
_LOCAL_RESOURCE = re.compile(r"(\./)?[A-Za-z0-9][A-Za-z0-9._-]*\.ya?ml", re.ASCII)


class _UniqueKeyLoader(yaml.SafeLoader):
    pass


def _unique_mapping(loader, node, deep=False):
    seen = set()
    for key_node, _ in node.value:
        key = loader.construct_object(key_node, deep=True)
        try:
            duplicate = key in seen
        except TypeError:
            raise yaml.constructor.ConstructorError(None, None, "unhashable mapping key", key_node.start_mark) from None
        if duplicate:
            raise yaml.constructor.ConstructorError(None, None, f"duplicate key {key!r}", key_node.start_mark)
        seen.add(key)
    return yaml.SafeLoader.construct_mapping(loader, node, deep)


_UniqueKeyLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _unique_mapping)


def _decode(path: str, content: str | bytes) -> tuple[str | None, list[str]]:
    if isinstance(content, bytes):
        try:
            content = content.decode("utf-8")
        except UnicodeDecodeError:
            return None, [f"{path}: not UTF-8"]
    found = [name for char, name in FORBIDDEN_CHARACTERS.items() if char in content]
    if found:
        return None, [f"{path}: contains {', '.join(found)} line breaks"]
    return content, []


def _documents(path: str, text: str) -> tuple[list | None, list[str]]:
    try:
        for token in yaml.scan(text, Loader=yaml.SafeLoader):
            if isinstance(token, (yaml.TagToken, yaml.DirectiveToken)):
                return None, [f"{path}: YAML tags and directives are not allowed"]
            if isinstance(token, yaml.AliasToken):
                return None, [f"{path}: YAML aliases are not allowed"]
        return [doc for doc in yaml.load_all(text, Loader=_UniqueKeyLoader) if doc is not None], []
    except yaml.YAMLError as exc:
        return None, [f"{path}: does not parse: {type(exc).__name__}"]


def _identity(path: str, doc: dict) -> tuple:
    api, kind = doc.get("apiVersion"), doc.get("kind")
    if api is None and kind is None and posixpath.basename(path) in KUSTOMIZATION_FILES:
        api, kind = KUSTOMIZE
    metadata = _mapping(doc.get("metadata"))
    return (_text(api), _text(kind), _text(metadata.get("namespace")), _text(metadata.get("name")))


def _mapping(value) -> dict:
    return value if isinstance(value, dict) else {}


def _text(value) -> str:
    return value if isinstance(value, str) else json.dumps(value, sort_keys=True, default=str)


def _index(path: str, docs: list) -> tuple[dict, list, list[str]]:
    keyed: dict = {}
    other: list = []
    errors: list[str] = []
    for doc in docs:
        if not isinstance(doc, dict):
            other.append(doc)
            continue
        key = _identity(path, doc)
        if key in keyed:
            errors.append(f"{path}: {key[1]} {key[3]} appears twice")
        keyed[key] = doc
    return keyed, other, errors


def _walk(node, trail=()):
    if isinstance(node, dict):
        for key, value in node.items():
            yield trail + (key,), key, value
            yield from _walk(value, trail + (key,))
    elif isinstance(node, list):
        for i, value in enumerate(node):
            yield from _walk(value, trail + (i,))


def _canon(value) -> str:
    return json.dumps(value, sort_keys=True, default=str)


def _sensitive(doc) -> set[tuple[str, str]]:
    found: set[tuple[str, str]] = set()
    for _, key, value in _walk(doc):
        if key in POD_FIELDS or key in SECRET_FIELDS:
            found.add((key, _canon(value)))
        elif key == "capabilities" and isinstance(value, dict):
            added = value.get("add")
            for cap in added if isinstance(added, list) else [added]:
                found.add(("capabilities.add", _canon(cap)))
        elif key == "runAsUser" and value == 0:
            found.add(("runAsUser", "0"))
    return found


def _sensitive_values(values) -> dict[tuple, str]:
    out: dict[tuple, str] = {}
    for trail, key, value in _walk(values):
        if isinstance(key, str) and SENSITIVE_VALUES.search(key) and not any(trail[:n] in out for n in range(len(trail))):
            out[trail] = _canon(value)
    return out


def _field_rules(where: str, key: tuple, old: dict | None, new: dict) -> list[str]:
    errors = []
    if old is None and isinstance(new.get("metadata"), dict) and "namespace" in new["metadata"]:
        errors.append(f"{where}: a new document may not set metadata.namespace")
    gained = _sensitive(new) - _sensitive(old or {})
    for field, value in sorted(gained):
        errors.append(f"{where}: adds or changes {field} ({value[:60]})")
    if key[:2] == HELMRELEASE:
        old_spec, new_spec = _mapping((old or {}).get("spec")), _mapping(new.get("spec"))
        for field in HELMRELEASE_FIXED_FIELDS:
            if _canon(old_spec.get(field)) != _canon(new_spec.get(field)):
                errors.append(f"{where}: changes spec.{field}")
        before, after = _sensitive_values(old_spec.get("values")), _sensitive_values(new_spec.get("values"))
        for trail in sorted(set(before) | set(after), key=str):
            if before.get(trail) != after.get(trail):
                errors.append(f"{where}: changes spec.values.{'.'.join(map(str, trail))}")
    if key[:2] == KUSTOMIZE:
        errors.extend(_kustomization_rules(where, old or {}, new))
    return errors


def _kustomization_rules(where: str, old: dict, new: dict) -> list[str]:
    errors = []
    for field in sorted(set(old) | set(new), key=str):
        if field not in KUSTOMIZATION_MUTABLE and _canon(old.get(field)) != _canon(new.get(field)):
            errors.append(f"{where}: changes {field}")
    listed = old.get("resources")
    before: list = listed if isinstance(listed, list) else []
    after = new.get("resources")
    if not isinstance(after, list):
        if "resources" in new:
            errors.append(f"{where}: resources is not a list")
        elif before:
            errors.append(f"{where}: removes resources")
        return errors
    missing = [r for r in before if r not in after]
    if missing:
        errors.append(f"{where}: removes resources {missing}")
    for entry in after:
        if entry in before:
            continue
        if not isinstance(entry, str) or not _LOCAL_RESOURCE.fullmatch(entry) or ".." in entry:
            errors.append(f"{where}: resource {entry!r} is not a local file in this directory")
    return errors


def _parse(path: str, content: str | bytes | None) -> tuple[dict | None, list, list[str]]:
    if content is None:
        return {}, [], []
    text, errors = _decode(path, content)
    if text is None:
        return None, [], errors
    docs, errors = _documents(path, text)
    if docs is None:
        return None, [], errors
    keyed, other, errors = _index(path, docs)
    return keyed, other, errors


def check_change(path: str, before: str | bytes | None, after: str | bytes | None) -> list[str]:
    if not path.endswith(ALLOWED_EXTENSIONS):
        return [f"{path}: only {', '.join(ALLOWED_EXTENSIONS)} files may change"]
    for content in (before, after):
        if content is not None:
            _, errors = _decode(path, content)
            if errors:
                return errors
    if path.endswith(UNPARSED_EXTENSIONS):
        return []
    old, old_other, errors = _parse(path, before)
    if old is None:
        return errors
    new, new_other, more = _parse(path, after)
    if new is None:
        return more
    errors = errors + more
    if new_other:
        errors.append(f"{path}: every document must be a mapping")
    elif old_other:
        errors.append(f"{path}: changes a document that is not a mapping")
    for key, doc in new.items():
        prior = old.get(key)
        if prior is not None and _canon(prior) == _canon(doc):
            continue
        where = f"{path}: {key[1]} {key[3] or '(unnamed)'}"
        if key[:2] not in ALLOWED_KINDS:
            verb = "adds" if prior is None else "changes"
            errors.append(f"{where}: {verb} a {key[0]} {key[1]}, which is not an allowed kind")
            continue
        errors.extend(_field_rules(where, key, prior, doc))
    for key in old:
        if key not in new and key[:2] not in ALLOWED_KINDS:
            errors.append(f"{path}: {key[1]} {key[3] or '(unnamed)'}: removes a {key[0]} {key[1]}")
    return errors
