# config

Hermes configuration baked into the `hermes` and `hermes-webui` images.

| Path | Contents |
| --- | --- |
| `hermes/config.yaml` | Managed config (`/etc/hermes/config.yaml`), deep-merged on top of the profile's config: LiteLLM provider, auxiliary models, approvals, security |
| `hermes/SOUL.md` | Hermes' rules, installed read-only into `$HERMES_HOME` on every start |
| `hermes/snapshot.sh` | Nightly commit of `skills/` and `memories/` into a local history repository on the PVC |
| `hermes/profiles/default/` | The only profile: `config.yaml` (h-cloud mounts it read-only over `/opt/data/config.yaml`) and the shipped skills, read-only at `/opt/agent-stack/hermes-profiles/default/skills` through `skills.external_dirs`. A skill of the same name in Hermes' own `/opt/data/skills` shadows a shipped one |

Shipped skills:

- `upgrade-h-cloud`: works the open Renovate PRs on `lkshrk/h-cloud`, with the `agent-npa` token, `just flux validate` and a health gate on the live cluster after every merge. Hermes' cluster identity reads everything except Secrets and may only request Flux reconciles; h-cloud enforces that with the `hermes-read-and-reconcile` admission policy.
