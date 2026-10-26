# Source discovery and identity recovery

Use when a profile is anonymized, data appears absent, an export is stale, or Collector records lack names. Prefer available sources and cross-source evidence over requiring more collection work. These are fallbacks, not a mandatory sequence when the first source is sufficient.

## Check configured local access first

Read the existing private setup note and relevant environment documentation or tool registry before requesting uploads or reverting to saved snapshots/public caches. Discover and use the configured read-only reader and documented transport: it may be an SFTP-backed adapter or a Coder `wow-sv` tool rather than an SMB mount. These are examples, not required infrastructure; do not hardcode a host, credentials or commands from another user's environment. A failed assumed mount does not establish that the documented reader is unavailable. Before personalized planning, attempt a current read and validated database update through the documented reader. If access, import or freshness validation fails, keep the prior database intact and ask the user for help/advice before dependent planning; a labeled stale fallback still needs the user's explicit instruction.

Save/read the complete exported JSON or files using the reader's documented output mechanism; a truncated CLI preview cannot establish missing records or full coverage. Check export/source file size and modification metadata, stable reads, and the relevant internal per-field scan clocks. Retrieval time or a newly written export does not make old game observations fresh. Public account APIs can complement positive-only addon observations with explicit account completion, remaining criteria or AP where their scope supports it; keep each source's dates and limitations separate.

On failure, report the attempted reader/source, the error or stale/conflicting scan dates, and the smallest question that unblocks it. For example: “The configured reader could not connect; did the access method change?” or “The export predates your latest play; can the game/save sync be refreshed, or is there another current source?” Try an obvious documented recovery, but do not endlessly probe unrelated transports or ask for passwords in chat. An unchanged export is acceptable only when freshness is established for the requested decisions; it is not fresh merely because the read succeeded. Record the verified import outcome in the existing setup/refresh note and read back the updated database before handing off to planning.

## 1. Inspect what the website actually exposes

Read the supplied profile page and discover the data URLs the current frontend uses. A `#/characters/...` fragment is client navigation and may contain a user-selected name; it does not establish which anonymized server record belongs to that character. Do not guess record order, class/level combinations or internal IDs to bridge the gap.

For WoWthing, the public page and JSON can be readable without an API key. Inspect the actual response and current serializer/model before decoding positional arrays; validate record length, field types and timestamps rather than hardcoding array offsets forever. Follow the site's published modified-version URLs/redirects. An upload API key is not necessarily accepted for private reads. Do not call key-generation/reset or upload endpoints to test read access.

A single failed fetch is not proof the profile is private: compare the homepage/profile response and error type, and try an ordinary HTTP client or supported browser with normal headers. Distinguish a tool fetch failure from a site's authorization denial. Do not evade an authentication challenge, switch identities or change profile privacy.

Observed in the September 2026 WoWthing implementation; recheck for the version in use:

- Anonymous public character records can have generated names and suppressed realm IDs.
- `LastSeenAddon` and Warband-bank contents can be omitted/redacted publicly even after a successful scan.
- Shared reputation data can depend on visibility of account records.
- Available public character count can differ from local Collector count due to filtering/visibility. Neither proves missing scans or deleted characters.

Inspect [website access and response construction](https://github.com/ThingEngineering/wowthing-again/blob/main/apps/web/Controllers/ApiController.cs), [character privacy handling](https://github.com/ThingEngineering/wowthing-again/blob/main/apps/web/Models/Api/User/ApiUserCharacter.cs), and [array serialization](https://github.com/ThingEngineering/wowthing-again/blob/main/apps/web/Converters/ApiUserCharacterConverter.cs) only as needed. A page-download timestamp does not date the underlying game observation.

## 2. Validate the supplied file or export

Use an available file, attachment or export from the user's chosen source. For typical Retail installations, addon saves live under `WTF/Account/<account>/SavedVariables/`. Check filename, size and modification time, then internal scan dates; a correctly named backup can still be stale.

Access is environment-specific: reuse the documented configured reader before asking for an export. If no supported reader/source is available, request only the missing export; do not build infrastructure or scrape credentials as part of onboarding.

Parse a consistent saved snapshot without editing the original. Check metadata before/after reading a file that may still be changing; retry a changing file rather than accepting a partial parse. Report coverage and validation findings, not raw account dumps.

## 3. Resolve GUIDs with other addon observations

Treat the Collector GUID as the stable join key within the verified account/game/region context. Inspect the current Collector schema: cleanup can remove old `name` fields while retaining the rest of a GUID-keyed character record. A missing name therefore does not require logging that character in again.

The demonstrated fallback is `AllTheThings.lua` in the same account's SavedVariables folder:

Use the [bundled importer](saved-variable-import.md) for this join and its repeat-import safeguards. The rules below describe what it must preserve; do not replace its data-only parser with ad-hoc nested-name matching.

- Parse `ATTCharacterData` as a data table.
- For each GUID-keyed entry, read its own direct `guid`, `name` and `realm` fields. Confirm the explicit `guid` agrees with the table key when present.
- Join only exact matching GUIDs to Collector records. Retain names/realms and identity-source date separately from the Collector's field scan dates.
- Do not scan arbitrary nested `name` fields: lockout data can contain boss names. The same GUID may also appear in unrelated tables; incomplete occurrences must not overwrite a complete identity record.
- Do not derive the player's identity from a GUID inside an item link; that can refer to another character, such as a crafter.

If ATT is absent or incomplete, inspect other installed roster-capable addons for explicit GUID-to-character records, or use an authorized account roster/export with a verifiable identifier cross-reference. Directory names and Armory URLs can corroborate a known name/realm but do not alone prove its GUID. Do not infer the join from a matching achievement score, class, item or level.

For duplicate or renamed/transferred identities, retain evidence and dates. Resolve with stronger current identity evidence or leave a conflict; never merge different accounts merely because names match. A donor addon with extra historic characters should not add those characters to the fresh Collector snapshot automatically.

Report `Collector records / exact identity matches / unmatched / conflicts`. Successfully joining local saves does not automatically link those GUIDs to WoWthing's anonymized database IDs; use the now-named local observations directly unless a separate reliable website mapping exists.

## 4. Preserve a repeatable acquisition record

Save the selected source references, addon/schema version, observation/import dates, identity provenance and field-specific limitations in the setup record. Record the reusable reader/tool name, documented transport and pointer to its environment instructions in the private setup note so future runs can repeat acquisition. Keep credentials and host/key configuration in the user's existing private environment configuration, outside portable skills and shared plans. Future updates should reuse the chosen data source and verify freshness and identity.

Before requesting a repeat scan, distinguish:

| Situation | Correct next action |
| --- | --- |
| Public field hidden, fresh local field present | Use the authorized local observation; no repeat scan. |
| Supplied export stale | Use a newer available snapshot or request an updated export. |
| Collector GUID lacks name, ATT exact identity exists | Join identity; no privacy change or login required. |
| Old identity evidence conflicts with current evidence | Preserve conflict and resolve narrowly; do not guess. |
| Correct fresh file genuinely lacks a needed observation | Guide only the missing character/window scan. |
| Website count differs from local count | Explain coverage/filtering; do not discard extra local records. |

Validate identity recovery with cases containing nested boss names, duplicate GUID occurrences, unrelated item-link GUIDs, same names on different realms, and a genuinely unmatched character. A populated table does not establish field completeness; follow onboarding's coverage checks after the join.
