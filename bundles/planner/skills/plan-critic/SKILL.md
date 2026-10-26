---
name: plan-critic
description: Third planner step. Skeptically check the draft against hard thresholds — the result validator plus a root-criterion coverage map — and send it back to decompose on any failure, at most 3 rounds, then BLOCKED.
---

# plan-critic — hard thresholds

Switch roles: you did not write this plan and you assume it is wrong until
the checks say otherwise. A threshold either passes or fails; there is no
"mostly".

## Thresholds

1. **Validator.** Build the exact final result block (with `critic.rounds`
   set to this round) as one line of JSON and pipe it through the validator:

   ````bash
   printf '%s\n' '```json' '<draft result as one line of JSON>' '```' | python3 -m agent_stack_policies.result planner
   ````

   Write every `'` inside the JSON as `'\''`. Do not use a here-document or
   here-string: the GitHub policy denies them.

   Any output, or a non-zero exit, is a failure. Each printed line is one
   failed threshold. The validator enforces: status, design, risk class and
   `force_gate_plan`, 1 to 8 children, keys unique, every child's title,
   description, files and Independent Test present, criteria IDs unique and
   in EARS form, `blocked_by` keys known and acyclic, parallel children with
   disjoint files, `critic.rounds` 1 to 3, every child criterion in
   `critic.coverage`, and no placeholders anywhere.
2. **Coverage map.** `critic.coverage` has one entry per child criterion:
   `{"criterion": "<child-key>-<n>", "child": "<key>", "root": "<root
   criterion ID>", "test": "<that child's independent_test>"}`. Every root
   criterion appears as `root` at least once. A root criterion with no
   child, or a child criterion with no test, fails.
3. **Split rules.** Every rule in `plan-decompose` holds, in particular: a
   reviewer could reject each child while approving its neighbour, and no
   child criterion is a copy of a root criterion.
4. **Gate.** `risk.force_gate_plan` is true when any forcing condition from
   the prompt applies.

## Rounds

- All thresholds pass: go to `plan-contract`.
- Any fail: list the failed thresholds, go back to `plan-decompose`, fix,
  and run this skill again. That is the next round.
- Still failing after round 3: return `BLOCKED` with `blocked.class:
  decision` and a `blocked.question` naming each failed threshold and the
  choice the owner has to make.

Never weaken a criterion, drop a root criterion or merge children just to
pass a threshold without saying so in the design.
