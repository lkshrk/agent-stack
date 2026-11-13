# config

Hermes configuration baked into the `hermes` and `hermes-webui` images.

| Path | Contents |
| --- | --- |
| `hermes/config.yaml` | Managed config (`/etc/hermes/config.yaml`), deep-merged on top of the profile's config: LiteLLM provider, auxiliary models, approvals, security |
| `hermes/SOUL.md` | Hermes' rules, installed read-only into `$HERMES_HOME` on every start |
| `hermes/snapshot.sh` | Nightly commit of `skills/` and `memories/` into a local history repository on the PVC |
| `hermes/profiles/default/` | The only profile's `config.yaml` (h-cloud mounts it read-only over `/opt/data/config.yaml`) |

Skills come from [lkshrk/useful-skills](https://github.com/lkshrk/useful-skills), cloned at the release in `USEFUL_SKILLS_VERSION` (both Dockerfiles, bumped by Renovate) to `/opt/useful-skills`, read-only. `skills.external_dirs` lists only the skill directories Hermes uses (Hermes scans each entry recursively, so listing a skill's own directory exposes just that skill):

- `upgrade-flux-cluster`: works the open Renovate PRs of `lkshrk/h-cloud`, reading h-cloud's specifics from its `AGENTS.md` ("Upgrade sweeps") and this pod's from `SOUL.md` ("Environment").
- `upgrade-deps`: dependency upgrades in other repositories, one at a time, gated on each repo's own checks.
- `wow-help`, `wow-spec`: World of Warcraft answers and spec setups on request over Signal.

A skill of the same name in Hermes' own `/opt/data/skills` shadows a shipped one.
