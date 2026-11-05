"""Render cmd/contribution-approver/forks.json, the config go:embedded into contribution-approver, from the public-contributions registry."""

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "policies" / "omnigent"))

from agent_stack_policies.public_contributions import FORK_OWNER, Registry, RegistryError  # noqa: E402

CI_WORKFLOW = "contribution-ci.yml"
DEFAULT_REGISTRY = ROOT / "config" / "public-contributions.yaml"
DEFAULT_OUT = ROOT / "cmd" / "contribution-approver" / "forks.json"


def render(registry_path: Path) -> str:
    forks = [
        {
            "upstream": entry.upstream.removeprefix("https://github.com/").removesuffix(".git"),
            "fork": entry.fork,
            "owner": FORK_OWNER,
            "default_branch": entry.default_branch,
            "title_format": entry.contribution.title_format,
            "ci_workflow": CI_WORKFLOW,
        }
        for entry in Registry.load(registry_path).repositories
    ]
    return json.dumps(forks, indent=2) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--check", action="store_true", help="exit 1 if the file differs instead of writing it")
    args = parser.parse_args(argv)
    try:
        text = render(args.registry)
    except RegistryError as error:
        print(f"render-approver-config: {error}", file=sys.stderr)
        return 1
    if args.check:
        if not args.out.exists() or args.out.read_text() != text:
            print(f"render-approver-config: {args.out} differs from the registry", file=sys.stderr)
            return 1
        return 0
    args.out.write_text(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
