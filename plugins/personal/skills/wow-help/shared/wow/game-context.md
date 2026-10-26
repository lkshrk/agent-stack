# Resolve the game before choosing mechanics or data

Reuse an explicit version in the request or the character/plan's established context. **If the version is not obvious, ask which WoW version the user plays.** Offer only plausible choices; clarify “Classic” into its actual variant. Remember the answer for that character or plan, not as a permanent account-wide preference. Do not assume Retail or build elaborate automatic detection. Ask about phase, ruleset or beta/live only when the distinction affects the task. An expansion mentioned as an objective does not establish the client: a Retail player can request TBC achievements.

Preserve these fields in setup records and generated datasets where applicable:

- `game`: `retail`, `classic-era`, `classic-hardcore`, `classic-sod`, `classic-anniversary`, `classic-progression`, or `forever`.
- `environment`: `live`, `beta`, or `ptr`; test progress never updates live progress.
- `expansion`, phase/season and patch/build: content applicability, not a replacement for game identity. Use `mists-of-pandaria` for the supported MoP Classic achievement view.
- Existing account alias/region and verified character identity, with realm **or ruleset** as appropriate. Keep faction, Hardcore/self-found restrictions, source dates and client language where they affect the answer.

Client/export metadata corroborates context; resolve conflicts before merging or making personalized claims. Keep one game/environment/account context per observation file, dashboard dataset and plan collection. Within it, use stable entity IDs and preserve Unicode identities. The same numeric ID or character GUID in another game is not the same observation. Never combine account totals, inventories, completion or caches across these contexts. Normal patch/phase updates preserve character history; progression into another expansion requires a verified transition and retained provenance.

## Capabilities determine the workflow

Verify only the capabilities needed for the request: native achievements/points, Legacy progression, talent representation, M+, shared storage/progress, grouping/trading, and available readers/navigation tools. Distinguish **supported**, **unsupported** and **unknown**. An unsupported system is not a missing scan or an observed zero. Do not assume all Classic variants share rules, or that historical guides describe a current Anniversary/seasonal implementation.

- Keep Retail M+, hero-tree, Warband and modern talent-codec instructions conditional on the matching client. Ordinary Classic dungeon requests use that edition's dungeon/leveling guidance; do not send them to Retail M+ rankings or silently convert them into raids.
- Use native achievement/AP planning where verified. For quest, reputation, profession, item or other goals without native achievement credit, use the skill's progression-goal branch. Addon collection percentages are not Blizzard achievements. Never fabricate an achievement ID or zero-AP achievement to fit a schema.
- Verify Hardcore/self-found restrictions before purchases, trades or risky routes. Include consequences and prerequisites rather than optimizing only speed. Duo players must be able to group in the chosen game, ruleset and faction; Retail cross-faction behavior is not a general rule.
- Forever is a separate game. Its announced rulesets replace traditional realm selection; grouping and shared progress have their own boundaries. Keep shared Legacy earnings distinct from individual perk allocation and from conventional achievement points. Verify current launch/beta status and build before applying announced mechanics. [Rulesets](https://news.blizzard.com/en-gb/article/24302070/choose-your-ruleset-in-world-of-warcraft-forever), [Legacy](https://news.blizzard.com/en-us/article/24307383/get-to-know-the-world-of-warcraft-forever-legacy-system).
- Verify TomTom/client support and map/floor/coordinates for this version. Familiar zone names or IDs do not establish identical geometry. If the command or location is unsupported/unverified, give sourced access instructions and state the waypoint gap; do not invent a command. Where supported, retain numeric map selectors and the existing copy-block rules.

## Existing sources first

Keep each skill's existing source preference and use that provider's matching edition section, database or endpoint. Check its navigation/version selector before declaring it unsupported. A Retail URL failing is not evidence that the provider lacks Classic/Forever coverage. Use an alternative only for a concrete required gap, name the gap and fallback, and honor explicit source restrictions. Never substitute a Retail guide, tooltip, build or statistic for another edition.

Provider version paths must stay attached to every entity link, tooltip request, observation, talent record and source fingerprint. Verify exact entity identity within that database, including localized pages. Source age, patch/phase and scope still matter; a correct site section alone does not establish current advice. Read the skill's provider reference when resolving an edition-specific source.

## Tested tool boundaries

The bundled Collector/ATT importer handles its documented **Retail** format with explicit game/environment and legacy-adoption checks. Other addon editions may exist without having a compatible tested importer here. Inspect a supported existing reader/export before requesting more data; do not feed Classic/Forever saves into the Retail adapter or silently build a collector.

The portable achievement renderer supports its documented Retail and MoP Classic live contexts, not arbitrary goals or Forever Legacy. Other contexts use static Markdown, including inside an Obsidian vault. The WCL sampler, Raider.IO diagnostic, modern talent exporter and distributed automatic refresh bundle remain Retail-specific. Use version-matched published guides for other editions; do not run those helpers with translated or guessed non-Retail identifiers. Provider support is not proof that a bundled helper supports it.

Personalized advice still requires supported current state or the user's explicit choice of a labeled fallback. When a reader cannot support the game, explain that exact limitation once and ask for an appropriate export or current user-reported state; do not request repeated impossible scans. General sourced help and clearly conditional planning can continue without claiming player eligibility.
