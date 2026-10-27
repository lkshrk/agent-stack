---
name: docs-audit
description: Audit a repository's docs, or the docs of listed modules, against the code. Read-only; report stale passages and the gaps that need code knowledge in a validated documenter result.
---

# docs-audit — report stale docs and gaps

Read-only: edit nothing, commit nothing, push nothing. The result is the
whole output.

## 1. Scope

The repository from the input, or only the listed modules. Work on the
default branch as checked out.

## 2. Inventory

List every doc in scope: READMEs, `docs/`, ADRs under `adr/`,
`CHANGELOG*` and other Markdown files outside agent instruction files.

## 3. Accuracy

For every doc, check:

- Every path it references exists.
- Every documented command runs in the sandbox, as long as it changes
  nothing outside it. Skip deploys, pushes and anything that needs secrets.
- Examples, flags, settings, env variables and defaults match the code.

## 4. Gaps

Find behaviour, commands, settings and env variables in the code in scope
that no doc covers (`rg -n` for settings and env reads, entry points and
CLI definitions).

## 5. Result

- `stale`: one entry per wrong passage, `{"file": "<doc>", "reason":
  "<what is wrong and what the code says>"}`.
- `doc_gaps`: one entry per undocumented behaviour, `{"file": "<doc that
  should cover it>", "reason": "<what is missing and where the code is>"}`.
- `changed_files` is empty and `pr` is null.
- No findings, or stale passages only: `DONE` with an empty `doc_gaps`. Any
  doc gap: `DONE_WITH_CONCERNS`; the validator rejects `doc_gaps` under
  `DONE`.
- Open questions about scope or intent: `BLOCKED` with `blocked.class:
  decision` and one `blocked.question` batching all of them. Keep the
  `stale` and `doc_gaps` found so far in the result, and record each
  question in `gaps` under the prompt's no-assumptions rule.

```json
{
  "status": "DONE_WITH_CONCERNS",
  "mode": "audit",
  "changed_files": [],
  "stale": [
    {"file": "docs/setup.md", "reason": "Documents --port; the CLI now reads PORT from the environment."}
  ],
  "doc_gaps": [
    {"file": "docs/setup.md", "reason": "ORDERS_QUEUE is read in backend/app/settings.py and documented nowhere."}
  ],
  "pr": null
}
```

Call `result_check`'s `validate_result` with `kind` `documenter` and the
exact final text. Fix every error it reports and call it again until it
returns `valid`. Then send that text unchanged.
