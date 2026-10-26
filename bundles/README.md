# bundles

Omnigent agent bundles. `images/omnigent-server/Dockerfile` copies this directory to `/agents/` in the server image, and the server starts with `--agent /agents/polly`.

| Bundle | Contents |
| --- | --- |
| `polly/` | Orchestrator adapted from Omnigent's example. `config.yaml` holds its instructions and tools, `agents/claude_code` and `agents/codex` the workers, `skills/` the skills `cross-review`, `fanout` and `investigate` |
| `planner/` | Read-only planner. `config.yaml` holds its instructions, the `code_graph` and `result_check` (`agent_stack_policies.mcp`) MCP servers and its policies (no writes, no pushes, GitHub read-only, validated final result), `skills/` the skills `plan-architect`, `plan-decompose`, `plan-critic` and `plan-contract` |
| `security-review/` | Read-only security reviewer on `codex-native` with `gpt-sol`. `config.yaml` holds its instructions, the `result_check` (`agent_stack_policies.mcp`) MCP server and its policies (the planner's read-only set plus Codex's `apply_patch` and `spawn_agent` denied, validated security result), `skills/` the skill `security-audit`. Sessions need `terminal_launch_args: ["--profile", "security-review"]`, which selects the runner image's read-only Codex profile `images/omnigent-host/codex/security-review.config.toml` |
