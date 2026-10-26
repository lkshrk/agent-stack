---
name: security-audit
description: Security review of one pull request's diff. Run gitleaks, semgrep and the dependency audit on the changed files, walk the checklist, grade findings with the severity rubric and return a validated security result. Reports only; never fixes.
---

# security-audit — scan, check, grade

Review only the diff and the code it touches. Never edit, patch or fix
anything, and never comment on or approve the pull request.

## 1. Scope

- PR URL `https://github.com/<owner>/<repo>/pull/<n>`: run
  `gh pr view <n> --repo <owner>/<repo> --json files,baseRefOid,headRefOid`
  and `gh pr diff <n> --repo <owner>/<repo>`.
- Saved diff with base and head SHAs: read the diff file and run
  `git diff --name-only <base> <head>`.
- `git rev-parse HEAD` must equal the head SHA, so the scanners see the
  changed code. If it doesn't, return `BLOCKED` with `blocked.class:
  environment`.

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
- Dependencies, for every ecosystem whose files the diff touches:
  - Python (`pyproject.toml`, `uv.lock`, `requirements*.txt`, `*.py`):
    `uv run --with pip-audit pip-audit`.
  - Node (`package.json`, `pnpm-lock.yaml`, `*.ts`, `*.js`):
    `pnpm audit --prod`.

  A non-zero exit with a list of advisories means findings.

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
of the diff could not be judged (say which in the prose).

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
