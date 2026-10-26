---
name: upgrade-deps
description: Upgrade a repository's dependencies safely, one at a time, in any ecosystem — npm/pnpm/yarn/bun, cargo, go modules, pip/poetry/uv, gradle/maven, Docker images, and Flux/GitOps Helm/OCI charts. Detects the stack, prioritizes security and patch bumps, reads each changelog before bumping, asks before major/breaking upgrades, and gates every change on validate + verify (build/test, or Flux health) before moving to the next. Use when the user wants to upgrade/update/bump dependencies, refresh lockfiles, process Renovate/Dependabot PRs, or update charts/images in any repo.
---

# Upgrade Dependencies

Upgrade dependencies safely and **one at a time**: discover → read changelog →
upgrade → validate → verify → only then the next. Never batch unrelated bumps
into one commit; never skip the verify gate. Single-upgrade commits keep a
failing bump cleanly revertable.

## 1. Detect the stack

Read repo instructions first (`README`, `CLAUDE.md`, `AGENTS.md`,
`CONTRIBUTING`) and honor project conventions — task runner (`just`/`make`),
package manager, any wrapper like RTK. Then detect ecosystems from the markers
present. A repo may have several; handle each.

## 2. Discover candidates

Run the bundled one-pass discovery (read-only — only query/list commands).
The script ships beside this SKILL.md; resolve its path so it runs from any cwd:

```bash
bash "${CLAUDE_PLUGIN_ROOT:-$HOME/.claude/skills/upgrade-deps}/scripts/discover.sh" [repo-folder]
```

It lists open **Renovate/Dependabot PRs**, **security advisories** (`npm audit`,
`govulncheck`, `cargo audit`, `pip-audit`, `osv-scanner`), and **outdated deps**
for every detected ecosystem. For GitOps/Flux it covers **all four source
classes**: OCIRepository refs, HelmRepository-backed HelmRelease chart versions
(with newest index versions + dates), plain `image:` refs, and
renovate-annotated `repository:`/`tag:` pins. Skipped checks name the missing
tool. Then:

- **Completeness check (GitOps)**: every `HelmRelease`/`OCIRepository` in the
  repo must appear in the discovery output. A whole class missing usually means
  `yq` is absent — install it and rerun rather than proceeding with a partial
  list.
- Close abandoned/superseded Renovate PRs (`gh pr close`, note why).
- Order: security → patch → minor → major. **Major always last.**

## 3. Upgrade cycle (repeat per dependency)

1. Read the upstream **changelog/release notes**; keep the link.
2. **48-hour embargo**: check the target version's publish date — released less
   than 48 h ago → do not take it. Fall back to the newest version older than
   48 h, or defer the dependency and say so in the summary. Date sources:
   `gh release view <tag> -R <owner>/<repo> --json publishedAt`,
   `npm view <pkg> time`, chart `index.yaml` `created` (printed by discovery),
   `skopeo inspect --format '{{.Created}}' docker://<image>:<tag>`.
3. **Check open issues** on the upstream repo for the target version:
   `gh issue list -R <owner>/<repo> --state open --search "<version>"` (or
   `gh search issues`). Open regression/breakage/upgrade-path reports about
   that version count as doubt.
4. **Ask before majors — and whenever in doubt.** Always ask before a major
   bump, or when notes mention breaking changes, deprecations, migrations,
   removed config, or CRD/value changes. Also ask whenever criticality is
   unclear: no changelog found, ambiguous notes, open-issue reports from
   step 3, or a multi-version jump. Asking = print a concise summary
   (dependency, old → new, changelog link, open-issue findings, your risk
   read) then offer update / skip / defer. Proceed without asking only when
   the notes are clear and clean.
5. **Highlight new features**: note new capabilities/config that could benefit or
   simplify this project, so the user can decide whether to adopt them.
6. Apply the **single** bump (manifest + lockfile together). Respect declared
   version ranges unless the user asks to widen them.
7. **Validate** (build/typecheck/lint per ecosystem; GitOps → `flux validate`).
8. **Verify** (tests, or CI green if no local tests). **GitOps/Flux has no
   build/test — verify means push → reconcile → health gate, and it is
   mandatory; see "GitOps / Flux specifics" below. Do not substitute "CI green"
   or "open a PR" for the health gate.**
9. **Commit** one logical upgrade. For GitOps/Flux: **one upgrade per push** to
   the reconciled branch (push is part of step 8, not optional). For other
   ecosystems: push / open a PR per the project's flow.
10. On failure: stop, diagnose, fix-forward or ask before skip/revert. Never
    revert unrelated user changes.

Keep output concise per item:
`dependency  old -> new  changelog-decision  issues-check  verify-result`.

## Per-ecosystem commands

| Ecosystem | Markers | Outdated | Apply one bump | Validate | Verify |
|---|---|---|---|---|---|
| Node | `package.json` + lock | `<pm> outdated` | `<pm> up <pkg>@<ver>` (pm = npm/pnpm/yarn/bun) + install | `<pm> run build`, `tsc --noEmit` | `<pm> test` |
| Rust | `Cargo.toml`/`Cargo.lock` | `cargo update --dry-run` / `cargo outdated` | `cargo update -p <crate> --precise <ver>` | `cargo check` | `cargo test` |
| Go | `go.mod` | `go list -u -m all` | `go get <mod>@<ver> && go mod tidy` | `go build ./... && go vet ./...` | `go test ./...` |
| Python | `pyproject.toml`/`requirements*.txt` | `uv pip list --outdated` / `poetry show -o` / `pip list --outdated` | `uv lock --upgrade-package <p>` / `poetry update <p>` / edit + `pip install -U <p>` | import/build | `pytest` |
| Java | `pom.xml`/`build.gradle*` | `mvn versions:display-dependency-updates` / `gradle dependencyUpdates` | edit version | `mvn -q compile` / `gradle assemble` | `mvn test` / `gradle test` |
| Docker | `Dockerfile`/`compose.y*ml` | `skopeo list-tags docker://<image>` | bump tag (pin digest if used) | `docker build` | smoke-run / compose up |
| GitOps/Flux | `OCIRepository`/`HelmRelease`/`Kustomization` | `skopeo list-tags` + Renovate PRs | edit tag/`chartRef` version | repo's `flux validate` (e.g. `just flux validate`) | Flux health gate (below) |

If no local validate/verify command exists, fall back to the repo's CI: push the
branch and treat a green pipeline as the verify gate.

## GitOps / Flux specifics

- List OCI/image tags with `skopeo list-tags` (prefer it over Docker/crane — no
  daemon). Discover candidates from Renovate PRs + `OCIRepository`/`HelmRelease`.
- For charts bundling **CRDs**, diff old vs new CRDs for removed/renamed fields
  before applying. After a chart bump, diff rendered values to catch renamed keys.
- Honor the repo's task runner / wrapper (e.g. `just`, RTK) for every step —
  prefer `just flux validate`, `just flux reconcile` over raw flux commands when
  they exist.
- **Strictly one upgrade per push** — edit one manifest, `flux validate`, commit,
  push, reconcile Flux, wait for the health gate, then the next. Never push
  multiple upgrades at once; a failing one must revert cleanly.
- After push, reconcile source + the affected Kustomization, then poll status:
  `flux reconcile source git <name>` → `flux reconcile kustomization <name>` →
  `flux get kustomizations`, `flux get helmreleases`, `kubectl rollout status`.
- **Health gate** (verify each, where present): Flux Kustomization Ready;
  HelmRelease Ready; Deployment/StatefulSet/DaemonSet rollout healthy; pods
  Running/Ready; Services have endpoints. Use the merged Kustomization/HelmRelease
  as the primary signal for non-standard workloads. CI-green or PR-merged is NOT
  a substitute for the live health gate.
