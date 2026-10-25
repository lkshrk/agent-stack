# syntax=docker/dockerfile:1.10
# Keep HERMES_IMAGE identical to images/hermes/Dockerfile: the WebUI runs this agent in-process next to that gateway.
ARG HERMES_IMAGE=docker.io/nousresearch/hermes-agent:v0.22.5@sha256:9c5561579f3ffdf333401797094ac3a743738d6c25d40f4331c7bf77b694238d
ARG WEBUI_IMAGE=ghcr.io/nesquena/hermes-webui:0.52.544@sha256:03e6d1dc9ebabbb53a6b270dc11395db00146f63e76ec8f51686f1e89a0be0a4

FROM ${HERMES_IMAGE} AS hermes

FROM ${WEBUI_IMAGE}

ARG OBSIDIAN_WIKI_VERSION=2026.10.3
ARG KUBECTL_VERSION=v1.37.1
# Same commit the Hermes plugin catalog pins for openviking.
ARG OPENVIKING_PLUGIN_SHA=9f150ece247ad683494ab172d738023bd6dc6170

USER root
# The OIDC SSRF guard rejects private IPs, but in-cluster DNS sends the issuer to the envoy ClusterIP.
RUN f=/apptoo/api/auth_oidc.py \
 && grep -qx 'def _is_disallowed_oidc_host(hostname: str) -> bool:' "$f" \
 && sed -i 's|^def _is_disallowed_oidc_host(hostname: str) -> bool:$|&\n    if hostname.lower() in os.getenv("HERMES_WEBUI_OIDC_TRUSTED_HOSTS", "").lower().split(","):\n        return False|' "$f" \
 && grep -q 'HERMES_WEBUI_OIDC_TRUSTED_HOSTS' "$f"

RUN apt-get update \
 && apt-get install -y --no-install-recommends jq \
 && rm -rf /var/lib/apt/lists/*

RUN curl -fsSL "https://dl.k8s.io/release/${KUBECTL_VERSION}/bin/linux/amd64/kubectl" -o /usr/local/bin/kubectl \
 && curl -fsSL "https://dl.k8s.io/release/${KUBECTL_VERSION}/bin/linux/amd64/kubectl.sha256" -o /tmp/kubectl.sha256 \
 && echo "$(cat /tmp/kubectl.sha256)  /usr/local/bin/kubectl" | sha256sum -c - \
 && chmod 0755 /usr/local/bin/kubectl && rm /tmp/kubectl.sha256

RUN python3 -m venv /opt/obsidian-wiki \
 && /opt/obsidian-wiki/bin/pip install --no-cache-dir "obsidian-wiki==${OBSIDIAN_WIKI_VERSION}" \
 && ln -s /opt/obsidian-wiki/bin/obsidian-wiki /usr/local/bin/obsidian-wiki

# docker_init.bash probes /opt/hermes for the agent source.
COPY --from=hermes /opt/hermes /opt/hermes
RUN rm -rf /opt/hermes/.venv \
 && git clone --quiet --filter=blob:none --no-checkout https://github.com/volcengine/OpenViking.git /tmp/ov \
 && git -C /tmp/ov sparse-checkout set examples/hermes-plugin \
 && git -C /tmp/ov checkout --quiet "${OPENVIKING_PLUGIN_SHA}" \
 && rm -rf /opt/hermes/plugins/memory/openviking \
 && cp -a /tmp/ov/examples/hermes-plugin /opt/hermes/plugins/memory/openviking \
 && rm -rf /tmp/ov /opt/hermes/plugins/memory/openviking/tests \
 && chmod -R a-w /opt/hermes

COPY images/hermes/bin/ /opt/agent/bin/
COPY images/hermes/gitconfig /etc/gitconfig.d/agent
COPY --chmod=0644 config/hermes/config.yaml /etc/hermes/config.yaml
# COPY --chmod also applies to the parent it creates, leaving /etc/hermes unsearchable for the runtime user.
RUN chmod 0755 /opt/agent/bin/* /etc/hermes \
 && git config --system include.path /etc/gitconfig.d/agent

# Build /app and its venv now so the pod starts offline; the init skips the install once .deps_installed exists.
ENV WANTED_UID=10000 \
    WANTED_GID=10000 \
    HERMES_WEBUI_STATE_DIR=/opt/data/webui
# The WebUI installs Hermes with `uv pip install <path>`, which ignores Hermes' [tool.uv] overrides; without them its extras do not resolve.
RUN python3 -c 'import tomllib; print("\n".join(tomllib.load(open("/opt/hermes/pyproject.toml","rb"))["tool"]["uv"]["override-dependencies"]))' \
      > /etc/hermes-overrides.txt \
 && cd /opt/hermes \
 && uv export --frozen --all-extras --no-hashes --no-emit-project -o /etc/hermes-constraints.txt >/dev/null
RUN mv /apptoo/server.py /apptoo/server.py.real \
 && printf 'raise SystemExit(0)\n' > /apptoo/server.py \
 && { UV_CONSTRAINT=/etc/hermes-constraints.txt UV_OVERRIDE=/etc/hermes-overrides.txt \
      HERMES_WEBUI_STATE_DIR=/tmp/prebuild/webui HERMES_HOME=/tmp/prebuild/home \
      /hermeswebui_init.bash > /tmp/prebuild.log 2>&1 || { tail -40 /tmp/prebuild.log; exit 1; }; } \
 && tail -5 /tmp/prebuild.log \
 && mv /apptoo/server.py.real /apptoo/server.py \
 && cp /apptoo/server.py /app/server.py \
 && test -f /app/venv/.deps_installed \
 && rm -rf /tmp/prebuild /tmp/prebuild.log

ENV PATH="/opt/agent/bin:${PATH}" \
    HERMES_MANAGED_DIR=/etc/hermes
