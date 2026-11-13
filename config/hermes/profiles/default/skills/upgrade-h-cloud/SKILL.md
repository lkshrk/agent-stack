---
name: upgrade-h-cloud
description: "Work the open Renovate PRs on lkshrk/h-cloud: research each bump, fix and merge it, health-gate every merge on the live cluster and report. Use when the owner asks to upgrade h-cloud, run the Renovate sweep or work the h-cloud Renovate PRs."
version: 2.0.0
metadata:
  hermes:
    tags: [h-cloud, Renovate, Upgrades, Flux]
    category: devops
---

# Upgrade h-cloud

Process the open Renovate PRs on `lkshrk/h-cloud` one at a time: research each release, judge whether its changes matter for *our* setup, merge the clean bumps, apply needed changes for relevant findings, and health-gate every merge. Research decides, never the size of the version jump.

The owner's request text narrows the sweep (for example one PR or one app). "Upgrade h-cloud" alone means every open Renovate PR.

## Prerequisites

Check these first; when one fails, reply with one 🔴 line naming it and stop.

- **GitHub:** `gh` and `git` pick the `hcloud` token for `lkshrk/h-cloud` themselves (`/var/run/agent/github/hcloud`, refreshed every 20 minutes). `gh pr list -R lkshrk/h-cloud --limit 1` must work. Set no `GH_TOKEN`.
- **Fresh checkout:** `~/work/h-cloud`. Clone it with `git clone https://github.com/lkshrk/h-cloud.git ~/work/h-cloud` when missing, then `git -C ~/work/h-cloud fetch --prune origin` and `git -C ~/work/h-cloud switch --detach origin/main` before every PR. Never commit on `main`.
- **Tools:** `just`, `yq`, `jq`, `flate`, `helm`, `crane`, `skopeo` and `rg` are installed; the repository's `.mise.toml` does not apply.
- **Cluster:** `kubectl` and `flux` use the `hermes` service account. `kubectl auth whoami` must print `system:serviceaccount:ai:hermes`. It reads everything except Secrets and may only request reconciles (`flux reconcile …` without `--force` or `--reset`). Without it the sweep can't gate merges: stop before merging anything.
- **Vault:** `~/knowledge/scripts/sync.sh`.

## Limits

- **No Secrets:** `helm history`, `helm get` and `helm rollback` read Helm's release Secrets and fail; use `kubectl get hr -n <ns> <name> -o yaml` (`.status.history`, conditions), events and pod logs.
- **Registries:** `crane ls <registry>/<repo>` and `skopeo list-tags docker://<image>` list tags; `crane manifest`, `crane config` and `skopeo inspect` inspect. Anonymous ghcr reads fail for private packages; that is a skip reason, never a login.
- **Changes you author** stay inside the app directory of the bump (`kubernetes/apps/<ns>/<app>/`). A needed change in `kubernetes/flux/`, `kubernetes/components/`, `bootstrap/`, `talos/`, `.github/` or `.renovate/` makes the PR a skip; Renovate's own edits there are fine.
- **Cluster writes** beyond a reconcile request are not available: no restarts, deletes, cordons, drains or Talos operations. When a PR needs one, skip it and put the exact command for the owner in the report.
- **Long waits:** a terminal call times out after 180 s. Wait in repeated short calls (`timeout 170 gh pr checks <n> -R lkshrk/h-cloud --watch`, `timeout 170 kubectl wait …`) or a background process, at most 30 minutes per wait, and never repeat an identical failing call three times.

## Procedure

1. **Load context.** Read `projects/h-cloud/runbooks/upgrade-deps-workflow.md` in the vault and the pitfalls that touch today's list, at least `chart-bump-hidden-breaking-changes`, `renovate-digest-and-regex-traps`, `cilium-netkit-veth-upgrades`, `helm-rollback-traps` and `latest-image-new-required-env`, plus the component page of every dependency on the list.
2. **Build the work list.** `gh pr list -R lkshrk/h-cloud --state open --author app/renovate-master --json number,title,headRefName,headRefOid,url`. Close stale or superseded PRs with `gh pr close <n> -R lkshrk/h-cloud --comment "<reason>"`, after checking the current base: an old PR's comments may describe a state `main` already fixed.
3. **Research each PR** before any merge:
   - How h-cloud uses it: `rg` the checkout (HelmRelease, OCIRepository, values, kustomize replacements) and its vault pages.
   - Chart release notes *and the app's own release notes* (`helm show chart` gives appVersion), `UPGRADE.md`, the values schema of the pinned version (`helm pull <chart> --version <v>` into `/tmp`), the rendered diff with our values, upstream issues for the target version (`gh issue list -R <upstream> --search …`).
   - Verdict: `clean` (no finding relevant to our setup), `relevant` (a finding touches how we run it) or `high-risk` (irreversible, datapath, or flagged in the vault).
4. **Ask once.** Send one ❓ message listing every `high-risk` PR with its finding and your recommendation, then continue with the others. Merge a high-risk PR only on the owner's explicit go; otherwise comment the reason and leave it open.
5. **Act on the verdict.**
   - `clean`: merge.
   - `relevant`: apply the fix on the Renovate branch so bump and fix land together (`git switch -c <branch> origin/<branch>`, edit, commit, `git push origin HEAD:<branch>`), wait for its checks, then merge.
   - Coupled bumps move together: litellm-proxy chart and app image; flux-operator, flux-instance, `distribution.artifact` and `bootstrap/helmfile.d/01-apps.yaml`; Cilium OCIRepository and bootstrap helmfile; rook-ceph operator and cluster; OpenHands chart first (wait for the migrate Job), then the image.
6. **Validate and merge.** In the checkout on the PR head: `just flux validate` must pass. One bump per push, so a failing bump reverts cleanly. Merge with `gh pr merge <n> -R lkshrk/h-cloud --squash --match-head-commit <head sha>` only when every check on that head passed.
7. **Health gate, after every merge.** `flux reconcile ks <name> -n <ns> --with-source` for the affected Kustomization (an app's Kustomization lives in its app namespace), then wait only for what the PR touched:
   - the Kustomization's `.status.lastAppliedRevision` contains the merge SHA, and
   - each touched HelmRelease is Ready with `.status.history[0].chartVersion` at the new version; for app-template image bumps, the pod's image tag and digest moved.

   Hardcode namespace and name per PR. A reconcile is a trigger, never the gate.
8. **Verify the app.** Read its logs right after the gate. Known checks: external-dns, the first sync loop has no `action=DELETE`; ARC, `kubectl get autoscalingrunnersets -A` lists every set and listener; ClickHouse, new `config.d` elements may be rejected.
9. **On failure: RCA, not rollback.** Read logs, events, HelmRelease status and history, the vault and upstream issues for the exact symptom. Fix forward on the next PR's branch when you can; otherwise stop and send the owner the RCA and the rollback commands (`git revert` on `main` **before** `helm rollback`, since Flux re-applies `main`). Stop the sweep when the cluster is unstable.
10. **Bookkeeping on every PR.** `gh pr comment <n> -R lkshrk/h-cloud --body-file /tmp/pr-<n>.md`: findings, verdict, changes applied, gate result. A merged PR without a comment is unfinished.
11. **Close the sweep.** Write new pitfalls and the per-dependency verdicts ("no relevant finding @ vX") into the vault as its `AGENTS.md` describes, then send one ✅ report: merged (with applied changes), failed (with RCA), closed, then skipped and high-risk items as discussion points.

## Pitfalls

- A harmless chart note is no evidence of a harmless upgrade; the break lives in the app notes, `UPGRADE.md` or the pinned values.
- Renovate digest pinning breaks app-template `image.digest` HelmReleases and kustomize `:`-delimiter replacements; a tag-bumped OCIRepository with a stale digest rolls out nothing.
- An empty tag list means a private package or a wrong name, not "no tags".
- CI green or PR merged is not the gate; the live revision and Ready are.
