# World of Warcraft skills

A set of skills that turn your AI assistant (ChatGPT, Claude or Codex) into a World of
Warcraft helper. Ask in plain language — no commands or coding needed.

## What you can ask

| Skill | What it does | Try asking |
| --- | --- | --- |
| `wow-help` | Quick answers about items, quests, vendors, reputation and game mechanics, with map waypoints. | "Where do I get this mount?" |
| `wow-spec` | Talent builds (with copyable import strings), stats, gems, enchants and consumables. | "Best Frost Mage build for Mythic+?" |
| `wow-account-setup` | One-time setup: connects your existing character data so plans fit *your* account. | "Set up my WoW account for achievement planning." |
| `wow-achievement-plan` | A personal achievement or progression checklist: quests, reputation, professions and items, with realistic times and eligible characters. | "Plan my TBC Anniversary reputation grind." |
| `wow-achievement-dashboard` | An overview of your plan: next actions, progress and routes, with static checklists for non-achievement goals. | "Make a dashboard for my achievement plan." |
| `wow-duo-world-tour` | A shared achievement or progression route for you and a friend, showing who gets credit for what. | "Plan a 2-hour quest and profession tour for us." |

## Install

Download [wow-skills.zip](https://github.com/lkshrk/useful-skills/releases/latest/download/wow-skills.zip)
and double-click it. You get a folder with one file per skill. Don't unzip those.

### ChatGPT

1. Open **Plugins** in the sidebar, then the **Skills** tab.
2. Click **Create** → **Upload from your computer** and pick a file from the folder.
   Repeat for each skill you want.
3. Start a new chat and ask away. ChatGPT picks the right skill by itself.

Web and desktop app keep separate skill lists, so upload in each one you use.
To update, download and upload again.

### Claude app

1. Open **Customize → Skills**.
2. Click **+** → **Create skill** → **Upload a skill** and pick a file from the folder.
   Repeat for each skill you want.

### Claude Code, Codex

```sh
npx skills add lkshrk/useful-skills --skill wow-help wow-spec wow-account-setup wow-achievement-plan wow-achievement-dashboard wow-duo-world-tour
```

## Getting started

1. For quick questions and builds (`wow-help`, `wow-spec`), just ask — no setup.
2. For personal achievement plans, start with *"set up my WoW account"*. The
   assistant uses data you already have — e.g. a WoWthing or Simple Armory
   profile (sites that track your characters), or addon data saved in your
   WoW folder — and only asks for the missing pieces.

## Example commands

Paste these into Codex chat to invoke a skill explicitly. In ChatGPT or Claude,
you can use the request after the `$skill-name` as a plain-language prompt.
Replace the game version, character details and goals with your own.

### Quick help (`wow-help`)

```text
$wow-help In Retail, how do I unlock flying in Battle for Azeroth zones? Include any prerequisites.
$wow-help In MoP Classic, where is the Alliance Shado-Pan quartermaster, and what can I buy at Revered? Include TomTom waypoints.
```

### Builds and talents (`wow-spec`)

```text
$wow-spec Recommend a current Retail Frost Mage Mythic+ build with a talent import, stat priorities, gems, enchants and consumables.
$wow-spec Give me a MoP Classic Protection Paladin raid setup using Wowhead as the source. Include talents and glyphs.
```

### Account setup (`wow-account-setup`)

```text
$wow-account-setup Set up my Retail account for achievement planning. Help me find usable character data and save the setup as plain Markdown.
$wow-account-setup Resume setup for our two Retail accounts. Check existing sources and ask only for missing character identities or scans.
```

### Personal plans (`wow-achievement-plan`)

```text
$wow-achievement-plan Using my configured Retail account, plan 90 minutes of unfinished Pandaria achievements. Include eligible characters and TomTom waypoints; save as Markdown.
$wow-achievement-plan Plan my TBC Anniversary Cenarion Expedition reputation grind to Exalted. Show prerequisites, repeatable steps and realistic time estimates.
```

### Dashboards (`wow-achievement-dashboard`)

```text
$wow-achievement-dashboard Turn my existing Retail achievement plan into a dated Markdown dashboard with progress and next actions.
$wow-achievement-dashboard Refresh my existing MoP Classic achievement dashboard in Obsidian using current data and preserve my manual notes.
```

### Two-player tours (`wow-duo-world-tour`)

```text
$wow-duo-world-tour Using our configured Retail accounts, plan a two-hour Pandaria achievement route for us. Show who needs each objective and who gets credit.
$wow-duo-world-tour Plan a 90-minute quest and profession route for our two TBC Anniversary characters. Check that we can group and save a shared Markdown checklist.
```

## Good to know

- **Game versions:** use Retail, a specific Classic variant, or Forever. If the
  version isn't clear, the assistant asks and remembers it for that character or
  plan. It uses the matching sections of existing sources such as Wowhead,
  Method, Raider.IO and Warcraft Logs, and explains any coverage gap.
- **Current tool coverage:** the bundled player-state importer and automated
  build/season-refresh helpers remain Retail-specific. The achievement views
  support Retail and MoP Classic live data. Other contexts and progression goals
  use ordinary Markdown plans; addon/API compatibility is checked before import.
- **Output:** plans are saved as simple checklist files you can open anywhere.
  If you use [Obsidian](https://obsidian.md) (a notes app), they can go there instead.
- **Waypoints:** routes come as commands for the [TomTom](https://www.curseforge.com/wow/addons/tomtom)
  map-arrow addon — copy, paste into game chat, follow the arrow.
- **In ChatGPT or the Claude app:** the assistant can't look into your computer,
  so drag any file it asks for (e.g. from your WoW folder) into the chat, and
  download the plans it creates.
- **Sources:** answers link to Wowhead and similar sites so you can double-check.
- **Privacy:** your passwords and login details are never stored, and no addons
  are installed without you.
