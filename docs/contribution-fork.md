# Contribution fork

Third-party contributions are staged in the owner's fork of a registered upstream (`config/public-contributions.yaml`). Agents push branches to the fork and open fork-internal PRs labelled `upstream-ready`. An approving review by the owner on such a PR makes `publish-upstream` in the fork publish exactly the reviewed commit upstream; an `approved-upstream` label applied by the owner on an `upstream-issue` issue files that issue upstream, and on an `upstream-comment` issue titled exactly `Comment on #<N>` posts its body as one comment on upstream issue or PR `#N`. Agents never hold the publishing token.

## Layout

- The fork's default branch is the upstream default branch plus the template commit on top.
- Agent branches start from the upstream default commit, not from the fork's default branch, so the upstream diff never carries the template. The gate refuses any reviewed commit whose diff against upstream touches `.github/` or `publish/`.
- An approved PR `#N` is published from the fork branch `upstream/pr-N`; a later approval force-updates that branch and edits the open upstream PR instead of opening another.
- Approve a PR only once its `contribution-ci` run is green: an approval while CI is still running is refused, so approve again after it passes.
- Issue and comment drafts are published once. A marker comment on the fork issue records the upstream URL; if that marker is lost, the gate finds the upstream issue or comment by the owner with the same text instead of publishing it again.

## Install (owner, once per fork)

The NPA bot can't push workflow files, so the owner does this with their own account.

1. In the fork's Actions tab, enable workflows (GitHub disables them on new forks).
2. Render the template into a checkout of the fork's default branch, then commit and push it as the owner:

   ```sh
   python3 templates/render_contribution_fork.py omnigent <checkout of lkshrk/omnigent main>
   ```

   This writes `.github/workflows/contribution-ci.yml` (the registry's validation profile on `ubuntu-latest`, installs with `--ignore-scripts`), `.github/workflows/publish-upstream.yml` and `publish/` (`gate.py`, `contribution.json`).
3. Create the labels:

   ```sh
   for label in upstream-ready upstream-issue upstream-comment approved-upstream; do gh label create "$label" --repo lkshrk/omnigent; done
   ```

4. Create a classic PAT with only the `public_repo` scope and store it as the Actions secret `UPSTREAM_PUBLISH_TOKEN` in the fork. Never give it the `workflow` scope.
5. Add a branch ruleset on the fork targeting `upstream/**` that restricts creations, updates and deletions, with the Repository admin role as the only bypass, so only the publisher moves the published branches.
6. Before go-live, prove that a push by the NPA bot touching `.github/workflows/` is rejected by GitHub.

## Signal approvals

`contribution-approver` (`cmd/README.md`) posts each ready pull request to the owner's Signal group with a single-use code. Replying `approve <code>` from the owner's number submits the owner's approving review pinned to the announced head SHA, which runs `publish-upstream` like a review in GitHub; `reject <code>` requests changes. signal-shim routes those replies straight to the approver, never through Hermes. Commits pushed after the announcement are never approved by that code; the new head is announced with a new code. The approver holds its own copy of the owner's PAT (`public_repo` only) and is the only cluster component that does.

## Upkeep

- Keep the fork current with GitHub's "Sync fork"; it merges upstream into the default branch and keeps the template commit.
- Re-render and push again whenever the registry entry changes; `templates/tests/test_render.py` keeps the committed Omnigent rendering in step with the registry.
- If publishing fails because GitHub wants the `workflow` scope (an upstream commit the fork hasn't synced changed workflows), sync the fork and have the agent rebase; don't widen the token.
