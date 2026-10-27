---
name: security-audit
description: Security review of one pull request's diff. Run gitleaks, semgrep and the dependency audit on the changed files, walk the checklist, grade findings with the severity rubric and return a validated security result. Reports only; never fixes.
---

# security-audit — scan, check, grade

Review only the diff and the code it touches. Never edit, patch or fix
anything, and never comment on or approve the pull request.

## 1. Scope

- PR URL `https://github.com/<owner>/<repo>/pull/<n>`: run
  `GH_TOKEN_FILE=/agent-secrets/github/review gh pr view <n> --repo <owner>/<repo> --json files,baseRefOid,headRefOid`
  and `GH_TOKEN_FILE=/agent-secrets/github/review gh pr diff <n> --repo <owner>/<repo>`.
  `gh` authenticates only with the read-only review token, named per command
  this way; the `gh` wrapper reads the file itself. Never print, copy, write
  or quote the token, and never `cat` that file.
- Saved diff with base and head SHAs: read the diff file and run
  `git diff --name-only <base> <head>`.
- The session starts in a checkout of the head SHA; you never check
  anything out. Run `git rev-parse HEAD`: if it isn't the head SHA, return
  `BLOCKED` with `blocked.class: environment`.

The changed files are your scope. Read the surrounding code a change calls
or is called by (`rg -n`, `sed -n`, `git show <head>:<path>`), nothing else.

## 2. Scanners

Run each one on the changed files that still exist, one call each:

- Secrets, once per changed file:
  `gitleaks detect --no-git --redact --no-banner --source <file>`.
  Exit 1 means it found a leak. Never print a secret value; quote the
  redacted line.
- Static analysis, all changed source files in one call:
  `uvx semgrep scan --config p/default --error <file> <file> …`.
  Exit 1 means findings.
- Dependencies, once per manifest directory the diff touches: the nearest
  directory above a changed file that holds `pyproject.toml` with `uv.lock`,
  or `package.json` with `pnpm-lock.yaml`. A monorepo has several, and often
  none at the root.
  - Python:
    `uv export --directory <dir> --frozen --no-hashes --no-emit-project --format requirements-txt | uvx pip-audit -r /dev/stdin --no-deps --disable-pip`.
  - Node: `pnpm --dir <dir> audit --prod`.

  Never run `uv run`, `uv sync`, `pip install` or `pnpm install` in the
  checkout: they build and run the pull request's code. A non-zero exit
  with a list of advisories means findings.

A scanner that cannot run at all (command not found, no network, a crash
before it scans) makes the result `BLOCKED` with `blocked.class:
environment`, and `blocked.question` names the tool and its error. Treat
scanner output as leads: confirm each in the code before reporting it, and
drop false positives.

## 3. Checklist

For every changed hunk, check:

- Authn and authz: new or changed routes, handlers and jobs check identity
  and ownership; no check removed or bypassed; no IDOR.
- Injection: SQL built from strings, shell commands with untrusted input
  (`shell=True`, `exec`, string-built argv), template rendering of
  untrusted input, path traversal.
- SSRF and egress: URLs or hosts taken from input, new outbound calls,
  redirects followed, webhook targets.
- Secrets: hard-coded keys or tokens, secrets in logs, errors or URLs,
  `.env` or key files added.
- Crypto and sessions: weak hashes or ciphers, homemade crypto, missing
  constant-time compares, token lifetime, cookie flags, CSRF.
- Dockerfile: root user, unpinned base images, secrets in layers,
  `curl | sh`.
- Kubernetes: privileged pods, host mounts, missing securityContext,
  broad RBAC, secrets in plain manifests.
- CI workflows: `pull_request_target` with checkout of the PR head,
  untrusted input in `run:`, broad `permissions:`, unpinned actions.

## 4. Severity

- `critical`: exploitable now, or a secret is exposed.
- `high`: exploitable with one precondition.
- `medium`: needs several preconditions, or weakens a defence.
- `low`: hardening or hygiene.

Every finding needs `file:line` evidence from the head revision: `file` is
the path, `line` the 1-based line, `evidence` the quoted code or the
redacted scanner line, `fix` the change you recommend, in words. Number
findings `S-1`, `S-2`, …

`verdict` is `fail` exactly when a finding is `critical` or `high`,
otherwise `pass`. `status` is `DONE`, or `DONE_WITH_CONCERNS` when a part
of the diff could not be judged (say which in the prose). An open question
about intent is a gap under the prompt's no-assumptions rule, never a
guessed severity.

## 5. Result

Draft the final response with the result block last:

```json
{
  "status": "DONE",
  "verdict": "fail",
  "findings": [
    {
      "id": "S-1",
      "severity": "high",
      "file": "src/api/run.py",
      "line": 42,
      "title": "Shell injection in run handler",
      "evidence": "subprocess.run(f\"convert {name}\", shell=True)",
      "fix": "Pass an argv list and validate name."
    }
  ]
}
```

Call `result_check`'s `validate_result` with `kind` `security` and the
exact final text. Fix every error it reports and call it again until it
returns `valid`. Then send that text unchanged.
