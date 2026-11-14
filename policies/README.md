# policies

The shared PR-Agent policy (`pr-agent.toml`, `review.md`) lives in [`deploy/pr-agent/`](../deploy/pr-agent/). h-cloud applies it from `main` as the `pr-agent-policy` ConfigMap, and Reloader restarts PR-Agent when it changes. Repository-local `.pr_agent.toml` overrides remain possible.
