# Condition-Based Waiting

Adapted from [obra/superpowers](https://github.com/obra/superpowers/blob/2ce8aa15d05bd57213e6f746f953aafb7ded7a8b/skills/systematic-debugging/condition-based-waiting.md), commit `2ce8aa15d05bd57213e6f746f953aafb7ded7a8b` (MIT).

Use when application or test code guesses at asynchronous completion with a fixed sleep. Wait for the condition required by the next operation, not an estimate of machine speed. This is not a reason to introduce retries or polling into unrelated code.

## Choose the completion mechanism

Prefer, in order:

1. Await the operation's existing promise, completion handle, or runner primitive.
2. Subscribe to an existing event or state transition. Subscribe before triggering the action so a fast completion cannot be missed; clean up the listener on success, failure, cancellation, or timeout.
3. Use the project's existing condition-wait helper when no completion signal exists and state can be observed safely.

Examples of meaningful conditions:

| Next operation needs | Wait for |
|---|---|
| Service accepts requests | Its readiness signal, not merely process creation |
| UI shows a result | The relevant rendered state, not a fixed animation delay |
| Background computation result | Its completion handle or result availability |
| Fully written artifact | Producer completion or the documented publication signal, not just file existence |
| Several events | The required correlated events, not an unfiltered global count |

**OMP orchestration is different:** background task results arrive automatically. Do not poll jobs through status reads, shell loops, or sleeps. Continue independent work; use the host `wait` tool only when blocked. In Codex, use the available completion/wait mechanism according to its host rules.

## Bound the actual operation

A readiness condition and a deadline solve different problems:

- A blind sleep delays unconditionally and says nothing about completion.
- A deadline bounds how long the operation is allowed to remain incomplete. Derive it from the operation's contract or the existing test budget, not an unexplained universal number.
- Preserve cancellation and genuine failure signals. A failed operation should fail promptly, not be hidden until the readiness deadline expires.
- For code that measures elapsed time, use a monotonic clock where available. Keep one overall deadline rather than restarting the budget on each observation.
- A timeout around a promise does not automatically cancel the underlying operation. Use existing cancellation/cleanup support so work and listeners do not leak after the waiter exits.

Do not simply increase a timeout to hide a missing event, deadlock, or performance regression. Trace the cause first.

## If condition polling is necessary

Reuse the existing helper rather than adding a generic framework. Its contract should be explicit:

- Read fresh state on each check; do not repeatedly inspect a stale snapshot.
- Define success explicitly. A valid result can be `0`, `false`, or an empty string, so generic truthiness is not a reliable success test.
- Keep observations read-only and cheap. Do not retry the side-effecting operation as part of checking whether it finished.
- Bound the entire wait by the operation deadline, support available cancellation, and avoid overlapping asynchronous checks.
- Ensure an individual asynchronous observation is itself bounded; an outer deadline cannot stop a hung predicate unless the underlying operation supports it.
- Choose an interval appropriate to the producer and its cost; there is no universally correct 10 ms interval. Limit the final delay to the remaining budget.
- Propagate unexpected errors. Do not catch every exception and reinterpret it as “not ready.”
- On timeout, identify the missing condition and safe last-observed state. Do not include secrets or sensitive payloads.

## When elapsed time is the behavior

Debounce, throttle, expiry, and periodic output tests may intentionally advance time. Prefer the repository's deterministic clock or scheduler controls when they accurately model the behavior. First establish the triggering condition, then advance or wait for the documented interval and assert the relevant outcome.

For a real-time integration check, explain why the duration is meaningful, respect the actual operation deadline, and account for scheduler behavior. Sleeping for two nominal tick intervals does not by itself prove two events occurred; inspect the required events or state.

Any experiment or regression check remains subject to [the main skill's execution boundaries](SKILL.md). This guide requires no executable example or external helper file.
