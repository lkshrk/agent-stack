# syntax=docker/dockerfile:1.10
ARG HERMES_IMAGE=docker.io/nousresearch/hermes-agent:v0.22.5@sha256:9c5561579f3ffdf333401797094ac3a743738d6c25d40f4331c7bf77b694238d

FROM ${HERMES_IMAGE}

ARG OBSIDIAN_WIKI_VERSION=2026.10.3
ARG KUBECTL_VERSION=v1.37.1

USER root
RUN apt-get update \
 && apt-get install -y --no-install-recommends git jq curl ca-certificates \
 && rm -rf /var/lib/apt/lists/*

RUN curl -fsSL "https://dl.k8s.io/release/${KUBECTL_VERSION}/bin/linux/amd64/kubectl" -o /usr/local/bin/kubectl \
 && curl -fsSL "https://dl.k8s.io/release/${KUBECTL_VERSION}/bin/linux/amd64/kubectl.sha256" -o /tmp/kubectl.sha256 \
 && echo "$(cat /tmp/kubectl.sha256)  /usr/local/bin/kubectl" | sha256sum -c - \
 && chmod 0755 /usr/local/bin/kubectl && rm /tmp/kubectl.sha256

RUN "${HERMES_PYTHON:-python3}" -m venv /opt/obsidian-wiki \
 && /opt/obsidian-wiki/bin/pip install --no-cache-dir "obsidian-wiki==${OBSIDIAN_WIKI_VERSION}" \
 && ln -s /opt/obsidian-wiki/bin/obsidian-wiki /usr/local/bin/obsidian-wiki

COPY bin/ /opt/agent/bin/
COPY gitconfig /etc/gitconfig.d/agent
RUN chmod 0755 /opt/agent/bin/* \
 && git config --system include.path /etc/gitconfig.d/agent

ENV PATH="/opt/agent/bin:${PATH}"
