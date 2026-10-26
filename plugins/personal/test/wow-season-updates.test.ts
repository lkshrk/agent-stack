import { test } from "bun:test";
import { execFileSync } from "node:child_process";
import { fileURLToPath } from "node:url";

test("weekly WoW source check distinguishes changes, noise and unavailable sources", () => {
  const script = fileURLToPath(new URL("../automation/test_wow_season_updates.py", import.meta.url));
  execFileSync("python3", [script], { timeout: 30_000, stdio: "pipe" });
});

test("AI refresh publishes validated data while rejecting unsafe or incomplete candidates", () => {
  const script = fileURLToPath(new URL("../automation/test_apply_wow_season_refresh.py", import.meta.url));
  execFileSync("python3", [script], { timeout: 30_000, stdio: "pipe" });
});

test("refresh run configuration targets the gateway root without exposing credentials", () => {
  const script = fileURLToPath(new URL("../automation/test_prepare_wow_refresh.py", import.meta.url));
  execFileSync("python3", [script], { timeout: 30_000, stdio: "pipe" });
});

test("WCL sampling excludes incompatible snapshots and respects its page budget", () => {
  const scripts = fileURLToPath(new URL("../skills/wow-spec/scripts/", import.meta.url));
  execFileSync("python3", ["-m", "unittest", "discover", "-s", scripts], { timeout: 30_000, stdio: "pipe" });
});
