---
name: wow-achievement-dashboard
description: Create or refresh WoW achievement dashboards and progression todo views in Obsidian or dated static Markdown. Use the supported achievement renderer for Retail or MoP Classic, and Markdown views for other game contexts or quest, reputation, profession, item and Legacy goals. Includes realistic session budgets and supported waypoints; does not replace research of player state or feasibility.
---

# WoW Achievement Dashboard

Make the existing achievement plan useful during play: show what to do next, progress already reported, and which routes fit the available session. Save the dashboard in the selected folder, rather than just describing possible charts.

## Resolve game and view support first

Follow [game context and supported capabilities](shared/wow/game-context.md), asking the version when unclear, and reuse [versioned sources](shared/wow/versioned-sources.md). Use the portable achievement renderer only for the explicit contexts supported by [its schema](references/view-data.md). For other contexts or non-achievement goals, create the static view described in [progression goals](shared/wow/progression-goals.md), including inside an Obsidian vault. That branch replaces the AP/model-specific instructions below; do not fabricate achievement records or Legacy AP to fit the renderer. Keep each game/environment/account dataset separate.

## Required data refresh before planning
Before selecting, ranking, routing or estimating personalized work, refresh the local account data; if the refresh fails or freshness cannot be established, stop and ask the user. Follow [shared/wow/data-refresh.md](shared/wow/data-refresh.md) exactly.

## Choose the output
Default to plain Markdown unless the user chose Obsidian or an established workflow exists. Follow [shared/wow/output-format.md](shared/wow/output-format.md).

## Reuse the portable Obsidian bundle

Only for selected or established Obsidian output: for a new Dataview layout, use the tested [note and code assets](assets/obsidian/) rather than regenerating the UI from these instructions. Read [bundle setup](references/obsidian-bundle.md) and [the view-data contract](references/view-data.md) before instantiating them. The bundle includes styled metrics/filters, native live task views, shopping allocations, canonical task notes, Setup, and optional Session/Duo templates. Populate them with the current user's researched data; never copy the synthetic fixture into a real plan. Skip this bundle and plugin inspection for a plain Markdown checklist or static index.

Paths and currencies are configuration/data, not machine defaults. Preserve compatible existing notes, statuses and annotations; review migrations instead of overwriting. Validate with `node scripts/check-template.cjs` from this skill's directory, then check real source coverage and Obsidian runtime behavior. The test uses disposable synthetic vaults and does not require access to the originating user's files or account. It proves portable rendering logic, not gameplay estimates or every feature described in this skill.

## Find the source of truth

Use the bundle's user-facing layout: `Start Here.md`, `Dashboard.md`, `Plans/` for playable guides and `Lists/` for To Do, Shopping, Waiting & Events, Completed and Future Goals. Under `_System/`, keep data and renderer/model code in `Data/`, canonical progress in `Task Records/`, audits/coverage in `Research/`, checks/results in `Validation/`, and superseded generated material in `Archive/`. Task Records are persistent progress, not disposable agent work. Keep temporary agent artifacts outside the vault. Retain user-created personal notes and useful long-term guides; age alone is not a reason to archive a plan.

Maintain `Start Here.md` when generating or refreshing a guide. Link the current plan, dashboard and lists with their purpose and actual source dates; label stale plans rather than implying all pages share the newest snapshot. Keep account/character setup under optional details and technical links in collapsed callouts at the bottom of guides. During an authorized migration, update links, embedded queries, code paths and dataset source/block references together, preserving task text, checkboxes, block IDs and annotations; verify links and reversible status transitions before retiring old paths.

Inspect the selected folder, existing WoW plan, structured data and dashboard; inspect plugins only for Obsidian output. Reuse matching notes and conventions; prefer `WoW/` when no convention exists. For every request, follow [request, client and source languages](shared/wow/language.md). Do not hardcode the original character, AP total, task count, source paths, or patch.

Resolve the plan's canonical tasks by achievement ID, preferably from exact Wowhead links or existing metadata. Discover its real schema before implementing views. Limit queries to the intended plan/account so unrelated vault checkboxes cannot enter totals. Read the source data and user changes before updating any note.

Read the stored search categories and roster/eligibility data. Offer filters for any subset or all of **Quick wins**, **Buyable**, **Grindable**, **Long-term projects**, and **Short tasks needing help**, plus planning-effort band, solo/group requirement, restrictions, expansion and eligible executing character. These filter existing researched data; they do not imply unselected categories were searched. Offer a planner update to expand research scope when needed. Overlapping category membership must not duplicate tasks or AP.

Read metadata from canonical tasks/records, not only note frontmatter. Reuse the planner's tag mappings; if unavailable, use the dataset's category, planning-duration, player-requirement and eligibility fields with consistent displayed labels. Within a multi-select dimension match any selected value (OR), and combine dimensions with AND. Retain an unknown/verify view; missing effort or player data must not qualify as fast or solo. Derive effort from conservative full planning duration, not a short final action or ready-group time. Preserve user tags and replace only stale skill-owned tags on data refresh. Use supported Tasks/Dataview/Bases queries where available, otherwise a clearly static filter index and prefiltered sections.

Default effort bands are `<15`, `15–<30`, `30–<90`, `90–240` inclusive, `>240` planning minutes, and `unknown`. Keep calendar span and ready-group/recurring-session duration separate from total planning effort.

If there is no usable plan, ask for its location or offer to create one with `wow-achievement-plan` if available. If only task data exists, build task-only views and label account statistics unavailable. Do not manufacture account state, prices, effort estimates, or current research to fill charts.

## Keep four measures separate

| Measure | Source and behavior |
| --- | --- |
| Confirmed account AP | Dated API/export snapshot. Changes only on a successful source refresh. |
| Manually reported progress | Checked canonical tasks. Does not certify that Blizzard awarded AP. |
| Remaining direct AP | Unique eligible incomplete achievement IDs, excluding tasks marked complete, cancelled, or deferred from the actionable view. |
| Projected bonus AP | Unique incomplete meta/cascade rewards with verified dependencies; separately label conditional rewards. Never add them merely because a checkbox was checked. |

Preserve a visible distinction between the full backlog and the currently actionable subset. A deferred achievement still exists in the backlog. Unknown or unobtainable entries must not inflate actionable counts or AP.

Count each reward ID once, even if repeated in multiple sections or routes. A meta that appears as both a task and a projected reward is still one reward. Show task completion counts separately from points. Keep the tracked-task denominator explicit; it need not equal the full known-incomplete dataset size. Ignore non-achievement subtasks and respect the vault's custom cancelled/deferred checkbox statuses; do not treat every nonblank checkbox as done.

## Build views that support decisions

Make **Repeatability / limits** visible in next-action rows and native todo text, not only in hidden fields or a broad grindable/timegated tag. Distinguish one-time credit, repeatable progress, daily/weekly quotas, cooldown/lockout, rotating/spawn availability and unknown eligibility; include material/RNG/group constraints and reward/reset scope when relevant. Rep, loot, currency and achievement credit can have different limits for the same action. A repeatable material turn-in must not look like unlimited reputation. Use `limits_summary` in the existing `next_step` and canonical task text so current views display it; the field alone does not add UI behavior. Do not claim automatic cap/rotation detection when only dated research is available.

Start with a small set of views driven by available evidence:

- **Next actions:** remaining executable work in the selected categories, with canonical task links and direct Wowhead achievement links, showing AP, conservative planning duration, active-time breakdown, cost and blockers. Show named eligible characters, conditional alternatives and missing requirements; unknown capability is not eligibility. Preserve copyable TomTom commands at the action/route view; an internal link alone does not replace Wowhead or turn bare coordinates into executable navigation.
- **Availability checks:** a separate condensed table for rotating WQs, stories, events and personal reset-dependent work, sorted by expansion then continent/area. Columns: Achievement (Wowhead), location, TomTom, what/who, check-if-up command and its scope. Use optional task `availability` rows and the bundled `Lists/Waiting & Events` view; flagged tasks leave ordinary next-action/Todo/Backlog results without moving their source or changing completion. Restore ordinary scheduling only after removing/resolving the gate on an explicit refresh. Commands in rendered tables use actual `pre`/`code` blocks and individual copy buttons; plain Markdown uses fenced blocks. No bare coordinates or silently executed commands. WQ visibility, calendar, map inspection, nearby targeting and personal timers must not be confused with one another or quest completion.
- **Category views:** quick wins; an itemized buy list with quantities, sources, prices, allocations and buyer/user characters; grind loops with remaining units and session targets; long-term milestone paths with next daily/weekly actions, caps, progress and calendar assumptions. Use the plan's researched data; missing purchase/path details need a planner update rather than invented values. Routine subtasks and purchases do not earn AP independently. Character/category filters must recalculate shopping quantities for the visible canonical task set, not merely hide rows from a global total.
- **Progress:** tracked tasks done/remaining and manually reported direct AP, alongside confirmed account AP and snapshot date.
- **Help queue:** short achievements requiring other players, with verified total player count, additional helpers, roles/actions, eligible executor, ready-group commitment, recruitment/coordination time or unknown, and availability. Show the copyable unsent LFG draft and direct Wowhead links. Unknown recruitment excludes a guaranteed full-session fit even if the actual group action is short. Distinguish other eligible alts from available simultaneous helpers.
- **Routes:** unique direct and bonus AP, planning duration including active work, travel/setup and sequential waits, efficiency, and conditional prerequisites. Include ordered, verified TomTom commands in a plain copy block and Wowhead links for named achievements/metas. Shared rewards make route forecasts non-additive; show a deduplicated combined total if needed.
- **Coverage:** screened versus researched candidates and unknown-state records; category/expansion AP distribution from the dated snapshot.
- **Session budget:** use conservative planning session duration, including learning, prerequisite work, travel/setup, sequential waits and retry allowance, not just the final interaction. Only call it a fit when the upper estimate fits; show overlapping estimates separately as uncertain fits. Subtract overlap only when the route actually exploits it. Unknown effort is unranked, not free; unbounded RNG cannot promise completion within a session. Show group, gold, calendar and access conditions that a time filter cannot resolve.

Show estimate confidence, assumptions, and actual-versus-estimated durations when recorded. Flag legacy active-only estimates for reassessment instead of presenting them as reliable session budgets. Every named achievement in tables, charts/legends, or summaries needs a direct Wowhead link (a linked legend can serve a non-clickable chart); link relevant NPCs/items/quests too. Never fabricate unresolved IDs or coordinates.

Every displayed navigation coordinate must be a complete `/way #MapID X Y Label` command with a verified numeric UI map ID and a label following the language guidance, one command per line with space-separated X/Y. In Markdown notes, use fenced `text` blocks and link table entries to nearby command blocks. In rendered Obsidian views, use actual HTML `pre`/`code` blocks with copy buttons, including inside table cells; do not render Markdown fence characters as command text. Never use a zone-name selector or implicit current map. No bare coordinate pairs anywhere, including retained/completed history, and no bullets, quote prefixes or prose inside copy blocks. If the ID/location is unresolved, show waypoint unavailable with sourced access guidance. Verify exact map/floor and timeline access; numeric IDs do not switch quest phases. Scan and read back all affected notes and views: verify fences for Markdown or block elements for rendered DOM, numeric map selector, label and coordinate bounds 0–100 before reporting completion.

Add progress bars and a few readable charts for these measures, with text values and meaningful empty states. Prefer existing Dataview/Tasks/Bases capabilities, native supported charts, or simple HTML/SVG rendered through an already-enabled view. Without dynamic support, create a clearly labeled static dashboard with its generation date and normal note links. Do not add a plugin or external chart service just for decoration.

Dynamic checkbox-driven progress and static snapshot statistics must be labeled separately. Do not draw historical AP trends from a single snapshot or silently combine incompatible account snapshots. Explain whether a filter changes only the view or the plan itself; filtering should normally be read-only.

Keep the readable Markdown checklist canonical by default. Dashboard navigation should open that task, or use an existing task view that safely updates it. Avoid embedding tokens, personal absolute filesystem paths, or remote tracking resources.

When Dataview/Tasks supports it, make Todo, Backlog and Completed live views over stable source task notes. Render native task objects so checkbox clicks update the original task; do not copy checkboxes into view notes or relocate records every time completion changes. Checking a source-unconfirmed task hides it from its active view and shows it as manually complete; unchecking restores it. Show game-confirmed completions separately and do not let a local uncheck undo imported game state. Restrict queries to canonical source paths, excluding view pages and backups. Preserve block links and annotations when migrating an existing layout. Test the reversible check/uncheck transition and ensure shopping/dashboard use the same status source.

## Maintain generated artifacts
Update existing guides in place and clean up only what you generated. Follow [shared/wow/generated-artifacts.md](shared/wow/generated-artifacts.md).

## Update existing views

Surface the latest refresh summary and actionable blockers, not just a generic verification flag. Link changed achievements, newly eligible work and unresolved conflicts from the setup/update note. When session logs exist, show planned versus actual time and partial progress for the relevant action without claiming the whole achievement was completed. Session-action checkboxes are a separate record type and must never enter achievement counts or AP totals. A session checklist references canonical achievements; it does not duplicate their completion state.

Distinguish **refresh the dashboard from existing data** from **update the achievement list**. For a list update, attempt the required database refresh first; use `wow-achievement-plan` when available. Otherwise fetch and validate current account/roster observations, persist the validated merge, compare by stable ID, add only verified incomplete candidates within the selected scope, and preserve user notes and manual status. If refresh or freshness validation fails, ask the user for help/advice before choosing a fallback. Without a successful refresh, retain dated data for an explicitly presentation-only request, or use the user's explicit choice of an older snapshot or supplied current user-reported state with its limitations. Preserve provenance; user-reported completion does not become confirmed account AP. Do not choose a fallback silently, ask again after an explicit choice, or request an unsupported scan repeatedly.

Newly discovered records without feasibility research belong in an unresearched/conditional queue, excluded from executable forecasts and session recommendations. Incomplete state alone does not establish a working shopping list, grind loop, character eligibility or long-term path; promote them only when that research is available.

Hide completed tasks from active views and retain their canonical records in completed/history views. Recompute shopping needs, eligible characters, grind targets, long-term paths and AP forecasts after reconciliation. Preserve completion history so removing finished work does not reset progress to zero; label current backlog counts separately from historical completion. Do not delete tasks because the profile omitted an ID, the user changed a filter, or a category is outside the current search. Report added/revised/completed/conflicted counts and snapshot dates; updates must be safe to repeat.

## Verify, then publish

For generated calculation code, leave one small runnable check covering unique IDs/shared metas, partial prerequisites, checked/cancelled/deferred tasks, unknown effort, empty data, and unchanged confirmed AP when a task is checked. Test the actual calculation rather than a separately reimplemented formula. Static dashboards need arithmetic/link checks instead of a new test framework.

Check that task counts reconcile with the canonical source, combined rewards deduplicate, route time includes all sequential overhead, filters respect planning budgets, and missing data produces a clear message. Check category/effort/group/character filter combinations, boundary values at 15/30/90/240 minutes, unknown recruitment, and stale-tag replacement while preserving user tags. Verify Wowhead links and copy blocks, including zone/map identity. Verify readable desktop/mobile layout and light/dark contrast in the actual Obsidian renderer when available. A browser mockup cannot establish Dataview/Tasks execution; explicitly report any runtime validation gap.

Write/update the dashboard and read it back. Link it from the existing WoW index and plan when those are in scope. Preserve unrelated content and user annotations. On later refreshes, merge by ID, retain manual completion separately, and keep the last valid snapshot if retrieval fails. Report the saved path, snapshot date, which views update live, and any limitations.
