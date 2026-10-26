import { test } from "bun:test";
import { execFileSync } from "node:child_process";
import { fileURLToPath } from "node:url";

test("saved-variable importer preserves scoped observations and user progress", () => {
  const script = fileURLToPath(
    new URL("../skills/wow-account-setup/scripts/check_import.py", import.meta.url),
  );
  execFileSync("python3", [script], { timeout: 30_000, stdio: "pipe" });
});
