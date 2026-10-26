#!/usr/bin/env bash
# Discover upgrade candidates across every ecosystem present in a repo, in one
# pass: open Renovate/Dependabot PRs, security advisories, then outdated deps per
# detected stack. Read-only — runs only query/list commands, changes nothing.
set -u

DIR="${1:-.}"
cd "$DIR" 2>/dev/null || { echo "not a directory: $DIR" >&2; exit 1; }

have() { command -v "$1" >/dev/null 2>&1; }
section() { printf '\n=== %s ===\n' "$1"; }
# Run a check only if its tool exists; otherwise note what's missing.
run() { if have "$1"; then shift; "$@" 2>&1; else printf '(skipped: %s not installed)\n' "$1"; fi; }

pm() {
  [ -f bun.lock ] || [ -f bun.lockb ] && { echo bun; return; }
  [ -f pnpm-lock.yaml ] && { echo pnpm; return; }
  [ -f yarn.lock ] && { echo yarn; return; }
  echo npm
}

section "Renovate / Dependabot PRs"
if have gh; then
  gh pr list --state open --search 'author:app/renovate OR author:app/dependabot' \
    --json number,title,labels --template '{{range .}}#{{.number}}  {{.title}}{{"\n"}}{{end}}' 2>/dev/null \
    || echo "(no gh auth or not a GitHub repo)"
else
  echo "(skipped: gh not installed)"
fi

section "Security advisories (prioritize these)"
if [ -f package.json ]; then
  case "$(pm)" in
    npm)  echo "# npm audit";  run npm npm audit 2>&1 | tail -5 ;;
    pnpm) echo "# pnpm audit"; run pnpm pnpm audit 2>&1 | tail -5 ;;
    yarn) echo "# yarn npm audit"; run yarn yarn npm audit 2>&1 | tail -5 ;;
    bun)  echo "# (bun has no audit — use osv-scanner or 'npm audit' against a generated lockfile)" ;;
  esac
fi
[ -f go.mod ] && { echo "# govulncheck"; run govulncheck ./... 2>&1 | tail -5; }
[ -f Cargo.toml ] && { echo "# cargo audit"; run cargo-audit cargo audit 2>&1 | tail -5; }
{ [ -f pyproject.toml ] || ls requirements*.txt >/dev/null 2>&1; } && { echo "# pip-audit"; run pip-audit 2>&1 | tail -5; }
have osv-scanner && { echo "# osv-scanner"; osv-scanner --recursive . 2>&1 | tail -5; }

if [ -f package.json ]; then
  P="$(pm)"; section "Node ($P) outdated"; run "$P" "$P" outdated
fi
if [ -f Cargo.toml ]; then
  section "Rust outdated"
  if have cargo-outdated; then cargo outdated 2>&1; else run cargo cargo update --dry-run 2>&1; fi
fi
if [ -f go.mod ]; then
  section "Go module updates"; run go go list -u -m all 2>&1 | grep '\[' || echo "(all current)"
fi
if [ -f pyproject.toml ] || ls requirements*.txt >/dev/null 2>&1; then
  section "Python outdated"
  if have uv; then uv pip list --outdated 2>&1
  elif have poetry; then poetry show -o 2>&1
  else run pip pip list --outdated 2>&1; fi
fi
if [ -f pom.xml ]; then
  section "Maven updates"; run mvn mvn -q versions:display-dependency-updates 2>&1 | tail -40
fi
if [ -f build.gradle ] || [ -f build.gradle.kts ]; then
  section "Gradle updates"; echo "run: gradle dependencyUpdates (needs the ben-manes versions plugin)"
fi

# GitOps/Flux: image/chart tags aren't in a lockfile. Enumerate every Flux
# source class: OCIRepository refs, HelmRepository-backed HelmRelease chart
# versions, plain image refs, and renovate-annotated repository/tag pins.
# yq (v4) parses multi-doc YAML reliably; without it a grep/awk fallback
# covers OCIRepository only — the output says so.
if grep -rlqE 'kind:\s*(HelmRelease|OCIRepository|HelmRepository)' . --include='*.yaml' --include='*.yml' 2>/dev/null; then
  FLUX_FILES=$(grep -rlE 'kind:\s*(HelmRelease|OCIRepository|HelmRepository)' . --include='*.yaml' --include='*.yml' 2>/dev/null)

  list_oci_tags() { # $1 = registry path without oci:// scheme
    skopeo list-tags "docker://$1" 2>/dev/null \
      | grep -oE '"[^"]+"' | tr -d '"' | grep -vE '^(Tags|Repository)$' \
      | grep -E '^v?[0-9]' | sort -V | tail -8
  }

  section "GitOps/Flux OCIRepository charts (current vs newest upstream)"
  if have yq; then
    printf '%s\n' "$FLUX_FILES" | while IFS= read -r f; do
      yq e 'select(.kind == "OCIRepository") | [.metadata.name, .spec.url // "?", .spec.ref.tag // .spec.ref.semver // .spec.ref.digest // "?"] | @tsv' "$f" 2>/dev/null \
        | while IFS="$(printf '\t')" read -r name url cur; do
            [ -n "$name$url" ] || continue
            printf '%s  %s\n  file: %s  current: %s\n' "$name" "$url" "${f#./}" "$cur"
            if have skopeo; then
              list_oci_tags "${url#oci://}" | sed 's/^/  tag: /' || echo "  (skopeo could not list tags)"
            fi
          done
    done
  else
    echo "(yq not installed — grep fallback; install yq for reliable multi-doc parsing)"
    while IFS= read -r f; do
      grep -qE 'kind:\s*OCIRepository' "$f" || continue
      url=$(grep -oE 'oci://[^ "'"'"']+' "$f" | head -1)
      tag=$(awk '/^[[:space:]]*ref:/{r=1;next} r&&/[[:space:]](tag|semver):/{gsub(/["'"'"']/,"",$2);print $2;exit}' "$f")
      [ -n "$url" ] || continue
      printf '%s\n  file: %s  current: %s\n' "$url" "${f#./}" "${tag:-?}"
      have skopeo && { list_oci_tags "${url#oci://}" | sed 's/^/  tag: /' || echo "  (skopeo could not list tags)"; }
    done < <(printf '%s\n' "$FLUX_FILES")
  fi

  section "GitOps/Flux HelmRepository-backed charts (current vs repo index; dates support the 48h embargo)"
  if have yq; then
    TMP=$(mktemp -d); trap 'rm -rf "$TMP"' EXIT
    printf '%s\n' "$FLUX_FILES" | while IFS= read -r f; do
      yq e 'select(.kind == "HelmRepository") | [.metadata.name, .spec.url // "?", .spec.type // "default"] | @tsv' "$f" 2>/dev/null
    done | sort -u > "$TMP/repos.tsv"
    printf '%s\n' "$FLUX_FILES" | while IFS= read -r f; do
      yq e 'select(.kind == "HelmRelease") | select(.spec.chart.spec.chart != null) | [.metadata.name, .spec.chart.spec.chart, .spec.chart.spec.version // "*", .spec.chart.spec.sourceRef.name // "?"] | @tsv' "$f" 2>/dev/null \
        | while IFS="$(printf '\t')" read -r rel chart ver src; do
            [ -n "$chart" ] || continue
            repo_line=$(awk -F'\t' -v n="$src" '$1==n{print; exit}' "$TMP/repos.tsv")
            url=$(printf '%s' "$repo_line" | cut -f2)
            rtype=$(printf '%s' "$repo_line" | cut -f3)
            printf '%s (chart: %s)\n  file: %s  current: %s  repo: %s\n' "$rel" "$chart" "${f#./}" "$ver" "${url:-sourceRef \"$src\" not found}"
            [ -n "$url" ] || continue
            if [ "$rtype" = "oci" ]; then
              have skopeo && { list_oci_tags "${url#oci://}/$chart" | sed 's/^/  version: /' || echo "  (skopeo could not list versions)"; }
            elif have curl; then
              idx="$TMP/$(printf '%s' "$url" | tr -c 'A-Za-z0-9' _).idx"
              [ -s "$idx" ] || curl -fsSL --max-time 15 "${url%/}/index.yaml" -o "$idx" 2>/dev/null || true
              if [ -s "$idx" ]; then
                yq e ".entries[\"$chart\"][] | .version + \"  (\" + ((.created // \"\") | sub(\"T.*\"; \"\")) + \")\"" "$idx" 2>/dev/null \
                  | head -6 | sed 's/^/  version: /'
              else
                echo "  (could not fetch ${url%/}/index.yaml)"
              fi
            fi
          done
    done
  else
    echo "(skipped: yq not installed — HelmRepository-backed chart versions NOT discovered)"
  fi

  section "GitOps/Flux container image refs (check newer tags with skopeo list-tags)"
  grep -rhE '^\s*image:\s*\S' . --include='*.yaml' --include='*.yml' 2>/dev/null \
    | grep -vE '^\s*#' | sed -E 's/.*image:\s*//' | tr -d '"'"'" | grep -vE '^[*&{$]' | sort -u

  section "Renovate-annotated pins (repository/tag pairs inside values)"
  grep -rh -A2 -E '#\s*renovate:' . --include='*.yaml' --include='*.yml' 2>/dev/null \
    | grep -vE '^--$' | sed -E 's/^\s+//' | head -120
fi

printf '\n(Review changelogs before bumping. Order: security -> patch -> minor -> major.)\n'
