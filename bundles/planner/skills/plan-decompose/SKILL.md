---
name: plan-decompose
description: Second planner step. Split the design into 1 to 8 child issues under the split rules, each with its own file set, EARS criteria, Independent Test and blocked_by dependencies, grouped into waves.
---

# plan-decompose — children under the split rules

## Split rules

These are rules, not guidance. A plan that breaks one fails the critic.

1. One change per child with a declared file set (`files`).
2. Decisions resolved before decomposition (or made their own issue).
3. Split only where a reviewer could reject one child while approving its
   neighbour.
4. Each child has an Independent Test (`independent_test`): one command that
   passes when that child is done, without the other children.
5. Dependencies as Linear `blocks` relations (`blocked_by` keys).
6. Parallel only with disjoint file sets, otherwise serialised (`blocked_by`).
7. Independent children run in waves: wave 1 has no `blocked_by`, wave n
   depends only on earlier waves.

## Children

- 1 to 8 children. More than 8 means the root issue is too big: return
  `BLOCKED` (`decision`) asking to split the root into milestones, with the
  split you propose.
- `key`: short, stable, lowercase (`api`, `migration`, `ui-form`).
- `title`, `description`: what this child changes and why, standalone; a
  worker reads only its own child.
- `files`: exact paths from `plan-architect`, no globs.
- `blocked_by`: list of keys, `[]` when none. No cycles.

## Acceptance criteria

Each child gets its OWN criteria, never the parent's. Rewrite the root
criteria into what this child alone delivers.

- ID: `<child-key>-<n>`, numbered from 1 per child (`api-1`, `api-2`).
- Text in EARS form: `WHEN <condition> THE SYSTEM SHALL <behaviour>`.
- Every root criterion lands in at least one child criterion. Keep the
  mapping; the critic checks it.
- No placeholders: no `TBD`, no `[NEEDS CLARIFICATION`, no "handle edge
  cases".

Write the waves into the design as a short list (`wave 1: a, c`; `wave 2: b`).
