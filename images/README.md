# images

Built from the repository root (`docker build -f images/<name>/Dockerfile .`) and published as `ghcr.io/lkshrk/agent-stack/<name>` by `.github/workflows/images.yml`.

| Image | Contents |
| --- | --- |
| `omnigent-host/` | Runner: Omnigent host, Claude Code, Codex, omp with the workstation's shared skills and MCP servers, the `runner` plugin and others, LSPs and toolchains, project tooling for the sandbox (`with-postgres` for a throwaway PostgreSQL 18, DuckDB with the spatial extension, libvips, osmium, Playwright Chromium), agent home seeding, credential helpers and the `agent_stack_policies` package, which the runner resolves at session start |
| `omnigent-server/` | Omnigent server with `bundles/`, `config/omnigent/config.yaml` and the `agent_stack_policies` package baked in |
| `agent-tools/` | The Go programs from `cmd/` on distroless |
| `hermes/` | Hermes with its managed configuration from `config/hermes/`, git and the vault tooling |
| `hermes-webui/` | Hermes web UI; pins the same Hermes image as `hermes/` (checked in CI) |

Both Omnigent images install `policies/omnigent/agent_stack_policies` under `/opt/agent-stack/policies` and add it to the venv with a `.pth` file. CI runs the policy tests inside the server image with `AGENT_STACK_IN_IMAGE=1`.

## Runner sandbox tooling

The runner image carries generic tooling so a project can run its own checks inside the sandbox pod, which runs as uid `1000660000` (`sandbox`, home `/sandbox`):

- `with-postgres <command...>` starts PostgreSQL 18 (PGDG) as the current user on `127.0.0.1:${PGPORT:-55432}` with database `${PG_DB:-app}` and user `${PG_USER:-app}`, exports `PGHOST`, `PGPORT`, `PGUSER`, `PGDATABASE` and `DATABASE_URL`, runs the command, keeps its exit code and removes the cluster. `images/omnigent-host/test/with-postgres.sh` tests it; CI runs it in the built image.
- DuckDB's spatial extension is preinstalled under `DUCKDB_EXTENSION_DIRECTORY=/opt/duckdb/extensions`. The CLI ignores that variable, so callers pass `SET extension_directory=...`.
- Playwright Chromium and its OS libraries live under `PLAYWRIGHT_BROWSERS_PATH=/opt/ms-playwright`; `/opt/ms-playwright/.playwright-version` holds the Playwright version they belong to (`PLAYWRIGHT_VERSION` in the Dockerfile). A project's Playwright pin must match it: bump both together. The Omnigent v0.19.1 Kubernetes sandbox cannot add a browser sidecar, so the browsers ship in the runner.
- Node comes from mise (`node` in `mise.toml`) and shadows the base image's Node 22; the global agent CLIs stay under `/usr/local`.

Projects own the check that drives this tooling, for example Routivo's `scripts/sandbox-check.sh`.
