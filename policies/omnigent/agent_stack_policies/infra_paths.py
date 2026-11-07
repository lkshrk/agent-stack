import fnmatch

RENOVATE = "renovate-master[bot]"
RENOVATE_PREFIX = "renovate/"
PROTECTED_PATHS = (
    "kubernetes/apps/security/**",
    "kubernetes/apps/omnigent-sandboxes/**",
    "kubernetes/apps/ai/hermes/**",
    "kubernetes/apps/ai/contribution-approver/**",
    "kubernetes/apps/actions-runner-system/**",
    "kubernetes/apps/flux-system/**",
    "kubernetes/apps/ai/omnigent/**",
    "kubernetes/apps/system-upgrade/**",
    "kubernetes/apps/kube-system/**",
    "kubernetes/flux/**",
    ".github/**",
    "talos/**",
    "**/*.sops.yaml",
    "**/ks.yaml",
)
AGENT_ALLOWED_ROOT = ("kubernetes", "apps")
AGENT_ALLOWED_MIN_SEGMENTS = 5
MAX_UNTRUSTED_PUSHES = 20
MAX_PUSH_FILES = 50
MAX_AGENT_MERGES = 50
COMPARE_FILE_CAP = 300
MAX_CONTENT_BYTES = 1_000_000
REWRITE_BASE_STATUSES = ("ahead", "diverged")


def protected_path(path: str) -> bool:
    for pattern in PROTECTED_PATHS:
        if pattern.endswith("/**"):
            if path.startswith(pattern[:-2]):
                return True
        elif fnmatch.fnmatchcase(path.rsplit("/", 1)[-1], pattern.removeprefix("**/")):
            return True
    return False


def agent_path_allowed(path: str) -> bool:
    parts = path.split("/")
    return (
        len(parts) >= AGENT_ALLOWED_MIN_SEGMENTS
        and tuple(parts[:2]) == AGENT_ALLOWED_ROOT
        and all(part and part not in (".", "..") for part in parts)
        and not protected_path(path)
    )
