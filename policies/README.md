# policies

The shared PR-Agent policy (`pr-agent.toml`, `review.md`) lives in h-cloud at
`kubernetes/apps/ai/pr-agent/app/resources/`. It ships as the `pr-agent-policy` ConfigMap,
and Reloader restarts PR-Agent when it changes. Repository-local `.pr_agent.toml` overrides
remain possible.
