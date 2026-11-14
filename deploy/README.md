# deploy

Agent behaviour that h-cloud deploys: what agents may do and how they behave. h-cloud reads this tree from `main` through the Flux `GitRepository` `agent-stack` (only `deploy/` is fetched) and applies each consumer directory with its own Flux `Kustomization`. Where and how it runs (HelmReleases, secrets, RBAC, routes) stays in h-cloud.

A merge to `main` reaches the cluster on Flux's next source fetch; Reloader restarts the consumer when its ConfigMap changes.

| Path | ConfigMap | Consumer |
| --- | --- | --- |
| [`pr-agent/`](pr-agent/) | `ai/pr-agent-policy`: shared PR-Agent policy `pr-agent.toml` and review guide `review.md` | PR-Agent; its init container merges `review.md` into `extra_instructions = ""` |
| [`litellm/`](litellm/) | `ai/litellm-linear-app-guard` (`linear_app_guard.py`) and `ai/litellm-request-size-guard` (`request_size_guard.py`): LiteLLM guardrail and callback code | LiteLLM proxy; its HelmRelease mounts both files into `/etc/litellm/` |
| [`litellm/`](litellm/) | `ai/litellm-mcp-servers` (`mcp-servers.yaml`): MCP server catalogue, tool allowlists, access groups and aliases; credentials only as `os.environ/` references | LiteLLM proxy; its HelmRelease merges the file into its values through `valuesFrom` |
| [`litellm/`](litellm/) | `ai/litellm-skills-seed` (`skills-seed.json`, `skills-seed.py`): Claude Code plugins the gateway offers and the script that registers them; Renovate bumps the pins | CronJob `litellm-skills-seed`, which runs the script every six hours |
| [`dsh/`](dsh/) | `dsh-yawn/dsh-yawn-agent-config`: dsh-superagent's team prompts and skills, executor models (`superagent.yaml`) and the default and subagent models (`models.yaml`), as `upsert-patch.mjs` entries | dsh-yawn control plane; its init containers upsert both files into the dsh profile after h-cloud's own rows |
| `tests/` | | Checks run by CI on every change under `deploy/`: the dsh entries (upsert shape, skill names, models within the subagent allowlist), the MCP catalogue and skills seed shape, the LiteLLM guardrail tests against the LiteLLM version pinned in `ci.yml` (`LITELLM_VERSION`, kept equal to the image h-cloud deploys) |

ConfigMaps keep fixed names (`disableNameSuffixHash`) because the consumers mount them by name. Repository-local `.pr_agent.toml` overrides remain possible.
