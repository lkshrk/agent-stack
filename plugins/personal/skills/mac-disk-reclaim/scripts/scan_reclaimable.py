#!/usr/bin/env python3
"""Read-only disk-bloat DISCOVERY scanner for macOS.

Finds reclaimable space by what it actually is, not a fixed path list:
  - build/dep dirs anywhere under home (node_modules, target, .next, .venv, ...)
  - cache/log/agent-history dirs (du sweep + known agent state)
  - large agent log FILES (codex sqlite)
  - git worktrees whose branch is already merged
Every item is something that is safe to delete; each carries the CONSEQUENCE of
deleting it. This script DELETES NOTHING."""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from glob import glob
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import decisions

HOME = Path.home()
DOCKER_OK = HOME / "Library" / "Containers" / "com.docker.docker"

BUILD_NAMES = {
    "node_modules", "DerivedData", "build", "dist", "target", "out", ".next",
    ".nuxt", ".turbo", ".svelte-kit", ".vite", ".parcel-cache", ".angular",
    "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".tox",
    ".venv", "venv", ".terraform", "Pods", ".gradle",
}
CACHE_HINTS = ("/caches/", "/cache/", "/logs/", "/deriveddata", "/devicesupport",
               "/.cache/", "/.trash")
# Electron/Chromium cache dir names scattered under Application Support — the
# classic distributed bloat a top-N-dir scan misses (each app moderate, sum huge).
CACHE_NAMES = {"Cache", "Caches", "Code Cache", "GPUCache", "DawnCache",
               "CachedData", "Crashpad", "crashpad", "Cache_Data",
               "ShaderCache", "GrShaderCache", "component_crx_cache"}
# du sweep is aimed at cache roots, not all of $HOME (build dirs are found by name).
DU_ROOTS = [
    HOME / "Library/Caches", HOME / "Library/Application Support",
    HOME / "Library/Developer", HOME / "Library/Logs", DOCKER_OK,
    HOME / "Library/pnpm", HOME / ".cache", HOME / ".codex", HOME / ".claude",
    HOME / ".ollama", HOME / ".npm", HOME / ".gradle", HOME / ".m2", HOME / "go",
    Path("/Library/Caches"),
]
AGENT_HINTS = ("/.claude/projects", "/.codex/sessions", "/.codex/log",
               "/.ollama/models", "/.cache/huggingface")
BROWSER_HINTS = ("/google/chrome", "/brave", "/vivaldi", "/firefox", "/chromium")

DANGER_SUBSTR = (
    "/library/containers/", "/library/group containers/", "/library/cloudstorage/",
    "/library/keychains/", "/library/mail/", "/library/messages/",
    "/library/application support/addressbook", "/library/application support/com.apple.tcc",
    ".photoslibrary", "/.ssh", "/.gnupg", "/library/mobile documents",
)
DANGER_UNDER = (HOME / "Documents", HOME / "Desktop", HOME / "Pictures",
                HOME / "Movies", HOME / "Music", Path("/System"), Path("/usr"),
                Path("/private/var/vm"))

# Preferred reclaim commands (used instead of rm where they apply).
RECLAIM_CMDS = [
    (HOME / "Library/Caches/Homebrew", "brew cleanup --prune=all"),
    (HOME / ".npm/_cacache", "npm cache clean --force"),
    (HOME / "Library/Caches/pip", "pip cache purge"),
    (HOME / ".cache/uv", "uv cache clean"),
    (HOME / "go/pkg/mod", "go clean -modcache"),
    (HOME / "Library/Caches/go-build", "go clean -cache"),
    (HOME / ".cache/go-build", "go clean -cache"),
    (HOME / "Library/pnpm/store", "pnpm store prune"),
    (DOCKER_OK, "docker system prune -af --volumes"),
]
# Large agent log files worth deleting directly.
KNOWN_FILE_GLOBS = ("~/.codex/logs_*.sqlite*", "~/.codex/history.jsonl")

# category -> (risk, consequence)
CONSEQUENCE = {
    "build": ("low", "build output / installed deps — regenerates on next build or install"),
    "cache": ("low", "cache — the tool recreates it automatically; no data loss"),
    "agent": ("med", "agent history — PERMANENT chat/session logs; deleting loses them for good"),
    "browser-cache": ("low", "browser cache — recreated on next browse; logins/cookies unaffected"),
    "ai-models": ("med", "model weights — must be re-downloaded (often many GB) to use again"),
    "worktree": ("low", "git worktree on an already-MERGED branch — work is safe in the main branch"),
    "trash": ("low", "Trash contents — permanently removed"),
    "git-gc": ("low", "bloated .git objects — `git gc` repacks them; no commits or data lost"),
    "dsstore": ("low", "thousands of Finder .DS_Store files — recreated on next folder open"),
}


def du_bytes(path: str) -> int:
    try:
        out = subprocess.run(["du", "-sk", path], stdout=subprocess.PIPE,
                             stderr=subprocess.DEVNULL, text=True, timeout=180)
        return int(out.stdout.split("\t")[0]) * 1024
    except (ValueError, IndexError, subprocess.SubprocessError):
        return 0


def human(n: int) -> str:
    f = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if f < 1024 or unit == "TB":
            return f"{f:.1f}{unit}"
        f /= 1024
    return f"{f:.1f}TB"


def under(path: str, base: Path) -> bool:
    try:
        Path(path).relative_to(base)
        return True
    except ValueError:
        return False


def is_danger(path: str) -> bool:
    if path.startswith(str(DOCKER_OK)):
        return False
    low = path.lower()
    if any(s in low for s in DANGER_SUBSTR):
        return True
    return any(under(path, d) for d in DANGER_UNDER)


def reclaim_cmd(path: str) -> str | None:
    for prefix, cmd in RECLAIM_CMDS:
        if path == str(prefix) or under(path, prefix):
            return cmd
    return None


def categorize(path: str) -> str | None:
    """Reclaimable category, or None if not safely reclaimable."""
    low = path.lower()
    name = Path(path).name
    if any(h in low for h in AGENT_HINTS):
        return "ai-models" if "ollama" in low or "huggingface" in low else "agent"
    if "/.trash" in low:
        return "trash"
    if any(h in low for h in BROWSER_HINTS) and ("cache" in low):
        return "browser-cache"
    if name in BUILD_NAMES:
        return "build"
    if name in CACHE_NAMES or any(h in low for h in CACHE_HINTS):
        return "cache"
    if reclaim_cmd(path):
        return "cache"
    return None


def make(path: str, size: int, category: str) -> dict:
    risk, desc = CONSEQUENCE[category]
    cmd = reclaim_cmd(path)
    cmd = cmd or f'rm -rf "{path}"'
    # reconfirm: lossy-beyond-regeneration items re-ask every run even if remembered.
    reconfirm = risk == "med" or "--volumes" in cmd
    return {"path": path, "key": f"{category}:{path}", "size_bytes": size,
            "size": human(size), "category": category, "risk": risk, "desc": desc,
            "deletable": True, "remembered": None, "reconfirm": reconfirm,
            "method": "cmd" if reclaim_cmd(path) else "rm", "cmd": cmd}


def name_sweep(min_bytes: int) -> dict[str, dict]:
    found: dict[str, dict] = {}
    name_args: list[str] = []
    for n in sorted(BUILD_NAMES):
        name_args += ["-name", n, "-o"]
    name_args = name_args[:-1]
    try:
        proc = subprocess.run(
            ["find", str(HOME), "-type", "d", "(", *name_args, ")", "-prune", "-print"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, timeout=240)
    except subprocess.SubprocessError:
        return found
    for line in proc.stdout.splitlines():
        if is_danger(line):
            continue
        size = du_bytes(line)
        if size >= min_bytes:
            found[line] = make(line, size, "build")
    return found


def du_sweep(min_bytes: int, depth: int) -> dict[str, dict]:
    found: dict[str, dict] = {}
    for root in DU_ROOTS:
        if not root.is_dir():
            continue
        try:
            proc = subprocess.run(["du", "-d", str(depth), "-k", str(root)],
                                  stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                  text=True, timeout=300)
        except subprocess.SubprocessError:
            continue
        for line in proc.stdout.splitlines():
            try:
                kb, p = line.split("\t", 1)
                size = int(kb) * 1024
            except ValueError:
                continue
            if size < min_bytes or p == str(root) or is_danger(p):
                continue
            cat = categorize(p)
            if cat:
                found[p] = make(p, size, cat)
    return found


def file_sweep(min_bytes: int) -> dict[str, dict]:
    found: dict[str, dict] = {}
    for pattern in KNOWN_FILE_GLOBS:
        for p in glob(str(Path(pattern).expanduser())):
            size = du_bytes(p)
            if size >= min_bytes:
                found[p] = make(p, size, "agent")
    return found


_GIT_DIRS: list[str] | None = None


def find_git_dirs() -> list[str]:
    global _GIT_DIRS
    if _GIT_DIRS is not None:
        return _GIT_DIRS
    try:
        proc = subprocess.run(
            ["find", str(HOME), "-type", "d", "-name", ".git", "-maxdepth", "7", "-prune", "-print"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, timeout=180)
        _GIT_DIRS = [l for l in proc.stdout.splitlines() if not is_danger(l)]
    except subprocess.SubprocessError:
        _GIT_DIRS = []
    return _GIT_DIRS


def gitgc_sweep(min_bytes: int) -> dict[str, dict]:
    found: dict[str, dict] = {}
    for gitdir in find_git_dirs():
        size = du_bytes(gitdir)
        if size < min_bytes:
            continue
        repo = str(Path(gitdir).parent)
        item = make(gitdir, size, "git-gc")
        item["method"] = "cmd"
        item["cmd"] = f'git -C "{repo}" gc --prune=now'
        found[gitdir] = item
    return found


def dsstore_sweep() -> dict[str, dict]:
    try:
        proc = subprocess.run(["find", str(HOME), "-name", ".DS_Store", "-type", "f"],
                              stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, timeout=180)
    except subprocess.SubprocessError:
        return {}
    files = proc.stdout.splitlines()
    if not files:
        return {}
    total = 0
    for f in files:
        try:
            total += Path(f).stat().st_size
        except OSError:
            pass
    item = make(str(HOME), total, "dsstore")
    item["path"] = f"{len(files)} .DS_Store files under {HOME}"
    item["method"] = "cmd"
    item["cmd"] = f'find "{HOME}" -name .DS_Store -type f -delete'
    return {"__dsstore__": item}


def volume_trash_sweep(min_bytes: int) -> dict[str, dict]:
    found: dict[str, dict] = {}
    vols = Path("/Volumes")
    if not vols.is_dir():
        return found
    for vol in vols.iterdir():
        t = vol / ".Trashes"
        if not t.is_dir():
            continue
        size = du_bytes(str(t))
        if size < min_bytes:
            continue
        item = make(str(t), size, "trash")
        item["method"] = "cmd"
        item["cmd"] = f'rm -rf "{t}"/*'
        found[str(t)] = item
    return found


def default_branch(repo: str) -> str | None:
    for ref in ("origin/HEAD", "main", "master"):
        r = subprocess.run(["git", "-C", repo, "rev-parse", "--verify", "-q", ref],
                           stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
        if r.returncode == 0:
            return ref
    return None


def worktree_sweep(min_bytes: int) -> dict[str, dict]:
    found: dict[str, dict] = {}
    for gitdir in find_git_dirs():
        repo = str(Path(gitdir).parent)
        base = default_branch(repo)
        if not base:
            continue
        wl = subprocess.run(["git", "-C", repo, "worktree", "list", "--porcelain"],
                            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
        path = head = None
        first = True
        for line in wl.stdout.splitlines() + [""]:
            if line.startswith("worktree "):
                path = line[len("worktree "):]
            elif line.startswith("HEAD "):
                head = line[len("HEAD "):]
            elif line == "":
                if path and head and not first:
                    merged = subprocess.run(
                        ["git", "-C", repo, "merge-base", "--is-ancestor", head, base],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    if merged.returncode == 0 and not is_danger(path):
                        size = du_bytes(path)
                        if size >= min_bytes:
                            item = make(path, size, "worktree")
                            item["method"] = "cmd"
                            item["cmd"] = f'git -C "{repo}" worktree remove "{path}"'
                            found[path] = item
                first = False
                path = head = None
    return found


def dedup_nested(findings: list[dict]) -> list[dict]:
    chosen: list[str] = []
    kept: list[dict] = []
    for f in sorted(findings, key=lambda x: len(x["path"])):
        if any(f["path"].startswith(parent + "/") for parent in chosen):
            continue
        chosen.append(f["path"])
        kept.append(f)
    return kept


def scan(min_bytes: int, depth: int, use_memory: bool = True) -> list[dict]:
    merged: dict[str, dict] = {}
    merged.update(du_sweep(min_bytes, depth))
    merged.update(name_sweep(min_bytes))
    merged.update(file_sweep(min_bytes))
    merged.update(worktree_sweep(min_bytes))
    merged.update(gitgc_sweep(min_bytes))
    merged.update(volume_trash_sweep(min_bytes))
    findings = dedup_nested(list(merged.values()))
    ds = dsstore_sweep()
    findings.extend(ds.values())  # DS_Store ignores the size floor (near-free win)
    findings.sort(key=lambda f: -f["size_bytes"])
    if use_memory:
        remembered = decisions.load()
        for f in findings:
            f["remembered"] = remembered.get(f["key"], {}).get("choice")
    return findings


def snapshot_note() -> str | None:
    try:
        out = subprocess.run(["tmutil", "listlocalsnapshotdates", "/"],
                             stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                             text=True, timeout=30).stdout.strip().splitlines()
        dates = [d for d in out if d and d[0].isdigit()]
        if not dates:
            return None
        return (f"{len(dates)} Time Machine local snapshot(s) on / using hidden SSD space. "
                "Remove: sudo tmutil thinlocalsnapshots / 21474836480 4")
    except (FileNotFoundError, subprocess.SubprocessError):
        return None


def main() -> int:
    parser = argparse.ArgumentParser(description="Discover reclaimable disk bloat on macOS (read-only).")
    parser.add_argument("--min-mb", type=float, default=200, help="Size floor (default 200MB)")
    parser.add_argument("--depth", type=int, default=5, help="du discovery depth (default 5)")
    parser.add_argument("--no-memory", action="store_true",
                        help="ignore remembered delete/skip choices; review everything fresh")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    if sys.platform != "darwin":
        print("This skill is macOS-only.", file=sys.stderr)
        return 1

    min_bytes = int(args.min_mb * 1024 * 1024)
    findings = scan(min_bytes, args.depth, use_memory=not args.no_memory)
    note = snapshot_note()
    total = sum(f["size_bytes"] for f in findings)

    if args.json:
        print(json.dumps({"findings": findings, "total_bytes": total,
                          "total": human(total), "snapshot_note": note}, indent=2))
        return 0

    new_n = sum(1 for f in findings if not f.get("remembered"))
    print(f"{len(findings)} reclaimable item(s) >= {args.min_mb}MB, {human(total)} total "
          f"({new_n} new, {len(findings) - new_n} remembered)\n")
    print(f"{'SIZE':>9}  {'RISK':<4} {'MEMORY':<8} {'CATEGORY':<13} PATH")
    for f in findings:
        mem = f.get("remembered") or "new"
        print(f"{f['size']:>9}  {f['risk']:<4} {mem:<8} {f['category']:<13} {f['path']}")
    if note:
        print(f"\nNote: {note}")
    print("\nSwap: /private/var/vm swapfiles are kernel-managed; cannot be safely "
          "deleted on a running system. Quit heavy apps or reboot. `sudo purge` != swap.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
