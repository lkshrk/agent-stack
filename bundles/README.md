# bundles

Omnigent agent bundles. `images/omnigent-server/Dockerfile` copies this directory to `/agents/` in the server image, and the server starts with `--agent /agents/polly`.

| Bundle | Contents |
| --- | --- |
| `polly/` | Orchestrator adapted from Omnigent's example. `config.yaml` holds its instructions and tools, `agents/claude_code` and `agents/codex` the workers, `skills/` the skills `cross-review`, `fanout` and `investigate` |
| `planner/` | Read-only planner. `config.yaml` holds its instructions, the `code_graph` and `result_check` (`agent_stack_policies.mcp`) MCP servers and its policies (no writes, no pushes, GitHub read-only, validated final result), `skills/` the skills `plan-architect`, `plan-decompose`, `plan-critic` and `plan-contract` |
