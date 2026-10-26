# Source validation — 2026-10-04

These are observations, not permanent API contracts. Recheck affected capabilities when access or provider behavior changes. The WCL raid and exact-key M+ workflows now pass end to end for the tested cases below. In-game import, broader spec coverage and Archon access remain open.

**Scope: all executable workflows and live-test results below are Retail-only.** The WCL sampler, Raider.IO diagnostic and Raidbots/Blizzard talent exporter have not been validated for Classic variants or Forever. Do not substitute their IDs, API hosts or talent strings to imply compatibility. For other editions use the existing providers' verified matching guides/calculators; see [versioned sources](../shared/wow/versioned-sources.md). Provider Classic/Forever coverage is distinct from bundled helper support. Verify each calculator's format and import destination: Method's Forever calculator offers Export/Import String, which is not proof of Retail-codec or in-game compatibility.

## End-to-end workflow

[wcl_builds.py](../scripts/wcl_builds.py) reads credentials from `WCL_CLIENT_ID` / `WCL_CLIENT_SECRET`, or a user-designated `--rbw-entry` / `--rbw-folder` with custom fields `client_id` and `secret_id`. Secrets stay in memory; never put them in CLI arguments. The script prints Markdown by default or structured JSON with `--format json`; it writes no files. Save user-facing output only where requested.

The helper accepts either `--encounter` (one boss/dungeon) or `--zone` (all encounters in a discovered current raid/M+ season). For broad raid/dungeon requests, do not ask for one encounter. Zone mode interleaves encounter/level streams and deduplicates per character **per encounter**, reporting observation totals, distinct players and both overall/per-build content coverage. A player can contribute different logged builds on different bosses. Missing or zero-observation encounters stay visible; broad output is a baseline, not proof one build is optimal everywhere.

General “any build” requests use known context or a labelled general PvE baseline. Leveling uses dedicated current leveling sources and point progression, not this max-level ranking helper; label destination-only import strings rather than pretending all talents are available at low levels.

Broad-scope live checks passed: `--zone 53 --sample 20` with no difficulty selected Heroic and returned 20 character–encounter observations from 14 distinct players, with 9/10 provider-listed encounters represented (Kith'ix had zero observations, disclosed). `--zone 55 --sample 16` with no dungeon/key/difficulty returned observations across all eight current-season dungeons from five distinct players, actual levels +22/+23, with a small-sample warning. Both cases validated their displayed imports against exact logged fights. These are smoke-test samples, not permanent recommendations. The leveling route is instructional and has not received an end-to-end live example in this test.

```sh
python3 skills/wow-spec/scripts/wcl_builds.py --encounter 3470 --difficulty 4 --class Warrior --spec Arms --spec-id 71 --since 2026-09-27 --reference '<current Arms reference string>'
python3 skills/wow-spec/scripts/wcl_builds.py --zone 53 --class Warrior --spec Arms --spec-id 71 --since 2026-09-27 --reference '<current Arms reference string>'
python3 -m unittest discover -s skills/wow-spec/scripts
```

Resolve encounter, season/partition and a suitable post-patch date before invoking. Difficulty and key level are optional: omit `--difficulty` to use Heroic (otherwise Normal) for raid metadata or the sole dungeon difficulty, with the chosen default disclosed. No key argument means no level filter; show the observed level distribution, not an implied equal sample of all levels. A single `--key 10` selects +8–12 by default (`--key-spread 2`); `--key 2` selects +2–4. Explicit `--min-key/--max-key` ranges are preserved, and `--exact-key` is reserved for an explicit exact-level request. A single API bracket cannot be combined with a target/multi-level range because it could silently narrow the sample.

Bound the sample with `--sample` (default 50) and `--pages` (automatic budget: at least one page per content/level stream, minimum 5, maximum 100). If bounded retrieval finds too few rows in the range, disclose that limitation; do not silently treat outside-range rows as eligible. The script holds one metadata snapshot per process, reports its hash with exports, rejects unknown/incompatible entries and verifies the exact logged representative of each displayed build. This is not automatic proof that every log belongs to the current patch: the caller must establish the date/partition scope. Transfers/renames may defeat composite region/server/name identity; disclose that limitation.

Multi-level ranges query a candidate bracket for each level, validate every returned row's actual key, then interleave those ranked streams nearest the target first. The first eligible appearance of each character is kept. This is a spread across per-level leaderboards, not the global top X; report the actual distribution and exclusions. `--pages` caps total bracket pages, so a five-level range needs at least five pages. The caller can increase the bounded page budget for wider ranges; do not silently omit levels.

Live optional-input test: omitted difficulty and supplied `--key 10 --sample 10`. It automatically selected the provider's Dungeon difficulty, sampled +8–12 and generated both displayed imports with exact-fight/codec checks. Observed levels: +8:3, +9:2, +10:1, +11:1, +12:3. A preliminary global-leaderboard-only filter found no eligible low keys, which prompted the verified per-level retrieval above.

For the verified Temple +11 test, use encounter61877, difficulty10, partition1, `--bracket 10 --min-key 11 --max-key 11 --metric dps`. **Bracket10 returned +11**, not +10. Do not extrapolate that offset to other brackets/versions; inspect actual levels. `--metric speed`/`score` map to WCL `playerspeed`/`playerscore`; score rankings can lack backing logs and must be excluded if no historical data exists. DPS samples are not highest-key samples.

Live tests on 2026-10-04, dated September27 onward, each with 50 identifiable characters:

| Case | Complete builds | Leading shares | Export checks |
| --- | ---: | --- | --- |
| Arms / Heroic Nek'zali | 30 | 7/50, 6/50 | Both Slayer builds: exact fight match + 76-entry codec round trip + independent reference round trip |
| Elemental / Heroic Nek'zali | 48 | 2/50, 2/50, tied | Both Farseer builds: exact fight match + 80-entry codec round trip + independent reference round trip |
| Arms / Temple of Sethraliss +11, WCL DPS sample | 19 | 12/50, 5/50 | Both Slayer builds: exact fight/key match + 76-entry codec round trip + independent reference round trip |

The raid Arms sample's leading pair differed in Second Wind/Interpose. Independent frequency reporting also found Fierce Followthrough19/50 versus Opportunist31/50, which the leading-build comparison alone would miss. Counts are per hero-tree cohort; different hero trees get separate full builds rather than misleading swap tables. Same-name talent entries retain distinguishing spell IDs in differences. Fragmented samples and ties are explicit.

The actual codec also passed a partial tiered-node test (75 logged-entry pairs after removing the final tier): fidelity test only, not a recommended full build. No eligible Stormbringer was found in the bounded three-page raid/date probe; do not claim that hero tree was tested.

Offline regression coverage includes conflicting hero trees, invalid choices, tiered rank gaps, duplicate/unknown entries, complete-build grouping, identity/log exclusions, actual-key filtering, timestamp offsets, exact-fight talent mismatches, API metric names and alternatives spread across rare full builds.

Sampling validates each row against the fixed current metadata before admitting it. Unknown entries and incompatible ranks count as `incompatible_talents` exclusions; malformed talent lists count as `invalid_talents`. Rejected rows do not reserve a character identity or fill a sample slot. Retrieval continues only within the original page budget, and a smaller valid sample is reported when that budget is exhausted. The end-to-end regression covers both a replacement row on the next page and a one-page budget with49 valid rows retained.

## Raider.IO

Verified via public API, without an API key:

- `/api/v1/mythic-plus/runs` supports season, region, dungeon, affixes and page. It does not document direct spec, key-range or date filters; those require local selection within bounded leaderboard coverage.
- Leaderboard roster entries contain `loadout` import strings.
- `/api/v1/mythic-plus/run-details` exposes selected nodes/ranks, spell names, positions, `dbcIndexVersion` and import/export strings under `character.talentLoadout`.
- These responses can disagree for the same character and run. One examined detail string also matched the current profile; two other examined detail strings did not. This does not establish one universal cause or identify which response is historically correct. Encoding equivalence has not been tested with a compatible decoder.

Fixed bounded diagnostic completed at 07:29 UTC: Arms Warrior (71), Temple of Sethraliss, `season-mn-2`, worldwide, timed runs from September 27 onward. Inspected 12 pages / 240 distinct runs; selected 50 unique characters from their first eligible leaderboard appearance, observed keys +21–23. Results: **35 matching exports, 14 unresolved export disagreements, one request/schema failure**. Matching responses reported tree version 12.1.0. Counts are consistency diagnostics, not verified historical talent frequencies. The earlier smaller test's build percentages must not be reused as recommendations.

Reproduce a fresh bounded diagnostic (results will change with the leaderboard):

```sh
python3 skills/wow-spec/scripts/check_raiderio_sample.py --self-test
python3 skills/wow-spec/scripts/check_raiderio_sample.py --season season-mn-2 --dungeon temple-of-sethraliss --since 2026-09-27 --spec 71
```

Consult current [API documentation](https://raider.io/api) / [schema](https://raider.io/swagger.json):

- `live-tracking/character/loadout` accepts character identity and returns the **most recent combat log** state; there is no documented historical run selector.
- `live-tracking/user/activity/mythic-plus` documents season + keystone `run_id` and combat-log-backed run details. Public probes for runs 16542188 and 16268990 returned **404**. Do not conclude all runs are unavailable or guess alternate IDs/authentication requirements. Resolve access/coverage with provider documentation or a supported accessible example before using it.

Remaining gate: establish run-time provenance and a reliable matching export/node snapshot. Matching strings alone do not close it. Do not replace disputed run-time data with the current profile or most recent combat log.

## Warcraft Logs

Authentication subsequently passed using the user's designated password-manager entry. Custom fields supplied the OAuth client ID and secret; credentials and access tokens stayed in process memory and were not printed or persisted. Initial vault failures were a stale local rbw session/cache, resolved by the user. `rbw login` is a no-op when already logged in; this version has no `logout` command. The documented explicit logout is `rbw purge`, which removes the local cache and requires fresh authentication. Do not perform this reset silently during ordinary queries.

Live test passed for **Arms Warrior / Heroic Nek'zali the Soulcoiler**, encounter 3470, difficulty 4, zone 53. IDs were discovered through authenticated `worldData`; do not infer IDs from display names, since different zones can share names. `characterRankings(className:"Warrior", specName:"Arms", difficulty:4, metric:dps, includeCombatantInfo:true, page:1)` returned 100 rows with report codes, fight IDs, timestamps, talent entry IDs and point counts.

Historical cross-check: [report WzqBryYVdRxpn478, fight 8, actor 45](https://www.warcraftlogs.com/reports/WzqBryYVdRxpn478#fight=8&source=45&type=summary). `fights` confirmed encounter 3470 and difficulty 4. All **76 (talent entry ID, rank) pairs** in the top ranking matched the exact fight's `events(dataType:CombatantInfo, fightIDs:[8], sourceID:45).data[].talentTree`. This is one verified historical spot-check, not a claim that every sample row was independently checked.

Bounded aggregation: 100 rows fetched, 73 examined to select 50 unique identifiable characters dated September 27 onward. Excluded 21 outside-window rows and two missing character identity. Identity used region + server ID + case-normalized name because ranking rows did not supply a stable character ID; transfers/renames remain a limitation. The 50 samples contained **30 complete talent-entry/rank sets**; leading counts were **7, 6, 3, 3, 2** (14%, 12%, 6%, 6%, 4%). This was a DPS-ranked sample; external buffs were present and not excluded. No single majority build emerged.

Neither the tested ranking payload, exact-fight CombatantInfo event nor Summary table supplied a ready-made import string. Summary `combatantInfo.talentTree` and the event expose entry IDs, ranks and node IDs. Some entries share a node ID; do not collapse them by node ID alone or treat entry IDs as spell IDs. The verified export method below now handles the tested build. In-game import remains untested.

Use the configured credential provider on future requests, rather than checking only environment variables. Keep passwords, OAuth tokens and the user's private vault locator out of shared skill files. [Warcraft Logs API documentation](https://www.warcraftlogs.com/api/docs).

### Verified export method

Use [export_wcl_talents.py](../scripts/export_wcl_talents.py), which executes Blizzard's existing codec with a small adapter, rather than a new bit-level encoder. Inputs are one WCL CombatantInfo event on stdin and a source-provided current reference import string for the same spec:

```sh
python3 skills/wow-spec/scripts/export_wcl_talents.py --reference '<known-current-string>' < combatant-event.json
```

Requires Python 3, curl and Lua 5.3+ (override the executable with `--lua`). It reads public tree metadata and two pinned UI source files, writes no files, and does not access credentials. Check exit status: failure emits no import string on stdout.

- [Blizzard ClassTalentImportExport](https://github.com/Gethe/wow-ui-source/blob/09b9db7948abc9b9648dedaab51eb0cf3ee67b31/Interface/AddOns/Blizzard_PlayerSpells/ClassTalents/Blizzard_ClassTalentImportExport.lua) and [ExportUtil](https://github.com/Gethe/wow-ui-source/blob/09b9db7948abc9b9648dedaab51eb0cf3ee67b31/Interface/AddOns/Blizzard_SharedXMLBase/ExportUtil.lua), pinned revision `09b9db7948abc9b9648dedaab51eb0cf3ee67b31`.
- [Raidbots live talents metadata](https://www.raidbots.com/static/data/live/talents.json): full node ordering, entry order, ranks, granted nodes, hero selection and tiered nodes. Metadata is live, not permanently patch-pinned; revalidation is required on each use. The WCL Summary field `gameVersion: 1` is not the client patch number.
- Serialization version is checked against the reference string (version 2 in this implementation). The tree hash is deliberately zero-filled, as documented for third-party exports by Blizzard; this skips the game's tree-hash check. Do not imply full compatibility merely because import syntax is valid.
- The logged build must decode to exactly the same entry/rank pairs, including grants, hero choices and tiered entries. An independent source string must also decode/re-encode byte-for-byte. This caught and corrected a mapping error: Raidbots calls a choice node `choice`, not `selection`.

Live test: all **76 entries** from the previously verified fight round-tripped exactly; the separate Raider.IO reference string round-tripped byte-for-byte. Generated string for that **individual logged build**, not the aggregate winner:

```text
CcEAAAAAAAAAAAAAAAAAAAAAAAzMzsMz8AmZGAAAghphZGzMWmZmZGMmZAAAAAMzyMDMhxyyALgBMDTgZwGYmhx2ALzsNAzMAYGGA
```

Validation scope now includes the end-to-end cases above. This is not an in-game import test, full legality/budget/path validator, or all-spec guarantee. Unknown entries, mismatching ranks, conflicting hero selections, invalid tiered ordering or a failed reference round trip block output rather than dropping talents.

## Archon explicit-source override

Exact target found: [Arms / Temple of Sethraliss / high keys / this week](https://www.archon.gg/wow/builds/arms/warrior/mythic-plus/talents/high-keys/sethraliss/this-week).

Direct retrieval returned an access-challenge page; web retrieval returned 403. Search indexing established the page exists, not its current build/export. The browser skill was read, but the required browser execution tool was not exposed in this session. No source build/import was extracted and no alternate source was silently substituted.

Next requirement: normal supported browser access or user-supplied page export. A supplied string can be analysed, but cannot be attributed to a currently verified Archon page without supporting evidence.

## Separate named-source override test: Method

[Method's Arms talent guide](https://www.method.gg/guides/arms-warrior/talents), labelled patch12.1 and updated2026-09-25, exposed its published import strings. The **Colossus — Single Target** string passed the independent decoder/re-encoder byte-for-byte check. Preserve this exact source build when requested; it is a general single-target editorial recommendation, not a verified Nek'zali-specific statistical winner. No usage percentages are attributed to Method. This test does not substitute Method for an explicit Archon request.

```text
CcEAAAAAAAAAAAAAAAAAAAAAAAzMzsMzMmZAAAAMMNMGzwyMzMzwMmZAAAAAM2mZAZBYzMG2gBmRb0YwCYmBz2MYZmlBzMAgZGGA
```

Wowhead's current Arms guide text was accessible, but its primary build codes were absent from the text rendering. An old code in the comments is not a replacement for the guide's current build.
