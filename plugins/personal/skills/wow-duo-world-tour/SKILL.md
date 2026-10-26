---
name: wow-duo-world-tour
description: Create or refresh a joint WoW achievement or progression tour for two players across Retail, Classic variants and Forever. Prioritize shared goals and worthwhile nearby objectives for either player, with compatible game/ruleset/faction, per-player credit, realistic timing, version-specific sources and supported waypoints. Save to Obsidian or plain Markdown.
---

# WoW Duo World Tour

## Resolve game and goal type first

Follow [game context and supported capabilities](shared/wow/game-context.md) for each player, asking the version when unclear, and reuse [versioned sources](shared/wow/versioned-sources.md). Verify they can group under the actual ruleset/faction before routing. For non-achievement quest/rep/profession/item/Legacy goals, follow [progression goals](shared/wow/progression-goals.md) instead of the achievement/AP-specific flow below. Keep separate progress and credit, and omit unsupported points and achievement schemas. Retain source freshness, timing and preservation rules.

Plan an enjoyable, executable achievement session for two players. Shared unfinished achievements form the route's backbone; short nearby objectives benefiting only one player are welcome when the detour is worthwhile. Do not restrict the search to the intersection of their incomplete lists, or simply concatenate two solo plans.

A broad world-tour request needs a comparison across available categories and regional clusters before choosing a route. Do not stop at the first convenient seasonal achievement or limit discovery to an existing curated solo list. Inspect unresolved meta prerequisites against public account records too. If the available work exceeds one session, publish a regional itinerary with independently playable legs and recommend the strongest first leg; do not silently shrink the entire search to an assumed short budget. A genuinely narrow result needs evidence explaining why other candidates are unsuitable.

This skill creates the tour, not two replacement account dashboards. Existing `wow-achievement-plan` datasets can supply research when available, but this skill must also work independently.

## Required data refresh before planning
Before selecting, ranking, routing or estimating personalized work, refresh the local account data; if the refresh fails or freshness cannot be established, stop and ask the user. Follow [shared/wow/data-refresh.md](shared/wow/data-refresh.md) exactly.

## Choose the output
Default to plain Markdown unless the user chose Obsidian or an established workflow exists. Follow [shared/wow/output-format.md](shared/wow/output-format.md).

## Resolve the pair and session

Use the request and existing plans for both account identities, region/game version, available characters, starting locations, session budget, travel access, spending limits and selected content categories. Ask only for missing inputs that materially change the route. A single character name does not establish all alts or their capabilities.

Reuse selected categories: quick wins, buyable, grindable, long-term projects, and short tasks needing help; allow any subset or all. Long-term work contributes a concrete stage feasible today, not its full eventual AP. Establish whether a third player is acceptable; do not assume two players satisfy every group mechanic.

For every request, follow [request, client and source languages](shared/wow/language.md). Respect the second account owner's sharing choices: use supplied exports, public profiles or authorized access, without requesting passwords or publishing their full account data. Honor the supplied output folder or the established Obsidian/Markdown destination; ask where to save only when neither is inferable.

## Compare observed state, then research

Reuse each account's setup record and identity map; use `wow-account-setup` for essential gaps when installed. Anonymized website records or Collector records missing names are not an automatic request for fresh logins: existing authorized saves may provide an exact GUID-to-name/realm join, such as ATT character records. Verify that join separately for each account and keep public redaction distinct from missing observations. Do not infer website database IDs from Collector GUIDs or mix the pair's source identities.

Keep separate dated account snapshots and per-character capabilities. Match by achievement ID, with completed/incomplete/unknown/unobtainable state per account. Profile-absent entries are unknown. Preserve per-character criteria where the source does not establish shared progress. Do not transfer one account's completion, currency, reputation, profession or unlocks to the other.

Public Armory category pages may include structured initial-state JSON containing completion dates and criteria. Parse the JSON payload as data, never execute embedded JavaScript. Retain successful categories when another fails and disclose coverage. A recorded achievement completion takes precedence over unchecked subcriteria on that same record; do not schedule an already-earned achievement merely because its criteria look unfinished. Check incomplete criteria in game before committing to a route.

Classify candidates as **Both**, **Player A only**, **Player B only**, or **Verify**. A shared candidate requires supported incomplete state on both accounts. For an unknown second account, an A-only recommendation means only A's benefit is verified, not that B has completed it. Distinguish progress-only objectives from immediate achievement awards.

Research current mechanics and remaining criteria using direct Wowhead pages and dated evidence. Verify difficulty, faction/cross-faction compatibility, phasing, quest prerequisites, lockouts, travel access, group size and whether credit is shared, individual, alternating or requires repeats. A lootable treasure, interaction or quest is not automatically credited to both grouped players.

Show the named eligible characters for each player, recommended pair, missing requirements and observation dates. Evaluate all restrictions on the same executing character. An alt is an alternative executor, not an extra simultaneous player. Never replace one player's character with an alt mid-route without accounting for travel, login, access and group-rejoining costs.

## Build the joint route

Before each distinct route method, show **Repeatability / limits**: one-time credit, repeatable progress, daily/weekly cap, cooldown/lockout, rotation/spawn availability or unknown. Include resource/RNG and group constraints. Check reward eligibility and quota/reset scope separately for each account and executing character; one player may already have used a reward while the other has not. Distinguish repeatable activity from rep/loot/currency/achievement credit, including standing ceilings and reduced/zero rewards after caps. Never assume switching alts or pairing up bypasses an account limit. Unknown limits remain conditional; show the next independent stop when credit is exhausted.

Keep rotations, required delve stories and seasonal/weekly availability checks in their own condensed table, ordered by expansion and region, with per-account benefit, full TomTom command, required target/story, and an accurately scoped check command. Do not put them into the core until their current availability is confirmed. Map opening is manual inspection; NPC targeting is local; quest completion is not evidence of availability. Render table commands as real copyable code blocks; never raw coordinate pairs. Ordinary instance lockouts are preflight checks and are not themselves a reason to call an achievement a rotation.

1. Find verified shared opportunities, including mechanics the two players can help each other complete. Cluster by accessible zone, instance, purchase/vendor trip and travel connections rather than expansion order.
2. For each proposed stop, inspect nearby remaining criteria for **both** accounts. Include worthwhile one-player finishes even if the other already completed them. Compare incremental detour/setup/interaction time with the benefit; travel already required by the shared route should not be charged twice.
3. State who benefits, what the other player does, and how much additional time the stop costs. Prefer helping or a nearby parallel objective over unexplained waiting. Mark larger or uncertain detours optional, with an explicit time/condition to skip them. Do not invent a fixed detour threshold; reuse the players' preference or state a practical assumption.
4. Keep the tour balanced. Report AP and useful progress for each player, plus estimated waiting time. Prefer jointly useful routes, then low-cost individual additions; do not maximize a combined score by making one player spend the session helping the other unless that is their preference.
   Compare the recommended character pair with recorded alts when a mechanic needs a particular capability. Name the actual suitable alt and budget its setup/travel; do not recommend an unreliable crowd-control method merely to keep the mains together.
5. Make optional branches genuinely skippable. A prerequisite for a later core stop cannot be optional; either keep the dependency in the core or mark the dependent branch optional too. Include rejoining instructions after parallel or individual objectives.

**Example of the intended decision:** a shared route already visits Ardenwald, and A needs two nearby treasures to finish an achievement B has completed. Verify the exact two criteria, availability and locations; add the short detour as A-only, give B a helper/parallel/wait role, and award forecast AP to A only. Do not add a distant multi-hour treasure sweep merely because it is in the same zone. Ardenwald is illustrative, not a mandatory destination.

## Time and rewards

Use conservative whole-session estimates with ranges, source/date, confidence and prerequisites. Include learning, preparation, shopping, both players reaching the start, travel/loading, coordination, required repeats, waits, retries and verification. Separate recruitment, ready-group duration and calendar gates. Unknown recruitment or unbounded RNG cannot guarantee a session fit.

For simultaneous independent work, elapsed time is the longer branch plus coordination/rejoining, not the sum; use this only when independence and the route are verified. For actions needing both players together or repeated credit attempts, include the sequential work. Show core duration, optional incremental durations and the total for the chosen version. Unknown effort belongs in an optional/verify queue, not a zero-minute stop.

Calculate AP separately per account using unique `(account, achievement ID)` pairs. The same 10-point achievement awarded to both is A +10, B +10, combined +20. Two stops contributing to the same meta do not award it twice. Include a meta only when all remaining requirements are satisfied for that account; helping B does not award A a meta. Show direct, additional meta, conditional and progress-only rewards separately. A combined AP figure is a sum across two accounts, not a new single-account total.

## Dedicated tour output

For an established collection, register the tour on `Start Here.md` with a one-line purpose and both accounts' snapshot dates. Keep playable tours in `Plans/`; account datasets go in `_System/Data`, canonical progress in `_System/Task Records`, audits in `_System/Research` and checks in `_System/Validation`. Put technical evidence links at the end of the guide, using a collapsed callout only in Obsidian. Temporary agent work stays outside the output folder; retain personal notes and useful older tours, archiving only superseded generated material under `_System/Archive`. Never discard canonical progress while organizing files.

For Obsidian output, if `wow-achievement-dashboard` is installed, its portable bundle includes an optional Duo World Tour note template. Populate that template with the researched pair/route; plain Markdown output works independently of that bundle. Keep each account's normalized achievement dataset separate; the single-account dashboard schema must not merge identical achievement IDs across players.

Create/update one distinct note such as `WoW/Plans/<pair> — <tour>.md`, adapting to vault conventions. Reference existing solo plans instead of overwriting them. Store stable pair/tour IDs, snapshot dates, selected categories, recommended characters and source references; use player aliases if requested.

Open with the character pair, meeting point, first action and first-stage budget. Use **Before the tour — check once** for access, required consumables and helper checks, then **Tour — start here** for actual route actions. Keep optional branches and waiting work explicitly separate; place detailed time/AP tables after the playable route. Explain unfamiliar quest branches at their first affected stop under **Prerequisite check**, label their actions and give the independent next stop if blocked. Then provide the ordered route with:

- Stop, zone and access instructions; core/optional status and skip/rejoin conditions.
- Linked achievement name and ID, with **Both / A only / B only** benefit labels and distinct per-account completion fields/checkboxes. Give progress-only criteria clear labels.
- What A does and what B does, including separate interactions or repeat attempts needed for credit.
- Every displayed navigation coordinate must be a complete `/way #MapID X Y Label` command using a verified numeric UI map ID and a label following the language guidance inside a fenced `text` block. Never use a zone-name selector or implicit current map. Separate X and Y with spaces, never commas. One command per line; no quotes, bullets, blockquote prefixes, Markdown styling or prose inside the block. No bare coordinate pairs anywhere, including tables, summaries and retained history, even if another section already has correct commands. Link table cells to the relevant command block. Verify exact map/floor and timeline access for both players; map IDs do not switch quest phases. If the ID/location is unverified, show waypoint unavailable and sourced access instructions rather than guessing.
- Direct Wowhead achievement ID links everywhere achievements are named, including metas, with language fallback as described above; link relevant NPCs/items/quests too. Internal note links do not replace Wowhead links.
- Conservative stop duration, incremental detour cost, AP per player, eligibility, and blockers.

Include an ordered route-wide TomTom block and a concise session checklist. Name stops with a verb and target, and refer to skipped/resumed destinations by those names; keep internal IDs out of reader-facing titles and waypoint labels. Preserve per-player checkmarks and point to the next unchecked action rather than restarting the route after a style update. Exact shopping quantities must distinguish separately consumed items from verifiably reusable/shared items and specify the buyer/user and transfer restrictions. A single shared stop checkbox is not proof both accounts earned the achievement.

Give checklist tasks plain step numbers and reuse those exact numbers in waypoint labels (`1. Clear destination name`, `2. ...`). Multiple approach/destination pins for one task share its number with descriptive suffixes and appear in travel order. Restart the shared numbering per playable leg; do not separately number pins and checklist rows. These visible step numbers are distinct from internal IDs and must survive naming simplification.

Make stops filterable using existing vault tags or task-level metadata: category memberships, beneficiary (`both`, `a`, `b`), core/optional, total planning effort, group/helper count, zone, eligible executor and verification status. Prefer numeric duration fields plus consistent effort bands (`<15`, `15–<30`, `30–<90`, `90–240`, `>240`, unknown minutes). Preserve user tags. For unsupported interactive views, provide readable sections and an index rather than requiring new plugins.

Do not contact the other player or send an LFG request automatically.

## Maintain generated artifacts
Update existing guides in place and clean up only what you generated. Follow [shared/wow/generated-artifacts.md](shared/wow/generated-artifacts.md).

## Refresh and verify

Attempt to refresh and validate both accounts before joint planning. If only one succeeds, preserve the other's prior snapshot and ask the user for help/advice on the failed refresh; do not calculate a new joint route or benefit labels from mixed fresh/stale data unless the user explicitly chooses that dated fallback. Independent method research can continue. After verified refreshes, recalculate benefit labels: a Both task may become B-only after A completes it. Remove a stop from active work only when neither player still benefits or it is explicitly skipped; retain history and notes. Preserve manual completion separately from game-confirmed AP and flag conflicting evidence. Do not clear data when a profile omits an ID.

Read back the published artifact. Scan all affected notes and retained history for bare coordinate pairs, commands outside `text` fences, unbalanced fences, missing map/zone or label, and coordinates outside 0–100; correct them before finishing. Check that every core stop works for the recommended pair, credit rules and helper counts are established, AP is unique per account, both-player costs fit the budget, optional dependencies are consistent, and TomTom/Wowhead references resolve. Distinguish a researched route from an in-game-tested route. Report the saved tour link, core duration, each player's forecast and unresolved checks.
