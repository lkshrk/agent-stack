"""Render the contribution fork's CI workflow for one registry entry into a directory; templates/contribution-fork is the omnigent render."""

import argparse
import json
import re
import shlex
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "policies" / "omnigent"))

from agent_stack_policies.public_contributions import RegisteredRepository, Registry, RegistryError  # noqa: E402

CI_WORKFLOW = "contribution-ci.yml"
CI_PATH = f".github/workflows/{CI_WORKFLOW}"
_PLAIN = re.compile(r"[A-Za-z0-9][A-Za-z0-9 ._/=,+-]*")
TOOLCHAINS = {
    "web": """      - uses: actions/setup-node@949feb2413d6458794dcd2491c4babbbce0c15c1 # v7.1.0
        with:
          node-version: "22"
      - uses: pnpm/action-setup@ea17c68df8912ef543352723c149a84f56e3d413 # v6.1.0
""",
}


def _scalar(text: str) -> str:
    return text if _PLAIN.fullmatch(text) and not text.endswith(" ") else json.dumps(text)


def ci_workflow(entry: RegisteredRepository) -> str:
    validation = entry.validation
    if "--ignore-scripts" not in validation.install.argv or validation.install.lifecycle_scripts != "denied":
        raise RegistryError(f"repository {entry.name}: the fork CI installs only with --ignore-scripts")
    toolchain = TOOLCHAINS.get(validation.name)
    if toolchain is None:
        raise RegistryError(f"repository {entry.name}: no GitHub-hosted toolchain for validation profile {validation.name}")
    steps = [f"      - name: install\n        run: {_scalar(shlex.join(validation.install.argv))}\n"]
    for command in validation.commands:
        steps.append(
            f"      - name: {_scalar(command.name)}\n"
            f"        working-directory: {_scalar(command.cwd)}\n"
            f"        run: {_scalar(shlex.join(command.argv))}\n"
        )
    return (
        "name: contribution-ci\n"
        "\n"
        "on:\n"
        "  pull_request:\n"
        "    branches:\n"
        f"      - {_scalar(entry.default_branch)}\n"
        "\n"
        "permissions:\n"
        "  contents: read\n"
        "\n"
        "concurrency:\n"
        "  group: contribution-ci-${{ github.event.pull_request.number }}\n"
        "  cancel-in-progress: true\n"
        "\n"
        "jobs:\n"
        "  validate:\n"
        "    runs-on: ubuntu-latest\n"
        "    timeout-minutes: 30\n"
        "    steps:\n"
        "      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1\n"
        "        with:\n"
        "          ref: ${{ github.event.pull_request.head.sha }}\n"
        "          persist-credentials: false\n"
        f"{toolchain}"
        f"{''.join(steps)}"
    )


def render(registry_path: Path, name: str, out: Path) -> None:
    target = out / CI_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(ci_workflow(Registry.load(registry_path).repository(name)))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("repository", help="registry key, e.g. omnigent")
    parser.add_argument("out", type=Path, help="directory to render into")
    parser.add_argument("--registry", type=Path, default=ROOT / "config" / "public-contributions.yaml")
    args = parser.parse_args()
    try:
        render(args.registry, args.repository, args.out)
    except RegistryError as error:
        print(f"render-contribution-fork: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
