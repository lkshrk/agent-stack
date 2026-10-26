#!/usr/bin/env python3
"""Guarded direct delete (NOT to Trash) for the mac-disk-reclaim skill.

Allows reclaimable build/cache/log dirs discovered anywhere under home, but hard-
refuses the danger list, shallow paths, and anything outside home. A bad path can
never become `rm -rf` on user data or the system."""
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

HOME = Path.home()

DOCKER_OK = HOME / "Library" / "Containers" / "com.docker.docker"

DENY_PREFIXES = [
    HOME / "Library" / "Containers",
    HOME / "Library" / "Group Containers",
    HOME / "Library" / "CloudStorage",
    HOME / "Library" / "Keychains",
    HOME / "Library" / "Mail",
    HOME / "Library" / "Messages",
    HOME / "Library" / "Mobile Documents",
    HOME / "Library" / "Application Support" / "AddressBook",
    HOME / "Library" / "Application Support" / "com.apple.TCC",
    HOME / "Pictures", HOME / "Documents", HOME / "Desktop",
    HOME / "Movies", HOME / "Music",
    HOME / ".ssh", HOME / ".gnupg",
    Path("/System"), Path("/usr"), Path("/bin"), Path("/sbin"),
    Path("/private/etc"), Path("/private/var/vm"), Path("/Library/Keychains"),
]

# Reclaimable dir names recognizable anywhere under home.
PURGE_DIR_NAMES = {
    "node_modules", "DerivedData", "iOS DeviceSupport", ".gradle", ".m2",
    "build", "dist", "target", "out", ".next", ".nuxt", ".turbo", ".svelte-kit",
    ".vite", ".parcel-cache", ".angular", "__pycache__", ".pytest_cache",
    ".mypy_cache", ".ruff_cache", ".tox", ".venv", "venv", ".terraform",
    "Pods", ".cocoapods", "go-build", "_cacache", "Cache", "Caches",
    "CachedData", "CachedExtensionVSIXs", "logs", "Logs",
}
PURGE_HINTS = ("/caches/", "/cache/", "/logs/", "/deriveddata", "/devicesupport",
               "/.cache/", "/.trash")
# Tool state dirs whose contents are reclaimable.
ALLOW_ROOTS = [
    HOME / "Library" / "Caches", HOME / "Library" / "Logs",
    HOME / "Library" / "Developer", DOCKER_OK,
    HOME / "Library" / "pnpm", HOME / ".npm", HOME / ".yarn", HOME / ".cache",
    HOME / ".bun", HOME / ".cargo", HOME / "go", HOME / ".gradle", HOME / ".m2",
    HOME / ".cocoapods", HOME / ".ollama", HOME / ".codex", HOME / ".claude",
    HOME / ".Trash",
]


def is_under(path: Path, base: Path) -> bool:
    try:
        path.relative_to(base)
        return True
    except ValueError:
        return False


def reject(path: Path) -> str | None:
    if path == HOME or path == Path("/") or len(path.parts) <= 2:
        return f"path too shallow / top-level to be safe: {path}"
    if not is_under(path, HOME):
        return "outside home directory; refusing"
    for deny in DENY_PREFIXES:
        if path == deny or is_under(path, deny):
            if is_under(path, DOCKER_OK):  # docker image is the one Containers exception
                continue
            return f"on the danger list (under {deny})"
    low = str(path).lower()
    recognized = (
        path.name in PURGE_DIR_NAMES
        or any(h in low for h in PURGE_HINTS)
        or any(path == r or is_under(path, r) for r in ALLOW_ROOTS)
    )
    if not recognized:
        return "not a recognized reclaimable path; refusing direct delete"
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description="Guarded direct delete for reclaimable paths.")
    parser.add_argument("path")
    parser.add_argument("--yes", action="store_true", help="Required confirmation flag")
    args = parser.parse_args()

    if sys.platform != "darwin":
        print("macOS-only.", file=sys.stderr)
        return 1

    path = Path(args.path).expanduser().resolve()
    if not path.exists():
        print(f"already gone: {path}")
        return 0

    why = reject(path)
    if why:
        print(f"BLOCKED: {path}\n  {why}", file=sys.stderr)
        return 2
    if not args.yes:
        print(f"would delete {path} — pass --yes to confirm", file=sys.stderr)
        return 3

    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path, ignore_errors=False)
    else:
        path.unlink()
    print(f"deleted {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
