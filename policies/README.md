# policies

Policies for other workloads: PR-Agent fetches its files from this repository at runtime, and the Omnigent package is baked into the Omnigent images.

| Path | Consumer |
| --- | --- |
| `pr-agent/pr-agent.toml`, `pr-agent/review.md` | The PR-Agent init container in h-cloud (`kubernetes/apps/ai/pr-agent`) fetches both from `main` at pod start and merges `review.md` into `extra_instructions`. Repository-local overrides remain possible |
| `omnigent/` | Omnigent policy package baked into the server and runner images |

## Omnigent shell gates

`no_merge`, `no_nested_agents`, `path_guard`, `docs_vcs`, `read_only_shell` and `git_verbs` share one parser and command resolver (`agent_stack_policies/_shell.py`). It walks `sh -c`, `eval`, `$(…)` and backtick bodies and here-strings into a shell, and strips leading keywords, `VAR=` assignments and the wrappers `env` (including `-S`), `command`, `builtin`, `exec`, `nohup`, `setsid`, `ionice`, `nice`, `timeout`, `flock`, `stdbuf`, `time`, `xargs`, `find -exec`, `sudo`, `doas`, `su -c`, `script -c`, `watch`, `parallel`, `npx`/`bunx`/`pnpm dlx`/`yarn dlx`/`npm exec`, `uv run`, `uv tool run`/`uvx` and `mise exec`/`mise x` (`--` or `-c`). `no_nested_agents` also denies a script piped into a shell (`| sh`, `bash -s`, `bash < file`) and commands piped into `parallel`, and exempts only each CLI's own help and version flags (`codex -V`, `claude`/`omp`/`opencode -v`).

`read_only_shell` is an allowlist for report-only agents: read commands (`rg`, `cat`, `head`, `sed -n` line ranges, git read verbs and similar, `gh` reads with `extra_commands: [gh]`), no `VAR=` or `env VAR=` assignments, no token containing `>` other than `>`/`>>` into `/dev/null`, no `find -delete`, `sort -o`/`--compress-program` or `rg --pre`, and `exact_commands` allowed verbatim. The planner, its critic and Polly's Codex worker run under it. `git_verbs` limits git to read verbs plus `extra_verbs`; Polly gets `fetch` and `worktree`. Process substitution bodies (`<(…)`, `>(…)`) are walked as statements by every gate. `read_only_shell` takes bare command names only, allows no wrapper except `command` and `find -exec` (no shell through `find`), and allows only the stderr redirects `2>/dev/null` and `2>&1`. `no_merge` and `git_verbs` deny `xargs`/`parallel` into a git push or gh merge, or into git, because stdin can supply arguments.

The gates read tokens, not a running shell. A command named through a variable (`$G push`), an alias, a copied binary or a launcher outside the list above (`ssh`, `chroot`, `docker exec`) is not resolved, and neither are writes such as `dd of=` or a relative path after `cd`. Branch protection and the GitHub token scopes are the backstop.
