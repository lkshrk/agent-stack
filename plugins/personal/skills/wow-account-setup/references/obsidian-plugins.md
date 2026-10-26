# Obsidian plugin readiness

Run only when Obsidian is the chosen output, for the intended vault during onboarding or when a requested feature cannot render. Plain Markdown output skips this check and needs no Obsidian installation or plugins. Inspect existing setup first; do not prescribe every installed plugin as a dependency.

## Choose the output mode

| Feature | Requirement | Status |
| --- | --- | --- |
| Markdown instructions, Wowhead links, TomTom copy blocks and ordinary checkboxes | Obsidian itself | No community plugin needed |
| Live Todo/Backlog/Completed task queries | [Dataview](https://blacksmithgu.github.io/obsidian-dataview/queries/query-types/#task), plugin ID `dataview` | Required for the Dataview implementation; ordinary DQL task queries do not require JavaScript queries |
| Interactive dashboard, session/category filters, and JSON-backed shopping/completion views | Dataview plus **Enable JavaScript Queries** | Required for the current `dataviewjs` / `dv.view` layout, including its Todo/Completed wrappers |
| Recurring daily/weekly tasks, dates and Tasks-specific queries | [Tasks](https://github.com/obsidian-tasks-group/obsidian-tasks), plugin ID `obsidian-tasks-plugin` | Optional; use when these features are selected. Dataview task checkbox editing alone does not require Tasks |
| Agent access through Obsidian REST/MCP | [Local REST API](https://github.com/coddingtonbear/obsidian-local-rest-api), plugin ID `obsidian-local-rest-api` | Optional; only for that access method. Accessible vault files or another working connector are alternatives |

No separate chart, map, templating or synchronization plugin is required for the current achievement views. Do not require inline JavaScript queries if the generated notes only use fenced `dataviewjs` blocks. Do not replace a user's working equivalent layout merely to standardize plugin choices.

## Inspect, then guide missing setup

1. Identify the active vault and its configuration directory; do not assume plugin settings are shared between vaults or devices.
2. Check installed plugin manifests/versions, the enabled-plugin list and relevant settings when accessible. Default locations are `.obsidian/plugins/<id>/manifest.json`, `.obsidian/community-plugins.json` and plugin-specific settings. Read only the required fields; do not copy API keys into reports. Presence on disk does not prove enabled or successfully loaded.
3. For the interactive layout, check Dataview's JavaScript-query setting. If disabled, explain which views need it and that it runs code from trusted vault notes. The user can instead choose ordinary task queries and a static dashboard. Continue game-data collection either way.
4. For a missing selected dependency, guide through **Settings → Community plugins → Browse → plugin name → Install → Enable**. If community plugins are disabled, explain that they must be enabled for this mode. Reuse existing authorization for selected setup changes; do not install optional plugins or modify unrelated settings.
5. Dataview's relevant toggle is under **Settings → Dataview → Enable JavaScript Queries**. Verify the actual version's setting label if it differs. Do not hardcode a current plugin version as a permanent minimum.
6. If Tasks is used, inspect its global filter and custom statuses. A required task tag or custom cancelled/deferred status can affect queries; adapt the achievement tasks/views without rewriting the vault-wide convention. Standard source checkboxes remain the manual completion authority.
7. If Local REST API is used, verify the configured endpoint and read access without exposing its token. Plugin installation alone does not establish access. Connection credentials and device-specific setup remain outside the portable achievement workflow.

See [Obsidian's installation instructions](https://obsidian.md/help/community-plugins), [Dataview JavaScript overview](https://blacksmithgu.github.io/obsidian-dataview/api/intro/) and [custom views](https://blacksmithgu.github.io/obsidian-dataview/api/code-reference/#dvviewpath-input) for the chosen mode.

## Verify the behavior, not just installation

Use a disposable test task and view, or the user's existing test fixture; never toggle a real achievement just to test the UI. Check in Reading view or Live Preview:

- The query renders instead of remaining a code block or showing an error.
- A live task checkbox edits the one canonical source task.
- Checked/unchecked transitions change Todo and Completed membership, and unchecking restores the task.
- For the JavaScript layout, the helper/view files and JSON data load, filters work and shopping quantities follow task status.
- Manual checkbox changes leave imported account AP unchanged.

Restore/remove only the disposable test artifacts created for this check. Record **installed**, **enabled**, **configured**, and **runtime verified** separately. If the agent cannot inspect Obsidian's renderer, report that exact gap and request a user rendering check; passing standalone JavaScript tests is not visual or in-app validation.

If required plugins/settings are unavailable or declined, publish usable Markdown source tasks and clearly dated static summaries. Do not leave JavaScript-only view pages as the sole way to reach the tasks or claim automatic refresh in that fallback.
