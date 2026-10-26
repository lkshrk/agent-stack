---
name: wow-spec
description: Recommend World of Warcraft spec setups with talents, stat guidance, gems, enchants and consumables for general PvE, dungeons, raids, leveling or exact encounters. Provide sourced talent imports, build aggregation, source overrides and season-scoped setup refreshes. Not rotation coaching, gear optimization simulations or achievement planning.
---

# WoW Spec

Give the requested spec advice in a usable, self-contained form across Retail, Classic variants and Forever. Talent recommendations include the edition's verified supported import or calculator representation and meaningful alternatives. Answer in chat unless the user requests Obsidian or Markdown. Do not create dashboards or full-tree renderers for ordinary questions. Reuse the small internal season reference described below only where applicable; keep it separate from user-facing guides.

## Route the requested sections

For every request, follow [game context and capability boundaries](shared/wow/game-context.md), [existing providers by game version](shared/wow/versioned-sources.md), and [request, client and source languages](shared/wow/language.md). Ask which version the user plays when unclear, including the specific Classic variant; remember it for the character or plan. An expansion named as a content target does not establish the client.

- “Build” or “talents”: talents only, unless the user includes other sections.
- “Stats”, “gems”, “enchants” or “consumables”: answer those sections without running a talent leaderboard scan unnecessarily.
- “Full setup”: talents, general stat guidance, gems, enchants and consumables.
- “Update spec data” / “refresh WoW spec data”: refresh the relevant stored season setup and shared item facts, then report changes and unresolved gaps.

For stats, gems, enchants, consumables or refresh requests, read [season-setup.md](references/season-setup.md). These recommendations default to one setup per spec and season across content; add content/build overrides only where supported differences exist. On activation, look for the matching public baseline in [assets/season-data](assets/season-data/) and any local cache. Use the applicable validated set with the newer verification date, preserving an explicit user source preference. A shipped match works on first use without a cache or network refresh. Do not load every spec/season, overwrite local updates, or treat copying a file as fresh verification. Refresh when no applicable verified data exists, on season change, a relevant patch/hotfix or explicit request. Calendar age alone does not invalidate otherwise applicable seasonal data. Talent-popularity samples follow their own date windows and are not frozen for a whole season.

## Resolve the intended content

The shipped setup baseline and automatic refresh pipeline are Retail-live only. For Classic/Forever, use verified matching provider guides; do not reuse a Retail bundle or promise a full pre-researched baseline. Partition any local records by game, environment, expansion, phase/season and compatible revision as described in the setup reference.

Reuse established game version, specialization and preferences. The game and spec must be known for a spec-specific recommendation; ask for whichever cannot be resolved from context. Resolve live/beta/phase/ruleset when it affects the advice. Missing content, dungeon, boss, difficulty, key level or leveling range does not by itself require a question.

| User request | Scope |
| --- | --- |
| “Any build” / “a build for this spec” | Reuse known intended content; otherwise give a clearly labelled general PvE baseline, using broad dungeon/solo suitability. Do not claim it is optimal for raid, leveling and PvP simultaneously. |
| “Dungeon build” | Relevant ordinary dungeons for the resolved edition/level, with important situational differences. Use established M+ context only when applicable; do not turn an ordinary dungeon request into M+. Do not demand a dungeon name. |
| “M+ build” | All current-season M+ dungeons only for a verified supporting client; disclose an unsupported mode instead of converting a Classic dungeon request into Retail M+. |
| “Raid build” | Current raid as a whole, or the named raid. Default difficulty as below; do not demand a boss. |
| “Leveling build” | Current leveling-specific guidance and point progression for the spec. A level is optional; cover the leveling path if none is supplied. |
| A named dungeon/boss | That exact content; never replace it silently with a broad pool. |

Resolve the edition's current patch, phase/season and talent-tree version from current sources; do not hardcode them. Clarify an ambiguous content name when needed, not a deliberately broad request. Consider pug versus coordinated group and preferred hero tree where supported; never infer group coordination from leaderboard placement. Use edition-appropriate difficulty names; the Heroic/Normal and key-sampling defaults below apply to Retail, not every edition.

Do not ask for key level or difficulty just to proceed. With no key level, use a general dungeon sample without a key filter and show the actual observed levels; ranked samples need not represent all levels evenly. With no raid difficulty, default to Heroic when available, otherwise Normal; for dungeon data use its provider-listed difficulty. Label the default rather than pretending the user chose it. Do not mix raid difficulties silently.

A supplied single key is a target, not an exact filter: default to **target ±2 levels**, clamped to the supported minimum (normally +2). Thus “+10” samples +8–12; “+2” samples +2–4. Honour an explicit range or an explicitly exact-level request. Show both the selected range and the observed level distribution. Do not let a single API bracket silently collapse the range to one level, and do not widen a sparse sample without disclosure.

For a multi-level WCL range, query each level's verified bracket and interleave ranked records across levels (nearest target first), deduplicating characters on their first eligible appearance. This keeps a global leaderboard's highest levels from swallowing the whole sample. Verify actual returned levels rather than trusting a fixed bracket offset. Label the sampling method and resulting distribution; it is not a global top-X ranking across the range, and uneven availability or duplicate players can still produce unequal counts. This interleaved sampling is the exception to the single-stream highest-ranked-appearance rule below.

Generic build advice needs no account refresh. Before claiming the user's actual spec, talents, gear or unlocks, refresh the configured game-data source and verify identity and field timestamps. If unavailable or stale, ask for help with that gap; continue generic advice without asserting personal eligibility. A supplied talent string may be compared directly without an account scan.

## Choose and identify the source

Explicit source requests override these defaults:

For Classic/Forever, first use the matching sections of our existing providers: Method, Wowhead, Icy Veins and supported Warcraft Logs/Raider.IO pages. Check the linked versioned-source reference and current provider navigation before assuming coverage. Prefer a verified edition-specific published guide when the bundled aggregation tools cannot support the edition. Keep editorial advice distinct from sampled statistics; do not invent usage, stat weights or simulation results. The aggregation defaults below apply only where the exact edition/content and retrieval path are verified.

| Request | Default |
| --- | --- |
| General PvE baseline | Broad relevant build statistics or a current explicitly general-purpose source build |
| Ordinary dungeons | Current edition-specific dungeon/leveling guide; use an appropriately scoped general guide only with its limits stated |
| All M+ dungeons / whole raid | Our aggregation across the resolved current season/raid; use a labelled published broad build if data access is unavailable |
| Specific M+ dungeon or top-X M+ sample | Raider.IO leaderboard/run data, aggregated by us |
| Specific raid boss or top-X raid sample | Warcraft Logs encounter data, aggregated by us |
| Leveling / leveling dungeons | Current dedicated leveling guidance and point order, with a verified import where supported or selected-build calculator/point allocation otherwise; max-level rankings are not leveling evidence |

For a named site's build, reproduce that source's actual build and label it editorial, published aggregate, individual example or our own aggregation as appropriate. Keep suggested modifications separate. Do not claim a general source build is dungeon/boss-specific. If the source cannot supply the requested scope or a usable talent representation, disclose the gap and offer a named fallback rather than silently switching. An unavailable import format alone is not a reason to replace the provider when its sourced point allocation and selected-build calculator satisfy the request. Comparisons retain separate provenance for each build.

Wowhead is a supporting source for mechanics and explanations, or a primary source when explicitly requested. Liquid Armory is an explicit-source option only where its requested talent feature can actually be accessed; do not treat simulations or announced features as a live build API.

Starting references (check current documentation and payloads at use time):

- [Raider.IO developer API](https://raider.io/api): leaderboard/run retrieval. Verify which filters, pagination and run-time talent fields the current endpoint supports; apply unsupported selection filters locally. A website feature does not prove equivalent API coverage.
- [Raider.IO live tracking](https://support.raider.io/kb/raider-dot-io-mythic-plus-addon/combat-log-tracking-benefits): recorded run equipment/talents. Do not substitute current profile talents for a missing historical snapshot.
- [Warcraft Logs API](https://www.warcraftlogs.com/api/docs) and [Retail schema](https://www.warcraftlogs.com/v2-api-docs/warcraft/): authenticated GraphQL rankings/report data. Inspect current encounter ranking filters and combatant information; verify talent coverage on a real response. Use configured OAuth credentials without exposing them. If credentials are missing, explain the setup needed; do not request secrets in chat.
- [Archon build methodology](https://www.archon.gg/wow/articles/news/elevate-your-game-with-new-build-guides-and-tier-lists-on-archon): published popularity statistics, not our own sampled aggregation.
- [Liquid Armory](https://liquidarmory.com/): verify the current feature rather than assume talent data exists.

No ready-made top-X talent-consensus endpoint is assumed for Raider.IO or Warcraft Logs. Retrieve eligible records and calculate our own aggregation when using those modes. If API access or required fields fail, report the limitation; never invent sample statistics from a handful of search results. Respect provider limits and stop rather than retrying indefinitely. Keep temporary responses outside user-facing guide folders.

Before using an unverified provider path, read [source-validation.md](references/source-validation.md) for the latest tested limitations. Defaults express the preferred source, not a guarantee that its end-to-end retrieval has passed. **Executable boundary:** the bundled WCL sampler, Raider.IO diagnostic and modern talent exporter are Retail-only. Do not run them for Classic/Forever with substituted IDs, URLs or reference strings. A provider's non-Retail section does not establish helper compatibility. Use verified matching public guides/calculators until a compatible retrieval/export path is validated. For Retail, Raider.IO consistency can be diagnosed with [check_raiderio_sample.py](scripts/check_raiderio_sample.py); its output is explicitly diagnostic, not a certified historical recommendation.

For an executable WCL sample, use [wcl_builds.py](scripts/wcl_builds.py): bounded ranking retrieval, unique-character aggregation, exact-fight validation of displayed builds, imports and named talent differences. See [commands and validation scope](references/source-validation.md#end-to-end-workflow). Select the appropriate metric explicitly: logged M+ DPS rankings answer a different question from highest-key leaderboards. WCL bracket IDs are not literal key levels; use actual min/max key filters and verify representative fights. When the default Raider.IO path cannot establish historical talents, a clearly labelled WCL alternative is available; an explicit Raider.IO-only request still requires disclosing the blocker rather than substitution.

Use `--zone` for an entire resolved raid or M+ season, versus `--encounter` for one boss/dungeon. Discover current zone IDs and their encounter membership first; do not average complete-raid score rankings as if they contain one talent build. Query the individual content streams and preserve the exact report/fight behind each displayed build. Broad source/guide fallbacks must really cover the requested category; don't relabel one boss's build “raid-wide.”

For leveling, prefer a maintained leveling source with level-sensitive talent ordering and any available partial-loadout exports. Show when spec/hero talents or prerequisites become available according to current sources. If only a max-level destination string exists, label it as the endpoint and give the progression separately; never promise it is fully usable at a lower level. Preserve known character level, but do not demand a level just to provide a general leveling path. Do not run the max-level ranking aggregator for this mode. Sources may include dedicated class resources, Method, Icy Veins or Wowhead; the user's explicit source still wins.

For Warcraft Logs, reuse the user-designated credential provider, including a password manager; missing environment variables do not prove credentials are absent. Keep secrets and tokens in memory and out of command arguments, logs and artifacts. Match a ranking to its report, fight and actor before checking CombatantInfo; verify boss, difficulty and spec. Ranking talent entry IDs are not spell IDs. Preserve entry IDs and ranks when grouping, including entries sharing a node ID. Anonymous rows cannot be treated as distinct identifiable characters; exclude them from unique-character samples and disclose any composite-identity fallback. State whether external buffs were filtered when ranking by throughput.

When Warcraft Logs access is missing, first check the existing private setup/context for a configured credential provider. If none is available, explain the one-time requirement in plain language and link [Connect Warcraft Logs — one-time setup](references/warcraftlogs-setup.md). Present its steps inline if the user cannot open the local guide. This is OAuth client-credentials access to public data, not a requirement to upload logs or authorise private reports. Offer a clearly labelled Method/public-guide alternative when appropriate; do not silently switch an explicit Warcraft Logs request.

Guide users through the actual client form and secure storage, not raw tokens, terminal exports or GraphQL. Ask for an existing password-manager entry's name/folder or a configured local credential location, never secret values in chat. The tested rbw adapter reads custom fields `client_id` and `secret_id`; do not imply all password managers are connected automatically. After the user says setup is done, retrieve the authorised entry, authenticate and perform a small public metadata query before claiming success; continue the original build request. Keep successful setup reusable rather than repeating onboarding.

On vault errors, distinguish missing entry, stale sync and failed authentication. Check the exact folder/name and attempt safe sync before asking the user to repeat setup. Do not clear a cached vault/session silently. `rbw login` can be a no-op for a stale logged-in session; inspect installed help before suggesting recovery commands, and never suggest a nonexistent `rbw logout`. A needed user login/unlock belongs in the password manager's own prompt, not chat.

## Select a defensible sample

Filter to the spec, requested content scope (one encounter, all season dungeons or the whole raid), difficulty/key range, current season and compatible patch/tree version before grouping builds. State the date window and actual filters. Exclude pre-change records when a relevant talent change makes them incomparable. Do not widen scope silently to fill a small sample.

Across content, interleave per-encounter ranking streams so one popular boss/dungeon does not dominate by raw volume. Count at most one eligible appearance per character per encounter, allowing that character's different builds on other encounters. This exception to global character deduplication must be labelled **character–encounter observations**, with distinct-character totals and per-content coverage shown separately. A 50-observation sample is not necessarily 50 different players. Report absent/zero-sample encounters; don't claim full coverage when only one instance returned usable data. Show whether the leading complete build is broadly used or whether encounter-specific variants dominate, without manufacturing a compromise tree.

For top-X requests, establish what is ranked: highest timed keys, fastest clears, damage parses, etc. Default M+ ordering to highest timed key then fastest completion at that level. A target-key request uses the nearby-level range defined above; an explicit range remains unchanged. For raid, state the metric; healing throughput is not a universal quality ranking for healers, nor damage for tanks. Ask when the choice materially changes the requested result, but missing key level/difficulty alone is not a reason to ask. Do not call a bounded or incomplete retrieval the global top X unless ordering and coverage support it.

Default to up to 50 unique characters, selecting each character's highest-ranked eligible appearance within the stated window. Honour a request for run-weighted statistics instead, and label the weighting. Paginate only as needed within provider limits. Deduplicate repeated records for the same character/fight, and identify characters by stable identity rather than name alone. Report eligible runs inspected, unique characters, usable samples, and exclusions for missing/invalid talent snapshots. If fewer than 50 are available, show the actual count; never inflate it. Fewer than 20 usable characters is a small sample under this skill's reporting convention, not a statistical confidence guarantee.

Use talents recorded during the selected run/fight. Capture source/report/run links, timestamp, content, spec, hero tree and selected nodes/ranks. A later armory loadout is not evidence of that encounter's build. Prefer available canonical selected-node data for equality: different serializations of identical selections must not create false alternatives. Never mix incompatible talent-tree versions.

Keep the selected leaderboard responses fixed for one analysis; a live leaderboard can change between requests. On Raider.IO, the leaderboard roster's `loadout` and run-details `character.talentLoadout` may disagree. Bind node selections and the export string to the same verified snapshot. Compare the leaderboard string with the detail object's `exportLoadoutText`/`importLoadoutText` and inspect `dbcIndexVersion`. If they differ, establish equivalence with a compatible decoder or exclude the record as unresolved; never attach one response's string to another response's nodes. Field presence or placement under a run response alone does not establish historical provenance. Report these exclusions separately from missing data.

Agreement is also insufficient proof of run-time provenance: both responses could use the same later profile. Prefer an explicitly run-bound combat-log snapshot with matching run and character identity. The Raider.IO `live-tracking/character/loadout` endpoint is documented as the **most recent** combat-log state; it is not arbitrary historical run data. The documented run-specific `live-tracking/user/activity/mythic-plus` endpoint is a candidate only when accessible and its payload is verified. If historical provenance remains unresolved, stop at a labelled diagnostic; do not publish percentages as “builds used in this dungeon,” even after excluding mismatches. Explain exclusions and their selection bias when a verified historical subset is available.

## Aggregate complete builds, then explain alternatives

Choose the most common observed complete legal build in the selected sample as the default candidate. Call it most common, not proven optimal. Keep all supported talent selections together for the full-build count; where hero trees exist, show their shares and compare variants within each hero tree. Do not invent hero trees for other editions or assemble a new build by independently choosing the most popular node in each slot.

Show n/N and percentages with an explicit denominator and weighting. Complete-build popularity and individual-talent popularity are different measures. A build can lead a fragmented sample without having a majority; say so. Report ties rather than inventing a performance winner.

Highlight meaningful alternatives reaching 20% usage by default, configurable on request. This is a reporting threshold, not evidence of superiority. For within-hero-tree percentages, state that denominator separately from the overall sample. Smaller situational variants may be included when supported and relevant. Only present “A instead of B” as a direct swap when the observed builds verify that relationship; multi-node changes require their own complete alternative with the supported talent representation described below. Preserve required path nodes, rank budgets and mutually exclusive choices.

Explain a dungeon/boss-specific swap only with supporting mechanics evidence. Popularity alone does not establish why players chose it, causal performance gains, or suitability for pugs. Separate sourced explanations from explicit inference. Do not promise damage gains without appropriate evidence.

## Provide a usable talent representation

For a talent recommendation, verify the edition's supported format first. Where supported, provide the exact source-backed import string for the recommended build and every complete alternative, each alone in a fenced `text` block with its format and destination labelled outside it. A calculator import is not necessarily an in-game import. Method's Forever calculator exposes Export/Import String; inspect and verify that format rather than claiming all non-Retail imports are unsupported. Never feed it to the Retail codec. If that edition has no verified import format, provide a verified selected-build calculator link and sourced point allocation/progression, clearly stating the import limitation. A generic empty calculator is not a selected build. Gems/enchants/consumables/stat-only answers need no talent string. Never fabricate, hand-edit or concatenate encoded strings.

Prefer the source's export for that exact observed build. If only node selections are available, use an existing verified encoder compatible with the current game/tree version; verify a decode/encode round trip preserves spec, hero tree, nodes and ranks. Do not create a speculative encoder during a routine answer. Where a compatible decoder is available, also check source strings against the selected build. Disclose the validation level; source retrieval is not an in-game import test.

For a WCL CombatantInfo event, [export_wcl_talents.py](scripts/export_wcl_talents.py) adapts current Raidbots tree metadata to a pinned copy of Blizzard's existing Lua exporter/importer, downloaded at runtime. Read [the tested method and limits](references/source-validation.md#verified-export-method) before use. It requires installed Lua 5.3+ and a known current reference string for the same spec; both the logged entry/rank round trip and reference-string round trip must pass. Consume JSON output only on exit zero. Verified on Arms/Slayer and Elemental/Farseer logged builds, a partial tiered-node case, and a Colossus reference string; not every specialization or patch, and not an in-game legality check.

If the edition supports importing but no reliable string can be obtained, mark the answer incomplete for importing and explain the missing capability. Do not present a tree link or an invented string as a working import. Offer a clearly identified source build with a verified compatible export as a fallback where available, keeping it distinct from the unexportable aggregate candidate. Where importing is unsupported, a sourced point allocation and verified selected-build link can fulfill talent guidance; disclose the unsupported capability without calling the entire recommendation unusable.

## Present the result

Keep the recommendation self-contained:

For setup sections, follow the output rules in [season-setup.md](references/season-setup.md). Include only requested sections; a talent-only question should not become a shopping list. The following structure applies to talent recommendations:

1. **Suitable for:** game/variant, environment, spec, scope (general, ordinary dungeons, M+, whole raid, leveling or exact encounter), applicable difficulty/level range, phase/patch and material assumptions.
2. **Recommended build:** source-backed selections, hero tree only where supported, observed complete-build share only for a real sample, and a verified edition-compatible import or selected-build calculator/point allocation as described above.
3. **Alternative talents:** a compact table of supported changes and situations; n/N usage only from a real sample. Include separate verified imports where supported or selected-build links/point allocations for recommended complete alternatives.
4. **Evidence and limits:** version-matched sources, verification dates, import destination/validation and any data gaps. For sampled builds, include dates/weighting/counts, small or mixed samples and whether live API aggregation actually ran.

For a named editorial build, omit invented sample counts and give its stated context and update information instead. For an explicit comparison, show each source separately before drawing conclusions.

First-version visualization is the differences table plus an exact linked interactive build where the source supports one. Do not invent a build URL or imply a generic calculator opens the recommended selections. Do not draw a misleading approximate full tree in Markdown. Full-tree SVG rendering is deferred; any future renderer must use verified current node positions and represent exactly the import string, with class/spec/hero trees distinct.

Before sending, check: game/environment and requested content/source preserved; real samples and denominators disclosed when sampled; historical talents used for sampled builds; whole builds retained; supported import formats sourced and labelled or calculator/point-allocation limitations stated; explanations supported; unavailable data not presented as verified.
