# config

Configuration that is baked into images.

| Path | Contents |
| --- | --- |
| `omnigent/config.yaml` | Omnigent server: Kubernetes sandbox settings, runner image, LiteLLM gateway and per-harness models, the `agent_stack_policies` policy module and the server-wide `no_merge` and `no_nested_agents` policies that apply to every agent and harness. Writing bundles carry their own `path_guard`. The runner pin checked in here is used by PR and local builds; a release build of `omnigent-server` rewrites it to the runner of the same release (`images/README.md`). Renovate bumps the checked-in tag |
| `hermes/` | Hermes managed `config.yaml`, `SOUL.md` and `snapshot.sh` (nightly commit of skills and memories into a local history repo) |
| `public-validator-attestation.pub` | Ed25519 public key (SPKI PEM) of the public validator. An attestation verifies when its `signature` is valid over `agent-stack.public-validator.attestation.v1`, a NUL byte, and the attestation without `signature` as compact sorted-key JSON, and its `nonce` is the one the verifier passed to the Job. The private key is the SOPS Secret `public-validator-signing-key` in h-cloud |
