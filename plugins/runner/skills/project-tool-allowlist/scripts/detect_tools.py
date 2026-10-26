#!/usr/bin/env python3
"""Detect a repo's stack/tooling plus installed helper tools and emit an agent
permission allowlist. Optionally merge it into a Claude Code settings file."""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path


def detect_client() -> str:
    """Infer the running client from inherited env. The detector is a subprocess
    of the agent, so these vars reflect whoever launched it."""
    if os.environ.get("CLAUDECODE") or any(k.startswith("CLAUDE_CODE_") for k in os.environ):
        return "claude"
    if any(k.startswith("CODEX_") for k in os.environ):
        return "codex"
    return "list"


# Safe-by-default helper tools: read-mostly, no network egress or infra mutation.
SAFE_TOOLS = (
    "gh", "jq", "rg", "fd", "bat", "fzf", "tree",
    "pre-commit", "lefthook", "direnv", "just", "task",
)
# Powerful tools: arbitrary network egress (exfil channel) or silent infra
# mutation. Gated behind --include-infra so they don't get blanket approval.
INFRA_TOOLS = (
    "curl", "wget", "http",
    "docker", "kubectl", "helm", "terraform", "ansible", "aws", "gcloud", "flyctl",
)

GIT_READONLY = ("git status", "git diff", "git log", "git show", "git branch")
GIT_SAFE_EXTRA = (
    "git add", "git commit", "git checkout", "git switch", "git fetch",
    "git stash", "git restore", "git tag",
)


def which(tool: str) -> bool:
    return shutil.which(tool) is not None


def package_manager(folder: Path) -> str:
    if (folder / "bun.lock").exists() or (folder / "bun.lockb").exists():
        return "bun"
    if (folder / "pnpm-lock.yaml").exists():
        return "pnpm"
    if (folder / "yarn.lock").exists():
        return "yarn"
    return "npm"


def node_prefixes(folder: Path) -> tuple[list[str], list[str]]:
    pkg_path = folder / "package.json"
    if not pkg_path.exists():
        return [], []
    try:
        pkg = json.loads(pkg_path.read_text())
    except (ValueError, OSError):
        return [], []
    pm = package_manager(folder)
    prefixes: list[str] = [f"{pm} install"]
    if pm == "npm":
        prefixes.append("npm ci")
    run = "bun run" if pm == "bun" else f"{pm} run"
    for name in sorted((pkg.get("scripts") or {}).keys()):
        prefixes.append(f"{run} {name}")
    if pm == "bun":
        prefixes.append("bun test")
        prefixes.append("bunx")
    notes = [f"node project ({pm})"]
    deps = {**(pkg.get("dependencies") or {}), **(pkg.get("devDependencies") or {})}
    for binary in ("eslint", "prettier", "biome", "vitest", "jest", "playwright", "tsc", "tsx"):
        if binary in deps or any(binary in k for k in deps):
            prefixes.append(binary)
    return sorted(set(prefixes)), notes


def stack_prefixes(folder: Path) -> tuple[list[str], list[str]]:
    prefixes: list[str] = []
    notes: list[str] = []

    node_p, node_n = node_prefixes(folder)
    prefixes += node_p
    notes += node_n

    if (folder / "Makefile").exists() or (folder / "makefile").exists():
        prefixes.append("make")
        notes.append("make")
    if (folder / "Cargo.toml").exists():
        prefixes += ["cargo build", "cargo test", "cargo run", "cargo check", "cargo clippy", "cargo fmt"]
        notes.append("rust (cargo)")
    if (folder / "go.mod").exists():
        prefixes += ["go build", "go test", "go run", "go vet", "gofmt"]
        notes.append("go")
    if (folder / "pyproject.toml").exists() or (folder / "setup.py").exists() or (folder / "requirements.txt").exists():
        prefixes += ["pytest", "python -m pytest", "ruff", "black", "mypy"]
        notes.append("python")
        if (folder / "uv.lock").exists():
            prefixes.append("uv")
            notes.append("uv")
        if (folder / "poetry.lock").exists():
            prefixes.append("poetry")
            notes.append("poetry")
    if (folder / "Gemfile").exists():
        prefixes += ["bundle", "rake", "rspec"]
        notes.append("ruby")
    if (folder / "composer.json").exists():
        prefixes.append("composer")
        notes.append("php (composer)")
        if (folder / "artisan").exists():
            prefixes.append("php artisan")
    if (folder / "pom.xml").exists():
        prefixes.append("mvn")
        notes.append("maven")
    if (folder / "build.gradle").exists() or (folder / "build.gradle.kts").exists():
        prefixes += ["gradle", "./gradlew"]
        notes.append("gradle")
    if (folder / ".pre-commit-config.yaml").exists():
        prefixes.append("pre-commit")
    if (folder / "lefthook.yml").exists() or (folder / "lefthook.yaml").exists():
        prefixes.append("lefthook")

    return prefixes, notes


def _claude_mcp_names() -> list[str]:
    if not which("claude"):
        return []
    try:
        out = subprocess.run(["claude", "mcp", "list"], capture_output=True,
                             text=True, timeout=20)
    except (OSError, subprocess.SubprocessError):
        return []
    names: list[str] = []
    for line in (out.stdout or "").splitlines():
        m = re.match(r"^([A-Za-z0-9_.-]+):\s", line.strip())
        if m:
            names.append(m.group(1))
    return names


def _json_keys(path: Path, *keys: str) -> list[str]:
    try:
        node = json.loads(path.read_text())
    except (OSError, ValueError):
        return []
    for key in keys:
        if not isinstance(node, dict):
            return []
        node = node.get(key)
    return list(node.keys()) if isinstance(node, dict) else []


def mcp_servers(folder: Path) -> list[str]:
    names: set[str] = set(_claude_mcp_names())
    # Project-committed servers.
    names.update(_json_keys(folder / ".mcp.json", "mcpServers"))
    # User-global and per-project servers from ~/.claude.json.
    home_cfg = Path.home() / ".claude.json"
    names.update(_json_keys(home_cfg, "mcpServers"))
    names.update(_json_keys(home_cfg, "projects", str(folder), "mcpServers"))
    return sorted(n for n in names if n.strip())


def _bash_prefix(pattern: str) -> str | None:
    m = re.fullmatch(r"Bash\((.*):\*\)", pattern)
    return m.group(1) if m else None


def _subsumes(broad: str, narrow: str) -> bool:
    """True if `broad` already grants everything `narrow` does."""
    if broad == narrow:
        return False
    pb, pn = _bash_prefix(broad), _bash_prefix(narrow)
    if pb is not None and pn is not None:
        return pn == pb or pn.startswith(pb + " ")
    if broad.startswith("mcp__") and narrow.startswith("mcp__"):
        # Whole-server `mcp__srv` covers tool-level `mcp__srv__tool`.
        return narrow.startswith(broad + "__")
    return False


def simplify(patterns) -> list[str]:
    """Drop entries already covered by a broader sibling (e.g. Bash(git:*)
    subsumes Bash(git status:*); mcp__srv subsumes mcp__srv__tool)."""
    pats = sorted(set(patterns))
    return [n for n in pats if not any(_subsumes(b, n) for b in pats)]


def file_patterns() -> list[str]:
    """Project-scoped file access. Patterns are gitignore-style relative to the
    settings file's project root, so `**` covers the whole project and nothing
    outside it. Codex's project trust already grants this, so it's Claude-only."""
    return ["Read(**)", "Edit(**)", "Write(**)"]


def git_prefixes(scope: str) -> list[str]:
    if scope == "read-only":
        return list(GIT_READONLY)
    if scope == "all":
        return ["git"]
    return list(GIT_READONLY) + list(GIT_SAFE_EXTRA)


def collect(folder: Path, git_scope: str, include_mcp: bool = True,
            include_files: bool = True, include_infra: bool = False) -> tuple[list[str], dict]:
    prefixes, notes = stack_prefixes(folder)
    prefixes += git_prefixes(git_scope)

    installed = [tool for tool in SAFE_TOOLS if which(tool)]
    infra_present = [tool for tool in INFRA_TOOLS if which(tool)]
    if include_infra:
        installed += infra_present
        infra_gated: list[str] = []
    else:
        infra_gated = infra_present
    prefixes += installed

    rtk = which("rtk")
    # Keep prefixes that name a real tool, deduped.
    prefixes = sorted(set(p for p in prefixes if p.strip()))

    patterns: list[str] = []
    for prefix in prefixes:
        patterns.append(f"Bash({prefix}:*)")
        # rtk wraps every command; mirror each prefix so the same scope (incl.
        # git safety) holds when commands run as `rtk <cmd>`.
        if rtk:
            patterns.append(f"Bash(rtk {prefix}:*)")
    if rtk:
        patterns.append("Bash(rtk --version)")
        # `rtk gain` escalates privileges — only blanket-allow under --include-infra.
        if include_infra:
            patterns.append("Bash(rtk gain:*)")

    # Whole-server entries allow every tool the server exposes.
    servers = mcp_servers(folder) if include_mcp else []
    patterns += [f"mcp__{server}" for server in servers]

    if include_files:
        patterns += file_patterns()

    detected = {
        "folder": str(folder),
        "stack": sorted(set(notes)),
        "installed_tools": installed + (["rtk"] if rtk else []),
        "infra_gated": infra_gated,
        "git_scope": git_scope,
        "rtk": bool(rtk),
        "mcp_servers": servers,
    }
    return simplify(patterns), detected


def settings_path(folder: Path, shared: bool) -> Path:
    name = "settings.json" if shared else "settings.local.json"
    return folder / ".claude" / name


def apply_claude(folder: Path, patterns: list[str], shared: bool) -> tuple[Path, int, int]:
    path = settings_path(folder, shared)
    data: dict = {}
    if path.exists():
        try:
            data = json.loads(path.read_text())
        except ValueError:
            raise SystemExit(f"{path} is not valid JSON; fix or remove it first")
    perms = data.setdefault("permissions", {})
    existing = perms.get("allow")
    if not isinstance(existing, list):
        existing = []
    before = set(existing)
    # Union existing + detected, then collapse redundant narrower entries.
    # Existing config (and every other key) is preserved, never overwritten.
    merged = simplify(before | set(patterns))
    merged_set = set(merged)
    added = len(merged_set - before)
    collapsed = len(before - merged_set)
    perms["allow"] = merged
    path.parent.mkdir(parents=True, exist_ok=True)
    # Atomic: write a sibling temp then rename, so a crash mid-write can't
    # truncate the user's existing settings.
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n")
    os.replace(tmp, path)
    return path, added, collapsed


def codex_config_path() -> Path:
    return Path.home() / ".codex" / "config.toml"


def _codex_header(folder: Path) -> str:
    return f'[projects."{folder}"]'


def codex_block(folder: Path) -> str:
    return f'{_codex_header(folder)}\ntrust_level = "trusted"\n'


def codex_trusted(folder: Path) -> bool:
    try:
        return _codex_header(folder) in codex_config_path().read_text()
    except OSError:
        return False


def codex_path_safe(folder: Path) -> bool:
    """The one-liner shell-quotes the path and writes it as a TOML basic string.
    A quote/backslash in the path would break either, so fall back to hand-edit."""
    return not any(c in str(folder) for c in ('"', "'", "\\"))


def codex_append_cmd(folder: Path) -> str:
    cfg = codex_config_path()
    header = _codex_header(folder)
    # mkdir: config dir may not exist yet. grep -qF: skip if already trusted, so
    # pasting twice never duplicates the block. printf %s: path is an argument,
    # not part of the format, so printf doesn't reinterpret it.
    return (
        f'mkdir -p "{cfg.parent}" && grep -qF \'{header}\' "{cfg}" 2>/dev/null || '
        f'printf \'\\n[projects."%s"]\\ntrust_level = "trusted"\\n\' \'{folder}\' >> "{cfg}"'
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate an agent permission allowlist from repo tooling.")
    parser.add_argument("folder", nargs="?", default=".", help="Repo folder to scan")
    parser.add_argument("--client", choices=["claude", "codex", "list", "auto"], default="auto",
                        help="Target client config format (default: auto — infer from env)")
    parser.add_argument("--git", choices=["read-only", "safe", "all"], default="safe",
                        help="Git command scope (default: safe)")
    parser.add_argument("--shared", action="store_true",
                        help="Claude: write .claude/settings.json instead of settings.local.json")
    parser.add_argument("--apply", action="store_true",
                        help="Claude: merge patterns into the settings file")
    parser.add_argument("--no-mcp", action="store_true",
                        help="Skip detecting MCP servers (no mcp__<server> entries)")
    parser.add_argument("--no-files", action="store_true",
                        help="Skip project-scoped Read/Edit/Write file permissions")
    parser.add_argument("--include-infra", action="store_true",
                        help="Also allow network/infra tools (curl, docker, kubectl, "
                             "aws, terraform, ...) and rtk gain — off by default")
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON")
    args = parser.parse_args()

    folder = Path(args.folder).expanduser().resolve()
    if not folder.is_dir():
        print(f"{folder} is not a directory", file=sys.stderr)
        return 1

    if args.client == "auto":
        args.client = detect_client()
        print(f"Client: {args.client} (auto-detected from env)\n", file=sys.stderr)

    patterns, detected = collect(folder, args.git, include_mcp=not args.no_mcp,
                                 include_files=not args.no_files,
                                 include_infra=args.include_infra)

    if args.json:
        print(json.dumps({"allow": patterns, "detected": detected}, indent=2))
        return 0

    print(f"Detected: {', '.join(detected['stack']) or 'no stack markers'}")
    print(f"Installed tools: {', '.join(detected['installed_tools']) or 'none'}")
    print(f"MCP servers: {', '.join(detected['mcp_servers']) or 'none'}")
    print(f"Git scope: {detected['git_scope']}  |  rtk: {'yes' if detected['rtk'] else 'no'}")
    if detected["infra_gated"]:
        print(f"Gated (not allowed): {', '.join(detected['infra_gated'])}  "
              "— pass --include-infra to allow these network/infra tools")
    print()

    if args.client == "codex":
        if codex_trusted(folder):
            print(f"Codex already trusts {folder} (block present in {codex_config_path()}).")
            return 0
        print("Codex has no per-command allowlist; project trust is all-or-nothing.")
        print("Codex's own sandbox cannot write its global config, so the user must")
        print("apply it (it trusts the whole project).\n")
        if codex_path_safe(folder):
            print("Run this command (re-running is safe — it skips if already trusted):\n")
            print(f"  {codex_append_cmd(folder)}\n")
            print("Block written:\n")
            print(codex_block(folder))
        else:
            print(f"Path contains a quote/backslash, so hand-edit {codex_config_path()}")
            print("and add this block:\n")
            print(codex_block(folder))
        return 0

    if args.client == "claude" and args.apply:
        path, added, collapsed = apply_claude(folder, patterns, args.shared)
        msg = f"Wrote {path} (+{added} new"
        if collapsed:
            msg += f", -{collapsed} redundant collapsed"
        print(msg + ", existing config preserved)")
        return 0

    print(f"{len(patterns)} allow patterns:")
    for pattern in patterns:
        print(f"  {pattern}")
    if args.client == "list":
        print("\nUnknown client: translate these Bash command prefixes into your")
        print("client's project-local permission/allow config and write that file.")
    else:
        print("\nRe-run with --client claude --apply to merge into the settings file.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
