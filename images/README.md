# images

Built from the repository root (`docker build -f images/<name>/Dockerfile .`) and published as `ghcr.io/lkshrk/agent-stack/<name>` by `.github/workflows/images.yml`.

| Image | Contents |
| --- | --- |
| `omnigent-host/` | Runner: Omnigent host, Claude Code, Codex, omp with the workstation's shared skills and MCP servers, the `runner` plugin and others, LSPs and toolchains, agent home seeding, credential helpers and the `agent_stack_policies` package, which the runner resolves at session start |
| `omnigent-server/` | Omnigent server with `bundles/`, `config/omnigent/config.yaml` and the `agent_stack_policies` package baked in |
| `agent-tools/` | The Go programs from `cmd/` on distroless |
| `hermes/` | Hermes with its managed configuration from `config/hermes/`, git and the vault tooling |
| `hermes-webui/` | Hermes web UI; pins the same Hermes image as `hermes/` (checked in CI) |

Both Omnigent images install `policies/omnigent/agent_stack_policies` under `/opt/agent-stack/policies` and add it to the venv with a `.pth` file. CI runs the policy tests inside the server image with `AGENT_STACK_IN_IMAGE=1`.
