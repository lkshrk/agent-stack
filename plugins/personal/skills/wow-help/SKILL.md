---
name: wow-help
description: Answer practical World of Warcraft questions about reputation, items, vendors, quests, unlocks and game mechanics. Give self-contained instructions with sources and numeric-map-ID TomTom locations using focused research. Use for quick WoW help, not addon development or a full personalized achievement plan.
---

# WoW Help

Answer the question so the player can act without opening a website. Sources support the answer; they are not a substitute for the instructions. Default to a chat answer, not a new guide, database project or vault file.

## Resolve only what changes the answer

For every request, follow [request, client and source languages](shared/wow/language.md).

Resolve the version using [game context and supported capabilities](shared/wow/game-context.md). Use the existing providers' matching sections in [versioned sources](shared/wow/versioned-sources.md).

Reuse the established game version, language and character/faction context. If the version is not obvious, ask which version the user plays; do not assume Retail. Ask a targeted question when an ambiguous item/quest/faction name, ruleset or quest phase changes the result. Accept a name, ID, game link or screenshot; resolve the actual entity in that version rather than guessing from a similar name. Do not request a full character profile for a general mechanics question.

Examples in scope: “How do I earn reputation with this faction?”, “Where can I get this item?”, “What is this item used for?”, “How do I finish this quest?” and “Why is this NPC not showing up?” A request to optimize an entire account or create a broad achievement tour belongs to `wow-achievement-plan` when available; do not silently expand a quick question into that workflow.

## Find enough evidence, then stop

Look up the exact entity for the relevant game version. Start with its Wowhead page, a focused Wowhead guide, or a useful positively voted comment. Usually one to three relevant pages are enough. A clear, applicable guide or comment can be sufficient by itself; do not require a multi-source audit for an ordinary gameplay answer.

Check dates, patch/version and faction where they affect the method. Votes help choose comments but do not make an obsolete strategy current. Use newer corrections or official hotfix notes when they contradict an older popular comment. Claim a comment is positively voted only when its rating is visible; do not invent vote counts. Attribute a comment-based workaround as player-reported when appropriate.

Stop once the answer, required unlocks, execution steps, reward repeatability/limits and locations are supported. Consult another source only for a material gap, conflicting instructions, current bug, or unverified location/limit. If Wowhead is unavailable, use an accessible reliable guide, Warcraft Wiki, official source or relevant player report and identify it. Do not pretend to have read a blocked page. Avoid broad account scans, exhaustive alternative lists, parallel research teams or a history of the mechanic unless the user asks.

## Always state repeatability and limits

For every reputation/progression method or farming recommendation, put a short **Repeatability / limits** line before the instructions. State whether the relevant credit is **one-time**, **repeatable now**, **daily/weekly capped**, **cooldown/lockout-limited**, **rotation/event/spawn-dependent**, or **unverified**. These can combine with resource, access, player/queue and RNG constraints; they are not mutually exclusive categories. A shared line can cover identical methods, but exceptions must be visible beside the affected action.

Classify the **reward the user wants**, not merely the activity. A mob can be killed repeatedly while reputation/loot is first-kill-only or daily; a quest can give repeatable material boxes but reputation only on the first daily hand-in. State separate rules for rep, loot, currency and achievement credit when they differ. One-time quest/treasure credit can be doable now without being a repeatable grind. RNG is not itself a reset gate, and a buff's duration is not necessarily its cooldown.

Where applicable, include the amount/number of eligible rewards, reset cadence and scope (character/account/Warband, difficulty or instance), one-time/first-of-day bonuses, standing/renown ceiling, diminishing returns, currency/acquisition caps, required resources and what happens after the limit. Say whether alts or another method actually bypass it; never assume that they do. Give exact values only when supported; mark an unresolved limit **unknown**, never silently **unlimited**. For reputation, explicitly answer “Can I keep earning rep from this now, and until what standing/cap?” A guide saying “repeatable” without explaining rep eligibility is insufficient evidence of unlimited rep.

Example of the required distinction: **Materials: repeatable hand-ins. Reputation: first eligible hand-in per day only; later hand-ins give none.** Attribute the actual method's rules to its source. Resolve this focused question rather than expanding into broad research.

## Let availability determine the instructions

Before drafting a route, establish each method's access condition: permanent unlock, still-active one-time quest, exact active world quest/event, or unknown. Distinguish **unlocked**, **currently available**, and **credit still obtainable**. A vehicle available during a story quest is not evidence that it remains usable afterward; a repeatable world quest is not evidence that it is available now. Check whether completing the quest removes the vehicle/interaction or ends eligible credit before recommending continued farming.

Put the blocking condition **before travel instructions**, in the affected action's heading or opening line, naming the required quest/event exactly. Separate **Prerequisite check**, **Available after the unlock**, and **Only while the required world quest/event is active** when methods have different availability. Do not present gated methods as subsequent steps in one uninterrupted executable tour. Keep simple step numbers matched to waypoints across these sections. An overview table or caveat at the end does not replace the gate beside the action.

For a general answer, give conditional instructions and say when live availability has not been checked; do not claim “do this now.” Check live availability only when the user asks what they can do now, using applicable region/character evidence. Unknown availability stays conditional. When an activity is blocked, state what can still be done independently or that the player must wait—do not send them to an inactive interaction.

Before sending, check: **Could a player follow any numbered action and discover an unstated prerequisite or availability gate? Have I implied unlimited credit from repeatable access?** If so, resolve the evidence gap or move the action into a clearly conditional section. For a multi-criterion achievement, check each criterion separately rather than assigning one availability label to the entire achievement.

## Give the usable answer

Lead with the direct answer or recommended method. Include only the detail needed for the question, but fill in the practical gaps instead of saying “see the guide.” Paraphrase; do not reproduce a whole guide or long comment thread.

Make prerequisite instructions actionable: instead of only “finish the introduction” or “unlock the faction,” name the starting quest and NPC, give its verified waypoint, and say what completion unlocks the requested activity. Do not dump an entire prerequisite history. If the starter is absent and the correct branch depends on character progress, ask a targeted follow-up rather than inventing one universal chain.

| Question | Information the player needs |
| --- | --- |
| Reputation | Which activity/quest/item awards rep, how to unlock and start it, turn-in NPC and location, repeatable loop, daily/weekly caps and relevant standing limits. Separate active farming from time-gated sources. Give rep per turn-in only when supported; distinguish character-bound and Warband reputation when relevant. |
| Where to get an item | Exact item and source: named vendor, quest, recipe, ordinary mob, rare or encounter; location/entrance; difficulty, access, faction, currency/material cost, binding/trading restrictions and lockout/RNG conditions that matter. Say whether the source is guaranteed or a chance, without inventing a drop rate or completion time. |
| What an item is for | What it does, where/how it is used or turned in, what it grants, relevant prerequisites, and whether it is consumed or reusable. If asked whether to keep/sell/delete it, establish remaining uses and replaceability first; appearance or an old comment alone is not evidence it is safe to discard. |
| How/where to do a quest | Exact quest and start/turn-in, necessary preceding unlocks, ordered objective steps, required item/vehicle/extra-action interaction, location/floor/phase and completion check. Explain the easily missed mechanic. If blocked, give a short supported check or workaround and distinguish a known bug from a missing prerequisite. |

For a short factual question, a paragraph may suffice. For a multi-step answer use precise headings such as **Prerequisite check**, **Do this**, and **If blocked**, in the reply language. Keep background separate from actions. Name NPCs, items and quests according to the language guidance and give their direct Wowhead ID links when available. Recommend a practical main method; include alternatives only when they materially help.

Always include clickable sources next to the claims or instructions they support. Link the exact Wowhead entity/guide or comment permalink when available; otherwise link its parent page and identify the relevant commenter/date. Distinguish verified instructions, player-reported workarounds and unresolved uncertainty. Never answer with only a search link, a pasted tooltip or “read the comments.”

## Locations must be copyable TomTom commands

Every actionable location mentioned—including alternative vendors, entrances and turn-ins—needs a verified waypoint in an adjacent fenced `text` block:

```text
/way #MapID X Y StepNumber. Clear destination name
```

Replace MapID with the **numeric UI map ID** for the actual map/floor and version. Never use a region/zone name as the selector or rely on the player's current map. Use spaces between X and Y, no commas. Keep commands alone in the block: no bullets, quotes, Markdown styling or prose. Do not display bare coordinate pairs in the answer or its tables.

Number instructions simply, and use the **same step number** on their waypoints. A task with an entrance and an interior destination uses its number on both pins, with clear suffixes and travel order. A non-travel action needs no invented waypoint. Human-readable action names replace opaque route codes.

Verify location IDs and coordinates; do not invent a pin at a zone's centre. Distinguish a verified entrance pin from the actual indoor objective and explain the path inside. If an exact destination/floor cannot be verified, say what is missing, provide only any verified access pin, and label the limitation rather than using a guessed or named-zone fallback. A numeric map ID does not switch quest phases/timelines: include any required unlock or timeline change separately. Mention TomTom is needed when not already established; multiple commands can be pasted through `/ttpaste`.

When the question is specifically about finding an objective or interaction, prioritize that target/entrance over an incidental quest-giver or return pin. Make a focused lookup for the missing target before settling for directions alone; one detailed route source may resolve it. If it still cannot be verified, keep the limitation explicit—do not present a nearby giver's pin as the objective.

## When the question depends on this player's data

General questions such as an item's purpose or a faction's reputation methods do **not** require an account refresh. Do not add onboarding or database work to them.

Before making claims or recommendations about **the user's** remaining rep, owned items, completed quests, eligible characters or current unlocks, attempt a refresh from the configured local/game-data reader and persist validated observations. Use the private setup record and current environment documentation to find the reader; do not assume a particular mount/transport. Verify identity, complete input and per-field scan dates, preserving existing progress. Reuse a verified refresh from the same request. `wow-account-setup` can handle gaps when installed; this skill must also work with the environment's existing reader without that companion.

If access/import fails or the needed data is stale, conflicting or missing, ask the user for help/advice before asserting their state or choosing a fallback. Explain the attempted source and concrete gap; never request passwords in chat or silently substitute checkmarks/public caches. General sourced instructions may still be provided without claiming personal eligibility. The user may explicitly choose a specified older snapshot or supply current user-reported state for the answer. Preserve its game/character scope, date, provenance and limits; do not call it an API-confirmed refresh. If that choice is already explicit, use it without asking again or repeatedly requesting an unsupported scan.

## Save only when requested

Answer in chat by default. If the user asks to save, use their Obsidian destination or chosen Markdown folder and reuse a matching note where appropriate. Plain Markdown uses standard relative links and ordinary checkboxes, without required plugins or wiki syntax. Register a saved guide in an existing index, keep temporary research outside the destination, and retain the source links. Do not create a dashboard, extra directory tree or one file per lookup automatically.
