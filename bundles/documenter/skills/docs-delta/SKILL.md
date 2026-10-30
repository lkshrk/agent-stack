---
name: docs-delta
description: Bring the docs one pull request affects up to date. Inventory the affected docs, check them against the changed code, fix stale passages and gaps, commit `docs: <summary>` to the PR head branch, push without force and return a validated documenter result.
---

# docs-delta — update the docs a PR affects

Scope is the pull request's changed files. Edit documentation only; never
touch the code, even when it looks wrong. Report that instead.

## 1. Scope

- PR URL `https://github.com/<owner>/<repo>/pull/<n>`: run
  `gh pr view <n> --repo <owner>/<repo> --json files,headRefName,headRefOid`
  and `gh pr diff <n> --repo <owner>/<repo>`. Keep `headRefOid` as the head
  SHA.
- `git rev-parse --abbrev-ref HEAD` must name the head branch from the
  input. If it doesn't, return `BLOCKED` with `blocked.class: environment`.
- `git status --porcelain` must print nothing. A dirty tree before you
  start returns `BLOCKED` with `blocked.class: environment`, listing the
  files.

## 2. Inventory

List the docs the change can affect: the README next to each changed file
and the root README, `docs/` pages that mention a changed path, command,
setting or symbol (`rg -n <name> docs README.md`), ADRs under `adr/` for
the area, and `CHANGELOG*`.

## 3. Accuracy

For every affected passage, check:

- Every path it references exists on the head branch.
- Every documented command runs in the sandbox, as long as it changes
  nothing outside it. Skip deploys, pushes and anything that needs secrets,
  and note the skip.
- Examples, flags, settings, env variables and defaults match the code.

## 4. Gaps and stale passages

- Gap: new behaviour, a new command, setting or env variable in the diff
  with no docs.
- Stale: a passage the diff made wrong, or one that describes removed
  behaviour.

A gap you can only document by guessing at intent becomes an open
question. Collect them; do not ask.

## 5. Edit and verify

Fix each gap and stale passage with `sys_os_edit` or `sys_os_write`, in the
style of the surrounding doc. Never edit `AGENTS.md`, `CLAUDE.md` or any
path the policy denies. Then re-check the links you touched and re-run the
commands you changed.

## 6. Commit and push

Only when you changed something:

1. `git add -- <each doc file you changed>`, never `git add -A` or `.`.
2. `git commit -m "docs: <summary>"`. No AI attribution, no trailers.
3. `git diff --name-only <head SHA>..HEAD` must list only docs paths. A
   commit hook or a documented command can stage code; if any other file
   appears, do not push. Return `BLOCKED` with `blocked.class: decision`,
   listing the files.
4. `git push origin HEAD:<head branch>`. Never `--force`, never the base
   branch. A rejected push becomes `BLOCKED` with `blocked.class:
   environment`.

## 7. Result

- Changes pushed: `DONE`, `changed_files` lists every file you committed,
  `pr` is the PR URL.
- Nothing to change: `DONE` with an empty `changed_files`.
- Stale passages you could not fix: list them in `stale` and return
  `DONE_WITH_CONCERNS`.
- Open questions: `BLOCKED` with `blocked.class: decision` and one
  `blocked.question` batching all of them, each with its options. Push what
  you could already fix first and list it in `changed_files`.

```json
{
  "status": "DONE",
  "mode": "delta",
  "changed_files": ["docs/setup.md", "README.md"],
  "stale": [],
  "pr": "https://github.com/<owner>/<repo>/pull/<n>"
}
```

Call `result_check`'s `validate_result` with `kind` `documenter` and the
exact final text. Fix every error it reports and call it again until it
returns `valid`. Then send that text unchanged.
