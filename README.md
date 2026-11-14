# agent-stack

Images, configuration and small tools for **Hermes**, the owner's always-on agent in the h-cloud cluster. Hermes is reached through a Signal group and a web UI, keeps memory in its own home and in OpenViking, runs cron jobs, and keeps h-cloud up to date by working its Renovate PRs (`upgrade-flux-cluster` from [useful-skills](https://github.com/lkshrk/useful-skills)).

Deployment manifests, secrets, RBAC and routes live in the h-cloud repository (`kubernetes/apps/ai/hermes`).

## Layout

| Path | Contents |
| --- | --- |
| [`images/`](images/README.md) | Dockerfiles for `hermes`, `hermes-webui` and `agent-tools`, and Hermes' wrappers and tool pins |
| [`config/`](config/README.md) | Hermes managed config, `SOUL.md` and its single profile `default` with the shipped skills |
| [`cmd/`](cmd/README.md) | Go program `signal-shim`: Hermes' Signal adapter to signal-cli-rest-api |
| `hermes/tests/` | Tests for the Hermes config and image |
| [`policies/`](policies/README.md) | Pointer to the PR-Agent policy, now in h-cloud |

## Credentials

- **Models**: all LLM traffic goes through the LiteLLM gateway with Hermes' key.
- **GitHub**: a classic PAT of the bot user `agent-npa` (`repo`, `workflow`, `read:packages`), the SOPS Secret `ai/hermes-github-token` in h-cloud, mounted as a file; git and `gh` read it on every call.
- **Cluster**: the `hermes` service account reads everything except Secrets and may only request Flux reconciles.

## Releases

Pushes to `main` run `semantic-release` (after `ci` succeeds), which tags a version and builds the images in `images.yml`. Each released image is deployed through an `image-update` dispatch to h-cloud, which pins the new tag on its `main`.

## CI

| Workflow | Purpose |
| --- | --- |
| `ci.yml` | actionlint, `go vet`, `go test -race`, golangci-lint, the Hermes config tests, and an image build with each image's tests: changed images on pull requests, every image on `main` and on manual runs. Pushes to `main` write the GHCR build cache `<image>:buildcache` |
| `images.yml` | Builds and pushes `ghcr.io/lkshrk/agent-stack/<image>` for a release tag |
| `release.yml` | `semantic-release` on `main`, then calls `images.yml` |
