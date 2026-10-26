# plugins

Plugins served by the `agent-stack` marketplace (`.claude-plugin/marketplace.json` for Claude Code, `.agents/plugins/marketplace.json` for Codex).

| Plugin | Contents | Installed by |
| --- | --- | --- |
| `runner/` | Skills `upgrade-deps`, `project-tool-allowlist`, `ai-context-generator` | Baked into the `omnigent-host` image |
| `linear/` | Linear workflow skills `linear-intake`, `-refine`, `-implement`, `-reconcile`, `-status`, with validators, templates and tests (`bun test`) | apm: `git: lkshrk/agent-stack`, `path: plugins/linear` |
| `personal/` | Machine-maintenance, documentation and World of Warcraft skills | apm: `git: lkshrk/agent-stack`, `path: plugins/personal` |

Any machine can use all of them with `claude plugin marketplace add lkshrk/agent-stack`.
