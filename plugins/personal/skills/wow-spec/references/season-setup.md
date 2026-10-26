# Seasonal stats, gems, enchants and consumables

Use this reference for those requested sections, “full setup”, or “update spec data”. Do not run a talent aggregation just to answer which enchant to buy.

## Stable defaults, explicit exceptions

Keep one verified default setup per game/variant, environment, expansion, phase/season, compatible patch revision and spec. Ask the user for an unclear game version rather than assuming Retail from a familiar expansion or spec name. Gems, enchants and consumables may share recommendations across content; do not duplicate identical setups or research every dungeon separately. Add content, hero-build (only where supported), role, profession, weapon-type or leveling overrides only when sourced differences matter. Apply only matching overrides and show the reason. Do not imply that max-level items are available or usable while leveling. Respect Hardcore/self-found acquisition and trading restrictions.

The checklist is stable; actual items and recommendations are refreshable:

| Section | Include when applicable |
| --- | --- |
| Stats | General priority and rationale; relevant supported breakpoints or exceptions |
| Gems | Recommended gem, eligible socket/unique limits, relevant alternatives or quality ranks |
| Enchants | Applicable equipment slot, exact enchant/effect and application item where relevant, weapon/level restrictions |
| Consumables | Flask, combat potion, healing potion, food/feast, weapon consumables, augment rune or other relevant buffs |

Mark a category inapplicable when supported instead of inventing an item to fill it. Allow a new season-specific category. This is not a permanent list of game mechanics.

Stats are general guidance, not universal numerical weights. Do not claim that the most popular stat is personally optimal or invent ratings/percent caps. Distinguish hard caps, diminishing-return thresholds, rotation breakpoints and rough targets. A personalized recommendation depending on gear requires a fresh supplied/collected gear snapshot and, where necessary, an actual compatible simulation. Do not claim a sim ran when it did not. Relevant talent/stat changes may invalidate a spec's setup even if the item catalogue itself is unchanged.

## Store shared facts separately from recommendations

Public verified baselines ship in `assets/season-data/<game>/<season>/` inside this skill. **The existing distributed baseline and automated refresh support Retail live only.** No full Classic/Forever baseline is provided. The Retail Midnight Season 2 [coverage index](../assets/season-data/retail/season-mn-2/index.json) lists all **40 current specs**, queried on 2026-10-04, with one setup per spec and a shared catalogue of 90 item/spell identities. Each spec's recommendations come from its own current guide; linked identities were checked against live tooltips. Some named gem examples are explicitly mapped from guide stat families. This is not a claim that every quality rank, cooldown interaction or personal optimum was verified. Keep unknowns, source disagreements and conditional overrides visible.

On skill activation, resolve the requested game/season/spec and inspect only its bundled and local records. With no local cache, read the bundled catalogue/setup directly: no launch hook, copy or startup network request is needed. This is first-use availability, not a background task at application launch. An optional local copy must preserve the original verification date and never replace an existing local record blindly.

For local updates, reuse an established private working-data location. If none exists, use `~/.cache/wow-spec/<game>/<season>/`; it contains internal reference data, not user guides. Honour a configured alternative and ordinary filesystem permissions. Do not put user caches in the Obsidian root, Plans folder or shipped skill references. Do not create placeholder files or research every spec upfront: populate only validated records needed by the request.

Keep the existing Retail-live path compatible. New non-Retail/test-environment records use a separate context path such as `~/.cache/wow-spec/<game>/<environment>/<expansion>/<phase-or-season>/<revision>/`, retaining ruleset conditions where relevant. Compare the complete context before reuse, not just spec ID or the newest timestamp. Existing Retail records must never become an implicit Classic/Forever fallback. Until matching records are verified, answer from current edition-specific sections of the existing providers and retain provenance; do not initialize a full catalogue or invent compatible IDs.

Choose the newer applicable verified setup, subject to the user's explicit source preference. Read it with its matching catalogue and validate every referenced item. Do not combine a bundled setup with unrelated local item versions without rechecking applicability. A newer valid local setup takes precedence over the bundle; a newer bundle can be read without deleting the older local cache. If the newest candidate is invalid or flagged for review, retain the previous verified set with the gap disclosed. Do not use a different season merely because it is the newest folder.

Use one `catalog.json` for shared item/enchant facts and one `spec-<specID>.json` per researched spec. This is a lightweight file convention, not a new database/service. No credentials, account inventories or market snapshots belong in these files.

Required catalogue information:

- Game/variant, environment, expansion, phase/season, compatible patch/build revision, schema version and verification timestamp. Preserve the known Retail-live scope of legacy bundled records; missing context on other records is not evidence of a match.
- Stable record key; exact localized name and item ID and/or enchant/spell ID as appropriate. Do not confuse an enchant effect with the tradable application item. Quality ranks may have distinct IDs; retain the exact verified version.
- Category, effect, eligible slot/socket/weapon/level/profession, quality/rank and restrictions relevant to choosing/using it.
- For consumables: duration, cooldown, shared/exclusive buff or potion restrictions, reusable versus consumed-on-use behavior, and other relevant use limits. Unknown fields stay explicitly unknown.
- Direct sources supporting facts, source update date if available, verified patch/date and status (`verified`, `needs-review`, `unverified` or `retired`).

Required spec-setup information:

- Edition-specific spec ID/name, matching game/environment/revision context, applicable level/build/role/ruleset assumptions, version-matched source provenance and last verification date.
- Default slot/category recommendations referencing catalogue keys; reasons, general stat guidance and supported alternatives.
- Only genuine overrides, each with a matching condition, replacement and supporting source.
- Verification status and any unresolved conditions. A catalogue item's existence alone is not evidence it is recommended for this spec.

Recommendations can be supported by a maintained specialist guide, a suitable published aggregate or validated simulations. Respect explicit source preferences and disclose disagreements. Use item/tooltips or official information to resolve disputed effects and restrictions; do not infer those facts from popularity. An override requested from another source must not silently overwrite the default recommendation's provenance.

## Reuse and refresh

On a normal question:

1. Resolve the game/variant from established context or ask if unclear; confirm environment and relevant phase/season/revision. Find the matching bundled and/or local spec record and catalogue references as above. Use existing providers' matching edition sections when no applicable record exists.
2. Reuse verified matching records when no relevant change is known. Display their verification date; do not claim a fresh lookup merely because the answer was generated today. No daily/weekly expiry or habitual full re-research.
3. Refresh only missing or affected records when no verified bundled/local match exists, on a new season, a relevant patch/hotfix, contradictory new evidence, or an explicit update request. First use alone does not require research if the bundle already covers it. A patch unrelated to this setup does not require rebuilding the catalogue. An old record from a different season is not a current recommendation.
4. If the spec or relevant mechanic changed, review its recommendations as well as the affected item facts. Talent samples remain separately time-scoped; this seasonal policy does not make old rankings current.

On “update spec data” / “refresh WoW spec data”:

1. Honour a named spec/category/season within the resolved game/environment. Otherwise refresh the active spec from context and its referenced items; if no active spec is known, refresh already-stored current-season setups within that same context. Do not expand across editions/environments or into all specs by default. If neither context nor stored data identifies a spec, ask which spec to initialise.
2. Fetch focused current evidence for the selected scope. Check season, item/effect IDs, quality ranks, restrictions, and applicability of the spec recommendation. Keep confirmed unchanged records; don't churn files just to change a date.
3. Stage candidate changes outside user-facing folders. Before publishing, check that every recommendation references a verified applicable catalogue record, source URLs are present, IDs/ranks are consistent, overrides have conditions, and no unsupported precise values have been introduced.
4. Publish only after the selected candidate set validates. Keep the last verified version intact if retrieval or validation fails; do not replace known data with an empty response or mark a failed refresh as fresh. Record the failed attempt/gaps separately and label retained recommendations as last verified, with any current applicability uncertainty. Successful unrelated scopes may update independently if clearly reported.
5. Report changed recommendations/items, confirmed unchanged sections, verification date, and anything still unresolved. An update refreshes reference data; it does not automatically rewrite previously saved user guides or checklists.

After a successful refresh, keep at most one prior verified version of the touched internal records for rollback. Retire old-season references outside the active lookup path; preserve user-authored notes and referenced sources. Remove only agent-created temporary candidates that are no longer needed. Do not accumulate a dated file for every read or refresh.

## Updating the distributed baseline

Normal refreshes update local working data, not the installed skill or a repository. When explicitly asked to update the bundled data, review and promote only public, source-backed catalogue/spec records into `assets/season-data`. Exclude credentials, vault locators, account/character data, personal preferences and market snapshots. Retain dates, unknowns and provenance; don't promote a personalized simulation as a universal default. Validate references and scope before replacing a bundled set. Repository changes remain reviewable and are distributed with the skill's next install/update; no scheduled job or service is required.

This repository's `.github/workflows/wow-season-updates.yml` runs weekly and on manual dispatch. The read-only checker detects guide/tooltip/roster/season changes; a credentialed AI step researches them and updates affected bundle JSON. A separate trusted publishing job validates data scope, catalogue identities, provenance and references, then commits verified updates to the default branch. Only handled sources have fingerprints advanced. Known inaccessible sources remain explicit gaps; a fingerprint alone never counts as fresh gameplay verification. The workflow supports direct OpenAI credentials or a configured Responses-compatible gateway URL, model alias and gateway key, plus permitted bot pushes. Its current write scope is the existing season bundle; new-season migration is separate. These repository maintenance tools need not exist in a standalone skill installation.

## Usable answers

- Start with the recommended setup, game/variant, environment, phase/season and scope. Include matching-edition sources and “verified on” date.
- Gems/enchants: a concise slot/category table with exact item/enchant names, relevant rank, effect/stat and conditions. Avoid bare IDs as user instructions; direct entity links can carry IDs.
- Consumables: exact names plus when to use them, duration and meaningful cooldown/shared-use restrictions. Keep combat potions distinct from healing potions and persistent flask/food buffs. Explain interactions only when they affect the player's choice or use.
- Alternatives: identify lower-rank or commonly lower-cost alternatives when supported, but call them budget-oriented rather than asserting a live price. “Cheapest”, exact prices or savings require fresh market evidence with region/realm/time; never persist those claims as season facts.
- State any gear-dependent choice instead of pretending one stat or item is universally best. Keep conditions near the affected recommendation.
- A full setup includes talent guidance plus these sections where applicable: verified edition-compatible imports where supported, otherwise a verified selected-build calculator link and sourced point allocation/progression with the import limitation stated. Never run the Retail exporter on Classic/Forever strings. Requests for individual sections remain focused. Save finished guides only to requested Obsidian/Markdown destinations, keeping the internal reference files separate and linking the guide from an existing index if appropriate.

## Behavioral checks when maintaining this workflow

- An ambiguous “Classic setup” request asks for the actual variant; a named expansion target alone does not select the client.
- A Classic/Forever request never reuses Retail catalogue identities, stats, talent strings or the automated Retail refresh pipeline; matching existing-provider guides provide the scoped fallback.
- Beta and live records, and incompatible phase/patch revisions, do not overwrite or satisfy each other's setup lookup.
- Ordinary dungeon guidance stays separate from M+; unsupported item categories remain explicitly inapplicable.

- A verified same-season Affliction consumables record is reused without another talent scan or arbitrary age-based refresh.
- A clean installation uses the matching bundled Affliction setup without creating a cache or fetching all specs.
- A newer applicable local setup wins over the bundled baseline; startup never overwrites it.
- A new season triggers verification; failed retrieval leaves the old record intact and visibly out of date for the new season.
- An item-effect hotfix updates the shared record and reviews referencing spec choices, rather than creating copies per dungeon.
- A raid-only exception does not replace the default M+ setup.
- “Enchants only” does not produce talent imports or a full shopping list; “full setup” includes the requested complete set.
- Cached budget alternatives do not become claims about today's cheapest auction-house item.
