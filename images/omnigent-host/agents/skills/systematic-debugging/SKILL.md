---
name: systematic-debugging
description: Use when investigating a bug, test or build failure, performance regression, flaky integration, or other unexpected behavior, before proposing fixes and especially after a failed fix.
---

# Systematic Debugging

Investigate the cause before changing behavior. A visible symptom is a starting point, not evidence that the code displaying it is responsible.

Adapted from [obra/superpowers](https://github.com/obra/superpowers/tree/2ce8aa15d05bd57213e6f746f953aafb7ded7a8b/skills/systematic-debugging), commit `2ce8aa15d05bd57213e6f746f953aafb7ded7a8b` (MIT). This adapter is self-contained for OMP and Codex; no upstream runtime or sibling skills are required.

## Execution boundaries

- Follow repository and host instructions. Use specialized file/search/edit tools and LSP symbol navigation where available, rather than shell substitutes.
- In OMP, use a read-only scout to map unknown code. Delegate only bounded, independent investigations in one task batch with clear ownership; use available equivalents in Codex. Keep integration and verification with the main agent.
- Treat user-reported errors and observations as facts. Do not rerun checks merely to confirm them. A targeted experiment must answer a new causal question, not reproduce evidence already supplied.
- Workers do not run builds, tests, linters, formatters, or smoke checks unless their assignment explicitly authorizes them. Propose the discriminating check and hand it to the main agent when execution is outside your assignment.
- Do not expose credentials, tokens, private payloads, or whole environments in diagnostics. Prefer presence flags, types, counts, redacted identifiers, and safe state summaries. Inspect and sanitize traces before sharing them.

## 1. Establish the failure boundary

Before proposing a fix:

1. Read the supplied error and relevant stack trace completely. Record expected versus observed behavior, failing operation, source locations, and relevant environment/version details.
2. Establish the triggering steps and frequency from existing evidence. If essential evidence is missing, gather it with the smallest permitted observation or temporary reproduction. An intermittent failure needs distinguishing conditions, not guesses.
3. Inspect relevant recent changes: code, dependencies, configuration, environment, and initialization order. A recent change is a lead, not proof.
4. Trace across component boundaries. For a chain such as request → service → queue → worker → database, identify the input, output, configuration propagation, and state at each relevant boundary. Start with existing logs/debugger evidence; add narrowly scoped temporary instrumentation only where needed.
5. Locate the first boundary where actual behavior diverges from the contract, then investigate that component. Trace the bad value or state backward to its producer; see [Root Cause Tracing](root-cause-tracing.md).

For example, when a configuration value reaches a launcher but not its child process, report only whether it is present at each boundary. Investigate export/launch behavior rather than printing its value or changing downstream consumers.

**Exit evidence:** what fails, where the earliest known divergence occurs, and what remains unknown. If evidence stops short of a cause, say so and continue targeted investigation rather than patching the symptom.

## 2. Compare working behavior

- Find a similar working path in the same repository before inventing a new convention.
- Read the relevant implementation and its contracts, not just a matching line. Understand lifecycle, dependencies, configuration, and assumptions before adapting it.
- List concrete differences between working and failing cases, including input shape, initialization order, environment, ownership, and timing. Do not dismiss small differences without evidence.
- For async failures, distinguish missing completion signals from genuinely exceeded operation deadlines; see [Condition-Based Waiting](condition-based-waiting.md).

**Exit evidence:** a specific difference that can explain the observed divergence.

## 3. Test one hypothesis

State: **“I think X causes Y because of evidence Z. If true, observing or changing A should produce B.”** Include what outcome would refute it.

- Choose the smallest permitted experiment that distinguishes this explanation from alternatives. Prefer inspection or a focused observation when it is sufficient.
- Change one causal variable at a time. Do not bundle speculative fixes or refactors.
- Record the actual result. If refuted, discard that hypothesis and remove only your own failed experimental changes before trying another; preserve unrelated user work.
- If the result is ambiguous, improve the observation rather than calling it confirmation. Separate established facts from inference.

A temporary reproduction can help isolate a cause; it does not require adding permanent tests. Investigation-only work ends with evidence and an explanation, not product changes or a new test suite.

## 4. Fix the cause within scope

Once evidence supports the causal explanation:

- Make the smallest complete change at the responsible source or contract owner. Preserve intended features and update affected callers when the contract changes.
- Do not suppress the symptom, silently accept invalid state, or add retries, validation layers, telemetry, or defensive fallbacks merely because debugging exposed an opportunity. Such behavior needs evidence and task scope.
- Add or update a regression test only when it usefully protects consumer-visible behavior and fits the assignment. Reuse the existing test conventions; do not mandate a permanent test for every investigation or trivial edit.
- Remove temporary probes, reproduction scripts, and other scaffolding you introduced. Update existing documentation when the permanent behavior change requires it.
- Hand the main agent the expected observable outcome and focused verification checks. The main agent runs authorized verification once after delegated edits land. Report only checks actually exercised; distinguish an implemented fix from a verified outcome.

## Stop and reassess

Return to evidence gathering when you are guessing, stacking speculative changes, or proposing a fix before tracing the relevant data or state.

After repeated failed fixes—three is a useful stop signal, not a theorem—stop further patching and summarize:

- hypotheses attempted and what each result ruled out;
- assumptions still unsupported and evidence still missing;
- whether failures share a boundary, lifecycle issue, or hidden dependency;
- the next observation that would discriminate among remaining causes.

Repeated failures may expose architectural coupling, but they do **not** prove the architecture is wrong. Re-examine the model and the experiments first. Discuss any necessary architectural or scope change with the user before undertaking it; do not turn debugging into an unsolicited redesign.

## External or unresolved causes

Timing, environment, and external dependencies are possible causes, not an excuse to stop tracing. State the established boundary, evidence gathered, unknowns, and exact missing prerequisite. Do not claim certainty or invent success when the dependency cannot be observed. Implement timeout, retry, or error-handling changes only when the demonstrated cause and requested behavior justify them.
