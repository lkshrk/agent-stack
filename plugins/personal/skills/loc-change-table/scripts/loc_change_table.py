#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path


CATEGORIES = ("frontend", "backend", "tests", "config", "docs", "generated", "other")
EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"

FRONTEND_EXTS = {
    ".js",
    ".jsx",
    ".ts",
    ".tsx",
    ".svelte",
    ".vue",
    ".css",
    ".scss",
    ".sass",
    ".less",
    ".html",
}
BACKEND_EXTS = {".py", ".go", ".rs", ".java", ".kt", ".cs", ".rb", ".php"}
CONFIG_EXTS = {
    ".json",
    ".yaml",
    ".yml",
    ".toml",
    ".ini",
    ".env",
    ".lock",
}
DOC_EXTS = {".md", ".mdx", ".rst", ".txt"}


@dataclass
class RepoStats:
    repo: Path
    values: dict[str, int] = field(default_factory=lambda: {name: 0 for name in CATEGORIES})
    base: str = ""
    full_history: bool = False

    @property
    def total(self) -> int:
        return sum(self.values.values())


def run_git(repo: Path, args: list[str]) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    return result.stdout.strip()


def discover_repos(root: Path) -> list[Path]:
    repos: list[Path] = []
    for dirpath, dirnames, _filenames in os.walk(root):
        path = Path(dirpath)
        if ".git" in dirnames:
            repos.append(path)
        dirnames[:] = [
            name
            for name in dirnames
            if name not in {".git", "node_modules", ".venv", "__pycache__", ".cache"}
        ]
    return sorted(repos)


def has_head(repo: Path) -> bool:
    try:
        run_git(repo, ["rev-parse", "--verify", "--quiet", "HEAD"])
        return True
    except subprocess.CalledProcessError:
        return False


def base_ref_for_since(repo: Path, since: str) -> tuple[str, bool]:
    """Return (base_ref, full_history). full_history is True when no commit
    predates `since`, so HEAD is diffed against the empty tree."""
    ref = run_git(repo, ["rev-list", "-1", f"--before={since}", "HEAD"])
    if ref:
        return ref, False
    return EMPTY_TREE, True


def validate_since(since: str) -> None:
    """git approxidate never errors: an unparseable date silently resolves to the
    current time, which would diff HEAD against ~now and report zero changes for
    every repo. Detect that by comparing the resolved epoch to wall-clock now —
    garbage lands within a second of now, real relative dates do not."""
    probe = subprocess.run(
        ["git", "rev-parse", f"--since={since}"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    out = probe.stdout.strip()  # --max-age=<epoch>
    _, _, epoch = out.partition("=")
    if since.strip().lower() in {"now"}:
        return
    if not epoch.isdigit() or abs(time.time() - int(epoch)) < 3:
        raise SystemExit(
            f"could not parse --since date: {since!r}. "
            "Use a form git understands, e.g. 2026-06-01, 'last friday', or '2 weeks ago'."
        )


def category_for(path: str) -> str:
    normalized = path.replace("\\", "/")
    lower = normalized.lower()
    parts = set(lower.split("/"))
    name = Path(lower).name
    suffixes = Path(lower).suffixes
    suffix = suffixes[-1] if suffixes else ""

    if (
        "generated" in parts
        or "gen" in parts
        or "dist" in parts
        or "build" in parts
        or "coverage" in parts
        or "__snapshots__" in parts
        or name.endswith(".snap")
        or name in {"package-lock.json", "pnpm-lock.yaml", "yarn.lock", "uv.lock"}
    ):
        return "generated"
    if (
        "test" in parts
        or "tests" in parts
        or "spec" in parts
        or "specs" in parts
        or "__tests__" in parts
        or ".test." in name
        or ".spec." in name
    ):
        return "tests"
    if "docs" in parts or suffix in DOC_EXTS or name.startswith(("readme", "changelog")):
        return "docs"
    if (
        suffix in CONFIG_EXTS
        or name in {"dockerfile", "makefile", ".env"}
        or ".github" in parts
        or ".woodpecker" in parts
        or ".gitlab" in parts
    ):
        return "config"
    if suffix in FRONTEND_EXTS:
        return "frontend"
    if suffix in BACKEND_EXTS:
        return "backend"
    return "other"


def parse_numstat(output: str, include_binary: bool) -> dict[str, int]:
    values = {name: 0 for name in CATEGORIES}
    for line in output.splitlines():
        fields = line.split("\t")
        if len(fields) < 3:
            continue
        added_raw, deleted_raw, path = fields[0], fields[1], fields[2]
        if added_raw == "-" or deleted_raw == "-":
            changed = 1 if include_binary else 0
        else:
            changed = int(added_raw) + int(deleted_raw)
        values[category_for(path)] += changed
    return values


def stats_for_repo(repo: Path, since: str, include_uncommitted: bool, include_binary: bool) -> RepoStats:
    stats = RepoStats(repo=repo)
    if not has_head(repo):
        raise subprocess.CalledProcessError(1, "git", stderr="no commits yet")
    base_ref, full_history = base_ref_for_since(repo, since)
    stats.base = "empty-tree" if full_history else run_git(repo, ["rev-parse", "--short", base_ref])
    stats.full_history = full_history
    output = run_git(repo, ["diff", "--numstat", base_ref, "HEAD"])
    stats.values = parse_numstat(output, include_binary)
    if include_uncommitted:
        uncommitted = run_git(repo, ["diff", "--numstat", "HEAD"])
        extra = parse_numstat(uncommitted, include_binary)
        for category, changed in extra.items():
            stats.values[category] += changed
    return stats


def label_for(root: Path, repo: Path) -> str:
    try:
        label = str(repo.relative_to(root))
    except ValueError:
        label = str(repo)
    return repo.name if label == "." else label


def format_table(root: Path, rows: list[RepoStats]) -> str:
    headers = ["repo", *CATEGORIES, "total"]
    aligns = ["---", *["---:" for _ in CATEGORIES], "---:"]
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join(aligns) + " |",
    ]
    for row in rows:
        values = [str(row.values[category]) for category in CATEGORIES]
        lines.append("| " + " | ".join([label_for(root, row.repo), *values, str(row.total)]) + " |")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Count git line changes by repo and category since a date."
    )
    parser.add_argument("folder", nargs="?", default=".", help="Folder to scan for git repos")
    parser.add_argument("--since", required=True, help="Date accepted by git --before")
    parser.add_argument(
        "--include-uncommitted",
        action="store_true",
        help="Also count current working tree changes against HEAD",
    )
    parser.add_argument(
        "--include-binary",
        action="store_true",
        help="Count each changed binary file as 1 changed line",
    )
    parser.add_argument(
        "--show-empty",
        action="store_true",
        help="Also list repositories with zero changes",
    )
    args = parser.parse_args()

    validate_since(args.since)

    root = Path(args.folder).expanduser().resolve()
    repos = discover_repos(root)
    if not repos:
        print(f"No git repositories found under {root}", file=sys.stderr)
        return 1

    rows: list[RepoStats] = []
    skipped: list[str] = []
    for repo in repos:
        try:
            rows.append(
                stats_for_repo(
                    repo,
                    args.since,
                    include_uncommitted=args.include_uncommitted,
                    include_binary=args.include_binary,
                )
            )
        except subprocess.CalledProcessError as exc:
            message = (exc.stderr or "").strip() or str(exc)
            skipped.append(f"{label_for(root, repo)}: {message}")

    rows.sort(key=lambda row: (-row.total, label_for(root, row.repo)))
    shown = rows if args.show_empty else [row for row in rows if row.total > 0]
    empty = [row for row in rows if row.total == 0]
    full_history = [row for row in rows if row.full_history and row.total > 0]

    print(format_table(root, shown))
    print()
    print(f"Counted additions + deletions since {args.since}.")
    if not args.show_empty and empty:
        print(f"Hid {len(empty)} repo(s) with no changes.")
    if full_history:
        names = ", ".join(label_for(root, row.repo) for row in full_history)
        print(
            f"Note: no commit predates {args.since} in: {names}. "
            "These were diffed against the empty tree (full history counted) — "
            "verify the date if those totals look too high."
        )
    if skipped:
        print()
        print("Skipped:")
        for item in skipped:
            print(f"- {item}")
    return 0 if shown else 1


if __name__ == "__main__":
    raise SystemExit(main())
