---
name: plan-architect
description: First planner step. Read the repository, code graph and vault, then fix the design — files touched, interfaces in and out, a design excerpt and unchanged behaviour — with every decision resolved or emitted as BLOCKED.
---

# plan-architect — design before decomposition

Read before you decide. Start from the root issue's acceptance criteria, then
the vault pages and pitfalls for the area, then the code graph (callers,
definitions, impact) and only then targeted file reads.

Produce these four parts; they become the result's `design` string:

1. **Files touched.** Every path that changes or is created, each with one
   line on what changes. No globs, no "and related files".
2. **Interfaces in and out.** For each changed unit: what calls it and with
   what (in), what it calls or returns (out). Name the types, functions,
   endpoints, events or tables. Flag every public API change.
3. **Design excerpt.** The approach in a few short paragraphs: the chosen
   shape, the main data flow, and why this shape over the obvious
   alternative when they differ.
4. **Unchanged behaviour.** What must keep working exactly as before, stated
   so a test could check it.

## Decisions

Every decision is resolved here, before decomposition. For each one, either:

- resolve it from evidence (code, vault, issue text) and record the choice
  and its reason in the design excerpt, or
- stop and return `BLOCKED` with `blocked.class: decision`. Batch all open
  decisions into one `blocked.question`, each with the options and the one
  you would pick.

Never carry a decision forward as a child, a placeholder, `TBD` or
`[NEEDS CLARIFICATION: …]`, and never as an unstated assumption. Record
each open one as a gap under the planner prompt's no-assumptions rule;
under `gate:plan` every gap means `BLOCKED`.

## Risk

Record here what forces `risk.force_gate_plan: true`: a schema migration,
auth or security, infra or deploy, a public API change, cross-repo work. The
child count is checked again after `plan-decompose`.
