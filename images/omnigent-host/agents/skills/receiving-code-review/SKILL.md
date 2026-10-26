---
name: receiving-code-review
description: Use when receiving code review feedback, before implementing suggestions or replying, especially when findings are unclear, technically questionable, or conflict with prior decisions. Classify findings, check repository evidence, and respond with fixes or substantive pushback.
---

# Receiving Code Review

Evaluate feedback against this codebase, not the reviewer's confidence. Evidence before conclusions; technical substance instead of performative agreement.

## Triage and understand

1. Read the complete review and relevant discussion before changing code.
2. Classify each finding: blocking correctness/security issue, nonblocking defect, question, or optional suggestion. Record dependencies and distinguish reported observations from proposed explanations or remedies.
3. Understand the requested outcome in your own words. Consult repository instructions, affected code, callers, contracts, tests, and prior user decisions before asking questions.
4. Resolve ambiguity from available context. Ask only about materially blocking choices that evidence cannot settle, such as changing supported platforms or reversing an architectural decision. Hold the dependent work, not the entire review; proceed with independent, understood items.

User-reported failures and observations are ground truth. Do not rerun a command merely to confirm them. Investigate the cause and assess the proposed fix; a reported failure does not establish that the reviewer's diagnosis is correct.

## Check the evidence

For each proposed change, establish:

- Does the finding apply to the current code and supported platforms or versions?
- What consumer behavior or contract is affected? Which callers depend on it?
- Why does the existing implementation exist? Would the suggestion remove wanted functionality or necessary compatibility?
- Does the proposal conflict with a prior user decision? Do not silently reverse that decision; explain the conflict and obtain a decision before changing that part.
- Is the suggested feature actually required? Do not add unused functionality, abstractions, validation, retries, or telemetry just to make a solution look more complete. Apparent lack of callers is not permission to remove a wanted feature or public contract.

Use available specialized search, file, symbol/LSP, and editing tools. In OMP, delegate unknown-code discovery to a read-only `scout`; in Codex, use available equivalent discovery tools. Prefer evidence already available over repeating commands. If evidence remains unavailable, state exactly what is missing and which conclusion is uncertain; do not present an assumption as verified.

## Respond or push back

Accept sound findings with a technical acknowledgment or the fix itself. Do not promise implementation before checking the claim. Ordinary courtesy is fine; praise or gratitude must not substitute for evaluation.

Push back when a suggestion is technically incorrect, breaks wanted behavior, exceeds the requested scope, ignores compatibility, or conflicts with the user's decisions. Give a concrete reason, cite relevant code, contracts, or existing test evidence, and identify the narrower fix when one exists. Escalate unresolved product or architectural choices to the user, not unrelated work.

Examples:

- “This branch supports the older API required by our minimum platform version. Removing it would break that target. The incorrect argument can be fixed without dropping support.”
- “The failure is established by your report. The proposed retry would hide the incorrect state transition; I will trace and fix that transition instead.”
- “Items 1 and 2 are independent and understood. Item 3 would reverse the agreed retention policy; should that policy change?”

If your pushback proves wrong, correct it plainly: “The caller does pass null here; my earlier reading missed that path.” Then fix the issue without a defensive explanation.

## Implement and verify

1. Prioritize blocking correctness and security findings, then other defects, then approved nonblocking suggestions. Respect dependencies rather than sorting only by ease.
2. Make the smallest complete correction that preserves wanted behavior. Update affected callers and relevant existing tests or documentation; do not broaden the feature set.
3. Delegate only genuinely independent slices, in a bounded batch with explicit file ownership and shared contracts. One integration owner coordinates changes. Children must not run builds, tests, linters, formatters, or smoke checks mid-flight; they report changes and suggested checks.
4. Investigation alone does not warrant permanent tests. Temporary reproduction is appropriate when it answers a causal question, not merely to reconfirm a reported failure. For a bug fix, add or adjust regression tests only when they exercise useful consumer behavior; remove temporary scaffolding.
5. When verification is authorized, the integration owner runs focused checks of affected behavior once after all fixes land, including relevant regressions. Do not test every child's partial state or duplicate checks. A new failure calls for diagnosis and correction before the necessary subsequent check, not blind retries. If execution is outside the assignment, hand off the exact recommended checks and state that they were not run.

## Close the review loop

Report what changed, where, and what verification actually ran. Distinguish fixed findings, evidence-backed pushback, and materially blocked decisions. Never claim a passing test or resolved behavior without evidence.

When responding to an inline GitHub review comment, reply in its original review thread, not a top-level PR comment. Use an available GitHub integration or `gh api repos/{owner}/{repo}/pulls/{pr}/comments/{comment_id}/replies` with the reply body. Do not resolve a thread merely to conceal an unresolved finding.

## Attribution

Adapted for OMP and Codex from [obra/superpowers receiving-code-review](https://github.com/obra/superpowers/blob/2ce8aa15d05bd57213e6f746f953aafb7ded7a8b/skills/receiving-code-review/SKILL.md), commit `2ce8aa15d05bd57213e6f746f953aafb7ded7a8b`, under the MIT license. See [LICENSE](LICENSE).
