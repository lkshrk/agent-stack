import argparse
import subprocess
import sys
from dataclasses import dataclass

from .infra_paths import MAX_PUSH_FILES, agent_path_allowed
from .manifests import check_change

NULL_SHA = "0" * 40
FILE_MODES = frozenset({"100644", "100755"})
ABSENT_MODE = "000000"
SHOWN = 20


class GitError(Exception):
    pass


@dataclass(frozen=True)
class Change:
    path: str
    previous: str
    old_mode: str
    new_mode: str
    old_blob: str
    new_blob: str


def _git(*args: str) -> bytes:
    proc = subprocess.run(["git", "--no-pager", *args], capture_output=True)
    if proc.returncode:
        raise GitError(proc.stderr.decode("utf-8", "replace").strip() or f"git {args[0]} failed")
    return proc.stdout


def _changes(raw: bytes) -> list[Change]:
    fields = raw.split(b"\0")
    out, i = [], 0
    while i < len(fields) and fields[i]:
        meta = fields[i].decode("ascii").lstrip(":").split()
        old_mode, new_mode, old_blob, new_blob, status = meta
        if status[0] in "RC":
            previous, path = fields[i + 1].decode("utf-8", "surrogateescape"), fields[i + 2].decode("utf-8", "surrogateescape")
            i += 3
        else:
            previous, path = "", fields[i + 1].decode("utf-8", "surrogateescape")
            i += 2
        out.append(Change(path, previous, old_mode, new_mode, old_blob, new_blob))
    return out


def _blob(mode: str, sha: str) -> bytes | None:
    if mode == ABSENT_MODE or sha == NULL_SHA:
        return None
    return _git("cat-file", "blob", sha)


def problems(changes: list[Change]) -> list[str]:
    if len(changes) > MAX_PUSH_FILES:
        return [f"changes {len(changes)} files, more than {MAX_PUSH_FILES}"]
    found: list[str] = []
    for change in changes:
        names = [n for n in (change.path, change.previous) if n]
        outside = [n for n in names if not agent_path_allowed(n)]
        if outside:
            found.extend(f"{n}: outside the allowed app paths" for n in outside)
            continue
        modes = {change.old_mode, change.new_mode} - {ABSENT_MODE}
        if not modes <= FILE_MODES:
            found.append(f"{change.path}: only regular files may change (mode {'/'.join(sorted(modes))})")
            continue
        before = _blob(change.old_mode, change.old_blob)
        after = _blob(change.new_mode, change.new_blob)
        found.extend(check_change(change.path, before, after, previous=change.previous or None))
    return found


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python3 -m agent_stack_policies.manifests_cli",
        description="Check what an infra push would change in h-cloud: app paths only, then the manifest checker.",
    )
    parser.add_argument("--staged", action="store_true", help="compare BASE with the index instead of HEAD")
    parser.add_argument("base")
    parser.add_argument("head", nargs="?")
    args = parser.parse_args(argv)
    if args.staged == bool(args.head):
        parser.error("give BASE HEAD, or --staged BASE")
    target = ["--cached", args.base] if args.staged else [args.base, args.head]
    try:
        for rev in [args.base] + ([] if args.staged else [args.head]):
            _git("rev-parse", "--verify", "--quiet", f"{rev}^{{commit}}")
        changes = _changes(_git("diff", "--raw", "-z", "-M", "--no-abbrev", "--no-ext-diff", "--no-textconv", *target))
        found = problems(changes)
    except (GitError, UnicodeDecodeError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if found:
        for line in found[:SHOWN]:
            print(f"violation: {line}")
        if len(found) > SHOWN:
            print(f"... and {len(found) - SHOWN} more")
        return 1
    print(f"ok: {len(changes)} file{'s' if len(changes) != 1 else ''} pass the app path and manifest checks")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
