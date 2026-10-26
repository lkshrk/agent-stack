import { expect, test } from "bun:test";
import { spawnSync } from "node:child_process";
import { mkdtempSync, mkdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
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
      mkdirSync(path.join(dir, "wow-refresh"));
      writeFileSync(path.join(dir, "wow-refresh/wow-refresh-result.json"), JSON.stringify(result));
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
  for (const [refresh, publish, needed, pushed, reason] of [
    ["success", "skipped", "false", "", "Source detection ran successfully"],
    ["skipped", "skipped", "", "", "only on the default branch"],
    ["failure", "skipped", "", "", "did not receive a successful candidate"],
    ["cancelled", "skipped", "true", "", "cancelled"],
    ["success", "failure", "true", "", "no end-to-end success"],
    ["success", "success", "true", "false", "Nothing was pushed"],
    ["success", "success", "true", "true", "pull request opened for review"],
  ]) {
    expect(summary("summary", { REFRESH_RESULT: refresh, PUBLISH_RESULT: publish, NEEDED: needed, PUSHED: pushed })).toContain(reason);
  }
});

test("refresh summary exposes only outcomes and distinguishes skipped AI from failed AI", () => {
  const stages = { inspect: { outcome: "success" }, provider: { outcome: "skipped", outputs: { secret: "NEVER-LOG-THIS" } }, refresh: { outcome: "skipped" } };
  const skipped = summary("refresh", { EVENT: "schedule", FORCE_REFRESH: "false", PROVIDER_MODEL: "", JOB_STATUS: "success", NEEDED: "false", STAGES: JSON.stringify(stages) });
  expect(skipped).toContain("force_refresh enabled");
  expect(skipped).toContain("Claude Code refresh | skipped");
  expect(skipped).not.toContain("NEVER-LOG-THIS");
  stages.refresh.outcome = "failure";
  const failed = summary("refresh", { EVENT: "schedule", FORCE_REFRESH: "false", PROVIDER_MODEL: "", JOB_STATUS: "failure", NEEDED: "true", STAGES: JSON.stringify(stages) });
  expect(failed).toContain("not evidence of unchanged sources");
  expect(failed).not.toContain("AI candidate generated");
});

test("publication summaries distinguish no-op, pull request and partial failure", () => {
  for (const [status, pushed, reason] of [
    ["success", "false", "No accepted data changes"],
    ["success", "true", "pull request opened"],
    ["failure", "true", "Do not assume rollback"],
  ]) {
    const stages = { apply: { outcome: "success" }, stage: { outcome: "success", outputs: { pushed } } };
    const text = summary("publish", { JOB_STATUS: status, PUSHED: pushed, STAGES: JSON.stringify(stages) }, {
      status: "updated", summary: "Validated fixture only", unresolved_sources: ["https://example.org/unavailable"],
    });
    expect(text).toContain(reason);
    expect(text).toContain("Validated fixture only");
    expect(text).toContain("previous values retained");
  }
  expect(summary("publish", { JOB_STATUS: "failure", STAGES: "{}" })).toContain("Publication incomplete");
});

test("summary environments allow only stage outcomes, and the output artifact is uploaded even after a failed refresh", () => {
  for (const job of ["refresh", "publish"]) {
    for (const match of summaryStep(job).env.STAGES.matchAll(/\$\{\{\s*(.*?)\s*\}\}/g)) {
      expect(match[1]).toMatch(/^steps\.\w+\.outcome$/);
    }
    const text = summary(job, { JOB_STATUS: "failure", NEEDED: "", EVENT: "schedule", FORCE_REFRESH: "false", PROVIDER_MODEL: "", STAGES: JSON.stringify({ checkout: { outcome: "failure" } }) });
    expect(text).toContain(job === "refresh" ? "Checkout | failure" : "Trusted checkout | failure");
  }
  const condition = wow.jobs.refresh.steps.find((step: any) => step.id === "collect").if;
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
