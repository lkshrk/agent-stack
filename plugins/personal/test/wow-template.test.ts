import { test } from "bun:test";
import { execFileSync } from "node:child_process";
import { fileURLToPath } from "node:url";

test("portable Obsidian templates preserve task and account semantics", () => {
  const script = fileURLToPath(
    new URL("../skills/wow-achievement-dashboard/scripts/check-template.cjs", import.meta.url),
  );
  execFileSync(process.execPath, [script], { timeout: 30_000, stdio: "pipe" });
});
