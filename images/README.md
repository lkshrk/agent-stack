# images

Built from the repository root (`docker build -f images/<name>/Dockerfile .`) and published as `ghcr.io/lkshrk/agent-stack/<name>` by `.github/workflows/images.yml`.

| Image | Contents |
| --- | --- |
| `omnigent-host/` | Runner: Omnigent host, Claude Code, Codex, omp with the workstation's shared skills and MCP servers, the `runner` plugin and others, LSPs and toolchains, agent home seeding and credential helpers |
| `omnigent-server/` | Omnigent server with `bundles/` and `config/omnigent/config.yaml` baked in |
| `agent-tools/` | The Go programs from `cmd/` on distroless |
| `hermes/` | Hermes with its managed configuration from `config/hermes/`, git and the vault tooling |
| `hermes-webui/` | Hermes web UI; pins the same Hermes image as `hermes/` (checked in CI) |
