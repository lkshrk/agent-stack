# config

Configuration that is baked into images.

| Path | Contents |
| --- | --- |
| `omnigent/config.yaml` | Omnigent server: Kubernetes sandbox settings, runner image, LiteLLM gateway and per-harness models. Renovate bumps the runner image tag |
| `hermes/` | Hermes managed `config.yaml`, `SOUL.md` and `snapshot.sh` (nightly commit of skills and memories into a local history repo) |
| `pr-agent/` | Deprecated copy of `policies/pr-agent/` for h-cloud until it points at the new path; to be removed |
