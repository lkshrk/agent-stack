ARG OMNIGENT_SERVER_IMAGE=ghcr.io/omnigent-ai/omnigent-server-kubernetes:v0.21.0.dev20261031@sha256:55a9b9d4cc494354f18d6729011e51fcd721189bcdf53db05aeb84a613b14ceb

FROM ${OMNIGENT_SERVER_IMAGE} AS image

COPY bundles/ /agents/
# Extra built-ins seed last, so they win over any packaged agent of the same name.
ENV OMNIGENT_BUILTIN_AGENT_DIRS=/agents/polly:/agents/planner:/agents/security-review:/agents/documenter \
    OMNIGENT_SEEDED_AGENTS=claude-native-ui,codex-native-ui
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
CMD ["omnigent", "server", "--host", "0.0.0.0", "--port", "8000", "--no-open", "--config", "/etc/omnigent/config.yaml"]

FROM image AS test
ARG AGENT_STACK_VERSION=""
COPY policies/omnigent/ /src/policies/omnigent/
COPY bundles/ /src/bundles/
COPY config/omnigent/ /src/config/omnigent/
COPY images/omnigent-host/codex/ /src/images/omnigent-host/codex/
WORKDIR /src
RUN AGENT_STACK_IN_IMAGE=1 /opt/venv/bin/python3 -m unittest discover -s policies/omnigent/tests -t policies/omnigent -v
COPY images/omnigent-server/test/ /src/images/omnigent-server/test/
RUN /opt/venv/bin/python3 images/omnigent-server/test/agent_label.py
RUN pin='^    image: ghcr\.io/lkshrk/agent-stack/omnigent-host:' \
 && got=$(grep -E "$pin" /etc/omnigent/config.yaml) \
 && if [ -n "$AGENT_STACK_VERSION" ]; then want="    image: ghcr.io/lkshrk/agent-stack/omnigent-host:${AGENT_STACK_VERSION}"; \
    else want=$(grep -E "$pin" config/omnigent/config.yaml); fi \
 && [ "$got" = "$want" ] || { echo "runner pin is '$got', want '$want'"; exit 1; }

# The default target is the published image, without the test stage.
FROM image
