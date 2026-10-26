# Portable saved-variable importer

Use [scripts/import_saved_variables.py](../scripts/import_saved_variables.py) for supplied `WoWthing_Collector.lua` and `AllTheThings.lua` exports. Python 3.9+ and its standard library are sufficient. It does not connect to a game host, use network credentials, execute Lua, modify source saves, or edit Markdown task status.

This adapter has verified **Retail** format fixtures only. It rejects a **declared** Classic/Forever game before any writes; it cannot independently authenticate the client that produced raw saves. Use version-scoped manual observations until a matching adapter and fixtures are verified. A familiar filename or GUID does not establish the game version. The caller must verify the version and environment from trustworthy export/client evidence, or ask the user when unclear, before selecting CLI context flags. PTR/beta context labels isolate observations; they do not establish compatibility with future addon schemas.

## Run

```sh
python3 /path/to/wow-account-setup/scripts/import_saved_variables.py \
  --collector /path/to/WoWthing_Collector.lua \
  --att /path/to/AllTheThings.lua \
  --account main \
  --region eu \
  --game retail \
  --environment live \
  --output /path/to/account-observations.json \
  --report /path/to/account-coverage.md
```

Use a stable user-chosen account alias, actual region, explicit `--game retail` and verified `--environment live|ptr|beta`. Import another account, game or environment into a different output. Source filenames/paths are provided by the user/environment, not discovered through machine-specific connection setup.

The same command can refresh an existing output. Only a compatible `wow-account-observations` snapshot for the same account/region/game/environment is accepted. Identical GUIDs across contexts are unrelated. Existing character records absent from the new export are retained and marked not seen in this import; absence does not mean deletion. Missing fields keep prior observations and dates. Repeated identical data does not duplicate characters or reset user progress.

Existing schema-1 snapshots without game/environment stop with an actionable error. After verifying that **both the existing snapshot and supplied exports are Retail in the selected environment**, rerun with `--adopt-legacy-retail`. This explicitly upgrades schema 1 to schema 2 while preserving retained observations and user notes. The flag cannot override a schema-2 context mismatch, an unknown schema, or another account/region. Never apply it merely because an old file lacks context; if its origin cannot be verified, keep it separate and create a new output.

Outputs must differ from inputs and each other. The JSON is replaced atomically after parsing/validation; input or format errors leave the previous snapshot intact. Existing non-generated Markdown is never overwritten. Snapshot/report writes are individually atomic, not a multi-file transaction; if report writing fails after the JSON succeeds, the CLI explicitly reports that the snapshot was saved. Do not run concurrent importers against one output; the script detects output changes during processing but is not a multi-writer database.

## What is normalized

| Observation | Handling |
| --- | --- |
| Character identity | Exact Collector GUID → direct `ATTCharacterData` GUID/name/realm; nested boss names, item-link GUIDs and unrelated addon tables do not participate |
| Reputation | Recorded faction IDs and Collector values, separated into character and account scopes; values still need faction definitions for standing, renown or friendship interpretation |
| Currency | Decodes the seven-field Collector format; preserves observed zero, omits its no-currency sentinel, and treats absent IDs as unknown |
| Profession | Partial skill-line entries, skill values and known **skill-line ability IDs**, not a complete recipe/spell roster or automatic eligibility proof |
| Achievement | Selected **character-earned** flags and available criterion quantities; not account-wide completion, complete catalog coverage or account AP |
| Inventory | Recorded containers/slots with item IDs, quantities and original item encoding; bag and bank scan times remain separate; missing containers/items do not establish absence everywhere |
| Shared bank | Recorded shared containers under account scope, with their scan timestamp |
| Character basics | Observed level, copper, current location and bind location; missing scalar fields do not clear prior values |

Account AP remains null. Do not sum Collector character achievements or promote a character-incomplete flag into an account-incomplete fact. Obtain account completion/AP from a validated account source and reconcile it in the planning layer.

The parser supports saved-variable literal assignments, tables, string/integer keys, quoted UTF-8 strings (including decimal byte escapes), numbers, booleans, nil and line comments. Calls, functions, expressions, duplicate keys, malformed data, excessive nesting and files over 64 MiB fail closed. It is not a general Lua interpreter. Unknown field encodings are marked unsupported and retained as raw field data; they are never guessed into valid quantities.

## Output contract and freshness

The JSON has `kind: wow-account-observations`, `schema_version: 2`, `game: retail`, `environment: live|ptr|beta`, account/region, import time, source filename/hashes, Collector format version, `characters`, `account_observations`, `coverage` and limitations. The context applies to every character and account observation, and appears prominently in the report. Each GUID record has:

- Identity status (`matched`, `unmatched`, `conflicting`), direct identity evidence, and whether identity was present in this import or retained.
- Last-seen time and `present_in_latest_import`; an ATT-only historical character is not added to the current Collector roster.
- Per-field `observations`: scope, partial/unsupported/conflicting status, normalized values, source hash, exact scan clocks, notes and whether the field was present in this import.

`partial` describes source coverage, not necessarily uncertainty in each present value. A recorded quantity can be useful while absent IDs remain unknown. Undated professions are not assigned the character's last-seen time as a fabricated profession scan time. Mixed inventory retains its component clocks rather than displaying a falsely fresh single timestamp.

Newer dated observations replace older ones. Differing values with older, equal, absent or incomparable clocks retain the last observation and place the incoming candidate in a conflict record. Missing fields retain the prior value/date. Same data and clocks do not create a conflict. Inventory refreshes require non-regressing bag/bank clocks; inspect conflicts rather than assuming every container has the newest timestamp. A newer valid observation can resolve an earlier field conflict.

Keep user-owned notes, manual completion, deferrals and feedback under a root or per-character `user` object. These survive refreshes; importer-owned identity/observations/coverage metadata may change. User task notes are never touched. Do not use the output JSON as a second editable checkbox store.

## Handoff to planning and views

This is the **observation dataset**, not `wow-achievement-dashboard`'s view JSON. The importer does not invent task names, base AP, categories, routes, prices, eligibility, timings or research. Feed these scoped observations into the planner; then populate the dashboard's separate schema and canonical task notes with researched decisions. A blank dashboard field is preferable to manufactured facts.

Onboarding uses the Markdown report to identify exact gaps. Resolve identity conflicts before naming an eligible executor; request a missing in-game scan only when data is genuinely absent, not merely hidden on a public website. Preserve dates and scope through every downstream transformation.

## Verify

```sh
python3 /path/to/wow-account-setup/scripts/check_import.py
```

The check uses synthetic fixtures and temporary outputs. It covers safe parsing, exact identity joins, scope separation, real zeros, partial coverage, clock conflicts, repeated/partial imports, preserved user metadata and CLI failure safety. It never reads the originating user's account or environment. Inspect actual source format/coverage when importing a different addon version; passing fixture tests is not evidence of a complete account scan.

Context regressions cover fresh and repeated independent live/PTR/beta snapshots sharing an account/GUID with distinct values; declared unsupported-game rejection without output creation; partial context, unknown schema, invalid environment and malformed nested snapshot rejection without changing JSON, report or inputs; explicit schema-1 adoption into each environment; preserved manual notes; and repeated multilingual imports. Synthetic PTR/beta cases test context isolation, not verified real-client addon compatibility.

Encoding references: [Collector reputations](https://github.com/ThingEngineering/wowthing-collector/blob/main/Modules/Reputations.lua), [currencies](https://github.com/ThingEngineering/wowthing-collector/blob/main/Modules/Currencies.lua), [character achievements](https://github.com/ThingEngineering/wowthing-collector/blob/main/Modules/Achievements.lua), [professions](https://github.com/ThingEngineering/wowthing-collector/blob/main/Modules/Professions.lua), [inventory encoding](https://github.com/ThingEngineering/wowthing-collector/blob/main/Core.lua).
