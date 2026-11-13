# syntax=docker/dockerfile:1.10
ARG HERMES_IMAGE=docker.io/nousresearch/hermes-agent:v0.22.5@sha256:9c5561579f3ffdf333401797094ac3a743738d6c25d40f4331c7bf77b694238d

FROM ${HERMES_IMAGE} AS image

ARG OBSIDIAN_WIKI_VERSION=2026.10.4
ARG USEFUL_SKILLS_VERSION=v0.6.0@36f406f557790afebac0c9f858de4b2e3da21b60
ARG MISE_VERSION=v2026.10.24
ARG MISE_SHA256=be97dbac1c0b1fe08bd8c0cf01690f4273ea9aa02ad766b98d4e20c43eb9059d
# Same commit the Hermes plugin catalog pins for openviking.
ARG OPENVIKING_PLUGIN_SHA=9f150ece247ad683494ab172d738023bd6dc6170

USER root
RUN apt-get update \
 && apt-get install -y --no-install-recommends git jq curl ca-certificates \
 && rm -rf /var/lib/apt/lists/*

RUN --mount=type=bind,source=images/hermes,target=/build \
    /build/install-tools /build/mise.toml

RUN "${HERMES_PYTHON:-python3}" -m venv /opt/obsidian-wiki \
 && /opt/obsidian-wiki/bin/pip install --no-cache-dir "obsidian-wiki==${OBSIDIAN_WIKI_VERSION}" \
 && ln -s /opt/obsidian-wiki/bin/obsidian-wiki /usr/local/bin/obsidian-wiki

RUN git clone --quiet --filter=blob:none --no-checkout https://github.com/volcengine/OpenViking.git /tmp/ov \
 && git -C /tmp/ov sparse-checkout set examples/hermes-plugin \
 && git -C /tmp/ov checkout --quiet "${OPENVIKING_PLUGIN_SHA}" \
 && cp -a /tmp/ov/examples/hermes-plugin /opt/hermes/plugins/memory/openviking \
 && rm -rf /tmp/ov /opt/hermes/plugins/memory/openviking/tests \
 && /opt/hermes/.venv/bin/python -c "import httpx, psutil, packaging"

RUN git clone --quiet --depth 1 --branch "${USEFUL_SKILLS_VERSION%@*}" https://github.com/lkshrk/useful-skills.git /opt/useful-skills \
 && test "$(git -C /opt/useful-skills rev-parse HEAD)" = "${USEFUL_SKILLS_VERSION#*@}" \
 && rm -rf /opt/useful-skills/.git \
 && chmod -R a-w,a+rX /opt/useful-skills

COPY images/hermes/bin/ /opt/agent/bin/
COPY --chmod=0644 images/hermes/profile.d/agent-path.sh /etc/profile.d/zz-agent-path.sh
COPY images/hermes/gitconfig /etc/gitconfig.d/agent
COPY --chmod=0644 config/hermes/config.yaml /etc/hermes/config.yaml
COPY --chmod=0644 config/hermes/SOUL.md /opt/hermes-soul/SOUL.md
COPY --chmod=0755 config/hermes/snapshot.sh /opt/agent/bin/hermes-snapshot
COPY --chmod=0755 images/hermes/cont-init.d/03-agent-stack /etc/cont-init.d/03-agent-stack
RUN chmod 0755 /opt/agent/bin/* \
 && git config --system include.path /etc/gitconfig.d/agent

COPY config/hermes/profiles/ /opt/agent-stack/hermes-profiles/
RUN chmod -R u=rwX,go=rX /opt/agent-stack

# Registry logins stay in the container, never on the backed-up /opt/data volume.
ENV PATH="/opt/agent/bin:${PATH}" \
    HERMES_MANAGED_DIR=/etc/hermes \
    REGISTRY_AUTH_FILE=/tmp/registry-auth/auth.json \
    DOCKER_CONFIG=/tmp/docker

FROM image AS test
COPY hermes/ /src/hermes/
COPY config/hermes/ /src/config/hermes/
WORKDIR /src
RUN AGENT_STACK_IN_IMAGE=1 /opt/hermes/.venv/bin/python -B -m unittest discover -s hermes/tests -t hermes -v

# The default target is the published image, without the test stage.
FROM image
