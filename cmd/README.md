# cmd

Go programs, all built into the `agent-tools` image (`images/agent-tools/Dockerfile`). Tests: `go test -race ./...`.

## signal-shim

Serves the subset of signal-cli's HTTP daemon API that the Hermes Signal adapter uses (`GET /api/v1/check`, `GET /api/v1/events` as SSE, `POST /api/v1/rpc`) and translates it to signal-cli-rest-api in json-rpc mode.

`send` keeps the adapter's formatting: the `textStyle`/`textStyles` ranges (bold, italic, strikethrough, monospace, spoiler) become signal-cli-rest-api's `text_mode: "styled"` markers, with literal `*`, `` ` ``, `~` and `|` escaped. A range the styled parser can't express is dropped and its text sent unstyled: ranges that cross, a style overlapping itself, or a marker directly after a literal `\`.

Polls: `sendPollCreate` (`question`, `options`, a `groupId` or one `recipient`) and `sendPollTerminate` (`pollTimestamp`) map to `/v1/polls`. Without an `account`, the shim uses the only registered account. The adapter reads only a message's text, so the shim gives a vote on the event stream a text of its own: `🗳️ Poll "<question>": <options> (poll <timestamp>)`. It remembers the last 256 polls it created to name the options; after a restart it falls back to option numbers. Hermes sends polls with `signal-poll` (`images/hermes/bin/signal-poll`).

Contribution approvals: when `APPROVER_URL` is set, a plain new message from `OWNER_NUMBER` whose whole text is `approve <code>` or `reject <code>` (8 digits, lowercase, single space, nothing else) goes to `contribution-approver` as `POST /v1/decisions` with the shared token (30 s timeout, above the approver's 20 s decision budget), and Hermes never sees it. If the approver is unreachable or refuses the call, the shim tells the sender in the same chat that nothing was submitted. Anything else, including edits, quotes, reactions, sync messages, attachments, mentions and other senders, reaches Hermes as before. The shim only receives while Hermes holds the event stream open.

| Variable | Meaning |
| --- | --- |
| `SIGNAL_REST_URL` | signal-cli-rest-api base URL (required) |
| `LISTEN_ADDR` | Listen address, default `127.0.0.1:8090` |
| `APPROVER_URL`, `APPROVER_TOKEN`, `OWNER_NUMBER` | contribution-approver base URL, shared token (at least 32 characters) and the owner's E.164 number; set all three or none |

## token-minter

Exchanges an Authentik machine identity for short-lived GitHub tokens through octo-sts and writes them into a Secret in every listed namespace, using the pod's service account. Runs once per invocation.

| Variable | Meaning |
| --- | --- |
| `AUTHENTIK_TOKEN_URL`, `AUTHENTIK_CLIENT_ID`, `AUTHENTIK_APP_PASSWORD` | Authentik client credentials |
| `OCTO_STS_URL`, `OCTO_STS_IDENTITY` | octo-sts endpoint and the default trust-policy identity |
| `TOKENS` | `key=owner[@identity],...`: Secret key, the GitHub owner whose trust policy applies, and optionally the trust-policy identity for that token (default `OCTO_STS_IDENTITY`), e.g. `review=routivo@security-review` for a read-only token. A malformed or duplicate entry fails the run |
| `SECRET_NAME`, `SECRET_NAMESPACES` | Secret to write and the comma-separated namespaces |
| `ENV_SECRETS` | Optional `ns/name:ENV_KEY=token_key;...`: also write chosen tokens into other Secrets, owning only those keys |

All except `ENV_SECRETS` are required.

## linear-bridge

Receives signed Linear webhooks at `POST /webhooks/linear` (`GET /healthz` for probes), verifies the signature, drops stale and duplicate deliveries, and handles events from a queue:

- A top-level issue created by the Hermes app is archived and the owner is told over Signal.
- A new agent session gets a thought: "Received. Checking readiness." for the owner, a refusal for anyone else.
- A prompted agent session is logged.

| Variable | Meaning |
| --- | --- |
| `LINEAR_WEBHOOK_SECRET` | Webhook signing secret |
| `LINEAR_APP_CLIENT_ID`, `LINEAR_APP_CLIENT_SECRET`, `LINEAR_APP_SCOPES` | Linear app OAuth client for API calls |
| `LINEAR_OWNER_ID` | Workspace owner allowed to start agent work |
| `LINEAR_APP_USER_ID` | User id of the Linear app, used to recognise its issues |
| `SIGNAL_REST_URL`, `SIGNAL_NUMBER`, `SIGNAL_RECIPIENT` | signal-cli-rest-api and the sender and recipient for notifications |
| `LISTEN_ADDR` | Listen address, default `:8080` |

All except `LISTEN_ADDR` are required.

## contribution-approver

Signal remote control for the contribution fork's publish gate (`docs/contribution-fork.md`). Every 60 s it reads `publish/contribution.json` from each fork's default branch, checks the token belongs to the fork owner, and lists open pull requests on the default branch. A pull request labelled `upstream-ready`, with its head in the fork and green CI on the head (the gate's rule), is announced once per head SHA to the Signal group: title, head SHA, files/additions/deletions, CI result, link and a single-use 8-digit code that expires after 30 minutes. A new head SHA replaces the code; a pull request that stops being ready loses it. A head the owner already approved or requested changes on is not announced. Codes live in memory, so a restart announces again with new codes.

`POST /v1/decisions` (`{"action":"approve"|"reject","code":"<8 digits>"}`, `Authorization: Bearer <APPROVER_TOKEN>`, compared in constant time) consumes the code and submits the owner's review pinned to the announced SHA: `APPROVE` for approve, which triggers the fork's `publish-upstream`, `REQUEST_CHANGES` for reject. Unknown, used or expired codes make no GitHub call. An expired code stays recognisable for 24 hours: replying with it once makes the next poll announce the pull request again with a fresh code, but only if it is still ready on the same head; otherwise the reply says it changed. Unknown and used codes trigger nothing, and a used code never brings the same head back. If GitHub refuses the review, the pull request is announced again with a new code. The result goes back to the Signal group. `GET /healthz` for probes. Logs never contain the token, the owner number or the group.

| Variable | Meaning |
| --- | --- |
| `GITHUB_TOKEN` | The owner's classic PAT with only `public_repo`; reviews are submitted as its owner |
| `CONTRIBUTION_FORKS` | Comma-separated forks, e.g. `lkshrk/omnigent` |
| `APPROVER_TOKEN` | Shared token signal-shim sends, at least 32 characters |
| `SIGNAL_REST_URL`, `SIGNAL_NUMBER`, `SIGNAL_RECIPIENT` | signal-cli-rest-api, the sender and the group (`group.<id>`) |
| `GITHUB_API_URL` | Default `https://api.github.com` |
| `LISTEN_ADDR` | Listen address, default `:8080` |

All except `GITHUB_API_URL` and `LISTEN_ADDR` are required.
