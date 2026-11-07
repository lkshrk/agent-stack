# syntax=docker/dockerfile:1.10@sha256:865e5dd094beca432e8c0a1d5e1c465db5f998dca4e439981029b3b81fb39ed5
ARG NODE_IMAGE=docker.io/library/node:22.23.4-trixie-slim@sha256:2117f713a417f3aff682ac8f5bc2ebbdf1beed51f36e67ab1e0fd3866b6d2d8f

FROM ${NODE_IMAGE} AS image
ARG PNPM_VERSION=11.15.1
SHELL ["/bin/bash", "-o", "pipefail", "-c"]
RUN apt-get update \
 && apt-get install -y --no-install-recommends python3 python3-yaml python3-cryptography \
 && rm -rf /var/lib/apt/lists/* \
 && npm install -g --no-fund --no-audit "pnpm@${PNPM_VERSION}" \
 && rm -rf /root/.npm /root/.cache \
 && userdel --remove node \
 && groupadd --gid 10001 validator \
 && useradd --uid 10001 --gid 10001 --no-create-home --home-dir /nonexistent --shell /usr/sbin/nologin validator \
 && install -d -m 0755 /work /opt/agent-stack/bin \
 && find / -xdev -perm /6000 -type f -exec chmod a-s {} +

COPY policies/omnigent/agent_stack_policies/ /opt/agent-stack/policies/agent_stack_policies/
COPY images/public-validator/bin/ /opt/agent-stack/bin/
RUN chown -R root:root /opt/agent-stack \
 && chmod -R u=rwX,go=rX /opt/agent-stack \
 && chmod 0755 /opt/agent-stack/bin/*

ENV PATH=/usr/local/bin:/usr/bin:/bin
WORKDIR /work
ENTRYPOINT ["/opt/agent-stack/bin/run-validation"]

FROM ${NODE_IMAGE} AS fixture
RUN apt-get update \
 && apt-get install -y --no-install-recommends git \
 && rm -rf /var/lib/apt/lists/*
RUN --mount=type=bind,source=images/public-validator/test/make-fixture.sh,target=/make-fixture.sh \
    bash /make-fixture.sh /fixture

FROM image AS test
COPY config/public-contributions.yaml /opt/agent-stack/public-contributions.yaml
COPY --from=fixture /fixture /fixture
COPY images/public-validator/test/ /src/test/
RUN bash /src/test/validation.sh

# The default target is the published image, without the test stage.
FROM image
