# Contribution fork

Third-party contributions are staged in the owner's fork of a registered upstream (`config/public-contributions.yaml`). Agents push branches to the fork, open pull requests inside the fork and draft upstream issues as fork issues. `contribution-approver` (`cmd/README.md`) runs in the cluster, checks what the owner approved and publishes it upstream with the owner's PAT. Agents never hold that token, and nothing on the fork configures the approver: its fork settings are compiled in from the registry.

Terms used below:

| Term | Meaning |
| --- | --- |
| fork PR | A pull request inside the fork, from a fork branch into the fork's default branch |
| ready | A fork PR labelled `upstream-ready` |
| announcement | The approver's Signal message for a ready fork PR, with a single-use code |
| approval | The owner's approving review on a fork PR, or the owner's `approved-upstream` label on an issue draft |
| publish branch | `upstream/pr-<N>` in the fork, the head of the upstream pull request for fork PR `#N` |
| issue draft | A fork issue labelled `upstream-issue` |
| owner notice | A message from the approver to the owner's Signal group |

## Layout

- The fork's default branch is the upstream default branch plus one commit that adds `.github/workflows/contribution-ci.yml`.
- Agent branches start from the upstream default commit, not from the fork's default branch, so the upstream diff never carries that commit.
- A diff that touches `.github/` is never published.
- Two rulesets protect the fork's default branch and `upstream/**`. Only the admin role bypasses them. The owner's PAT has that role; the agents' octo-sts token never has it.

## Add a fork

Do these steps in order. The agents' token must never cover a fork that has no rulesets yet, so the policy line comes after the setup.

1. **Fork.** Fork the upstream into `lkshrk/<name>` on GitHub, keeping the upstream's default branch. The setup refuses a fork that doesn't exist.
2. **Registry entry.** Add the upstream to `config/public-contributions.yaml`, then run `go generate ./cmd/contribution-approver`. That renders `cmd/contribution-approver/forks.json`, which the approver embeds. `templates/tests/test_approver_config.py` fails if the two differ.
3. **Release.** Merge to `main` with a `feat:` or `fix:` commit, so semantic-release cuts a tag that contains the entry, and builds the images.
4. **Setup.** Run the setup as the owner, from a clean checkout of the release tag, then check the result:

   ```sh
   git clone https://github.com/lkshrk/agent-stack.git && cd agent-stack
   git checkout v<release>
   gh auth refresh -s workflow
   scripts/agent-stack-fork-setup <name>
   scripts/agent-stack-fork-setup <name> --check
   ```

   `<name>` is the registry key, for example `omnigent`. `--check` must report no drift.
5. **App coverage.** Before the policy line merges, check that the octo-sts GitHub App's installation covers the fork: in GitHub, Settings → Applications → Installed GitHub Apps → octo-sts → Configure, "Repository access" must show "All repositories" or list every fork the policy will name, the new one included. The token minter mints every token in one run and stops at the first failed exchange; an exchange fails when the policy names a repository the App can't see, and then every agent token stops within about an hour, Hermes's included.
6. **Policy line.** In `lkshrk/.github`, add the fork's repository name to `repositories:` in `.github/chainguard/agent-stack-fork-writer.sts.yaml`. Keep its permissions as they are: contents, pull requests and issues write, never administration or workflows. The token minter mints this policy as the `forks` key, and Polly's sandboxes mount it at `/agent-secrets/github/forks`. octo-sts reads only the merged policy and caches it for 5 minutes, so the exchange against the new repository set can only run after the merge: wait for a token-minter run (every 20 minutes) that starts at least 5 minutes after the merge, and check that it succeeded, with `kubectl -n omnigent-sandboxes get jobs --sort-by=.metadata.creationTimestamp` showing the newest `token-minter` Job as complete. If it failed, revert the policy line at once.
7. **h-cloud pins.** Pin the release in h-cloud: `agent-tools` for the approver, which then watches the new fork, and `omnigent-host` for the agents' copy of the registry. The agents' git credential helper, and `gh` called with `-R`/`--repo`, use the `forks` token for every fork in that copy and the `code` token for everything else; if the registry check fails, they refuse. The token scopes are the boundary, not the wrappers (`images/README.md`).

The `omnigent-host` pin may come before steps 4 and 5. The wrappers give a fork only the `forks` token and refuse while `/agent-secrets/github/forks` is missing, so until the policy covers the fork, its writes just fail.

The setup refuses to run unless all of these hold:

- HEAD is exactly a tag, the checkout is the repository's top level, and the tree is clean, untracked files included.
- PyYAML is installed system-wide, for example `python-yaml` on Arch. The script re-runs itself in Python's isolated mode (`-I`), which ignores the user site, so `pip install --user` does not help.
- The `gh` login is the fork owner, and its token has the `workflow` scope. Committing `contribution-ci.yml` needs that scope; `gh auth refresh -s workflow` adds it.
- The fork belongs to the owner, is a fork of the registry upstream and has the registry's default branch.

It then runs these steps:

| Step | What it does |
| --- | --- |
| 1 | Creates or fixes the labels `upstream-ready`, `approved-upstream`, `upstream-issue` and `upstream-comment` |
| 2 | Creates or fixes the rulesets `contribution-default-branch` and `contribution-upstream-branches`: creation, update, deletion and force push restricted, the admin role as the only bypass |
| 3 | Shows the diff between the fork's `contribution-ci.yml` and the registry render and asks `[y/N]`; "no" stops here |
| 4 | Commits `contribution-ci.yml` to the fork's default branch |
| 5 | Enables Actions and the CI workflow |
| 6 | Disables every other workflow; GitHub's `dynamic/` workflows are only reported |
| 7 | Sets read-only default workflow permissions and turns off "Allow GitHub Actions to create and approve pull requests" |

A second run changes nothing. If the setup says GitHub doesn't list the CI workflow yet, run it again a minute later.

## Owner's flows

### Approve a fork PR

1. An agent opens a fork PR and labels it `upstream-ready` once `contribution-ci` is green.
2. The approver announces it to the Signal group once per head commit: title, the first 300 characters of the body, the commit, diff size, CI result, link and a code. It announces only when all of these hold:
   - CI on the head commit is green: every `contribution-ci` run for the commit completed with the conclusion `success`;
   - the title follows the registry's `title_format`;
   - the diff isn't empty or too large and doesn't touch `.github/`;
   - the diffs against the fork's and the upstream's default branch list the same files;
   - the fork PR's base was never changed.

   A ready fork PR that fails one of these checks gets no Signal message. The approver only logs it.
3. Open the link and read the whole body: the announcement shows only an excerpt, but the whole body is published.
4. After the announcement, the owner approves in one of two ways:
   - submit an approving review on the fork PR in GitHub, or
   - reply `approve <code>` in Signal. The approver then submits the owner's approving review on the announced commit. `reject <code>` requests changes instead.
5. The approver publishes on its next poll, within about a minute.

The code works once and expires after 30 minutes. Replying with an expired code within 24 hours brings a new announcement with a new code, if the fork PR is still ready on the same head. An approving review in GitHub doesn't need the code.

The owner's newest approving, changes-requested or dismissed review decides. Only an approval publishes, and it publishes exactly the commit it was given on. An approval counts only if it came after the announcement of that commit, with the title and body unchanged since. An approval given before the announcement, for example while CI still runs, is ignored: approve again after the announcement. After an approver restart no announcement is known, so the fork PR is announced again and needs a new approval.

What gets published:

- The approver points the publish branch at the approved commit and opens an upstream pull request from `lkshrk:upstream/pr-<N>` into the upstream default branch, with the fork PR's title and body. Maintainers can't push to it.
- If that upstream pull request is already open, the approver moves the publish branch first, then updates the upstream title and body. Once the branch is on the approved commit, it never changes the upstream text again, so a maintainer's retitle stays.
- A commit that a closed or merged upstream pull request carried is never published again. A different commit opens a new upstream pull request only with an owner approval newer than the close.

Owner notices for fork PRs:

| Notice | Meaning |
| --- | --- |
| Published `<PR>` at `<commit>` upstream: `<link>` | The upstream pull request was opened or updated |
| Published …; the code is published, but the title and body may not be updated | A write failed halfway and a retry found the branch already moved; check the upstream text |
| … is already upstream: `<link>`; the title and body were not changed. | The approved commit was already on the open upstream pull request |
| … is already upstream: `<link>`; later changes to the pull request are not published. | The approved commit is upstream, or a closed upstream pull request carried it; later fork PR edits don't count |
| Not publishing …: `<reason>`. The approval was dismissed; approve a new announcement to publish. | A hard refusal; the approval is spent |
| Not publishing …: `<reason>`. The approval could not be dismissed yet; the approver keeps trying. | A hard refusal whose dismissal failed; the dismissal is retried |
| Not publishing …: `<reason>`. No approval was dismissed. | A hard refusal that found no single approval to dismiss, for example when the fork PR has more reviews than the approver reads |
| Not publishing …: the upstream pull request was closed or merged after the approval: `<link>` | Approve again to open a new upstream pull request |
| Not publishing …: a closed or merged upstream pull request already carried `<commit>`: `<link>` | That commit was upstream once and is never published again; a new commit needs a new approval |
| Publishing … waits: the upstream head is taken or not visible yet … | GitHub refused to open the upstream pull request: another open one uses the publish branch, or the branch isn't visible yet; retried on every poll |
| Publishing … waits: moving the publish branch needs the fork synced first … | GitHub refused the branch move because the PAT has no `workflow` scope; sync the fork (see Upkeep); retried on every poll |

Each notice is sent once per approval and reason. A notice that Signal doesn't accept waits for the next poll.

### Approve an issue draft

1. An agent opens a fork issue labelled `upstream-issue`.
2. The owner adds the `approved-upstream` label. The label counts only if the owner added it; anyone else's is removed without a notice. If an agent removes and re-adds the label, the newest label is the agent's, so it is removed and the owner's approval is gone: add the label again.
3. On its next poll the approver files one upstream issue with exactly the draft's title and its body, followed by a blank line and a hidden marker.
4. It comments a publish record with the upstream link on the fork issue, removes `approved-upstream` and closes the fork issue.

The marker is `<!-- contribution-fork:<fork>#<n> -->`, for example `<!-- contribution-fork:lkshrk/omnigent#12 -->`. Every upstream issue body the approver files carries one. GitHub hides it in the rendered issue, but it shows in the raw markdown and in the edit box. The approver finds earlier publishes by this marker, so leave it in place.

Removing `approved-upstream` spends the approval: a reopened draft never publishes again. Adding the label again to a draft that wasn't published approves its current text. On a published draft, a new label only closes it again with the notice that it already has a publish record.

A draft is refused when any of these is true:

- it lacks the `upstream-issue` label, or carries `upstream-comment`;
- its body is empty;
- its title or body contains `contribution-fork:` in any case;
- its title was renamed or its body edited at or after the label;
- a kind label changed after the label, its labels don't match their history, or one of the three repository labels was edited at or after the label;
- its body was never edited and anything happened on it after the label, such as a comment or a label change;
- a publish record on it was edited.

Owner notices for issue drafts:

| Notice | Meaning |
| --- | --- |
| Filed the draft `<fork>#<n>` upstream as an issue: `<link>` | Published |
| The draft … is already upstream: `<link>`; later changes are not published. | An upstream issue with this draft's marker exists from an earlier approval, and the draft changed since |
| The draft … already has a publish record, so it was not published again. | An unedited publish record was on the draft; the draft was closed and nothing was posted |
| Not publishing the draft …: `<reason>`. The approval label was removed; add it again to publish. | A hard refusal; the approval is spent |
| Not publishing the draft …: `<reason>`. The approval label could not be removed yet; the approver keeps trying. | A hard refusal whose label removal failed; the removal is retried |
| Not publishing the draft … yet: `<reason>`. Upstream cannot be searched for an earlier copy right now, so the approval label stays until it can. | The refusal waits until the approver can check upstream for this draft's marker |
| Not publishing the draft …: a publish record on the draft was edited; delete the edited publish record comment on the draft, then approve again. | Delete that comment, then add the label again |

## Comment drafts are off

A draft labelled `upstream-comment` is refused with "comment drafts are not published yet; file it as an issue draft or post it by hand". The refusal removes `approved-upstream`, so approving a comment draft spends the approval. Once comment drafts come back, approve it again.

## Behaviour changes

- The owner approves after the Signal announcement, which comes once CI is green. An approval given before the announcement is ignored.
- If CI on the approved commit is re-run after the announcement, the approval waits for that run and publishes once it is green.
- A hard refusal spends the approval. On a fork PR the approver dismisses the owner's approval and gives the reason. On an issue draft it removes `approved-upstream`. Examples: an edit after the approval, a `.github/` change, mismatched diffs, CI that completed without `success` (`neutral` and `skipped` count as failed), a changed base, or a closed and reopened fork PR.
- A transient refusal changes nothing and retries on the next poll. Examples: a CI re-run still running, or a GitHub API error.
- If the approver is down, issue-draft approvals wait and publish on its first poll after it is back. Fork PRs are announced again after a restart and need a new approval after that announcement.

## Accepted residuals

- **A maintainer removes the trace.** The approver files an issue draft, but its answer is lost or it restarts before it writes the publish record. If a maintainer deletes the marker from the upstream issue, or transfers, deletes or converts that issue, in that window, the next poll files the draft a second time.
- **An edit in the same second as the label.** For a draft whose body was never edited, only GitHub's `updated_at` shows later activity, and only to the second. If GitHub ever drops an edit from a draft's edit history, an edit in the same second as the owner's label would publish unseen. A go-live check covers whether GitHub drops edits; if it does, issue drafts don't go live until draft approvals are bound to a Signal announcement of the draft's content.
- **A draft edited while the owner reads a stale page.** The label approves the draft's content at the moment it is applied, not what the owner read. If an agent edits the draft after the owner opened it and before the owner adds the label, the edited text is published. Reload the draft right before adding the label.
- **Label denials apply only inside a contribution checkout.** The agent policy refuses `upstream-comment` and `approved-upstream` only for `gh` calls made inside a registered contribution checkout. From any other directory, an agent can still set either label on a fork issue with `-R`. The approver bounds it: it refuses comment drafts with a notice, and it counts only the owner's own `approved-upstream` label.
- **The unedited check on publish records works at second precision.** A publish record counts as unedited when its `updated_at` equals its `created_at`. An edit in the same second the approver wrote the record passes as unedited. An agent would have to beat the approver's own write.
- **A second refusal notice for one approval.** If removing `approved-upstream` after a refusal fails and an agent then removes `upstream-comment`, the owner gets a second, different refusal notice for the same approval. Both refusals are true.

## Go-live checks

Prove each of these before the first real contribution, and approve nothing real until the list is done. Run this phase only after the first fork's rollout is complete, after the pre-merge `READS PASS` re-run of the octo-sts proof and the Kyverno follow-up (merge order in h-cloud's `kubernetes/apps/ai/contribution-approver/README.md`): that proof checks that the App covers exactly `lkshrk/omnigent`, so a stand-in onboarded earlier makes it fail. Use a stand-in: a throwaway upstream repository in an organisation the owner owns, with its fork `lkshrk/<name>` as a temporary registry entry. `lkshrk/omnigent` can't be used, because it sits in Omnigent's fork network, and the registry refuses an upstream owned by `lkshrk`.

- [ ] The setup clears the fork's "Actions disabled" banner through the API.
- [ ] Disabling a workflow works before Actions are enabled. If it does, the setup can disable upstream workflows before it enables Actions.
- [ ] GitHub lists a newly committed workflow while Actions are disabled.
- [ ] GitHub allows disabling a `dynamic/` workflow, or refuses it.
- [ ] `gh` OAuth tokens send `X-OAuth-Scopes`, so the setup's scope check works.
- [ ] A setup that fails at step 6 leaves Actions on, with upstream workflows active, until it runs again: note how that looks and how to spot it.
- [ ] A bot push to the default branch or to `upstream/**`, and a bot push that touches `.github/workflows/`, are rejected.
- [ ] A `contribution-ci` run's "Set up job" log shows only `Contents: read`, and the bot token can read fork CI.
- [ ] A fork PR approved in GitHub and one approved over Signal each publish once.
- [ ] A non-owner review publishes nothing, and a stale code publishes nothing.
- [ ] A commit pushed after the review isn't published.
- [ ] A body edit after the review is refused, and the approval is dismissed.
- [ ] A later reject or dismissal stops a republish.
- [ ] Closing the upstream pull request doesn't reopen it.
- [ ] One issue draft publishes, and a comment draft is refused with the comment-drafts notice.
- [ ] A draft's `updated_at` equals the second of its `approved-upstream` labeled event.
- [ ] GitHub never drops `userContentEdits` revision nodes. If it does, issue drafts don't go live: draft approvals must first be bound to a Signal announcement of the draft's content.
- [ ] `Label.updatedAt` when a label is created and applied in the same second.
- [ ] A collaborator's edit of an owner comment bumps its `updated_at`.
- [ ] Reactions on a comment, or hiding it, bump its `updated_at`.
- [ ] The HTML comment marker survives edits in GitHub's web editor.
- [ ] The issues list with `since` includes closed issues.
- [ ] Renaming a repository label rewrites the name in past `labeled` events.
- [ ] Moving `upstream/pr-<N>` onto a commit whose history adds a workflow the fork lacks fails with a message containing "without `workflow` scope", which the approver matches to send the sync notice.
- [ ] Opening an upstream pull request on a head that another open pull request uses, and right after creating the branch, fails with "A pull request already exists" or `field: head, code: invalid`. The approver waits on those texts; other wording would count as a hard refusal and dismiss a valid approval.
- [ ] In h-cloud, `CONTRIBUTION_FORKS` is dropped in the same change as the `agent-tools` pin bump, because 1.22.0 exits without it.
- [ ] Afterwards, remove the stand-in's policy line, wait for a token-minter run that starts at least 5 minutes after that merge and succeeds, and only then delete both repositories. Then drop the stand-in from the registry, release, and pin `agent-tools` in h-cloud again; until then the approver logs a fork check error on every poll.

## Working on a contribution

- The implementer runs `agent-stack-contribute prepare <name>`. It clones the upstream default branch into `/home/omnigent/work/<checkout>` (remote `origin`) and adds the fork as remote `fork`. Agents never clone or change remotes themselves.
- Validation never runs in the agent's checkout. The agent pushes its branch to `fork`, opens a fork PR and waits for `contribution-ci` to go green before it adds `upstream-ready`.
- A fork PR that conflicts with the fork's default branch has no merge ref, so `contribution-ci` doesn't run on it and it is never announced. Sync the fork, then have the agent rebase the branch onto the upstream default branch and push again.

## Upkeep

- Keep the fork current with GitHub's "Sync fork". It merges upstream into the default branch and keeps the CI commit.
- After every "Sync fork", run `scripts/agent-stack-fork-setup <name> --check` from a release tag checkout. Workflows that upstream added arrive enabled, and `--check` reports them as drift. `--check` writes nothing; it only warns when the checkout isn't a clean release tag, and it still needs the `workflow` scope. Fix drift by running the setup without `--check`.
- After a registry entry changes, release and pin it in h-cloud, then run the setup again.
- Keep `repositories:` in lkshrk/.github `agent-stack-fork-writer.sts.yaml` equal to the registry forks; nothing checks it.
- Never delete, rename or transfer a fork that `repositories:` still lists, and never remove it from the octo-sts App's installation: the next token-minter run fails and every agent token stops. Remove the policy line first and wait for a successful minter run.
- Moving `upstream/pr-<N>` needs the fork synced. If the approved commit's history carries upstream workflow changes the fork lacks, GitHub refuses the move, because the PAT has no `workflow` scope. The approver tells the owner once per approval that the move needs the fork synced first, and retries on every poll. Syncing the fork clears the wait, and the approval then publishes. Rebase only if the sync doesn't clear it; a rebase makes a new head that needs a new approval. Don't widen the token.
