# config

Configuration that is baked into images.

| Path | Contents |
| --- | --- |
| `omnigent/config.yaml` | Omnigent server: Kubernetes sandbox settings, runner image, LiteLLM gateway and per-harness models, the `agent_stack_policies` policy module and the server-wide `no_merge` and `no_nested_agents` policies that apply to every agent and harness. Writing bundles carry their own `path_guard`. The runner pin checked in here is used by PR and local builds; a release build of `omnigent-server` rewrites it to the runner of the same release (`images/README.md`). Renovate bumps the checked-in tag |
| `hermes/` | Hermes managed `config.yaml`, `SOUL.md` and `snapshot.sh` (nightly commit of skills and memories into a local history repo) |
