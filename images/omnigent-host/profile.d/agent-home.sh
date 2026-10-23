# shellcheck shell=sh
export PATH="/opt/mise/shims:/opt/agent/bin:${PATH}"
export GH_TOKEN_FILE="${GH_TOKEN_FILE:-/var/run/agent/github/code}"
export KNOWLEDGE_TOKEN_FILE="${KNOWLEDGE_TOKEN_FILE:-/var/run/agent/github/knowledge}"

if [ -n "${LITELLM_BASE_URL:-}" ] && [ -n "${LITELLM_API_KEY:-}" ]; then
  export ANTHROPIC_BASE_URL="$LITELLM_BASE_URL"
  export ANTHROPIC_AUTH_TOKEN="$LITELLM_API_KEY"
  export OPENAI_BASE_URL="${LITELLM_BASE_URL%/}/v1"
fi

# HOME is a fresh emptyDir per pod; seed it from the image once.
if [ -n "${HOME:-}" ] && [ -w "$HOME" ] && [ ! -e "$HOME/.agent-seeded" ]; then
  /opt/agent/bin/seed-agent-home && : >"$HOME/.agent-seeded"
fi
