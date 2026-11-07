---
name: upgrade-h-cloud
description: "Start the infra agent's Renovate sweep on lkshrk/h-cloud. Use when the owner asks to upgrade h-cloud, run the Renovate sweep or work the h-cloud Renovate PRs."
version: 1.0.0
metadata:
  hermes:
    tags: [h-cloud, Renovate, Upgrades, Omnigent]
    category: devops
---

# Upgrade h-cloud

The sweep runs in the Omnigent `infra` agent, not here. You only start it: never look at, merge, comment on or change h-cloud's Renovate PRs, branches or cluster yourself, even when the owner's request names a PR or a change.

1. The owner's request text is the sweep's input, as written. When it says no more than "upgrade h-cloud" or "run the Renovate sweep", pass no text and the agent gets its default task.
2. Run exactly one command, with the text as one single-quoted argument (write each `'` inside it as `'\''`):

   ```sh
   runner-start infra '<request text>'
   ```

3. Reply once, short, from its output:
   - `started infra card <id> …` (exit 0): `Started the h-cloud sweep (card <id>). I'll report when it's done.`
   - `not started: infra card <id> is still <status>` (exit 3): `A sweep is already <status> (card <id>); not starting another.` When the status is `blocked`, add that archiving that card (`hermes kanban archive <id>`) clears the way for a new sweep, and leave that to the owner.
   - anything else: one 🔴 line with the error output. Don't retry and don't create a card any other way.

When the card finishes or blocks, linear-bridge sends the sweep report to the owner over Signal. Do nothing after your reply: don't watch, poll or report the card yourself.
