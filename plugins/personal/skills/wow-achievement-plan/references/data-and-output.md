# Dataset and output contract

Use the root's `_System/Data/` for JSON and useful source snapshots, excluding credentials. Playable guides belong in `Plans/`; canonical progress in `_System/Task Records/`; detailed audits/coverage in `_System/Research/`; validation in `_System/Validation/`. Adapt existing paths together with their consumers when migrating. Null means unknown; zero means measured zero. Keep IDs stable across locale and name changes.

## Minimum useful record

Snapshot metadata: plan/account identity, character, realm, region, game version, selected search categories, focused goal and constraints (expansion, zones, target achievement IDs, activities, exclusions such as no time gates), update operation/scope, fetched-at timestamp, sources, reported account AP, calculated completed AP, reconciliation result, coverage limitations. Keep a dated character roster and capability evidence; distinguish unobserved alts from known ineligible ones.

Per achievement:

- ID, verified display name following the skill's language guidance, base points, direct Wowhead URL, game category, expansion, and search-category memberships (`quick-wins`, `buyable`, `grindable`, `long-term`, `needs-help`).
- Observed state (`completed`, `incomplete`, `unknown`, `unobtainable`), source/time, account versus character scope.
- Criteria IDs and labels, required/current amounts, completed criteria, prerequisites, meta parent/child IDs.
- Verified numeric UI map ID, floor and sourced coordinates, rendered only as `/way #MapID X Y Label` in fenced `text` blocks, with the label following the skill's language guidance; executing character and travel/timeline requirements. Use spaces between coordinates; no zone-name selector or implicit current map. No bare pairs or quoted/inline commands, including history and tables; link tables to copy blocks. Unverified IDs/locations get a waypoint-unavailable note instead of guessed coordinates. Map IDs do not prove the required quest phase is active.
- Group/PvP, profession/recipe, currency/gold, daily/weekly/lockout, RNG and phasing requirements.
- Method-specific activity repeatability versus reward-credit rules; quota/reset/scope, standing ceilings or diminishing returns, availability/resource/RNG limits, source/date and verification status. A concise `limits_summary` belongs in the visible task and next-action text, not just hidden metadata.
- Verified minimum total players and additional helpers, helper roles/actions and eligibility, ready-group duration, recruitment/coordination estimate or unknown, and current helper availability. Store filter tags/fields using the contract below.
- Per-character eligibility: character identity, eligible/conditional/unknown/ineligible, required thresholds, observed capabilities and date, missing setup, and recommended executor. Keep buyer versus user roles where different.
- Estimated active, travel/setup, unattended/waiting minutes and calendar days; session range and conservative planning duration, step breakdown, justified overlaps, evidence/date/confidence, starting point and prerequisite assumptions. Actual durations and overrun reasons when supplied; gold estimate/date/region.
- Research status and evidence links; blockers and verification notes; route IDs and canonical task location.
- Action/session references, original estimates and actual-play observations when supplied; actionable blockers with the exact missing fact, character, next check and evidence required to clear them. Session-action completion is separate from achievement completion.

Route/action records reference achievement IDs, list verified additional reward IDs, distinguish conditional reward IDs, and record shared time once. Store base points in achievement records; derive route totals from the unique reward set. Manual task completion and API completion are separate fields or separately named sources.

Do not fabricate values to fill the schema. Unknown effort belongs in an unranked/verify queue until researched, not in a zero-minute bucket.

## Repeatability and limits

Every method in a help-derived guide, canonical todo or route carries a visible **Repeatability / limits** summary before its instructions. Use plain descriptions: **one-time credit — doable now**, **repeatable progress**, **daily/weekly capped**, **cooldown/lockout**, **rotation/event/spawn-dependent**, or **unknown**. Add material/access/player/RNG constraints separately; several may apply. A shared section label can cover equivalent tasks, with exceptions beside the affected step.

Store and explain activity repetition separately from each relevant reward: reputation, loot, currency, criteria and final achievement credit. Record the known quota, reset cadence and character/account/Warband/difficulty scope; reputation ceiling, first-time/daily bonus, diminishing returns or zero credit after a cap; resource costs and availability dependencies; evidence/date and unresolved limits. Missing cap data means unknown, not unlimited. Do not assume alts reset an account limit.

For example, repeatedly turning in a bundle can remain a useful **material** grind after its daily **reputation** reward is exhausted. Conversely, a fixed treasure or finite quest can be completed now once and belongs in a no-wait tour, even though it is not a repeatable rep farm. Available RNG attempts do not guarantee the drop or completion by a deadline.

Use these distinctions when selecting work: a no-wait request excludes required reset/camp waits and unresolved credit eligibility. Keep useful current partial progress explicit, with blocked portions separate. Stop a rep method at its verified standing/cap and show the next verified method or reset. Do not turn a source's bare “repeatable” label into an uncapped forecast.

## Note structure

Plain Markdown is the default for a new user without an output preference. Mention that Obsidian is also available, then proceed without requiring a format decision. Preserve an existing choice and ask only for a missing save location. Do not create Obsidian templates or inspect plugins for the default Markdown path.

For plain Markdown, write the requested checklist directly in the selected folder with relative standard links and normal checkboxes; use heading links for canonical targets. A one-off plan needs no separate index, dashboard, task-record files or directory tree. Apply the folders below to maintained collections as needed. Obsidian-only tags, queries, block anchors and collapsed callouts are optional for Obsidian and are not plain Markdown requirements. A requested static dashboard/index shows both generation and source dates; use a normal supporting-details section at the end of Markdown guides.

Frontmatter: title, character/realm/region, status, goal, selected categories, snapshot date, source dataset path, and tags. Avoid absolute paths so notes remain portable.

For a focused goal, open with the executing character, starting point, first task and first-stage budget, then make the page hierarchy explicit:

- **Before the tour — check once:** brief prerequisites and what to skip if each is unmet. Checks are not achievement-completion tasks.
- **Tour — start here:** actual ordered actions. Nest zones beneath this heading and name chunks by what to do, such as **Tour actions — collect four central treasures**. Number each checkbox plainly and give it a verb and target, such as **1. Collect Offerings of the Chosen**. Restart per self-contained section and use those same numbers on the waypoint labels. Avoid codes such as Z1a or V2k; numbers plus names are sufficient. Refer to destinations by their step number and descriptive action name, using heading links where appropriate. Put the first checkbox immediately after short checks, not after statistics or a mechanics essay.
- **Optional grinds — separate activities** and **Waiting and blocked work — excluded from the tour:** visibly outside the ordered core.
- **Reference — time estimates and rewards** and supporting evidence come after the playable instructions.

Explain an unfamiliar NPC/activity where it first matters: what it is, which achievement it serves, and whether its unlock is required for the whole route or only one branch. Use a local **Prerequisite check — [unlock]** heading before the affected stage, followed by **Tour actions — [stage]**; put shared quest mechanics under **Quest instructions — only for [labeled steps]**. For example, Jani gives the Get Hek'd quest branch, not a prerequisite for unrelated treasures. Label every affected checkbox **Jani quest** and identify the next independent stop if access is missing. Do not leave an unclassified "Jani" paragraph between the opening and the first task. Use the same distinctions with standard headings/links in plain Markdown. Keep stable IDs in supporting records or existing anchors, not task titles or TomTom labels. Preserve checkbox states when restyling and update the opening to the next unchecked action; manual route progress does not prove game achievement completion.

Combine compatible quest, treasure, exploration and farming actions within each stage. For "just do it" requests, only include work available for productive progress now; no rare camping, long respawn waits or daily/rotation detours. Link actions to canonical achievements; keep action progress separate from achievement completion. Label ungated steps toward a still-gated achievement as partial progress, excluded from completion/AP forecasts. Keep waiting requirements linked to the affected achievement, with blockers, cadence/uncertainty and checks; exclude their waits from main-route estimates. Category filters remain secondary views, not execution order. Preserve the focused constraints on refresh and shorter-session generation.

For a category-led plan, render selected categories in this order, omitting unselected categories from new recommendation views:

1. `⚡ Quick Win TODO List`
2. `🛒 Buyable — Shopping List`
3. `🔁 Grindable — Session Plan`
4. `📅 Long-Term Projects — Structured Paths`
5. `🤝 Short Tasks Needing Help`

Add `🌍 World Tour` mini-tours for geographically related selected tasks, with planning duration, unique AP and ordered TomTom commands. Passive opportunities link to their canonical tasks. Effort tiers are secondary labels/views: under 15, 15–<30, 30–<90, 90–240, and over 240 planning minutes. They do not replace the five search categories or collapse long-term paths into a deferred bucket. Label total-project effort separately from the next repeatable session and calendar span.

Checklist titles and waypoint labels share plain `1.`, `2.`, `3.` task numbers within each self-contained section. If one task needs entrance and destination pins, both retain its number and use clear names such as `4. Dazar's Chest - entrance` and `4. Dazar's Chest - chest`; list them in travel order. A non-travel task may have no pin. Never number only the waypoint block or use separate counters that diverge from the task list.

## Category deliverables

**Quick wins:** use the chosen session budget and conservative complete-job estimates. Separate immediately executable tasks from conditional opportunities; low active time alone is insufficient.

**Buyable:** publish both per-achievement requirements and a consolidated buy list. Each line needs exact item/currency ID and Wowhead link, required quantity, verified owned/usable quantity, remaining buy quantity, purchase source (AH or named vendor with Wowhead link and TomTom command), unit price, line total, region/date, buyer/user character, binding/transfer restrictions and remaining steps after purchase. Unknown inventory or price is unknown, not zero. Currency requirements need exact units and acquisition path, not invented gold conversions. Check collection duplicates and prerequisites before recommending a purchase.

Consolidate across selected tasks by actual consumption and reuse: add quantities for separate consumptions; share an item only when one purchase verifiably satisfies both tasks. State allocations, avoid spending the same character-bound currency balance twice, and show the total cost/range and conditional lines excluded from that total. Purchasing alone must not imply learning/using the item or gaining AP.

**Grindable:** specify the remaining target, ordered finite steps or repeatable activity/route, per-session duration and output, total effort range, eligible executor, prerequisites, turn-ins and stop condition. Quest chains, exploration and treasure collection need remaining objectives and geographic/dependency order; repeatable farms also need units per run/hour with evidence and estimated session counts. Distinguish immediately progressable work from daily/weekly caps and hidden prerequisite gates; for a no-time-gates request, keep gated paths out of the executable list and explain exclusions. Otherwise link gated portions to a long-term path. For RNG, show attempt budgets and uncertain completion rather than a guaranteed number of runs.

**Long-term:** give an ordered milestone/dependency path from current state to completion, not merely a time-gated tag. Each stage lists the executing character, unlocks, exact remaining reputation/currency/material amounts where known, acquisition activities, rewards/rates and evidence, caps/reset cadence for the user's region, per-visit effort, next action, and a completion check. Separate one-time setup, daily/weekly routines and final purchases/turn-ins. Forecast a calendar range with start date and assumptions only when rates/caps are established; flag rotations or unknown income. Account for currency spent on earlier unlocks before budgeting later purchases. Show what can run in parallel and what must wait; do not invent real-world due dates for optional routines.

**Short tasks needing help:** state why help is required, minimum total players, additional helpers beyond the executing player, roles/classes/faction/access constraints, what each person does, location/TomTom commands, difficulty/lockout and failure conditions. Show ready-group duration (including setup/travel), recruitment/coordination separately, and full planning duration or unknown. Distinguish verified helpers available now from a group that still needs arranging. Known alts are eligible executors, not simultaneous helpers unless separate-player/account availability is established. Include a short copyable LFG/friend request with achievement Wowhead link, helper count and expected commitment; draft it only, do not contact players. Bundle compatible achievements for the same group without double-counting rewards.

Each canonical task needs a direct Wowhead achievement-ID link, base AP, additional effective AP and linked reward IDs if applicable, planning duration and time breakdown, gold when relevant, concise execution instructions, and prerequisite warnings. Display eligible character names and conditional alternatives with missing requirements for restricted tasks. Provide copyable TomTom commands for locations and exact Wowhead NPC/item/quest links for instructions involving them. Include estimate evidence and unknowns. Reuse tags such as `#quickwin`, `#gold`, `#worldtour`, `#instance`, `#profession`, `#collections`, `#pvp`, `#passive`, `#timegated`, `#rng`, and `#defer` when useful.

For maintained collections, put detailed evidence and the full coverage ledger in `_System/Research/`, linked from the bottom of the guide; use a collapsed callout only in Obsidian. Register the guide on an existing `Start Here.md` with its purpose and source date. Add Tasks/Dataview views only for selected, supported Obsidian output. Do not require a plugin installation to make the checklist usable.

## Filter metadata

Use the vault's existing equivalents when present; otherwise apply these consistent tags to each canonical achievement task. Plain Obsidian tags work without an extra plugin. Retain structured values in the dataset for numeric/range filters, multi-character eligibility and unknown states; tags are derived views of that data, not a second source of truth.

| Dimension | Default tags / fields |
| --- | --- |
| Search categories (multi-valued) | `#wow/category/quick-wins`, `#wow/category/buyable`, `#wow/category/grindable`, `#wow/category/long-term`, `#wow/category/needs-help` |
| Total planning effort (one value) | `#wow/effort/under-15m`, `#wow/effort/15-30m`, `#wow/effort/30-90m`, `#wow/effort/90-240m`, `#wow/effort/over-240m`, or `#wow/effort/unknown` |
| Players required | `#wow/players/solo`, `#wow/players/group`, or `#wow/players/unknown`; numeric `players_total` and `helpers_needed` fields |
| Restrictions / properties | Relevant `#wow/requires/reputation`, `#wow/requires/profession`, `#wow/requires/unlock`, `#wow/timegated`, `#wow/rng`, `#wow/pvp`, `#wow/gold`, `#wow/passive` |
| Context | `#wow/expansion/<stable-slug>`; structured game category, zone, eligible character identities and recommended executor |

Effort boundaries are exactly `<15`, `15–<30`, `30–<90`, `90–240` inclusive, and `>240` minutes, derived from the conservative total planning estimate. Unknown total time gets `unknown`, even when the ready-group action is brief; keep `ready_group_minutes` and recurring-session effort as separate fields. Calendar duration is separate from effort tags. Confirmed soloability is required for the solo tag; absence of group data is not evidence.

Preserve user-created tags and record which tags the skill owns. On refresh replace stale generated effort/category/restriction values and rebuild filters without accumulating contradictory tags. Avoid creating a tag per character; use exact region/realm/name identities in structured eligibility data and the displayed text. Provide useful supported views, such as selected category + effort band + group/solo + eligible character. Within each multi-select dimension use OR; combine different dimensions with AND. Unknown values remain visible in an explicit unknown/verify filter rather than silently qualifying for numeric limits. For portable Markdown, render a compact filter index and prefiltered sections; do not claim unsupported interactive controls.

## Completion report

Report note/data paths, selected categories, source date and account AP; known unfinished point-bearing count, in/out-of-scope count, screened count, researched count, and unknown coverage separately. Include relevant results for the chosen scope:

- Quick Win count, unique AP, planning duration and active/setup/travel/waiting breakdown, plus relevant gold/calendar costs.
- World Tour unique AP and planning duration, with shared costs and any justified overlap shown.
- Combined unique AP available before Medium Effort, removing overlaps across sections.
- Buy-list totals and unresolved inventory/prices; grind session/total-effort estimates; long-term next milestones and calendar assumptions when those categories are selected.
- Eligible/recommended characters and capability-data gaps for restricted work.
- For selected help tasks: minimum group size, ready-group commitment, recruitment assumptions and helper availability.
- Top 10 researched individual opportunities and top 5 clusters, or all if fewer qualify.
- Data limitations, conditional rewards excluded from totals, and in-game verification needed.
- For updates: previous/current snapshot dates and counts added, revised, archived as completed, manually complete, conflicted and untouched outside scope.

The detailed report may live in the note; keep the chat handoff concise with links and the material limitations.
