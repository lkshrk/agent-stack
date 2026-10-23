# agent-stack

Images, agent definitions and deploy manifests for two agents in the h-cloud cluster:

- **Omnigent** runs coding sessions. Each session is a Kubernetes Job in `omnigent-sandboxes`, started from the runner image built here. Polly plans and delegates to Claude Code (implementer) and Codex (reviewer); work lands as pull requests.
- **Hermes** is an always-on general agent reached through a Signal group. It keeps memory in its own home and in OpenViking, and starts Omnigent sessions when code needs to change.

Both read and write the knowledge vault through its normal workflow.

## Layout

| Path | Contents |
| --- | --- |
| `images/omnigent-host/` | Runner image: Omnigent host + Claude Code + Codex + plugins, LSPs and toolchains for every language in use |
| `images/token-minter/` | Exchanges an Authentik machine identity for short-lived GitHub tokens through octo-sts and writes them into a Secret |
| `images/hermes/` | Hermes image with git and the vault tooling |
| `agents/polly/` | Polly orchestrator and its workers, adapted from Omnigent's example |
| `config/hermes/` | Hermes `config.yaml`, `SOUL.md`, cron jobs |
| `deploy/omnigent/` | Kustomize: Omnigent server, runner namespace, token minter |
| `deploy/hermes/` | Kustomize: Hermes Deployment |

Cluster-specific values (hostnames, secrets, database) live in the h-cloud repository, which references the bases here.

## Credentials

Runners never hold long-lived credentials. LLM traffic goes through the LiteLLM gateway with a per-consumer key. GitHub tokens come from the token minter, are mounted as files and expire after an hour; git and `gh` read the current token on every call.
