# Runner rules

You run in a disposable Kubernetes pod. Everything outside a pushed branch or the knowledge vault is gone when the session ends.

## Knowledge vault

`lkshrk/knowledge` is the shared knowledge vault for every repository. Its `AGENTS.md` is the schema; follow it when writing.

Before working in a repository:

1. `git clone --depth 50 https://github.com/lkshrk/knowledge.git ~/knowledge` unless it already exists, then `~/knowledge/scripts/sync.sh`.
2. Read `~/knowledge/projects/<repo>/<repo>.md` and the pages it links whose `paths` overlap the files you will touch. Check `pitfalls/` before changing code in an area that has one.
3. Search `~/knowledge/index.md` for cross-repository knowledge.

After learning something durable (a root cause, a trap, a decision with its reason, a working procedure):

1. Write `~/knowledge/raw/sessions/<YYYY-MM-DD>-<repo>-<slug>.md` with what happened, error text, fix and commit or file references.
2. Edit or add the matching page under `projects/<repo>/<category>/` per the vault's `AGENTS.md`, linked from the project overview.
3. `cd ~/knowledge && obsidian-wiki memory sync INGEST source=<raw path> project=<repo> --vault "$PWD" && git add -A && git commit -m "ingest: <repo> <slug>" && scripts/sync.sh`. This pushes straight to `main`; the vault's hooks run the lints and the secret scan. If `sync.sh` exits 2, resolve the conflict; never force-push.

Not durable: typos, session status, anything already in the repository's own docs. Never put secrets, tokens, private hostnames or personal data in the vault.

## Code changes

- Work on a branch, never on `main`. Deliver through a pull request; never merge, never force-push.
- Push with an explicit destination: `git push origin HEAD:<branch>`. A bare `git push` or `git push origin HEAD` is denied.
- Commit messages and PR texts carry no AI attribution: no `Co-authored-by` trailers, no session links, no sentence saying it was generated.

## Code comments

Default: no comment. When one is truly warranted, one short line. Never restate the code, never narrate steps, never reference the task, PR or ticket, never leave TODOs. Keep only non-obvious facts a future reader needs: why an unusual approach was chosen, an invariant a later edit could break, why an error is swallowed, a workaround for a specific bug.

## Tools

- You already start in your worktree. `EnterWorktree` and `ExitWorktree` are denied; use `cd` and `git worktree` instead.
- `rtk` compresses command output; prefer `rtk <cmd>` for noisy commands.
- `codebase-memory-mcp` holds the code graph; use it for definitions, callers and impact before grepping whole files.
- Toolchains and language servers come from mise (`mise ls`).
