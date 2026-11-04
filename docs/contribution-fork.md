# Contribution fork

Third-party contributions are staged in the owner's fork of a registered upstream (`config/public-contributions.yaml`). Agents push branches to the fork and open fork-internal PRs labelled `upstream-ready`. An approving review by the owner on such a PR makes `publish-upstream` in the fork publish exactly the reviewed commit upstream; an `approved-upstream` label applied by the owner on an `upstream-issue` issue files that issue upstream, and on an `upstream-comment` issue titled exactly `Comment on #<N>` posts its body as one comment on upstream issue or PR `#N`. Agents never hold the publishing token.

## Layout

- The fork's default branch is the upstream default branch plus the template commit on top.
- Agent branches start from the upstream default commit, not from the fork's default branch, so the upstream diff never carries the template. The gate refuses any reviewed commit whose diff against upstream touches `.github/` or `publish/`.
- An approved PR `#N` is published from the fork branch `upstream/pr-N`; a later approval force-updates that branch and edits the open upstream PR instead of opening another.
- Approve a PR only once its `contribution-ci` run is green: an approval while CI is still running is refused, so approve again after it passes.
- Issue and comment drafts are published once. A marker comment on the fork issue records the upstream URL; if that marker is lost, the gate finds the upstream issue (open or closed) or comment by the owner with the same text instead of publishing it again. The draft type, title and body must be unchanged since the owner's label.

## Install (owner, once per fork)

The NPA bot can't push workflow files, so the owner does this with their own account. Until step 4 is done, upstream's own workflows (`pull_request_target`, `issue_comment`, schedules, pushes with `contents: write`) would run in the fork, so keep the order.

1. In the fork's Settings → Actions → General, set Workflow permissions to "Read repository contents and packages permissions" and leave "Allow GitHub Actions to create and approve pull requests" off. Every fork workflow's `GITHUB_TOKEN` is then read-only unless the workflow asks for more.
2. Render the template into a checkout of the fork's default branch, then commit and push it as the owner:

   ```sh
   python3 templates/render_contribution_fork.py omnigent <checkout of lkshrk/omnigent main> > disable-workflows.sh
   ```

   This writes `.github/workflows/contribution-ci.yml` (the registry's validation profile on `ubuntu-latest`, installs with `--ignore-scripts`), `.github/workflows/publish-upstream.yml` and `publish/` (`gate.py`, `contribution.json`). It prints one `gh workflow disable <file> --repo lkshrk/omnigent` line for every other workflow file in the checkout.
3. Add branch rulesets on the fork, each with the Repository admin role as the only bypass:
   - the default branch: restrict updates, restrict deletions, block force pushes. Only the owner then moves `publish/gate.py` and `publish/contribution.json`, which run with the owner's token and which the approver trusts;
   - `upstream/**`: restrict creations, updates and deletions, block force pushes, so only the publisher moves the published branches.
4. In the fork's Actions tab, enable workflows (GitHub disables them on new forks), then run `sh disable-workflows.sh` as the owner straight away. Check with `gh workflow list --repo lkshrk/omnigent --all` that only `contribution-ci` and `publish-upstream` are active.
5. Create the labels:

   ```sh
   for label in upstream-ready upstream-issue upstream-comment approved-upstream; do gh label create "$label" --repo lkshrk/omnigent; done
   ```

6. Create the environment `upstream-publish` (Settings → Environments) with no required reviewers and no deployment branch rule: `pull_request_review` runs on the pull request's merge ref, which a branch rule wouldn't match. As the owner account named in `publish/contribution.json`, create a classic PAT with only the `public_repo` scope and store it as the environment secret `UPSTREAM_PUBLISH_TOKEN`; only `publish-upstream.yml` names that environment. Never give the token the `workflow` scope, and don't add a repository secret of that name. The gate refuses a token whose `GET /user` login isn't the owner, because its duplicate and marker checks trust the owner's authorship. If the go-live checks show that the publish job gets no environment secret on `pull_request_review`, store the token as a repository Actions secret instead; the `environment:` line can stay.
7. Give the NPA bot its fork token: install the octo-sts GitHub App on `lkshrk/omnigent`. The `lkshrk/.github` trust policy `agent-stack-omnigent-writer` grants contents, pull requests and issues write there (never workflows), and h-cloud's token minter mints it as the `omnigent` key, which agents read from `/agent-secrets/github/omnigent`.

### Go-live checks

Prove each of these on the fork before the first real contribution:

- A push by the NPA bot that touches `.github/workflows/` is rejected.
- A push, force push or deletion of the default branch by the NPA bot is rejected, and so is a push to `upstream/test`.
- A `contribution-ci` run's "Set up job" log shows only `Contents: read` for `GITHUB_TOKEN`.
- `gh workflow list --repo lkshrk/omnigent --all` lists every other workflow as disabled.
- An approving review on a test pull request runs `publish-upstream`, and the gate gets `UPSTREAM_PUBLISH_TOKEN` from the environment (otherwise use the fallback in step 6).

## Working on a contribution

- The implementer runs `agent-stack-contribute prepare omnigent`. It clones the upstream default branch into `/home/omnigent/work/omnigent` (remote `origin`) and adds the fork as remote `fork`; agents never clone or change remotes themselves.
- Validation never runs in the agent's checkout. The agent pushes its branch to `fork`, opens a PR inside the fork and waits for `contribution-ci` to go green before it adds `upstream-ready`.
- A pull request that conflicts with the fork's default branch has no merge ref, so neither `contribution-ci` nor `publish-upstream` runs on it and an approval does nothing. Sync the fork, then have the agent rebase the branch onto the upstream default branch and push again.

## Signal approvals

`contribution-approver` (`cmd/README.md`) posts each ready pull request to the owner's Signal group with a single-use code. Replying `approve <code>` from the owner's number submits the owner's approving review pinned to the announced head SHA, which runs `publish-upstream` like a review in GitHub; `reject <code>` requests changes. signal-shim routes those replies straight to the approver, never through Hermes. Before announcing, the approver runs the gate's own checks for the title format and an upstream diff that touches `.github/` or `publish/`, and skips what the gate would refuse. A code is bound to what the owner read: if the head, title, body or `upstream-ready` label changed since the announcement, the reply is refused and the pull request is announced again with a new code. The approval confirmation links the fork's `publish-upstream` runs, where the gate reports whether it published. The approver holds its own copy of the owner's PAT (`public_repo` only) and is the only cluster component that does.

## Upkeep

- Keep the fork current with GitHub's "Sync fork"; it merges upstream into the default branch and keeps the template commit. After each sync, re-render into a fresh checkout and run the printed `gh workflow disable` lines: workflows that upstream added arrive enabled.
- Re-render and push again whenever the registry entry changes; `templates/tests/test_render.py` keeps the committed Omnigent rendering in step with the registry.
- If publishing fails because GitHub wants the `workflow` scope (an upstream commit the fork hasn't synced changed workflows), sync the fork and have the agent rebase; don't widen the token.
