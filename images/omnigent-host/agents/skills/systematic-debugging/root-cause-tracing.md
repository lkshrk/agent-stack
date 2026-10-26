# Root Cause Tracing

Adapted from [obra/superpowers](https://github.com/obra/superpowers/blob/2ce8aa15d05bd57213e6f746f953aafb7ded7a8b/skills/systematic-debugging/root-cause-tracing.md), commit `2ce8aa15d05bd57213e6f746f953aafb7ded7a8b` (MIT).

Use when an error appears far from the code that created the invalid value or state. Trace backward from the failing operation until you find where the required contract first stopped holding. The last stack frame is not necessarily the cause.

## Trace backward

1. **Name the symptom.** Identify the operation, expected behavior, observed result, and relevant input or state from existing evidence.
2. **Find the immediate cause.** Locate the statement performing the operation. Use symbol definitions, references, and call hierarchy where available; use a scout for unknown repository structure in OMP.
3. **Identify the caller and value source.** What arguments reached this statement? Who produced them? Which initialization, configuration, or state transition was required first?
4. **Follow the chain.** Repeat at each caller or producer. Across async boundaries, connect scheduling and execution using safe correlation identifiers, not just the current stack.
5. **Find the first violated contract.** Compare with a working path. Distinguish the place that exposes invalid state from the place responsible for creating or ordering it.
6. **Test the explanation.** State one causal hypothesis and a discriminating observation. Apply the execution/verification boundaries in [the main skill](SKILL.md).

Keep a compact evidence chain:

| Boundary | Expected | Observed | Evidence |
|---|---|---|---|
| Fixture setup → caller | Directory initialized before use | Caller captures uninitialized value | Relevant initialization and call sites |
| Caller → operation | Intended directory passed through | Captured value propagated | Arguments or safe diagnostic summary |
| Operation → filesystem | Write in isolated workspace | Write in source tree | Existing failure report or permitted observation |

These entries illustrate what to collect; they are not evidence about the current repository.

## Example: workspace operation in the wrong directory

Suppose a fixture initially exposes an empty directory value and initializes it during per-test setup. A top-level caller captures that value before setup. Later, a workspace operation uses it, and the **observed runtime behavior** resolves the target to the process working directory.

Trace:

`unexpected filesystem write ← wrong target at operation ← captured empty value ← read before fixture setup`

Confirm the actual runtime semantics rather than assuming every API treats an empty path the same way. The targeted correction is to obtain the initialized directory at the appropriate lifecycle point, not to conceal the bad path with a fallback. Additional validation is not automatically required at every layer.

## When static tracing is insufficient

Prefer existing logs or debugger inspection. If temporary instrumentation is necessary:

- Place it immediately before the relevant operation or at the unresolved boundary.
- Capture a safe state summary and the originating call site; async stacks may need correlation with the producer.
- Use a diagnostic channel that the relevant runner actually captures. Do not assume a particular logger or console method is always visible.
- Never dump entire environments, credentials, request bodies, or raw objects. Paths and stack traces can also contain private data; sanitize before sharing.
- Collect only enough evidence to distinguish the hypothesis, then remove your probes. Do not rerun a supplied failure merely to reconfirm it.

## State pollution and order-dependent failures

First inspect fixture setup/teardown, shared state, working-directory changes, and the ordering already reported. If authorized execution is needed to locate the producer, use the repository runner's existing selection or ordering controls in an isolated disposable workspace.

For large candidate sets, narrowing subsets can help, but preserve the failing order: a test that passes alone may still pollute the next test. Record the minimal producer/consumer sequence and the state crossing their boundary. Never delete user files or reset their working tree to manufacture a clean baseline. No external bisection script is required by this guide.

If the chain stops at an unobservable boundary, report that boundary and the missing evidence. Lack of visibility is not proof that a downstream workaround is correct.
