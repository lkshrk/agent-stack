import { expect, test } from "bun:test";
import { readFile } from "node:fs/promises";
import path from "node:path";

const ROOT = path.resolve(import.meta.dirname, "../skills/wow-spec/assets/season-data");

function references(value: unknown): string[] {
  if (typeof value === "string") return /^(item|spell):\d+$/.test(value) ? [value] : [];
  if (Array.isArray(value)) return value.flatMap(references);
  if (value && typeof value === "object") return Object.values(value).flatMap(references);
  return [];
}

test("bundled season setups have public provenance and complete catalogue references", async () => {
  let setups = 0;
  for await (const relative of new Bun.Glob("*/*/catalog.json").scan(ROOT)) {
    const folder = path.dirname(path.join(ROOT, relative));
    const catalog = JSON.parse(await readFile(path.join(folder, "catalog.json"), "utf8"));
    expect(catalog.schema_version).toBe(1);
    expect(catalog.game).toBe(path.basename(path.dirname(folder)));
    expect(catalog.season).toBe(path.basename(folder));
    expect(Number.isNaN(Date.parse(catalog.verified_at))).toBe(false);
    const index = JSON.parse(await readFile(path.join(folder, "index.json"), "utf8"));
    expect(index.game).toBe(catalog.game);
    expect(index.season).toBe(catalog.season);
    expect(index.spec_count).toBe(index.specs.length);
    expect(index.catalogue_record_count).toBe(Object.keys(catalog.records).length);
    expect(new URL(index.roster_source).protocol).toBe("https:");
    const expectedSpecs = new Set(index.specs.map((spec: { spec_id: number }) => spec.spec_id));
    expect(expectedSpecs.size).toBe(index.spec_count);
    const foundSpecs = new Set<number>();
    for (const [key, value] of Object.entries(catalog.records)) {
      const record = value as Record<string, any>;
      const [kind, id] = key.split(":");
      expect(["item", "spell"]).toContain(kind);
      expect(record[`${kind}_id`]).toBe(Number(id));
      expect(record.status).toBe("verified");
      expect(record.name.length).toBeGreaterThan(0);
      expect(record.sources.length).toBeGreaterThan(0);
      for (const source of record.sources) expect(new URL(source).protocol).toBe("https:");
      expect(record.identity_verified_at).toBeTruthy();
      expect(record.name).not.toMatch(/^(Formula|Recipe):/);
    }
    for await (const specFile of new Bun.Glob("spec-*.json").scan(folder)) {
      const setup = JSON.parse(await readFile(path.join(folder, specFile), "utf8"));
      setups++;
      foundSpecs.add(setup.spec_id);
      expect(specFile).toBe(`spec-${setup.spec_id}.json`);
      expect(setup.game).toBe(catalog.game);
      expect(setup.season).toBe(catalog.season);
      expect(setup.status).toBe("verified");
      expect(Number.isNaN(Date.parse(setup.verified_at))).toBe(false);
      expect(setup.sources.length).toBeGreaterThan(0);
      expect(Object.keys(setup.stat_guidance).length).toBeGreaterThan(0);
      for (const section of ["gems", "enchants", "consumables"]) {
        expect(Object.keys(setup.recommendations[section]).length).toBeGreaterThan(0);
      }
      for (const key of references(setup)) expect(catalog.records[key]?.status).toBe("verified");
    }
    expect(foundSpecs).toEqual(expectedSpecs);
    for (const spec of index.specs) expect(spec.file).toBe(`spec-${spec.spec_id}.json`);
  }
  expect(setups).toBeGreaterThan(0);
});
