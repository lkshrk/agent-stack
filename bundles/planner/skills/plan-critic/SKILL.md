---
name: plan-critic
description: Third planner step. Skeptically check the draft against hard thresholds — the result validator plus a root-criterion coverage map — and send it back to decompose on any failure, at most 3 rounds, then BLOCKED.
---

# plan-critic — hard thresholds

Switch roles: you did not write this plan and you assume it is wrong until
the checks say otherwise. A threshold either passes or fails; there is no
"mostly".

## Thresholds

1. **Validator.** Build the exact final response (with `critic.rounds` set
   to this round and the result as the last fenced ```json block) and pass
   it to the `result_check` tool `validate_result` with `kind: planner` and
   the response as `text`. Never validate through the shell.

   Anything but `valid: true` is a failure. Each entry in `errors` is one
   failed threshold. The validator enforces: status, design, risk class and
   `force_gate_plan`, 1 to 8 children, keys unique, every child's title,
   description, files and Independent Test present, criteria IDs unique,
   of the form `<child-key>-<n>` and in EARS form, `blocked_by` keys known and acyclic, parallel children with
   disjoint files, `critic.rounds` 1 to 3, every child criterion in
   `critic.coverage`, and no placeholders anywhere.
2. **Coverage map.** `critic.coverage` has one entry per child criterion:
   `{"criterion": "<child-key>-<n>", "child": "<key>", "root": "<root
   criterion ID>", "test": "<that child's independent_test>"}`. Every root
   criterion appears as `root` at least once. A root criterion with no
   child, or a child criterion with no test, fails.
3. **Split rules.** Every rule in `plan-decompose` holds, in particular: a
   reviewer could reject each child while approving its neighbour, no
   child criterion is a copy of a root criterion, and every criterion id is
   `<its child key>-<n>`.
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
