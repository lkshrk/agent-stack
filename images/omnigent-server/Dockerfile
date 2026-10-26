ARG OMNIGENT_SERVER_IMAGE=ghcr.io/omnigent-ai/omnigent-server-kubernetes:v0.19.1@sha256:5746692f3c3a95502f76f83260c3e5ff36348ad8c3cdce1fab85784065e44dad

FROM ${OMNIGENT_SERVER_IMAGE}

COPY bundles/ /agents/
COPY config/omnigent/config.yaml /etc/omnigent/config.yaml
COPY policies/omnigent/agent_stack_policies/ /opt/agent-stack/policies/agent_stack_policies/
RUN echo /opt/agent-stack/policies > "$(/opt/venv/bin/python3 -c 'import sysconfig; print(sysconfig.get_path("purelib"))')/agent_stack_policies.pth" \
 && chmod -R a+rX /opt/agent-stack

# Release builds pin the runner of the same release, which carries the policy package these bundles need.
ARG AGENT_STACK_VERSION=""
RUN if [ -n "$AGENT_STACK_VERSION" ]; then \
      printf '%s\n' "$AGENT_STACK_VERSION" | grep -Eqx '[0-9]+\.[0-9]+\.[0-9]+([-+][0-9A-Za-z.-]+)?' \
      && sed -i -E "s#^(    image: ghcr\.io/lkshrk/agent-stack/omnigent-host:).*#\1${AGENT_STACK_VERSION}#" /etc/omnigent/config.yaml \
      && grep -Fqx "    image: ghcr.io/lkshrk/agent-stack/omnigent-host:${AGENT_STACK_VERSION}" /etc/omnigent/config.yaml; \
    fi

# The database URI is a secret, so the deployment appends --database-uri "$(DATABASE_URL)".
CMD ["omnigent", "server", "--host", "0.0.0.0", "--port", "8000", "--no-open", "--config", "/etc/omnigent/config.yaml", "--agent", "/agents/polly"]
