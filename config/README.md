# config

Configuration that is baked into images.

| Path | Contents |
| --- | --- |
| `omnigent/config.yaml` | Omnigent server: Kubernetes sandbox settings, runner image, LiteLLM gateway and per-harness models, the `agent_stack_policies` policy module and the server-wide `no_merge` and `path_guard` policies that apply to every agent and harness. Renovate bumps the runner image tag |
| `hermes/` | Hermes managed `config.yaml`, `SOUL.md` and `snapshot.sh` (nightly commit of skills and memories into a local history repo) |
