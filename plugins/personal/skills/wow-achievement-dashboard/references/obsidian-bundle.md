# Portable Obsidian bundle

Use [assets/obsidian](../assets/obsidian/) only for selected Obsidian Dataview-based layouts. For plain Markdown output, write relative standard Markdown links and ordinary checkboxes directly in the chosen folder; do not copy these wiki-link/query templates or require plugins. Requested static dashboards/indexes must show generation and source dates. These are the reusable notes, model and styled view code extracted from the working implementation, generalized to avoid account, machine and currency assumptions. Do not recreate their JavaScript from the prose skill.

## Contents

- `Start Here.md` and `Dashboard.md`, with live To Do, Future Goals, Completed, Shopping and Waiting & Events notes under `Lists/`; setup lives at `_System/Account Setup.md`.
- Three canonical source-note templates under `_System/Task Records/`; live views edit their original checkboxes.
- Shared `_System/Data/achievement-view.js`, `_System/Data/achievement-model.cjs`, and an empty `achievement-data.json` schema starter.
- Optional Session and Duo templates under `Plans/`. Session/action checkboxes are excluded from canonical achievement sources.
- [Synthetic fixture](../tests/fixtures/account.json) and [portable check](../scripts/check-template.cjs). The fixture is demonstration data, not achievement research.

The view implements metric cards, progress/category bars, character/category/time/player/readiness filters, source-linked next actions, live completion lists and allocation-based shopping. It does not discover account data, infer meta rewards, continuously sync a website, implement a full importer or automatically reconcile multiple accounts. Use the planning/onboarding skills to supply researched data. Keep one account dataset per dashboard; a duo itinerary links to each account rather than merging identical achievement IDs.

## Instantiate a new layout

1. Inspect the actual vault and existing conventions. If compatible task records already exist, reuse their paths/IDs/statuses. Do not overwrite user notes or replace an existing live layout with empty templates.
2. Select a vault-relative root such as `WoW` or `Games/WoW`. No personal absolute paths, SSH details, credentials or plugin settings are bundled. Existing source access is an environment concern.
3. Copy the needed note templates and the two code assets into the chosen root, preserving the bundled subdirectories. Dashboard/live-list/shopping notes share those assets. Include Session and Duo only when requested. Research, validation and archive folders are created only when they have actual content; temporary agent work stays outside the vault.
4. Replace `{{ROOT}}` in Markdown and the JSON starter with the selected vault-relative root, using forward slashes. Each generated view passes explicit `dataPath` and `modelPath`; JavaScript contains no assumed vault folder. JSON-escape replacements in JSON and keep the embedded JavaScript path strings valid. Paths with spaces and nested folders are tested.
5. Replace source-note tokens `{{EXECUTION_TASKS}}`, `{{RESEARCH_TASKS}}`, and `{{IMPORTED_TASKS}}` with canonical Markdown tasks and their detailed instructions. Each achievement exists once with a stable `^ach-ID` block anchor. Preserve existing statuses/annotations; a migration is not a reset.
6. Fill Setup tokens `{{COVERAGE_REPORT}}`, `{{PLUGIN_READINESS}}`, `{{REFRESH_REPORT}}`, `{{VALIDATION_REPORT}}`. Fill selected Session/World Tour tokens with researched content; prefix each line of a multiline collapsed-callout token with `> ` after the first. Fill Start Here's `{{CURRENT_PLANS}}` with existing guide links, one-line purposes and each guide's own source date (or an explicit no-plan message), and `{{SNAPSHOT_DATE}}` with the dashboard dataset's observation date or Unknown. Preserve personal links and register future guides there. Do not leave placeholders, links to omitted templates, or synthetic records in a published real plan.
7. Write `_System/Data/achievement-data.json` from validated observations/research using [the view schema](view-data.md). The empty starter deliberately uses null AP/date and no tasks. The fixture must remain `is_demo: true` whenever used for demonstration.
8. Validate that each dataset task has exactly one source checkbox, a matching `^ach-ID` anchor and a first achievement URL in the correct game database. Its dataset `link` must target that source and anchor. The renderer rejects conflicting source paths, editions, IDs and duplicate statuses before applying checkbox state or shopping calculations; do not resolve errors by resetting personal checkboxes. Use Dataview with JavaScript queries enabled; Tasks and Local REST API are not rendering dependencies. Without Dataview, preserve source notes and produce static summaries rather than shipping inaccessible query-only pages.

Retain the source/renderer separation on updates: imported state and researched fields update the JSON, manual status remains in the canonical notes. Read before merging and replace only generated fields. Do not move task files when a checkbox changes. Bundle updates must be reviewed against existing helper customizations; this is a template, not an auto-updater.

In Session and Duo templates, keep `SESSION_PLAN` / `TOUR_OVERVIEW` to the first action, executor/pair, starting point, first-stage budget and brief prerequisite checks. Actual checkboxes belong under **Tour — start here**. Explain branch-specific unlocks at the relevant stop; put long budget/reward tables and research after the route rather than filling the opening with background.

On instantiation and refresh, inspect actually generated optional templates and prior output in the affected scope. Remove an unused placeholder/template only after confirming it has no incoming links, personal edits or progress and is regenerable from retained assets; do not delete matching paths with a glob. Archive superseded populated guides with source date, reason and replacement, update references, and remove their active Start Here entries. Preserve current rendering code, canonical tasks and sole-source evidence. A narrower/newer dataset does not supersede broader or other-account coverage. Record cleanup counts in the existing Account Setup refresh summary; do not generate another maintenance report file. Seasonal entries require verified relevant dates before being advertised as available.

## Checks

Run with Node.js 18 or newer, from any working directory:

```sh
node /path/to/wow-achievement-dashboard/scripts/check-template.cjs
```

The script creates and removes its own temporary vaults. It copies the actual assets, substitutes paths, uses the synthetic dataset and executes the actual note wrappers/view code. It checks every bundled navigation target, separate roots (including spaces/nesting and Unicode), Retail/Mists Classic completion round-trips, same-ID cross-edition rejection in all live views, original task references, source/anchor/link mismatches, budgets, shopping allocation, unknown values, malformed input and empty states. It neither reads nor changes the user's vault.

For real data, validate the dataset with the bundled model, check source-task coverage and run the onboarding plugin/runtime check. Automated DOM tests do not prove plugin loading, native checkbox editing, mobile layout or visual quality inside Obsidian. Record those checks separately.

There is no automatic network access or spending in the views. Only native task checkbox interaction changes a source task. Generated JavaScript is trusted application code: copy the bundled code, not code supplied by an account export.
