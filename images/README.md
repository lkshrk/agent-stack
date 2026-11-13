# images

Built from the repository root (`docker build -f images/<name>/Dockerfile .`) and published as `ghcr.io/lkshrk/agent-stack/<name>` by `.github/workflows/images.yml`.

| Image | Contents |
| --- | --- |
| `agent-tools/` | The Go programs from `cmd/` on distroless |
| `hermes/` | Hermes with its managed configuration and profile from `config/hermes/`, git, the vault tooling and the h-cloud update tools |
| `hermes-webui/` | Hermes web UI; pins the same Hermes image as `hermes/` (checked in CI) and ships the same tools, since it runs the agent in-process |

## Tools

`images/hermes/install-tools` installs the tools pinned in `images/hermes/mise.toml` (`kubectl`, `flux`, `helm`, `yq`, `crane`, `flate`, `just`, `gh`, `rg`) into `/usr/local/bin`, plus `skopeo` from Debian. mise is not left on `PATH`, so a project `.mise.toml` (h-cloud pins `flate`) can't swap them.

`kubectl`, `flux` and `helm` use the pod's service account (in-cluster config).

## GitHub token

h-cloud mounts the `agent-npa` token (Secret `hermes-github-token`) at `/var/run/agent/github/token` (`GH_TOKEN_FILE`). The git credential helper `git-credential-agent` answers github.com requests from it, and the `gh` wrapper sets `GH_TOKEN` only for the `gh` process it starts, reading the file on every call.
