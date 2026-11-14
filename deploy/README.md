# deploy

Agent behaviour that h-cloud deploys: what agents may do and how they behave. h-cloud reads this tree from `main` through the Flux `GitRepository` `agent-stack` (only `deploy/` is fetched) and applies each consumer directory with its own Flux `Kustomization`. Where and how it runs (HelmReleases, secrets, RBAC, routes) stays in h-cloud.

A merge to `main` reaches the cluster on Flux's next source fetch; Reloader restarts the consumer when its ConfigMap changes.

| Path | ConfigMap | Consumer |
| --- | --- | --- |
| [`pr-agent/`](pr-agent/) | `ai/pr-agent-policy`: shared PR-Agent policy `pr-agent.toml` and review guide `review.md` | PR-Agent; its init container merges `review.md` into `extra_instructions = ""` |
| `tests/` | | Checks run by CI on every change under `deploy/` |

ConfigMaps keep fixed names (`disableNameSuffixHash`) because the consumers mount them by name. Repository-local `.pr_agent.toml` overrides remain possible.
