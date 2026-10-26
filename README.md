# agent-stack

Images, agent definitions and deploy manifests for two agents in the h-cloud cluster:

- **Omnigent** runs coding sessions. Each session is a Kubernetes Job in `omnigent-sandboxes`, started from the runner image built here. Polly plans and delegates to Claude Code (implementer) and Codex (reviewer); work lands as pull requests.
- **Hermes** is an always-on general agent reached through a Signal group. It keeps memory in its own home and in OpenViking, and starts Omnigent sessions when code needs to change.

Both read and write the knowledge vault through its normal workflow.

## Layout

| Path | Contents |
| --- | --- |
| `images/omnigent-host/` | Runner image: Omnigent host + Claude Code + Codex + plugins, LSPs and toolchains for every language in use |
| `images/omnigent-server/` | Omnigent server with the agent bundles and sandbox configuration baked in |
| `images/agent-tools/` | Go tools from `cmd/`: `token-minter` (octo-sts GitHub tokens into Secrets) and `signal-shim` (signal-cli HTTP API for Hermes on top of signal-cli-rest-api) |
| `images/hermes/` | Hermes with its managed configuration, `SOUL.md`, git and the vault tooling |
| `agents/polly/` | Polly orchestrator and its workers, adapted from Omnigent's example |
| `config/omnigent/` | Omnigent server configuration: runner image, model gateway, per-harness models |
| `config/hermes/` | Hermes managed `config.yaml`, `SOUL.md`, learning snapshot script |
| `plugins/runner/` | Plugin `runner`: skills `upgrade-deps`, `project-tool-allowlist`, `ai-context-generator` |
| `plugins/linear/` | Plugin `linear`: Linear workflow skills (`linear-intake`, `-refine`, `-implement`, `-reconcile`, `-status`) with their validators, templates and tests |
| `plugins/personal/` | Plugin `personal`: machine-maintenance, documentation and World of Warcraft skills |
| `.claude-plugin/`, `.agents/plugins/` | Marketplace manifests (`agent-stack`) for Claude Code and Codex |
| `config/pr-agent/` | PR-Agent review policy (`pr-agent.toml`) and reviewer guide (`review.md`), loaded by the h-cloud init container |

Everything here ships inside images, except `config/pr-agent/`, which h-cloud fetches at runtime, and `plugins/`, which clients install from this repository as the `agent-stack` marketplace (`claude plugin marketplace add lkshrk/agent-stack`). Deployment manifests, secrets, databases and routes live in the h-cloud repository.

## Credentials

Runners never hold long-lived credentials. LLM traffic goes through the LiteLLM gateway with a per-consumer key. GitHub tokens come from the token minter, are mounted as files and expire after an hour; git and `gh` read the current token on every call.
