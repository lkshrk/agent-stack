# images

Built from the repository root (`docker build -f images/<name>/Dockerfile .`) and published as `ghcr.io/lkshrk/agent-stack/<name>` by `.github/workflows/images.yml`.

| Image | Contents |
| --- | --- |
| `omnigent-host/` | Runner: Omnigent host, Claude Code, Codex, omp with the workstation's shared skills and MCP servers, the `runner` plugin and others, LSPs and toolchains, sandbox tooling (gitleaks, Playwright Chromium), agent home seeding, credential helpers and the `agent_stack_policies` package, which the runner resolves at session start |
| `omnigent-server/` | Omnigent server with `bundles/`, `config/omnigent/config.yaml` and the `agent_stack_policies` package baked in. A release build pins the runner image of the same release (see below) |
| `agent-tools/` | The Go programs from `cmd/` on distroless |
| `hermes/` | Hermes with its managed configuration from `config/hermes/`, git and the vault tooling |
| `hermes-webui/` | Hermes web UI; pins the same Hermes image as `hermes/` (checked in CI) |

Both Omnigent images install `policies/omnigent/agent_stack_policies` under `/opt/agent-stack/policies` and add it to the venv with a `.pth` file. CI runs the policy tests inside both images with `AGENT_STACK_IN_IMAGE=1`.

## Runner pin in the server image

The bundles resolve `agent_stack_policies` handlers inside the runner, so a server image must never point at a runner older than its bundles. `images.yml` builds `omnigent-server` with `AGENT_STACK_VERSION` set to the release version (the leading `v` removed), and the Dockerfile rewrites `sandbox.kubernetes.image` in the baked config to `ghcr.io/lkshrk/agent-stack/omnigent-host:<version>`, the runner built in the same release run. The rewrite is a tag pin without a digest. PR and local builds leave the argument empty and keep the pin checked in to `config/omnigent/config.yaml`. CI builds the server once more with the argument set and checks the rewritten line.

## Runner sandbox tooling

The runner image carries generic tooling so a project can run its own checks inside the sandbox pod, which runs as uid `1000660000` (`sandbox`, home `/sandbox`):

- `gitleaks` (mise) for the `security-review` bundle.
- Playwright Chromium and its OS libraries live under `PLAYWRIGHT_BROWSERS_PATH=/opt/ms-playwright`; `/opt/ms-playwright/.playwright-version` holds the Playwright version they belong to (`PLAYWRIGHT_VERSION` in the Dockerfile). A project's Playwright pin must match it: bump both together. The Omnigent v0.19.1 Kubernetes sandbox cannot add a browser sidecar, so the browsers ship in the runner.
- `node` is the base image's Node 22, and the agent CLIs are installed under `/usr/local`. mise `auto_install` is off: the sandbox user cannot write `/opt/mise`, so a project pinning a node that isn't installed falls back to Node 22 instead of breaking every node CLI.
- Codex: `seed-agent-home` writes `~/.codex/config.toml` with `features.unified_exec = false`, because input sent to a running exec session through `write_stdin` never reaches the `PreToolUse` policy hook. It also installs the file profile `~/.codex/security-review.config.toml`, which a session selects with `terminal_launch_args: ["--profile", "security-review"]`. The profile sets `approval_policy = "never"`, a permission profile that can read everything and write only `/tmp`, with network on, and moves the tool caches into `/tmp`. Codex's Linux sandbox (bubblewrap) needs user namespaces and a `/proc` mount, which Docker's default seccomp profile refuses. `images/omnigent-host/test/codex-profile.sh` checks the profile, and checks the sandbox when it can start.
- Git: `/etc/gitconfig.d/agent` sets `safe.bareRepository = explicit`, so a bare repository committed inside a checkout is never discovered and its `diff.external`, pager or hooks never run (only an explicit `--git-dir` opens one). It also sets `core.fsmonitor = false` and `protocol.file.allow = user`. `images/omnigent-host/test/git-safety.sh` checks all three against a planted bare repository.
