---
name: wow-account-setup
description: Guide initial or resumed WoW achievement-planning setup, including duo accounts, Obsidian setup or plain Markdown records. Discover website/export/SavedVariables sources, resolve character identities across addons, validate coverage, and guide only missing setup or scans. Use for onboarding or essential data gaps, not before every search or dashboard refresh.
---

# WoW Account Setup

Make the user's existing account data usable before requesting more collection work. Produce a resumable setup record, normalized observations and a clear readiness handoff. Onboarding is not achievement research, a mandatory full-alt login exercise, or automatic installation of an addon.

## Choose the output

If setup instructions include navigation, use only verified numeric UI map IDs in fenced TomTom commands (`/way #MapID X Y Label`), never a region-name selector. Verify the actual map/floor and required timeline or quest phase separately; an ID alone does not establish access.

Default new todo lists, plans and static indexes to **plain Markdown (.md) files** when the user has not chosen a format. Briefly mention the alternative once: “I’ll save this as a Markdown checklist—no Obsidian setup needed. If you use Obsidian, I can put it in your vault instead.” Then proceed; do not turn the format choice into a required confirmation. Honor an explicit choice or an established Obsidian/Markdown workflow rather than silently migrating it. Reuse a supplied or established folder; ask where to save only when the destination cannot be inferred. Do not repeatedly ask existing users to choose a format.

For plain Markdown, use relative standard Markdown links, ordinary checkboxes and heading links; do not require wiki links, Obsidian block links, Dataview, plugin setup, credentials or integrations. The following vault/plugin/query instructions apply only to Obsidian. Any requested Markdown dashboard or index is static and shows generation and source dates; checkbox changes affect summaries only on explicit refresh.

Adapt `Plans/`, `Lists/` and `_System/` to a maintained collection, with user-facing guides separate from supporting data. A one-off checklist stays one readable file in the chosen folder: no mandatory dashboard, Start Here or empty directory tree. Its checklist remains canonical; register it if an index already exists. In plain Markdown use a normal end-of-file supporting-details section instead of an Obsidian callout.

## Resolve scope with minimal questions

For every request, follow [request, client and source languages](shared/wow/language.md).

First resolve [game context and supported capabilities](shared/wow/game-context.md); ask which version the user plays when unclear. Preserve game/environment and realm or ruleset with each account's observations. Inspect existing providers' [matching version sections](shared/wow/versioned-sources.md) before considering a new source. Unsupported achievements, storage or APIs are capability limits, not reasons for repeat scans. For non-achievement goals, collect only the relevant quest/rep/profession/item or Legacy state.

Reuse supplied identities and established preferences. Resolve one or two account aliases, region/game version, relevant characters, desired categories, available source locations and output destinations. Ask only for missing information that changes the collection steps; do not ask the user to repeat available facts.

Support any subset of quick wins, buyable, grindable, long-term projects and short tasks needing help. Do not demand inventory scans for a reputation-only project or all alts when only two characters are relevant. Distinguish known characters from a verified complete roster. For duo setup, each account keeps separate identity, access and coverage; use only data the other owner has shared or authorized.

Support Obsidian or plain Markdown in the user's chosen folder. Honor a supplied folder and preserve the established destination; ask where to save only when neither is inferable. Plain Markdown needs no vault, plugins, credentials or integrations. Unresolved output preferences do not block source inspection. Do not create external issues, change sharing settings, register an OAuth application, upload account files or install addons merely to explore available sources.

## Discover before collecting

For Obsidian output, run the [plugin readiness check](references/obsidian-plugins.md) before promising live views. Check installed/enabled status and the settings required by the chosen layout. Dataview with JavaScript queries supports the interactive dashboard; Tasks and Local REST API are optional, feature-dependent choices. Record missing setup separately from missing game data and continue independent collection work.

When `wow-achievement-dashboard` is installed and the user selects its layout, use that skill's bundled Obsidian templates and schema. They supply the portable UI; this onboarding supplies the user's data and readiness report. Do not depend on files from the skill author's vault. If the bundle is unavailable, complete data onboarding and provide plain Markdown or identify the missing dashboard package instead of claiming the same tested UI was installed.

Inspect only relevant supplied locations and existing setup records. Recognize website profiles, API/export data, addon SavedVariables and prior normalized snapshots. Do not search unrelated personal files or scrape credentials from browser profiles/configuration. Reuse existing supported tools and importers; no new service or custom addon is needed just to begin onboarding.

Before falling back to old snapshots/public caches or asking for uploads, check the existing private setup record and relevant environment documentation/tool registry for a configured local/game-data reader and its documented transport. Inspect user-designated current local data; a missing mount is not proof it is inaccessible through a read-only adapter. Follow [source discovery](references/source-discovery.md#check-configured-local-access-first), capture complete exports rather than truncated CLI previews, and verify file modification times together with per-field scan clocks. Record failures and coverage gaps explicitly instead of silently substituting an older source.

When source coverage is incomplete, follow [source discovery and identity recovery](references/source-discovery.md): inspect available website data, distinguish redaction from missing scans, validate supplied snapshots, and join GUIDs to named records in other addons. Check these available alternatives before requesting privacy changes or repeat scans. Connection setup and troubleshooting belong to the user's environment, not this skill.

| Source | Establish before relying on it |
| --- | --- |
| WoWthing or another account site | Profile identity, public/authenticated access, actual readable fields, character coverage and observation timestamps. A website API key may be upload-only; verify capability rather than treating it as a login substitute. |
| Simple Armory / Blizzard profiles | Which character/account scope each response represents, achievement completeness, provenance and freshness. A public main-character profile is not proof of the entire account roster. |
| Addon SavedVariables / exports | Addon and game version, actual schema, account/character IDs, collected fields and scan times. Directory names are roster hints, not proof the character still exists or that its data is current. |
| User corrections | Source-labeled actual timings, blockers, character preferences and manual completion. These must not masquerade as API-confirmed state. |

Inspect current documentation or source for the installed addon/version when scan behavior matters. Avoid assuming every addon captures reputations, recipes, bank contents or all alts. Useful starting points: [WoWthing Collector modules](https://github.com/ThingEngineering/wowthing-collector/tree/main/Modules), [RepHub](https://github.com/LorenzoRogai/RepHub), and [Blizzard profile API documentation](https://community.developer.battle.net/documentation/world-of-warcraft/profile-apis). Verify their current behavior rather than copying a permanent ritual from this skill.

Read SavedVariables as data with a suitable non-executing parser; never evaluate arbitrary Lua. Work from a consistent saved copy after normal logout/reload, preserving the original. Do not persist cookies, OAuth tokens, API keys or raw chat logs into setup notes or normalized datasets.

## Prove one-character collection first

Before asking for an account-wide login tour, inspect one character's existing sample or guide a single sample scan. Confirm that expected fields are actually populated and mapped to the correct account/realm/character. File existence, modification time, a successful upload or a silent addon is not proof every scan completed.

Build a coverage matrix with one row per relevant character and columns for:

- Achievement completion and partial criteria, with account/character scope identified.
- Reputation/renown, separating shared and character-specific standings.
- Professions, expansion skill levels and recipes actually available in the source.
- Currencies and gold, distinguishing transferable/shared resources from character-bound ones.
- Bags, personal bank and shared bank as separate coverage areas.
- Quest/access prerequisites, travel capabilities and lockouts where relevant.
- Relevant reward eligibility/cap consumption: daily/weekly completion, personal loot/instance lockouts, standing/renown ceilings and character versus account/Warband scope where observed. Keep source-observed personal usage separate from researched game rules; missing usage/cap fields are unknown, not an unused allowance or an unlimited farm.

Each cell records **available**, **stale**, **missing**, **hidden by source/privacy**, **unsupported**, **conflicting**, or **not needed**, plus source and observed-at time where known. A zero value is valid only if actually observed; a redacted field serialized as zero/empty is not an observed value. File modification/download time is not automatically the game's observation time. Do not apply a universal freshness cutoff: explain why the age matters for the specific decision.

## Guide only missing collection steps

Create a short per-character checklist from the gaps, using verified scan triggers for the chosen addon. Separate automatic login scans from actions requiring a window or interaction. Give the user a concrete success check for each step and an alternative when a source cannot supply the field.

- Explain which addon must be enabled, when it starts scanning, any queue/delay, and how to tell whether the requested data was captured. A suggested waiting buffer is not a completion guarantee.
- Request profession windows or expansion/recipe views only when the collector needs them. State when recipe coverage is partial rather than promising a complete recipe book.
- Request a personal-bank visit only when inventory is needed and not already observed. Treat shared-bank scans separately; do not require repetition on every alt without a reason. Do not assume mail, auction or guild-bank data is captured by opening the personal bank.
- Give exact platform-appropriate export/file instructions from the actual installation and addon. Explain the save step (normal logout/reload as supported), then inspect the resulting copy before marking the collection step done.
- Check local retention behavior. If the chosen collector expires older character records, import/export in batches and retain dated observations; never require frequent alt logins solely to preserve already imported history.

Do not pretend to log into characters or operate the game. When an in-game action is required, leave that checklist item pending for the user and continue independent validation. Do not repeatedly poll for a file the user has not yet produced. Record enough detail to resume after their next message without restarting setup.

## Normalize and validate

For supported Retail Collector and ATT files, use the bundled [saved-variable importer](references/saved-variable-import.md) instead of reconstructing a parser or GUID join. It requires explicit file paths/account/region/game/environment, produces normalized observations and a coverage report, and safely merges a compatible previous output. Contextless legacy data needs the reference's explicit adoption procedure after verifying its original context. For Classic/Forever, use a verified compatible existing reader/export; addon availability alone does not establish this importer's compatibility. No connection setup or network access is built in. Run `python3 scripts/check_import.py` from this skill's directory when validating changes to the importer.

Its observation JSON is an input to research, not the dashboard's view JSON. Preserve that boundary: Collector character flags do not prove account incompleteness, and absent AP/prices/timing/eligibility must not be fabricated to fill the UI schema.

Reuse the existing planner dataset schema if one exists. Otherwise retain these minimal concepts in structured records: account alias, character identity (region/realm/name and stable ID when present), entity ID, field/value, scope, source, observed-at, imported-at, confidence and coverage limitations. Preserve unknowns explicitly. Separate raw observed facts from researched feasibility and manually reported progress.

Persist a provenance-backed identity map for collector GUIDs and any independently verified website/API identifiers. Read identity fields from their owning character table, not nested NPC/boss names or GUIDs embedded in item links. Report matched/unmatched/conflicting counts. Collector GUIDs, website database IDs, anonymized display names and Armory identifiers are different namespaces until a reliable cross-reference establishes their relationship.

Match achievements/factions/items by stable IDs rather than translated names. A website and an addon export may contain the same underlying observation; track that relationship instead of treating them as independent confirmation. Choose a value based on scope, observation freshness and source completeness, not a fixed website-versus-file priority. Flag unresolved contradictions and retain their evidence. Do not combine one alt's reputation with another alt's profession to claim eligibility.

Check a few known completed/incomplete achievements and partial counters, reconcile reported account AP where source coverage allows, and spot-check reputation/skill/currency values against the provided sample. Verify bank coverage and region/faction/character identity before using quantities. Missing IDs or failed refreshes must not erase prior observations, turn unknown into zero, or resurrect completed work. Refresh only the fields actually observed by a partial import, preserving older values with their dates and stale status.

## Maintain generated artifacts
Update existing guides in place and clean up only what you generated. Follow [shared/wow/generated-artifacts.md](shared/wow/generated-artifacts.md).

## Save progress and hand off

Keep the setup note under the root's `_System/` and link it from optional details on `Start Here.md`. Save normalized observations in `_System/Data`, detailed coverage/audits in `_System/Research`, and check scripts/results in `_System/Validation`; temporary agent artifacts stay outside the vault. If canonical progress already exists in `_System/Task Records`, preserve it through imports and moves. `_System/Archive` is for superseded generated artifacts, not a destination for personal notes or still-useful playable guides. Keep dates visible so an account refresh cannot imply unrelated plans were refreshed too.

Maintain one dedicated setup note, for example `WoW/_System/Account Setup.md` or a pair-specific equivalent, adapting to existing conventions. Include account aliases, source references, relevant characters, selection/preferences, the coverage matrix, pending collection checklist, last validation time and readiness summary. Record the chosen output mode; only for Obsidian, record required versus optional plugins, enabled/configured status, and whether rendering and checkbox updates were actually verified. Keep machine-specific connection details outside shared plans. Record snapshot freshness, identity-map provenance and any remaining source limitations. Store normalized data in the established dataset/attachment location; link it rather than copying the same data into every note.

Read back saved artifacts; if the chosen destination is inaccessible, report that saving there remains pending and retain a local copy without claiming it reached the requested folder.

After a successful validated partial refresh, offer **start with what we have** for the scope supported by current observations. Missing bank inventory can make a purchase quantity conditional while unrelated verified work proceeds. This is not a bypass for a failed or stale refresh: ask the user for help/advice before planning from an older snapshot, and use it only if explicitly selected. Missing verified achievement state blocks personalized achievement recommendations for that account, not independent onboarding/source investigation. Missing eligibility keeps restricted tasks conditional. Do not label the full account ready because one character scanned successfully.

Finish or pause with three explicit facts:

1. Accounts/characters and fields covered, with dates.
2. Which selected planning categories and output features can proceed, which are conditional, and exactly why.
3. Remaining user collection actions and the expected file/export to resume from.

Hand the setup record and observations to `wow-achievement-plan` or `wow-duo-world-tour` when installed and requested. A standalone setup invocation stops at the validated handoff; it does not automatically publish achievement plans. Future runs resume existing setup and revisit only missing, stale or newly relevant fields.
