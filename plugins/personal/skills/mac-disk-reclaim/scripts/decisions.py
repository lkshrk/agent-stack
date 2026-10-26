#!/usr/bin/env python3
"""Persistent per-item delete/skip memory for the mac-disk-reclaim skill.

Stored at ~/.config/mac-disk-reclaim/decisions.json — deliberately OUTSIDE every
path the scanner sweeps, so the cleanup can never erase its own memory. The
scanner imports load() to annotate findings; the skill calls `set` after the
user answers, so the next run only asks about items it has not seen."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

STORE = Path.home() / ".config" / "mac-disk-reclaim" / "decisions.json"
CHOICES = ("delete", "skip")


def load() -> dict[str, dict]:
    try:
        data = json.loads(STORE.read_text())
        return data.get("decisions", {}) if isinstance(data, dict) else {}
    except (FileNotFoundError, ValueError, OSError):
        return {}


def save(decisions: dict[str, dict]) -> None:
    STORE.parent.mkdir(parents=True, exist_ok=True)
    STORE.write_text(json.dumps({"version": 1, "decisions": decisions}, indent=2))


def main() -> int:
    parser = argparse.ArgumentParser(description="Delete/skip memory for mac-disk-reclaim.")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("path")
    sub.add_parser("list")
    g = sub.add_parser("get")
    g.add_argument("key")
    s = sub.add_parser("set")
    s.add_argument("key")
    s.add_argument("choice", choices=CHOICES)
    s.add_argument("--path", default="")
    s.add_argument("--category", default="")
    f = sub.add_parser("forget")
    f.add_argument("key", nargs="?")
    f.add_argument("--all", action="store_true")
    args = parser.parse_args()

    if args.cmd == "path":
        print(STORE)
        return 0
    if args.cmd == "list":
        print(json.dumps(load(), indent=2))
        return 0
    if args.cmd == "get":
        print(load().get(args.key, {}).get("choice", ""))
        return 0
    if args.cmd == "set":
        d = load()
        d[args.key] = {"choice": args.choice, "path": args.path, "category": args.category}
        save(d)
        print(f"remembered: {args.key} -> {args.choice}")
        return 0
    if args.cmd == "forget":
        d = load()
        if args.all:
            save({})
            print("forgot all decisions")
        elif args.key and args.key in d:
            del d[args.key]
            save(d)
            print(f"forgot: {args.key}")
        else:
            print("nothing to forget", file=sys.stderr)
            return 1
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
