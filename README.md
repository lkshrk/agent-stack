# agent-stack

Everything the h-cloud cluster needs to run its coding agents, built and released from one repository:

- container images (Omnigent runner and server, Hermes, Go tools)
- Omnigent agent bundles
- an agent plugin marketplace (`agent-stack`) for Claude Code and Codex
- small Go services and tools
- runtime policies fetched by other workloads

Two agents run on this stack:

- **Omnigent** runs coding sessions. Each session is a Kubernetes Job in `omnigent-sandboxes`, started from the runner image. Polly plans and delegates to Claude Code (implementer) and Codex (reviewer); work lands as pull requests.
- **Hermes** is an always-on general agent reached through a Signal group. It keeps memory in its own home and in OpenViking, and starts Omnigent sessions when code needs to change.

Deployment manifests, secrets, databases and routes live in the h-cloud repository.

## Layout

| Path | Contents |
| --- | --- |
| [`images/`](images/README.md) | Dockerfiles and image assets |
| [`bundles/`](bundles/README.md) | Omnigent agent bundles (`polly`) |
| [`plugins/`](plugins/README.md) | Plugins `runner`, `linear`, `personal` |
| [`cmd/`](cmd/README.md) | Go programs: `signal-shim`, `token-minter`, `linear-bridge` |
| [`config/`](config/README.md) | Omnigent server and Hermes configuration baked into images |
| [`policies/`](policies/README.md) | Policies other workloads fetch at runtime (PR-Agent) |
| `.claude-plugin/`, `.agents/plugins/` | Marketplace manifests for Claude Code and Codex |
| `.github/workflows/` | CI, image build, release and the WoW refresh |

## Plugin marketplace

- Runners: the `runner` plugin is baked into the `omnigent-host` image (`images/omnigent-host/bin/install-claude-plugins`).
- `linear` and `personal` are installed through apm with `git: lkshrk/agent-stack` and `path: plugins/<name>`.
- Claude Code on any machine: `claude plugin marketplace add lkshrk/agent-stack`.

## Credentials

Runners never hold long-lived credentials.

- **Models**: all LLM traffic goes through the LiteLLM gateway with a per-consumer key.
- **Linear**: agents read through the MCP server `linear_read`. Writes go through `linear_app` as the Linear app "Daedalus", behind the guard in h-cloud.
- **GitHub**: tokens come from `token-minter`, are mounted as files and expire after an hour; git and `gh` read the current token on every call.

## Releases

Pushes to `main` run `semantic-release` (after `ci` succeeds), which tags a version and builds the images in `images.yml`. Renovate in h-cloud bumps the image tags.

## CI

| Workflow | Purpose |
| --- | --- |
| `ci.yml` | `go vet`, `go test -race`, golangci-lint, and a build of every changed image on pull requests |
| `images.yml` | Builds and pushes `ghcr.io/lkshrk/agent-stack/<image>` for a release tag |
| `release.yml` | `semantic-release` on `main`, then calls `images.yml` |
| `wow-season-updates.yml` | Weekly WoW season refresh with Claude Code. Needs secret `WOW_REFRESH_API_KEY` and variables `WOW_REFRESH_BASE_URL`, `WOW_REFRESH_MODEL`; the key is the LiteLLM key `agent-stack-ci` in the CI team |
