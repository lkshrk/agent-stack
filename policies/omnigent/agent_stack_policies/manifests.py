import base64
import binascii
import json
import posixpath
import re
from collections import Counter

import yaml
from yaml.parser import ParserError
from yaml.scanner import ScannerError

ALLOWED_EXTENSIONS = (".yaml", ".yml", ".json", ".md")
UNPARSED_EXTENSIONS = (".md",)
FORBIDDEN_CHARACTERS = {"\r": "CR", "\x85": "NEL", "\u2028": "LS", "\u2029": "PS"}
KUSTOMIZE = ("kustomize.config.k8s.io/v1beta1", "Kustomization")
KUSTOMIZATION_FILES = ("kustomization.yaml", "kustomization.yml")
HELMRELEASE = ("helm.toolkit.fluxcd.io/v2", "HelmRelease")
CONFIGMAP = ("v1", "ConfigMap")
PVC = ("v1", "PersistentVolumeClaim")
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
    "hostPort",
    "hostUsers",
    "hostAliases",
    "privileged",
    "allowPrivilegeEscalation",
    "runAsNonRoot",
    "procMount",
    "seccompProfile",
    "appArmorProfile",
    "shareProcessNamespace",
    "nodeName",
    "sysctls",
    "priorityClassName",
})
ID_FIELDS = frozenset({"runAsUser", "runAsGroup", "fsGroup"})
SECRET_KEY_PART = "secret"
SECRET_SOURCES = frozenset({
    "basicauth",
    "authorization",
    "oauth2",
    "csi",
    "flexvolume",
    "nfs",
    "iscsi",
    "serviceaccounttoken",
    "imagepullsecrets",
})
PVC_FIXED_FIELDS = ("volumeName", "dataSource", "dataSourceRef", "storageClassName")
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
    r"rbac|serviceaccount|clusterrole|extraobjects|extramanifests|extradeploy|extraresources|additionalresources"
    r"|rawresources|hostnetwork|hostpath|privileged|secret",
    re.IGNORECASE | re.ASCII,
)
KUSTOMIZATION_MUTABLE = ("images", "resources")
MAX_NESTING = 64
MAX_EXPANDED_NODES = 100_000
IDENTITY_KEYS = ("name", "mountPath", "containerPort", "key")
_OPENERS = (yaml.BlockSequenceStartToken, yaml.BlockMappingStartToken, yaml.FlowSequenceStartToken,
            yaml.FlowMappingStartToken)
_CLOSERS = (yaml.BlockEndToken, yaml.FlowSequenceEndToken, yaml.FlowMappingEndToken)
_LOCAL_RESOURCE = re.compile(r"(\./)?[A-Za-z0-9][A-Za-z0-9._-]*\.ya?ml", re.ASCII)


class _UniqueKeyLoader(yaml.SafeLoader):
    yaml_implicit_resolvers = {
        first: [(tag, pattern) for tag, pattern in resolvers if tag != "tag:yaml.org,2002:timestamp"]
        for first, resolvers in yaml.SafeLoader.yaml_implicit_resolvers.items()
    }


def _unique_mapping(loader, node, deep=False):
    seen = set()
    for key_node, _ in node.value:
        if key_node.tag == "tag:yaml.org,2002:merge":
            raise yaml.constructor.ConstructorError(None, None, "merge keys are not allowed", key_node.start_mark)
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


def _token_problem(text: str) -> str | None:
    depth = 0
    for token in yaml.scan(text, Loader=yaml.SafeLoader):
        if isinstance(token, (yaml.TagToken, yaml.DirectiveToken)):
            return "YAML tags and directives are not allowed"
        depth += isinstance(token, _OPENERS) - isinstance(token, _CLOSERS)
        if depth > MAX_NESTING:
            return f"nests deeper than {MAX_NESTING} levels"
    return None


def _shape_problem(docs: list) -> str | None:
    stack, seen = [(doc, 0) for doc in docs], 0
    while stack:
        node, depth = stack.pop()
        seen += 1
        if seen > MAX_EXPANDED_NODES:
            return f"expands to more than {MAX_EXPANDED_NODES} nodes through aliases"
        if depth > MAX_NESTING:
            return f"nests deeper than {MAX_NESTING} levels through aliases"
        if isinstance(node, dict):
            stack.extend((child, depth + 1) for child in (*node.keys(), *node.values()))
        elif isinstance(node, list):
            stack.extend((child, depth + 1) for child in node)
    return None


def _documents(path: str, text: str) -> tuple[list | None, list[str]]:
    try:
        problem = _token_problem(text)
        if problem:
            return None, [f"{path}: {problem}"]
        docs = [doc for doc in yaml.load_all(text, Loader=_UniqueKeyLoader) if doc is not None]
        problem = _shape_problem(docs)
        if problem:
            return None, [f"{path}: {problem}"]
        return docs, []
    except (yaml.YAMLError, RecursionError, ValueError) as exc:
        detail = getattr(exc, "problem", None) if isinstance(exc, yaml.constructor.ConstructorError) else None
        return None, [f"{path}: does not parse: {detail or type(exc).__name__}"]


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


def _element_id(value, index: int):
    if isinstance(value, dict):
        for key in IDENTITY_KEYS:
            if isinstance(value.get(key), (str, int)) and not isinstance(value.get(key), bool):
                return f"[{key}={value[key]}]"
    return index


def _walk(node, trail=()):
    if isinstance(node, dict):
        for key, value in node.items():
            yield trail + (key,), key, value
            yield from _walk(value, trail + (key,))
    elif isinstance(node, list):
        for i, value in enumerate(node):
            yield from _walk(value, trail + (_element_id(value, i),))


def _canon(value) -> str:
    return json.dumps(value, sort_keys=True, default=str)


def _secret_key(key) -> bool:
    return isinstance(key, str) and (SECRET_KEY_PART in key.lower() or key.lower() in SECRET_SOURCES)


def _plain_id(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 1


def _sensitive(doc) -> Counter:
    found: Counter = Counter()
    for trail, key, value in _walk(doc):
        at = ".".join(map(str, trail[:-1]))
        if key in POD_FIELDS or _secret_key(key):
            found[(at, key, _canon(value))] += 1
        elif key == "capabilities" and isinstance(value, dict):
            added = value.get("add")
            for cap in added if isinstance(added, list) else [added]:
                found[(at, "capabilities.add", _canon(cap))] += 1
        elif key in ID_FIELDS and not _plain_id(value):
            found[(at, key, _canon(value))] += 1
    return found


def _embedded(text: str):
    if any(char in text for char in FORBIDDEN_CHARACTERS):
        return None, "contains odd line breaks"
    try:
        problem = _token_problem(text)
        if problem:
            return None, problem
        docs = [doc for doc in yaml.load_all(text, Loader=_UniqueKeyLoader) if doc is not None]
    except (ScannerError, ParserError):
        return None, None
    except (yaml.YAMLError, RecursionError, ValueError) as exc:
        detail = getattr(exc, "problem", None) or type(exc).__name__
        return None, f"is YAML the checker cannot read ({detail})"
    problem = _shape_problem(docs)
    if problem:
        return None, problem
    structured = [doc for doc in docs if isinstance(doc, (dict, list))]
    if not structured:
        return None, None
    if len(docs) != 1:
        return None, "holds several YAML documents"
    return structured[0], None


def _configmap_entries(doc: dict) -> dict[str, str]:
    out: dict[str, str] = {}
    for key, value in _mapping(doc.get("data")).items():
        if isinstance(value, str):
            out[f"data.{key}"] = value
    for key, value in _mapping(doc.get("binaryData")).items():
        if isinstance(value, str):
            try:
                out[f"binaryData.{key}"] = base64.b64decode(value, validate=True).decode("utf-8")
            except (binascii.Error, ValueError):
                continue
    return out


def _values_rules(where: str, label: str, old, new) -> list[str]:
    errors = []
    before, after = _sensitive_values(old), _sensitive_values(new)
    for trail in sorted(set(before) | set(after), key=str):
        if before.get(trail) != after.get(trail):
            errors.append(f"{where}: changes {label}.{'.'.join(map(str, trail))}")
    for at, field, value in sorted(_sensitive(new or {}) - _sensitive(old or {})):
        errors.append(f"{where}: adds or changes {field} in {label} at {at or '(top)'} ({value[:60]})")
    return errors


def _configmap_rules(where: str, old: dict | None, new: dict) -> list[str]:
    errors = []
    before, after = _configmap_entries(old or {}), _configmap_entries(new)
    for entry, text in sorted(after.items()):
        if before.get(entry) == text:
            continue
        values, why = _embedded(text)
        if why:
            errors.append(f"{where}: {entry} {why}")
            continue
        if values is None:
            continue
        prior, _ = _embedded(before[entry]) if entry in before else (None, None)
        errors.extend(_values_rules(where, entry, prior or {}, values))
    return errors


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
    for at, field, value in sorted(gained):
        errors.append(f"{where}: adds or changes {field} at {at or '(top)'} ({value[:60]})")
    if key[:2] == HELMRELEASE:
        old_spec, new_spec = _mapping((old or {}).get("spec")), _mapping(new.get("spec"))
        for field in HELMRELEASE_FIXED_FIELDS:
            if _canon(old_spec.get(field)) != _canon(new_spec.get(field)):
                errors.append(f"{where}: changes spec.{field}")
        before, after = _sensitive_values(old_spec.get("values")), _sensitive_values(new_spec.get("values"))
        for trail in sorted(set(before) | set(after), key=str):
            if before.get(trail) != after.get(trail):
                errors.append(f"{where}: changes spec.values.{'.'.join(map(str, trail))}")
    if key[:2] == CONFIGMAP:
        errors.extend(_configmap_rules(where, old, new))
    if key[:2] == PVC:
        old_spec, new_spec = _mapping((old or {}).get("spec")), _mapping(new.get("spec"))
        for field in PVC_FIXED_FIELDS:
            if _canon(old_spec.get(field)) != _canon(new_spec.get(field)):
                errors.append(f"{where}: changes spec.{field}")
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
        if not isinstance(entry, str) or not _LOCAL_RESOURCE.fullmatch(entry):
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


def check_change(
    path: str, before: str | bytes | None, after: str | bytes | None, previous: str | None = None
) -> list[str]:
    if previous and posixpath.dirname(previous) != posixpath.dirname(path):
        before = None
    try:
        return _check(path, before, after)
    except (RecursionError, ValueError) as exc:
        return [f"{path}: cannot be checked: {type(exc).__name__}"]


def _check(path: str, before: str | bytes | None, after: str | bytes | None) -> list[str]:
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
        if key not in new and (key[:2] not in ALLOWED_KINDS or key[:2] == KUSTOMIZE):
            errors.append(f"{path}: {key[1]} {key[3] or '(unnamed)'}: removes a {key[0]} {key[1]}")
    return errors
