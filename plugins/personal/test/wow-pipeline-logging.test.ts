import { expect, test } from "bun:test";
import { spawnSync } from "node:child_process";
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";

const workflow = (name: string) => Bun.YAML.parse(readFileSync(new URL(`../../../.github/workflows/${name}.yml`, import.meta.url), "utf8")) as any;
const wow = workflow("wow-season-updates");
const summaryStep = (job: string) => wow.jobs[job].steps.at(-1);

function summary(job: string, env: Record<string, string>, result?: object) {
  const dir = mkdtempSync(path.join(tmpdir(), "wow-log-check-"));
  try {
    const file = path.join(dir, "summary.md");
    if (result) {
      writeFileSync(path.join(dir, "wow-apply.json"), JSON.stringify(result));
    }
    const run = spawnSync("bash", ["-e", "-o", "pipefail", "-c", summaryStep(job).run], {
      env: { ...process.env, ...env, GITHUB_STEP_SUMMARY: file, RUNNER_TEMP: dir }, encoding: "utf8",
    });
    expect(run.status, run.stderr).toBe(0);
    const saved = readFileSync(file, "utf8");
    expect(run.stdout.trim()).toBe(saved.trim());
    return saved;
  } finally { rmSync(dir, { recursive: true, force: true }); }
}

test("pipeline outcome explains no-op, branch skip, failures and pull request handoff", () => {
  for (const [inspect, refresh, publish, needed, pushed, reason] of [
    ["success", "skipped", "success", "false", "false", "no spec needed Claude Code"],
    ["skipped", "skipped", "skipped", "", "", "only on the default branch"],
    ["failure", "skipped", "skipped", "", "", "no candidate was produced"],
    ["success", "success", "failure", "true", "", "no end-to-end success"],
    ["success", "success", "success", "true", "false", "Nothing was pushed"],
    ["success", "failure", "success", "true", "true", "pull request opened for review"],
  ]) {
    expect(summary("summary", { INSPECT_RESULT: inspect, REFRESH_RESULT: refresh, PUBLISH_RESULT: publish, NEEDED: needed, PUSHED: pushed })).toContain(reason);
  }
});

test("detection summary exposes only outcomes and distinguishes skipped Claude runs from failed detection", () => {
  const stages = { inspect: { outcome: "success" }, upload: { outcome: "success", outputs: { secret: "NEVER-LOG-THIS" } } };
  const skipped = summary("inspect", { EVENT: "schedule", FORCE_REFRESH: "false", JOB_STATUS: "success", CLAUDE_NEEDED: "false", STAGES: JSON.stringify(stages) });
  expect(skipped).toContain("force_refresh");
  expect(skipped).toContain("Claude Code skipped");
  expect(skipped).not.toContain("NEVER-LOG-THIS");
  const ran = summary("inspect", { EVENT: "schedule", FORCE_REFRESH: "false", JOB_STATUS: "success", CLAUDE_NEEDED: "true", STAGES: JSON.stringify(stages) });
  expect(ran).toContain("one Claude Code run each");
  const failed = summary("inspect", { EVENT: "schedule", FORCE_REFRESH: "false", JOB_STATUS: "failure", CLAUDE_NEEDED: "", STAGES: JSON.stringify({ ...stages, inspect: { outcome: "failure" } }) });
  expect(failed).toContain("not evidence of unchanged sources");
});

test("publication summaries distinguish no-op, pull request and partial failure", () => {
  for (const [status, pushed, reason] of [
    ["success", "false", "No accepted data changes"],
    ["success", "true", "pull request opened"],
    ["failure", "true", "Do not assume rollback"],
  ]) {
    const stages = { apply: { outcome: "success" }, stage: { outcome: "success", outputs: { pushed } } };
    const text = summary("publish", { JOB_STATUS: status, PUSHED: pushed, STAGES: JSON.stringify(stages) }, {
      status: "partial", summary: "Validated fixture only",
    });
    expect(text).toContain(reason);
    expect(text).toContain("Validated fixture only");
    expect(text).toContain("previous values");
  }
  expect(summary("publish", { JOB_STATUS: "failure", STAGES: "{}" })).toContain("Publication incomplete");
});

test("summary environments allow only stage outcomes, and the observation is uploaded after a successful detection only", () => {
  for (const job of ["inspect", "publish"]) {
    for (const match of summaryStep(job).env.STAGES.matchAll(/\$\{\{\s*(.*?)\s*\}\}/g)) {
      expect(match[1]).toMatch(/^steps\.\w+\.outcome$/);
    }
    const text = summary(job, { JOB_STATUS: "failure", CLAUDE_NEEDED: "", EVENT: "schedule", FORCE_REFRESH: "false", STAGES: JSON.stringify({ checkout: { outcome: "failure" } }) });
    expect(text).toContain(job === "inspect" ? "Checkout | failure" : "Trusted checkout | failure");
  }
  const condition = wow.jobs.inspect.steps.find((step: any) => step.id === "upload").if;
  for (const outcome of ["success", "failure", "skipped"]) {
    const evaluated = condition.replaceAll("always()", "true").replaceAll("steps.inspect.outcome", JSON.stringify(outcome));
    expect(new Function(`return (${evaluated})`)()).toBe(outcome === "success");
  }
});

test("pipeline workflow shell blocks remain syntactically valid", () => {
  for (const job of Object.values(wow.jobs) as any[]) {
    for (const step of job.steps) {
      if (!step.run) continue;
      const checked = spawnSync("bash", ["-n"], { input: step.run, encoding: "utf8" });
      expect(checked.status, `${step.name}: ${checked.stderr}`).toBe(0);
    }
  }
});

test("refresh job sets up Bun before the Claude Code step", () => {
  const steps = wow.jobs.refresh.steps;
  const bun = steps.findIndex((step: any) => String(step.uses).startsWith("oven-sh/setup-bun@"));
  const claude = steps.findIndex((step: any) => String(step.uses).startsWith("anthropics/claude-code-action@"));
  expect(bun).toBeGreaterThanOrEqual(0);
  expect(bun).toBeLessThan(claude);
});

test("each changed spec gets its own capped Claude run on the gateway without sub-agents", () => {
  const refresh = wow.jobs.refresh;
  expect(refresh.needs).toBe("inspect");
  expect(refresh.strategy["fail-fast"]).toBe(false);
  expect(refresh.strategy.matrix.spec).toContain("fromJSON(needs.inspect.outputs.matrix)");
  expect(JSON.stringify(wow.jobs.inspect.steps)).toContain("WOW_REFRESH_MAX_SPECS");
  const claude = refresh.steps.find((step: any) => String(step.uses).startsWith("anthropics/claude-code-action@"));
  expect(claude.env.ANTHROPIC_BASE_URL).toBe("${{ steps.provider.outputs.base_url }}");
  expect(claude.with.anthropic_api_key).toBe("${{ secrets.WOW_REFRESH_API_KEY }}");
  expect(claude.with.claude_args).toBe("${{ steps.provider.outputs.claude_args }}");
  expect(claude.with.prompt).toContain("${{ matrix.spec }}");
  expect(wow.permissions).toEqual({ contents: "read" });
  expect(wow.jobs.publish.permissions).toEqual({ contents: "read", "id-token": "write" });
  expect(wow.concurrency).toEqual({ group: "wow-season-source-check", "cancel-in-progress": false });
});

test("publish pushes and opens the PR as the h-cloud-npa app via octo-sts", () => {
  const text = readFileSync(new URL("../../../.github/workflows/wow-season-updates.yml", import.meta.url), "utf8");
  const publish = wow.jobs.publish;
  const sts = publish.steps.find((step: any) => step.id === "sts");
  expect(sts.uses).toMatch(/^octo-sts\/action@[0-9a-f]{40}$/);
  expect(sts.with).toEqual({ scope: "lkshrk/agent-stack", identity: "agent-stack-wow-refresh" });
  expect(publish.steps.indexOf(sts)).toBe(0);
  expect(publish.steps.find((step: any) => step.id === "pr").env.GH_TOKEN).toBe("${{ steps.sts.outputs.token }}");
  const checkout = publish.steps.find((step: any) => step.id === "checkout");
  expect(checkout.with.token).toBe("${{ steps.sts.outputs.token }}");
  expect(text).toContain('git config user.name "h-cloud-npa[bot]"');
  expect(text).not.toContain("github-actions[bot]");
});

test("workflow reads the active season from data instead of hardcoding it", () => {
  const text = readFileSync(new URL("../../../.github/workflows/wow-season-updates.yml", import.meta.url), "utf8");
  expect(text).not.toMatch(/season-mn-\d|season-data\/retail/);
  expect(text).toContain("--print-bundle");
});

test("publishing validates and applies per-spec results from a fresh job", () => {
  const publish = wow.jobs.publish;
  const apply = publish.steps.find((step: any) => step.id === "apply").run;
  expect(apply).toContain("--parts");
  expect(apply).toContain("bun test");
  expect(publish.steps.findIndex((step: any) => step.id === "parts")).toBeLessThan(publish.steps.findIndex((step: any) => step.id === "apply"));
});

test("the per-run spec cap is clamped to 1..40 and rejects non-numeric input", () => {
  const step = wow.jobs.inspect.steps.find((item: any) => item.id === "inspect").run;
  expect(step).toContain("min(max(int(os.environ['MAX_SPECS']), 1), 40)");
  expect(step).toContain("must be a whole number between 1 and 40");
});
