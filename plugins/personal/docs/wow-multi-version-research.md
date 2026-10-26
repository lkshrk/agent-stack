# Supporting multiple versions of World of Warcraft

Research date: **4 October 2026**. The findings below describe the pre-implementation baseline and proposed rollout.

Initial implementation now adds shared [game context](../shared/wow/game-context.md), [existing-provider routing](../shared/wow/versioned-sources.md) and [Markdown progression goals](../shared/wow/progression-goals.md), explicit importer context isolation/migration, and game-bound Retail/MoP live achievement views. It does not add unverified Classic/Forever Collector adapters, modern talent-codec compatibility or automated non-Retail baseline refreshes. Those remain gated on real supported fixtures and sources; see the current skill references for executable contracts.

**Recommendation: keep the existing six skills, add shared game-context and capability rules, and enable data integrations one verified version at a time.** Most quest, reputation, item, profession, leveling and duo planning can be reused. The boundaries that need real changes are player identity, source selection, progression systems, talent representations and automated data refreshes. Disabling Mythic+ alone is insufficient.

## Which versions need distinct treatment

Blizzard currently distinguishes modern WoW, Classic Era, Hardcore, Season of Discovery, Burning Crusade Classic Anniversary and Mists of Pandaria Classic. These are not interchangeable instances of one Classic database. See the [Classic product page](https://worldofwarcraft.blizzard.com/en-us/classic) and [current update notes](https://worldofwarcraft.blizzard.com/en-us/content-update-notes).

| Game context | Useful initial coverage | Important boundary |
| --- | --- | --- |
| Retail | Preserve current help, account setup, achievements, builds and M+ workflows | Existing tested baseline; no regression in Warband/account versus character scope |
| Classic Era | Quests, reputation, professions, purchases, leveling, dungeon preparation and duo routes | Version-specific talents, world geometry and progression; do not promise a native achievement dashboard |
| Hardcore | Era-style help and plans with survival and death consequences considered | Ruleset restrictions affect feasibility; self-found is an additional restriction, not synonymous with Hardcore |
| Season of Discovery | Similar workflows, using that season's class systems and content | Era advice and item IDs do not establish SoD mechanics or current-phase availability |
| TBC Anniversary | Outland quests, reputation, attunements, professions, ordinary dungeons, raids and builds | Identify the Anniversary track, not merely “TBC”; use current implementation and phase evidence |
| MoP Classic | The above plus native achievement planning | Classic-specific changes, achievement credit scope and talent representation require verification |
| Forever | Version-aware help and planning now; tested player-state support as actual beta fixtures become available | Separate product, beta/live boundary, rulesets, Legacy progression and revised world/class systems |

The table describes a proposed rollout, not verified support in this repository. Historical Wrath/Cataclysm records should remain distinguishable from a currently progressing Classic service; an expansion label alone does not establish which service the player uses.

Blizzard describes **Forever as a new permanent game alongside modern WoW and Classic**, with launch announced for **4 November 2026** and beta beginning **17 September**. Treat beta recommendations as build-specific, not launch-verified. [Forever announcement](https://news.blizzard.com/en-us/article/24302093/carve-a-new-path-with-world-of-warcraft-forever), [What’s Next recap](https://news.blizzard.com/en-us/article/24303862/world-of-warcraft-forever-whats-next-panel-recap).

Forever materially changes the context model: players select **rulesets rather than traditional realms**, and ordinary grouping requires the same ruleset and faction. A mandatory realm field would therefore be wrong for some supported identities. Collections/progress also have explicit Hardcore boundaries. [Blizzard ruleset explanation](https://news.blizzard.com/en-gb/article/24302070/choose-your-ruleset-in-world-of-warcraft-forever).

Its **Legacy Challenges award shared points, while characters choose their perks independently**. This belongs in progression planning, but those points must not be added to Retail achievement points or mistaken for one character's unlocked perk configuration. Native achievement coverage beyond that system needs separate verification. [Blizzard Legacy system explanation](https://news.blizzard.com/en-us/article/24307383/get-to-know-the-world-of-warcraft-forever-legacy-system).

MoP Classic has documented native achievements. For earlier Classic variants, use goals/milestones unless the actual client supplies a verified native achievement system; addon completion must stay separately labeled. The historical introduction of achievements with Wrath is useful context, not sufficient proof of every current variant's capabilities. [MoP Classic raid achievements](https://news.blizzard.com/en-gb/article/24223022/mists-of-pandaria-classic-enter-the-terrace-of-endless-spring-raid-now), [Wrath Classic announcement](https://worldofwarcraft.blizzard.com/en-us/news/23793177).

Hardcore self-found explicitly restricts trading, mail and auction-house use. A buyable objective or a duo material-sharing plan must account for that before recommending travel or purchases. [Blizzard self-found announcement](https://worldofwarcraft.blizzard.com/en-us/news/24056985/this-week-in-wow-february-12-2024).

## Resolve context once and reuse it

Proposed minimal shared context:

- **Edition or service track:** Retail, Era, Hardcore, SoD, Anniversary progression, regular Classic progression or Forever. These are internal identifiers; users can describe them naturally.
- **Environment:** live, beta or PTR. Never let a test snapshot update live progress.
- **Content revision:** expansion, phase/season and client build or patch where relevant.
- **Player world:** existing account alias and region, plus realm or ruleset as applicable; preserve verified character identifiers, faction and restrictions.
- **Language:** retain the existing distinction between reply language and game-client language.

Reuse the version when it is clear from the request or established context. **When it is not obvious, simply ask which WoW version the user plays.** This is the preferred fallback; elaborate automatic detection is unnecessary. For example: “Which version are you playing—Retail, Classic Era/Hardcore, Season of Discovery, TBC Anniversary, MoP Classic or Forever?” Offer only plausible choices when context narrows them, and clarify the variant if the answer is just “Classic.” Remember the answer for that character or plan rather than asking again in every skill; users may play more than one version.

Trusted client/export metadata can corroborate the answer. If it conflicts, clarify before importing or giving version-dependent advice. Client folder name or addon interface version must not silently collapse Era, Hardcore and seasonal rulesets into one world. Ask about phase, ruleset or beta/live only when that additional distinction affects the task.

Keep stable identity separate from content revision. A phase or patch update should preserve the same character's history. Progression into another expansion needs an explicit, verified transition; it must not silently reinterpret older observations or discard their provenance.

Use a small, evidence-backed capability table rather than six copies of every skill. Initial capabilities worth representing are native achievements and points, Legacy progression, M+, talent representation, shared storage/progress, grouping restrictions, and available data/waypoint providers. Distinguish **supported**, **unsupported** and **unknown**. Do not ask someone to rescan achievements when the version does not provide them.

## What changes in the current repository

| Existing boundary | Finding | Proposed minimum change |
| --- | --- | --- |
| [Observation importer](../skills/wow-account-setup/scripts/import_saved_variables.py) | `import_data` checks kind/schema/account/region when merging, but not game context; records are keyed by GUID | Add explicit context to snapshots and reject incompatible merges before writing. Preserve character history and partial observations |
| [Source discovery](../skills/wow-account-setup/references/source-discovery.md) | Documentation already calls for account/game/region verification | Make the executable importer enforce that promise; recognize actual supported addon/client combinations |
| [Dashboard model](../skills/wow-achievement-dashboard/assets/obsidian/_System/Data/achievement-model.cjs) | Numeric achievement IDs, `ach-N` anchors, required points, achievement/meta kinds and achievement-linked purchases | Keep the current dashboard for verified achievement datasets. Use plain Markdown for other goals initially; do not create fake achievements or zero-AP records |
| Same dashboard model | URL validation supports optional locale but only Retail-style entity paths | Bind accepted version/database paths to the dataset's game context; an arbitrary optional prefix is not sufficient validation |
| [Duo skill](../skills/wow-duo-world-tour/SKILL.md) | Shared unfinished achievements and per-player AP shape the route, with useful one-player detours | Permit quest, rep, profession, item and supported Legacy objectives; retain individual credit and grouping checks; omit AP where inapplicable |
| [Spec skill](../skills/wow-spec/SKILL.md) | Modern defaults, hero trees and import strings shape the workflow | Resolve the client's actual capabilities first. Use a verified calculator/build allocation when that version has no supported import format |
| [WCL helper](../skills/wow-spec/scripts/wcl_builds.py) and [talent exporter](../skills/wow-spec/scripts/export_wcl_talents.py) | Retail endpoint and modern Raidbots/Blizzard talent codec | Keep these Retail-only until separate provider/data/codec compatibility has passed tests. Generic Classic build advice need not wait for aggregation support |
| [Season setup storage](../skills/wow-spec/references/season-setup.md) | Already partitions bundles and cache by game/season | Extend this existing layout to verified edition/revision bundles; do not introduce another cache system |
| [Source checker](../scripts/check_wow_season_updates.py), [publisher](../scripts/apply_wow_season_refresh.py), [workflow](../.github/workflows/wow-season-updates.yml) | One Retail bundle and Retail tooltip assumptions | Explicitly select supported bundles and their source/database context. Preserve publisher rejection of game/season migration |

Prefer a **single game context per observation snapshot, dashboard dataset and guide collection** initially. That allows existing numeric IDs and task anchors to remain useful inside an isolated dataset. Globally, identify entities by database/edition + entity type + ID, and retain revision provenance. Never join two editions merely because an item ID, achievement ID, character name or GUID matches.

Existing Retail records need a deliberate migration: known repository-generated Retail data can receive an explicit Retail context; ambiguous imported records need context resolution before merging. Adding `retail` by default to every contextless import would hide the current risk.

## Reuse providers before adding new collectors

**User preference: stay with the providers already used by the Retail skills wherever possible.** Start with their matching Classic/Forever sections, databases and endpoints, preserving each skill's existing source preference. This applies to gameplay pages and build guides as well as APIs and addons. Audit the existing providers' version coverage before considering alternatives; do not infer that a provider lacks support because its Retail URL is unsuitable.

For each existing provider, record the verified edition-specific entry point, covered content and any concrete gap. Use an alternative only for a required gap, and explain that fallback. Do not silently substitute Retail content when the requested edition is missing. The addon/API findings below are compatibility evidence and possible fallbacks, not a proposal to replace the current guide sources or add collectors by default.

These are source findings as of the research date, not guarantees that this repository's importer supports their formats.

| Source or tool | Evidence | Recommended handling |
| --- | --- | --- |
| WoWthing Collector | Current source declares a Retail interface and `WWTCSaved` | Retain existing Retail adapter; no demonstrated Classic/Forever parity. [Collector TOC](https://raw.githubusercontent.com/ThingEngineering/wowthing-collector/main/WoWthing_Collector.toc) |
| All The Things | Published releases include Classic-family and explicit Forever beta fixes, including achievements and maps | Strong candidate for real sample fixtures. Addon support does not prove that our ATT identity join understands every branch. [ATT releases](https://github.com/ATTWoWAddon/AllTheThings/releases) |
| DataStore | Author lists multiple Classic variants and Forever; designed to store observations for other addons | Inspect installed modules and actual fields before enabling a narrow adapter. Core support is not coverage of every profession, quest or inventory field. [Author page](https://www.curseforge.com/wow/addons/datastore) |
| Syndicator | Author lists multiple variants including Forever | Potential inventory source only; do not infer complete player-state coverage. [Author page](https://www.curseforge.com/wow/addons/syndicator) |
| TomTom | Author lists Retail and several Classic variants | Verify the installed branch and command syntax, then the actual map/floor/coordinates. Forever support was not verified in the inspected listing. [Author page](https://www.curseforge.com/wow/addons/tomtom) |

ATT's Forever fixes include different Stormwind map geometry. Even when the location and numeric entity IDs look familiar, an Era waypoint may be wrong in Forever. Keep map evidence with the client version. If the waypoint mechanism is unsupported or unverified, provide sourced access instructions instead of a plausible but untested command. [ATT release notes](https://github.com/ATTWoWAddon/AllTheThings/releases).

Blizzard API coverage needs endpoint-by-endpoint checks. Candidate namespace families include Retail, `classic1x`, `classic`, and Anniversary's `classicann`. A developer's published 2026 response shows `profile-classicann-eu` and `dynamic-classicann-eu` while also reporting a character-profile 404. This is firsthand response evidence, **not a Blizzard guarantee of full profile coverage**. Official developer documentation could not be fully enumerated in this research, and no Forever namespace was verified. [Anniversary response example](https://us.forums.blizzard.com/en/blizzard/t/404-retrieving-character-profile-on-anniversary/58401), [namespace discussion](https://us.forums.blizzard.com/en/blizzard/t/tbc-anniversary-namespaces-and-data-refreshes/57155).

Warcraft Logs also has separate sites: Era/Hardcore on `vanilla.warcraftlogs.com`, SoD on `sod.warcraftlogs.com`, current TBC Anniversary on `fresh.warcraftlogs.com`, and MoP progression on `classic.warcraftlogs.com`. Resolve and verify the provider for the chosen service; these host names are not timeless expansion identifiers. Preserve provider site with report identity. Forever WCL support was not verified. [Provider routing guidance](https://www.archon.gg/classic-mop/articles/news/fresh-vanilla-on-warcraft-logs), [SoD API schema](https://sod.warcraftlogs.com/v2-api-docs/warcraft/character.doc.html), [TBC reports](https://fresh.warcraftlogs.com/guild/reports-list/780000), [Classic site](https://classic.warcraftlogs.com/), [API documentation](https://www.warcraftlogs.com/api/docs).

WCL's documented Classic `talentPoints` contains three tree totals. That is insufficient to reconstruct individual selections, let alone produce a valid import string. Provider access, complete build evidence, and a compatible export format are three separate capabilities. [CombatantInfoEvent schema](https://www.warcraftlogs.com/scripting-api-docs/warcraft/interfaces/RpgLogs.CombatantInfoEvent.html).

Wowhead likewise has edition-specific paths, for example `/classic/`, `/tbc/` and `/mop-classic/`. Carry the verified database path through entity links, tooltips, validation and fingerprints; a locale change must not drop it. A Classic database path alone still does not establish SoD-specific mechanics. [TBC entity example](https://www.wowhead.com/tbc/npc=13278/duke-hydraxis), [MoP entity example](https://www.wowhead.com/mop-classic/item=27790/mask-of-penance).

## Proposed delivery order

1. **Shared context and useful answers.** First audit the existing Retail providers' matching Classic/Forever sections and reuse them wherever they cover the request. Add one bundled game-context reference, adjust intake/defaults in all six skills, and resolve version-specific sources and capabilities. Support Classic/Forever help and build guidance without claiming working automated imports. Allow plain Markdown quest/rep/profession/item/Legacy plans and duo routes; keep unsupported achievement features out of those outputs. Enable automated non-Retail imports only after stage 2 establishes context isolation and tested adapters.
2. **Safe personalized state.** Add context and merge validation to observations. Start with one real Classic sample and one real Forever beta sample from supported existing addons; expand only the fields actually proven. Preserve manual progress and differentiate unsupported systems from missing data.
3. **Verified build and refresh bundles.** Add one chosen Classic edition's baseline and sources, validate talent output, then generalize the workflow's bundle selection. Reuse the existing validation, staging, CI and release gates. Log edition, revision, source coverage and the run/skip reason per bundle. Initially keep publication serialized because it uses one staging branch and a moving default branch; parallel fetches must not become competing publishers.
4. **Broader dashboard only if wanted.** Design a versioned goal schema for quests, reputation, professions, collections and Legacy. Points become typed/optional metrics. Preserve existing Retail history and links; do not route non-achievement goals through achievement parsing. This is a distinct feature, not a prerequisite for useful Classic plans.

Keep the current skill names during the first stages to avoid breaking installed workflows. Their descriptions can advertise the broader goal planning where supported. Renaming the achievement skills or creating one skill per expansion is not required to establish compatibility.

## Checks required before claiming support

- Same account/character/entity ID across editions or beta/live never merges or reuses an incompatible cache.
- A normal patch or phase change preserves identity/history while updating applicable content evidence.
- “Classic” ambiguity is resolved when material; an ordinary Classic dungeon request never invokes Retail M+ ranking.
- Unsupported achievements still yield a useful goal plan, without fabricated AP or repeated requests for an impossible scan.
- Localized game-specific entity links retain both locale and database context; a correct numeric ID in the wrong database is rejected.
- Classic talent advice never runs the Retail codec. Incomplete tree totals never become invented individual selections or imports.
- Hardcore/self-found and duo faction/ruleset restrictions affect purchases, trading, route feasibility and credit.
- Forever shared Legacy points and individual perk allocations remain separate; beta observations do not update live state.
- Map/waypoint fixtures come from the supported client; source/build changes can invalidate an old route.
- A failed or unsupported bundle cannot overwrite another bundle or advance its verification date.

The first useful milestone is **version-aware help, builds and plain Markdown progression plans, with strict player-state separation**. Full dashboard parity and automated build aggregation can follow actual demand and verified fixtures. This keeps most of the existing skills useful across versions without claiming mechanics or integrations that have not been tested.
