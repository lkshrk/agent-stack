# policies

Policies for other workloads: PR-Agent fetches its files from this repository at runtime, and the Omnigent package is baked into the Omnigent images.

| Path | Consumer |
| --- | --- |
| `pr-agent/pr-agent.toml`, `pr-agent/review.md` | The PR-Agent init container in h-cloud (`kubernetes/apps/ai/pr-agent`) fetches both from `main` at pod start and merges `review.md` into `extra_instructions`. Repository-local overrides remain possible |
| `omnigent/` | Omnigent policy package baked into the server and runner images |

## Omnigent shell gates

`no_merge`, `no_nested_agents`, `path_guard` and `docs_vcs` share one parser and command resolver (`agent_stack_policies/_shell.py`). It walks `sh -c`, `eval`, `$(…)` and backtick bodies and here-strings into a shell, and strips leading keywords, `VAR=` assignments and the wrappers `env` (including `-S`), `command`, `builtin`, `exec`, `nohup`, `setsid`, `ionice`, `nice`, `timeout`, `flock`, `stdbuf`, `time`, `xargs`, `find -exec`, `sudo`, `doas`, `su -c`, `script -c`, `watch`, `parallel` and `npx`/`bunx`/`pnpm dlx`/`yarn dlx`/`npm exec`. `no_nested_agents` also denies a script piped into a shell (`| sh`, `bash -s`, `bash < file`) and commands piped into `parallel`.

The gates read tokens, not a running shell. A command named through a variable (`$G push`), an alias, a copied binary or a launcher outside the list above (`ssh`, `chroot`, `docker exec`) is not resolved, and neither are writes such as `dd of=` or a relative path after `cd`. Branch protection and the GitHub token scopes are the backstop.
