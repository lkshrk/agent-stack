import assert from "node:assert/strict";
import { readFile, readdir } from "node:fs/promises";
import { existsSync } from "node:fs";
import { spawn } from "node:child_process";
import path from "node:path";
import { test } from "bun:test";

const ROOT = path.resolve(import.meta.dirname, "..");
const SKILLS_DIR = path.join(ROOT, "skills");
const SHARED_LINK = /shared\/wow\/[A-Za-z0-9_.-]+/g;

async function markdownFiles(dir: string): Promise<string[]> {
  const out: string[] = [];
  for (const entry of await readdir(dir, { withFileTypes: true })) {
    if (entry.name === "shared") continue;
    const abs = path.join(dir, entry.name);
    if (entry.isDirectory()) out.push(...(await markdownFiles(abs)));
    else if (entry.name.endsWith(".md")) out.push(abs);
  }
  return out;
}

async function wowSkills(): Promise<string[]> {
  return (await readdir(SKILLS_DIR)).filter((n) => n.startsWith("wow-") && existsSync(path.join(SKILLS_DIR, n, "SKILL.md"))).sort();
}

test("sync --check passes, so every skill copy of shared/wow matches its source", async () => {
  const { code, stderr } = await new Promise<{ code: number; stderr: string }>((resolve) => {
    const child = spawn("bun", ["scripts/sync_skill_references.ts", "--check"], { cwd: ROOT });
    let stderr = "";
    child.stderr.on("data", (d) => (stderr += d));
    child.on("close", (code) => resolve({ code: code ?? 1, stderr }));
  });
  assert.equal(code, 0, `sync --check failed:\n${stderr}`);
});

test("every shared/wow file a skill links is carried by that skill and matches the source", async () => {
  for (const name of await wowSkills()) {
    const skillDir = path.join(SKILLS_DIR, name);
    const linked = new Set<string>();
    for (const file of await markdownFiles(skillDir)) {
      for (const m of (await readFile(file, "utf8")).matchAll(SHARED_LINK)) linked.add(m[0]);
    }
    for (const rel of linked) {
      assert.ok(existsSync(path.join(ROOT, rel)), `${name} links ${rel}, which has no source`);
      const copy = path.join(skillDir, rel);
      assert.ok(existsSync(copy), `${name} links ${rel} but does not carry it`);
      assert.equal(await readFile(copy, "utf8"), await readFile(path.join(ROOT, rel), "utf8"), `${name}/${rel} drifted from ${rel}`);
    }
  }
});
