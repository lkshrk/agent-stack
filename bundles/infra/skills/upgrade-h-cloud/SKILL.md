---
name: upgrade-h-cloud
description: Unattended Renovate sweep on lkshrk/h-cloud. Research every open Renovate PR, push needed fixes to its branch, merge verified PRs with land_renovate_pr, health-gate each merge on the live cluster, update the vault and return a validated infra result.
---

# Upgrade h-cloud

Process the open Renovate PRs on `lkshrk/h-cloud` one at a time: research each
release, judge whether its changes matter for *our* setup, merge the clean
bumps, apply needed changes for relevant findings, and health-gate every merge.
Research decides — never the size of the version jump.

Ported from the owner's Hermes skill `upgrade-h-cloud` 0.1.0; the procedure and
rules are the owner's, only the environment differs.

## Environment

- **Checkout:** your working directory is a fresh h-cloud checkout of the run
  spec's ref. `gh` and `git` authenticate with the `hcloud` token
  (`GH_TOKEN_FILE`). Search with `rg`, read with the Read tool or `sed -n`.
- **Vault:** `git clone --depth 50 https://github.com/lkshrk/knowledge.git ~/knowledge`
  unless it exists, then `~/knowledge/scripts/sync.sh` (the knowledge token
  authenticates it). Write vault pages with the file tools; commit and push only
  through `scripts/sync.sh`, as the vault's `AGENTS.md` describes.
- **Cluster:** `kubectl` and `flux` use the `omnigent-infra` identity that
  h-cloud mounts into this pod: the projected service account token at
  `/var/run/secrets/kubernetes.io/serviceaccount` (in-cluster config), or
  `KUBECONFIG` when it is set. It reads everything except Secrets and may only
  annotate Flux Kustomizations, HelmReleases and sources to request a reconcile.
  Check it first with `kubectl auth whoami`; without it the sweep can't gate
  merges, so end `BLOCKED` with `blocked.class: environment` before merging
  anything.
- **No Secrets:** `helm history`, `helm get` and `helm rollback` read Helm's
  release Secrets and fail; use `kubectl get hr -n <ns> <name> -o yaml`
  (`.status.history`, conditions), events and pod logs instead.
- **Registries:** public charts and images need no login. List tags with
  `crane ls <registry>/<repo>`, inspect with `crane manifest`/`crane config`,
  and use `helm show chart|values oci://…` or `helm pull` into `/tmp`. Never log
  in to ghcr; a private package that refuses anonymous reads is a skip reason.
- **Long waits:** every blocking call ends within 30 minutes (`--timeout=20m`,
  a bounded poll loop with `sleep`), and you never repeat an identical call
  three times in a row.
- **Blocked operations:** cordon, drain, Talos operations, deletes, restarts
  and writes to the cluster are not available. When a PR needs one, skip it and
  put the exact command for the owner in its summary.

## Procedure

1. **Load context.** Sync the vault, then read
   `projects/h-cloud/runbooks/upgrade-deps-workflow.md` and the pitfalls that
   touch today's list at minimum: `chart-bump-hidden-breaking-changes`,
   `renovate-digest-and-regex-traps`, `cilium-netkit-veth-upgrades`,
   `helm-rollback-traps`, `latest-image-new-required-env` — plus the component
   page of every dep on the list. Done when you can say which pitfall applies
   to which PR.
2. **Build the work list.** `gh pr list -R lkshrk/h-cloud --state open --json number,title,author,headRefName,url`,
   Renovate-authored (`renovate-master[bot]`) PRs only; ignore anything under
   `.worktrees/`. Close stale/superseded PRs with `gh pr close <n> -R lkshrk/h-cloud --comment "<reason>"`
   — but first check the current base, because an old PR's comments may
   describe a state the owner's commits already fixed. Done when open Renovate
   PR count = processed + closed-stale.
3. **Research each PR** (one at a time, before any merge):
   - **How h-cloud uses the dep:** `rg` the repo for it — HelmRelease,
     OCIRepository, values, kustomize replacements; read its vault pages and
     the Do-Not-Repeat entries for it.
   - **Findings, all sources:** chart release notes *and the app's own release
     notes* (chart version ≠ app version — `helm show chart` gives appVersion;
     breaking changes hide in app notes, `UPGRADE.md`, or the values schema of
     the *pinned* version: `helm pull <repo>/<chart> --version <pinned>` into
     `/tmp`, never the default branch), rendered diff via `helm template` with
     the real values, upstream open issues/bugs for the target version
     (`gh issue list -R <upstream> --search …`, `gh release view`), and the
     project website or 3rd-party reports when notes are thin. Note the
     release age in the findings; there is no embargo — research decides.
   - **Verdict:** `clean` (no finding relevant to our setup) / `relevant`
     (finding touches how we run this dep) / `high-risk` (irreversible or
     vault-flagged). Never estimate change volume from the version-number
     diff — an unfound guess from semver is a rule violation, not a shortcut.
4. **Act on the verdict:**
   - `clean` → merge the PR.
   - `relevant` → research the needed change/workaround and apply it on the
     Renovate branch so bump and fix land atomically (`git switch -c <branch>
     origin/<branch>`, edit, commit), then merge. Prefer fixing runtime config
     over deferring (e.g. an image that newly requires env vars gets the env
     vars, not a deferral). A fix that needs a change outside the agent's
     limits (the bundle instructions list them) turns the PR into a skip.
   - `high-risk` (datapath flips, irreversible migrations, anything on the
     Do-Not-Repeat list) → do not merge and **do not close**: skipped PRs are
     never closed. Comment the skip reason, list them in `skipped`; an open
     skipped PR heads the next sweep's work list.
   - **Coupled bumps move together:** litellm-proxy chart + pinned app image;
     flux-operator tag + flux-instance tag + `distribution.artifact` version
     + `bootstrap/helmfile.d/01-apps.yaml`; Cilium OCIRepository + bootstrap
     helmfile; rook-ceph operator + cluster tags; OpenHands chart PR first
     (wait for the migrate Job to succeed), then the image PR. A coupled set
     that touches a protected path (flux-system, kube-system, bootstrap) is
     skipped as a whole.
5. **Validate and merge.** `./scripts/flate-test.sh --path ./kubernetes/flux/cluster --allow-missing-secrets --no-progress`
   (offline flate validation) must pass. Strictly one bump per push — a failing
   bump must revert cleanly. Before each push run
   `/opt/venv/bin/python3 -m agent_stack_policies.manifests_cli origin/<branch> HEAD`;
   push with `git push origin HEAD:<branch>` only when it prints `ok`, then
   wait for the PR's checks. Merge with the `land_renovate_pr` tool (`number`
   only); it squash-merges the verified head and returns the merge SHA, which
   is the "close PR" step. When it refuses, read the reason: wait for pending
   checks, fix a failing one on the branch, or skip the PR.
6. **Health gate (mandatory, per merge).** Request a reconcile of the source
   and the affected Kustomization (`flux reconcile ks <name> -n <namespace> --with-source`;
   an app's Kustomization lives in its app namespace, e.g. `litellm-proxy` in `ai`),
   then wait only for the objects that PR touched — a cluster-wide wait hangs
   on unrelated degraded resources. Gate on revision **and** Ready (Ready alone
   matches the old revision): the Kustomization's
   `.status.lastAppliedRevision` must contain the merge SHA, and each touched
   HelmRelease must be Ready with `.status.history[0].chartVersion` at the new
   version. For app-template image bumps the chart revision never changes, so
   verify the pod image tag and digest actually moved. Hardcode namespace and
   release per PR — deriving them by grepping `metadata.namespace` fails
   silently. The repo's `scripts/merge-ready-prs.sh` shows the gate logic;
   don't run it, it merges with `gh pr merge`.
7. **Verify the app, not just Helm.** Read the app's logs right after the gate.
   Known post-bump checks: external-dns → watch the first sync loop for
   `action=DELETE`; ARC → `kubectl get autoscalingrunnersets -A` lists every
   set and listener; ClickHouse → new `config.d` elements may be rejected.
8. **On failure: RCA, not rollback.** Keep calm. Read logs, events, the
   HelmRelease status and history, re-check the vault, search upstream issues
   for the exact symptom; try fixes forward on a new Renovate PR's branch when
   one exists, or report the fix. You can't revert `main` or roll back a
   release: list the PR in `failed` with its RCA, and when a rollback is the
   only way out, give the owner the commands (git-revert `main` **before**
   `helm rollback` — Flux re-applies whatever `main` says seconds after a helm
   rollback — knowing rollbacks leave fields behind via 3-way merge). Stop the
   sweep if the cluster is unstable.
9. **Bookkeeping on every PR, success or failure:** comment the result with
   `gh pr comment <n> -R lkshrk/h-cloud --body-file /tmp/pr-<n>.md` (write the
   file first) — findings summary, relevance verdict, changes applied,
   health-gate outcome. A merged PR with no comment is an incomplete step.
10. **Next.** Success (or failure recovered forward) → immediately the next
    PR on the list. Only stop early if the cluster is unstable.
11. **Close the sweep.** Update the vault: new pitfalls via `raw/sessions/` +
    page edit, and record per-dep verdicts ("no relevant finding @ vX") in the
    upgrade-deps runbook so the next sweep starts from them; commit and run
    `scripts/sync.sh`, then take `git -C ~/knowledge rev-parse HEAD` as
    `vault.commit` (null only when nothing changed). The sweep report is the
    final infra result, which Hermes sends to the owner: `merged` bumps with the
    changes applied, `failed` with their RCA, `closed` stale PRs, then the
    `skipped` high-risk items as discussion points. Every agent commit that
    landed appears in its PR's summary.

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
- An empty tag list from `crane ls` means the package is private or the name
  is wrong, not that no tags exist.
- Renovate presets are fetched from h-cloud `main` per run; a preset change is
  a `.renovate/` edit, which is outside your limits — report it instead.

## Verification

- [ ] Every PR on the work list ended merged-with-comment, skipped-for-discussion,
      or closed-stale with a reason — counts add up.
- [ ] Every merged bump passed flate validation, revision+Ready gate on the
      touched objects, and an app-log read.
- [ ] No upgrade was judged by its version-number diff; every verdict cites
      sources (notes, issues, rendered diff).
- [ ] Failures have an RCA and their PRs carry the result.
- [ ] Vault verdicts updated; the final result validates as kind `infra`.
