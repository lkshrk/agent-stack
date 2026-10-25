# Swap hermes-workspace for hermes-webui

Replace `outsourc-e/hermes-workspace` with `nesquena/hermes-webui` in the upstream two-container layout: our hermes image keeps running the gateway (Signal, cron, API, dashboard); a new `hermes-webui` image serves browser chat on `:8787` and runs the agent in-process against the shared `HERMES_HOME`.

## Decisions

- **Own image, not upstream as-is.** Browser-chat tools run in the webui container (upstream #681), so it needs our tools: kubectl, `git-credential-agent`, `/etc/gitconfig.d/agent`, obsidian-wiki, the OpenViking plugin.
- **Hermes source from our hermes image.** `docker_init.bash` already probes `/opt/hermes`; `COPY --from=` the built hermes image keeps webui and gateway on the same Hermes version by construction (upstream requires matching versions).
- **Dependencies installed at build time.** The init script skips `install_venv_dependencies` once `/app/venv/.deps_installed` exists, so the pod starts without network access.
- **Native OIDC against Authentik** replaces the Envoy OIDC SecurityPolicies on the webui routes; the Authentik `hermes` provider gets the webui callback URLs.

## agent-stack

1. `images/hermes-webui/Dockerfile`: `FROM ghcr.io/nesquena/hermes-webui:<pinned>`; `COPY --from` hermes `/opt/hermes`, `/opt/agent/bin`, `/etc/gitconfig.d/agent`, `/etc/hermes`, obsidian-wiki venv, kubectl; run the venv build step; set `WANTED_UID/GID=10000`.
2. Pin `HERMES_WEBUI_VERSION` next to `HERMES_IMAGE`; Renovate must bump them together (group rule).
3. Add `hermes-webui` to the `images` matrix and the release.
4. CI smoke test: container starts, `/health` 200, `/api/health/agent` reports the agent importable.

## h-cloud

5. Replace the `workspace` container with `webui` (`ghcr.io/lkshrk/agent-stack/hermes-webui`), port 8787, `HERMES_HOME=/opt/data`, same token and data mounts, `HERMES_WEBUI_STATE_DIR=/opt/data/webui`.
6. `HERMES_WEBUI_OIDC_*` from a SOPS secret; Authentik `hermes` provider gets `https://hermes.ai.h-cloud.{lan,io}/` webui callback URIs.
7. Routes keep both hostnames; drop `hermes-oidc` and `hermes-external-oidc` once native OIDC works; the external route then needs crowdsec back (remove `crowdsec.h-cloud.io/skip`).
8. Remove workspace-only settings: `HERMES_API_TOKEN`, `CLAUDE_PASSWORD`, `KNOWLEDGE_DIR`, `COOKIE_SECURE`, `TRUST_PROXY`, `HERMES_UID/GID`.
9. agent-stack: drop the `/api/mcp` alias patch once nothing calls it.

## Rollout

- Run webui on a temporary hostname (`hermes-next.ai.h-cloud.lan`) next to the workspace first; switch hostnames after chat, tools (kubectl, git push to the vault), Signal and cron are verified.

## Open questions

- Exact OIDC callback path and claim for `allow_claim`/`allow_values` (group `ai`).
- Whether webui's in-process agent and the gateway can share `/opt/data` without lock contention (upstream says single-home is the supported case; verify `state.db` locking).
