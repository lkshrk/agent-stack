# syntax=docker/dockerfile:1.10
ARG OMNIGENT_HOST_IMAGE=ghcr.io/omnigent-ai/omnigent-host:v0.19.1@sha256:46ddb52b8013e69bcd2068842d9746bc896d50f5884a2469d51a27a6211c5814

FROM ${OMNIGENT_HOST_IMAGE} AS upstream-env
RUN printenv >/upstream.env

FROM ${OMNIGENT_HOST_IMAGE} AS upstream
USER root
# Claude Code and Codex are reinstalled at pinned versions below; kiro-cli is unused.
RUN --mount=type=bind,source=images/omnigent-host/build,target=/build-tools \
    rm -rf /usr/local/bin/kiro-cli /usr/local/bin/kiro-cli-chat /root/.local/bin/kiro-cli* \
      /usr/local/bin/claude /usr/local/bin/codex \
      /usr/local/lib/node_modules/@anthropic-ai /usr/local/lib/node_modules/@openai \
 && /build-tools/prune-native-platforms /usr/local/lib/node_modules

# A removal in a later layer still ships the bytes, so the base is flattened into one layer without them.
FROM scratch AS image
COPY --from=upstream / /
ENV PATH=/opt/venv/bin:/usr/local/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin \
    LANG=C.UTF-8 \
    PYTHON_VERSION=3.12.15 \
    PYTHON_SHA256=c2c4321961fab0fb999d66e0cecf521c2ab3994c7992873ea99e306c1094fd5a \
    NPM_CONFIG_REGISTRY= \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    IS_SANDBOX=1 \
    AGY_VERSION=1.1.16 \
    AGY_SHA256_AMD64=7742953b7835b457e9102f1357a493913657dfd147435584f609d58356ec085a \
    AGY_SHA256_ARM64=c71599bb548cf72dfe7a8f6c411b3c7a3623cd4b0f6276bae89c69f769d6c92d

ARG CLAUDE_CODE_VERSION=2.1.309
ARG CODEX_VERSION=0.164.0
ARG OMP_VERSION=18.12.6
ARG CONTEXT_MODE_VERSION=1.0.169
ARG RTK_VERSION=v1.0.1
ARG RTK_SHA256=5b919c3bccb799b67a61866d0e617410a4fcdf173e2614440e59de2fa2003436
ARG CBM_VERSION=v0.12.1
ARG CBM_SHA256=8c3d0341064301e78ecc84cf7348d4e2c6562a0f57448243ea560aaa34de36c1
ARG KOTLIN_LSP_BUILD=263.7084.0
ARG KOTLIN_LSP_VERSION=0.0.27
ARG KOTLIN_LSP_SHA256=6787185abb0e7f3b527484bb6cc4a881ce42b2e39320626937e65839751075e9
ARG MISE_VERSION=v2026.10.15
ARG PLAYWRIGHT_VERSION=1.60.0

USER root
SHELL ["/bin/bash", "-o", "pipefail", "-c"]

RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      build-essential pkg-config libssl-dev zsh jq xz-utils zip \
      lua5.4 libcurl4-openssl-dev libxml2 libsqlite3-0 libncurses6 tzdata \
      libhdf5-dev \
 && rm -rf /var/lib/apt/lists/*

ENV MISE_DATA_DIR=/opt/mise \
    MISE_CONFIG_DIR=/etc/mise \
    MISE_CACHE_DIR=/opt/mise/cache \
    MISE_GLOBAL_CONFIG_FILE=/etc/mise/config.toml \
    MISE_YES=1
ENV PATH="/opt/mise/shims:/opt/agent/bin:${PATH}"
# codebase-memory-mcp refuses a store under the world-writable HOME emptyDir that Omnigent mounts.
ENV CBM_CACHE_DIR=/tmp/codebase-memory-mcp

RUN curl -fsSL "https://github.com/jdx/mise/releases/download/${MISE_VERSION}/mise-${MISE_VERSION}-linux-x64" -o /usr/local/bin/mise \
 && chmod 0755 /usr/local/bin/mise

COPY images/omnigent-host/mise.toml /etc/mise/config.toml
RUN mise install \
 && mise reshim \
 && rm -rf /opt/mise/cache /root/.cache /root/go /root/.npm \
 && chmod -R a+rX /opt/mise

RUN --mount=type=bind,source=images/omnigent-host/build,target=/build-tools \
    npm install -g --prefix /usr/local --no-fund --no-audit \
      "@anthropic-ai/claude-code@${CLAUDE_CODE_VERSION}" \
      "@openai/codex@${CODEX_VERSION}" \
      "@oh-my-pi/pi-coding-agent@${OMP_VERSION}" \
      "context-mode@${CONTEXT_MODE_VERSION}" \
 && /build-tools/prune-native-platforms /usr/local/lib/node_modules \
 && rm -rf /root/.npm

RUN mkdir -p /opt/agent/bin /tmp/dl && cd /tmp/dl \
 && curl -fsSL "https://github.com/rtk-ai/rtk/releases/download/${RTK_VERSION}/rtk-x86_64-unknown-linux-musl.tar.gz" -o rtk.tgz \
 && echo "${RTK_SHA256}  rtk.tgz" | sha256sum -c - \
 && tar -xzf rtk.tgz && install -m 0755 "$(find . -type f -name rtk | head -1)" /opt/agent/bin/rtk \
 && curl -fsSL "https://github.com/DeusData/codebase-memory-mcp/releases/download/${CBM_VERSION}/codebase-memory-mcp-linux-amd64-portable.tar.gz" -o cbm.tgz \
 && echo "${CBM_SHA256}  cbm.tgz" | sha256sum -c - \
 && tar -xzf cbm.tgz && install -m 0755 "$(find . -type f -name codebase-memory-mcp | head -1)" /opt/agent/bin/codebase-memory-mcp \
 && curl -fsSL "https://download.jetbrains.com/language-server/kotlin-server/${KOTLIN_LSP_BUILD}/kotlin-server-${KOTLIN_LSP_VERSION}-linux-amd64.vsix" -o kotlin.vsix \
 && echo "${KOTLIN_LSP_SHA256}  kotlin.vsix" | sha256sum -c - \
 && mkdir -p /opt/kotlin-lsp && unzip -q kotlin.vsix 'extension/server/*' -d /tmp/dl/k \
 && cp -a /tmp/dl/k/extension/server/. /opt/kotlin-lsp/ \
 && chmod 0755 /opt/kotlin-lsp/kotlin-lsp.sh /opt/kotlin-lsp/bin/intellij-server \
 && ln -s /opt/kotlin-lsp/kotlin-lsp.sh /opt/agent/bin/kotlin-lsp \
 && cd / && rm -rf /tmp/dl

ENV PLAYWRIGHT_BROWSERS_PATH=/opt/ms-playwright
RUN apt-get update \
 && npx -y "playwright@${PLAYWRIGHT_VERSION}" install --with-deps chromium \
 && echo "${PLAYWRIGHT_VERSION}" >"${PLAYWRIGHT_BROWSERS_PATH}/.playwright-version" \
 && chmod -R a+rX "${PLAYWRIGHT_BROWSERS_PATH}" \
 && rm -rf /var/lib/apt/lists/* /root/.npm

COPY images/omnigent-host/bin/ /opt/agent/bin/
COPY images/omnigent-host/claude/ /opt/agent/claude-home/
COPY images/omnigent-host/codex/ /opt/agent/codex-home/
COPY images/omnigent-host/omp/ /opt/agent/omp-home/
COPY images/omnigent-host/agents/ /opt/agent/agents-home/.agents/
COPY .claude-plugin/ /opt/agent/marketplace/.claude-plugin/
COPY .agents/ /opt/agent/marketplace/.agents/
COPY plugins/ /opt/agent/marketplace/plugins/
COPY images/omnigent-host/profile.d/agent-home.sh /etc/profile.d/zz-agent-home.sh
COPY images/omnigent-host/gitconfig /etc/gitconfig.d/agent
RUN chmod 0755 /opt/agent/bin/* \
 && git config --system include.path /etc/gitconfig.d/agent

RUN HOME=/opt/agent/claude-home /opt/agent/bin/install-claude-plugins \
 && /opt/agent/bin/install-omp-skills /opt/agent/agents-home/.agents/skills \
 && rm -rf /opt/agent/claude-home/.claude/plugins/cache/*/.git \
 && chmod -R a+rX /opt/agent

COPY policies/omnigent/agent_stack_policies/ /opt/agent-stack/policies/agent_stack_policies/
RUN echo /opt/agent-stack/policies > "$(/opt/venv/bin/python3 -c 'import sysconfig; print(sysconfig.get_path("purelib"))')/agent_stack_policies.pth" \
 && chmod -R a+rX /opt/agent-stack

WORKDIR /root
CMD ["sleep", "infinity"]

FROM image AS test
COPY policies/omnigent/ /src/policies/omnigent/
COPY bundles/ /src/bundles/
COPY config/omnigent/ /src/config/omnigent/
COPY images/omnigent-host/codex/ /src/images/omnigent-host/codex/
COPY images/omnigent-host/test/ /src/images/omnigent-host/test/
COPY --from=upstream-env /upstream.env /src/upstream.env
WORKDIR /src
RUN AGENT_STACK_IN_IMAGE=1 /opt/venv/bin/python3 -m unittest discover -s policies/omnigent/tests -t policies/omnigent -v
RUN install -d -m 0777 /home/omnigent
RUN bash images/omnigent-host/test/removed-clis.sh
USER 1000660000
RUN \
    --mount=type=bind,source=images/omnigent-host/test/fixtures/startup-file,target=/home/omnigent/.bashrc \
    --mount=type=bind,source=images/omnigent-host/test/fixtures/startup-file,target=/home/omnigent/.bash_profile \
    --mount=type=bind,source=images/omnigent-host/test/fixtures/startup-file,target=/home/omnigent/.bash_login \
    --mount=type=bind,source=images/omnigent-host/test/fixtures/startup-file,target=/home/omnigent/.bash_logout \
    --mount=type=bind,source=images/omnigent-host/test/fixtures/startup-file,target=/home/omnigent/.profile \
    --mount=type=bind,source=images/omnigent-host/test/fixtures/startup-file,target=/home/omnigent/.zshrc \
    --mount=type=bind,source=images/omnigent-host/test/fixtures/startup-file,target=/home/omnigent/.zshenv \
    --mount=type=bind,source=images/omnigent-host/test/fixtures/startup-file,target=/home/omnigent/.zprofile \
    --mount=type=bind,source=images/omnigent-host/test/fixtures/startup-file,target=/home/omnigent/.zlogin \
    --mount=type=bind,source=images/omnigent-host/test/fixtures/startup-file,target=/home/omnigent/.zlogout \
    --mount=type=bind,source=images/omnigent-host/test/fixtures/startup-file,target=/home/omnigent/.inputrc \
    fail=0; for t in images/omnigent-host/test/*.sh; do echo "== $t"; bash "$t" || fail=1; done; exit "$fail"

# The default target is the published image, without the test stage.
FROM image
