---
name: plan-contract
description: Fourth planner step. Give every child the sprint contract Polly's cross-review grades the PR against — criteria IDs, the Independent Test command, the file set and the docs to update.
---

# plan-contract — what the reviewer grades

Polly's cross-review judges a child's PR only against its contract. Anything
missing here cannot be enforced later, so be exact.

Add a `contract` object to every child:

```json
"contract": {
  "criteria": ["api-1", "api-2"],
  "independent_test": "pnpm --filter api test -- orders",
  "files": ["backend/src/orders/service.ts", "backend/src/orders/service.test.ts"],
  "docs": ["docs/api/orders.md"]
}
```

- `criteria`: every criterion ID of this child, and only this child's.
- `independent_test`: the same command as the child's `independent_test`,
  runnable from the repository root.
- `files`: the same set as the child's `files`. A PR touching other files
  fails review unless the worker explains why.
- `docs`: every doc, README or vault page this change makes stale; `[]` when
  none.

After adding contracts, run `validate_result` once more on the exact final
response. Then send the final response with that block last.
