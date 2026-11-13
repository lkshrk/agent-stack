---
name: upgrade-h-cloud
description: Upgrade h-cloud deps from Renovate PRs with research gates.
version: 0.2.0
author: lkshrk, Hermes Agent
license: MIT
platforms: [linux]
metadata:
  hermes:
    tags: [h-cloud, renovate, flux, helm, upgrades, gitops]
    category: devops
---

# Upgrade h-cloud

Process the open Renovate PRs on `lkshrk/h-cloud` one at a time: research each
release, judge whether its changes matter for *our* setup, merge the clean
bumps, apply needed changes for relevant findings, and health-gate every merge.
Research decides — never the size of the version jump.

## When to Use

- The owner asks to upgrade h-cloud, run an upgrade sweep, or process Renovate PRs.
- Don't use for: dependency work in other repositories; bumps Renovate has not
  proposed (no self-discovery — central Renovate in `lkshrk/gh-ops` runs daily
  and is the only bump source); Talos node operations beyond what a bump needs.

## Prerequisites

- `gh` and `git` use the `hcloud` token for `lkshrk/h-cloud` on their own
  (`/var/run/agent/github/hcloud`, rotated every 20 minutes; set no
  `GH_TOKEN`, run no `gh auth login`); a fresh h-cloud checkout
  (`git clone https://github.com/lkshrk/h-cloud.git`, then `git fetch` before
  every PR); `just`, `yq`, `skopeo`, `crane`, `helm`, `flate` are installed.
- ghcr: no ghcr credential is mounted (GitHub App tokens such as `hcloud`
  can't read ghcr). Public ghcr packages list anonymously; a private one
  answers 403, which looks like "zero tags". Never read that as "no new
  version": name the package as unverified in the PR comment and the report.
- `kubectl`/`flux` use the `hermes` service account: they read everything
  except Secrets and may only request reconciles (`flux reconcile` without
  `--force`/`--reset`). Check with `kubectl auth whoami`
  (`system:serviceaccount:ai:hermes`).
- Knowledge vault at `/opt/data/knowledge` (host path; sync before every sweep).
- Several cluster operations are blocked for the agent (cordon, drain, Talos
  reboots/certs, deletes, restarts, any write besides a reconcile request,
  `helm history`/`get`/`rollback`, which read Secrets). Hand the operator a
  command to run and keep blocked calls in their own `terminal` call.

## Procedure

1. **Load context.** Run the vault sync, then read
   `projects/h-cloud/runbooks/upgrade-deps-workflow.md` and the pitfalls that
   touch today's list at minimum: `chart-bump-hidden-breaking-changes`,
   `renovate-digest-and-regex-traps`, `cilium-netkit-veth-upgrades`,
   `helm-rollback-traps`, `latest-image-new-required-env` — plus the component
   page of every dep on the list. Done when you can say which pitfall applies
   to which PR.
2. **Build the work list.** `gh pr list -R lkshrk/h-cloud --state open`,
   Renovate-authored PRs only; ignore anything under `.worktrees/`. Close
   stale/superseded PRs with a reason comment — but first check the current
   base, because an old PR's comments may describe a state the owner's commits
   already fixed. Done when open Renovate PR count = processed + closed-stale.
3. **Research each PR** (one at a time, before any merge):
   - **How h-cloud uses the dep:** `search_files` the repo for it — HelmRelease,
     OCIRepository, values, kustomize replacements; read its vault pages and
     the Do-Not-Repeat entries for it.
   - **Findings, all sources:** chart release notes *and the app's own release
     notes* (chart version ≠ app version — `helm show chart` gives appVersion;
     breaking changes hide in app notes, `UPGRADE.md`, or the values schema of
     the *pinned* version: `helm pull <repo>/<chart> --version <pinned>`, never
     the default branch), rendered diff via `helm template` with the real
     values, upstream open issues/bugs for the target version, and the
     project website or 3rd-party reports when notes are thin. Note the
     release age in the findings; there is no embargo — research decides.
   - **Verdict:** `clean` (no finding relevant to our setup) / `relevant`
     (finding touches how we run this dep) / `high-risk` (irreversible or
     vault-flagged). Never estimate change volume from the version-number
     diff — an unfound guess from semver is a rule violation, not a shortcut.
4. **Act on the verdict:**
   - `clean` → merge the PR.
   - `relevant` → research the needed change/workaround and apply it — prefer
     pushing to the Renovate branch so bump and fix land atomically — then
     merge. Prefer fixing runtime config over deferring (e.g. an image that
     newly requires env vars gets the env vars, not a deferral).
   - `high-risk` (datapath flips, irreversible migrations, anything on the
     Do-Not-Repeat list) → do not merge and **do not close**: skipped PRs are
     never closed. Comment the skip reason, queue them for the end-of-sweep
     discussion; an open skipped PR heads the next sweep's work list.
   - **Coupled bumps move together:** litellm-proxy chart + pinned app image;
     flux-operator tag + flux-instance tag + `distribution.artifact` version
     + `bootstrap/helmfile.d/01-apps.yaml`; Cilium OCIRepository + bootstrap
     helmfile; rook-ceph operator + cluster tags; OpenHands chart PR first
     (wait for the migrate Job to succeed), then the image PR.
5. **Validate and merge.** `just flux validate` (offline flate validation) must
   pass. Strictly one bump per push — a failing bump must revert cleanly.
   `gh pr merge` closes the PR; that is the "close PR" step.
6. **Health gate (mandatory, per merge).** Reconcile source and the affected
   Kustomization, then wait only for the objects that PR touched — a
   cluster-wide wait hangs on unrelated degraded resources. Gate on revision
   **and** Ready (Ready alone matches the old revision); for app-template image
   bumps the chart revision never changes, so verify the pod image tag and
   digest actually moved. Hardcode namespace and release per PR — deriving
   them by grepping `metadata.namespace` fails silently. The repo's
   `scripts/merge-ready-prs.sh` implements this; it waits up to 20 minutes,
   longer than one `terminal` call, so run it in the background.
7. **Verify the app, not just Helm.** Read the app's logs right after the gate.
   Known post-bump checks: external-dns → watch the first sync loop for
   `action=DELETE`; ARC → `kubectl get autoscalingrunnersets -A` lists every
   set and listener; ClickHouse → new `config.d` elements may be rejected.
8. **On failure: RCA, not rollback.** Keep calm. Read logs, events, the
   HelmRelease's `.status.history` and conditions, re-check the vault, search
   upstream issues for the exact symptom; try fixes forward (values, config,
   re-reconcile). Roll back only when genuinely out of ideas — and then
   git-revert `main` **before** `helm rollback` (Flux re-applies whatever
   `main` says seconds after a helm rollback), knowing rollbacks leave fields
   behind via 3-way merge. `helm rollback` is the operator's command.
9. **Bookkeeping on every PR, success or failure:** comment the result —
   findings summary, relevance verdict, changes applied, health-gate outcome.
   A merged PR with no comment is an incomplete step.
10. **Next.** Success (or failure recovered forward) → immediately the next
    PR on the list. Only stop early if the cluster is unstable.
11. **Close the sweep.** ✅ Signal report: merged bumps, changes applied,
    failures with their RCA, then the skipped high-risk items as discussion
    points. Update the vault: new pitfalls via `raw/sessions/` + page edit,
    and record per-dep verdicts ("no relevant finding @ vX") in the
    upgrade-deps runbook so the next sweep starts from them.

## Monitoring model

No active post-merge watches. Gate + app logs immediately after merge, then
the existing alerting (Gatus, vmalert, GlitchTip) owns the dep. A merged dep
"comes up again" as a new Renovate PR on the next daily run: at the next
sweep, read the recorded verdict first, then do fresh research for the new
version — an old `clean` verdict is context, not a free pass.

## Pitfalls

- A harmless-looking chart note is not evidence of a harmless upgrade; the
  break usually lives in the app notes, `UPGRADE.md`, or the pinned values.
- Renovate digest pinning breaks app-template `image.digest` HelmReleases and
  kustomize `:`-delimiter replacements; tag-bumped/digest-stale OCIRepositories
  roll out nothing — check `HelmRelease .status.history[0].chartVersion`.
- `flux reconcile` is a trigger, never the gate. CI-green or PR-merged is not
  a substitute for the live health gate.
- ghcr needs auth for private packages; anonymous 403s look like "no tags available".
- Renovate presets are fetched from h-cloud `main` per run — push config
  changes before dispatching a targeted run.

## Verification

- [ ] Every PR on the work list ended merged-with-comment, skipped-for-discussion,
      or closed-stale with a reason — counts add up.
- [ ] Every merged bump passed `just flux validate`, revision+Ready gate on the
      touched objects, and an app-log read.
- [ ] No upgrade was judged by its version-number diff; every verdict cites
      sources (notes, issues, rendered diff).
- [ ] Failures have an RCA and their PRs carry the result.
- [ ] Vault verdicts updated; ✅ sweep report sent to the owner.
