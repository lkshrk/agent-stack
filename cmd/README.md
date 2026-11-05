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
| `APPROVER_URL`, `APPROVER_TOKEN`, `OWNER_NUMBER` | contribution-approver base URL, shared token (at least 32 characters) and the owner's E.164 number; set all three or none. If `OWNER_NUMBER` is not exactly one E.164 number (for example a list), approvals are disabled with a log line and everything still reaches Hermes |

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

Signal remote control for the contribution fork's publish gate (`docs/contribution-fork.md`). Fork settings come from `forks.json`, embedded in the binary and rendered from `config/public-contributions.yaml` by `templates/render_approver_config.py` (`go generate`; a test fails on drift), so a registry change ships with the next release. Every 60 s, for each fork, it checks the token belongs to the fork owner and that GitHub reports the fork as owned by that owner with the registry upstream as its parent, then lists open pull requests on the default branch. A pull request labelled `upstream-ready`, with its head in the fork and green CI on the head (the gate's rule), is announced once per head SHA to the Signal group: title, a body excerpt, head SHA, files/additions/deletions, CI result, link and a single-use 8-digit code that expires after 30 minutes. Before announcing it runs the gate's own refusals: a title that doesn't follow `title_format`, or an upstream diff (compare against the upstream default branch) that is empty, too large or touches `.github/` or `publish/`, is logged once per head and not announced. A new head SHA replaces the code; a pull request that stops being ready loses it. A head the owner already approved or requested changes on is not announced, unless the approval cannot be bound to an announcement (after a restart, or when the text changed), in which case it is announced once more. Codes live in memory, so a restart announces again with new codes, except for a pull request whose approved commit is already on its upstream pull request or was carried by a closed or merged one. If listing the reviews after an announcement fails, the announcement of that head is retried on the next poll and then at most every 30 minutes, doubling up to a day; a new head is announced at once.

`POST /v1/decisions` (`{"action":"approve"|"reject","code":"<8 digits>"}`, `Authorization: Bearer <APPROVER_TOKEN>`, compared in constant time) consumes the code, re-reads the pull request and refuses with 409 (announcing it again if it is still ready) when its head, title, body or `upstream-ready` label changed since the announcement, so the approval is bound to what the owner read. Otherwise it submits the owner's review pinned to the announced SHA: `APPROVE` for approve, `REQUEST_CHANGES` for reject, and wakes the poll loop. Unknown, used or expired codes make no GitHub call. An expired code stays recognisable for 24 hours: replying with it once makes the next poll announce the pull request again with a fresh code, but only if it is still ready on the same head; otherwise the reply says it changed. Unknown and used codes trigger nothing, and a used code never brings the same head back. If GitHub refuses the review, the pull request is announced again with a new code. The result goes back to the Signal group; an approval says publishing follows, naming the last known upstream pull request when there is one. Only the poll loop publishes: on every poll it checks each ready pull request's newest owner review and publishes an approval upstream, then tells the group the upstream pull request link. A refusal, a closed or merged upstream pull request, or an upstream head that is taken is told once per approval and reason, and a notice waits until Signal accepts it. Once a commit is upstream, or was carried by a closed or merged upstream pull request, a later change to the fork pull request never dismisses the approval: the group is told once that the commit is already upstream and that later changes to the pull request are not published; a settled pull request costs only its reviews list and the pull request read, and GETs revalidate with `If-None-Match`. Each fork is polled with its own 45 s timeout, and each pull request within it with 20 s. `GET /healthz` for probes.

The same loop publishes issue and comment drafts: open fork issues labelled `approved-upstream` (pull requests are ignored), in their own 45 s window per fork and 20 s per draft. A draft publishes only when the newest `approved-upstream` label event is the owner's, it has exactly one of `upstream-issue` or `upstream-comment`, a comment draft's title is exactly `Comment on #<N>`, the body is not blank, and no rename, body edit, deleted body revision or kind-label change happened at or after that label (an owner's kind change listed before the label in the same second counts as before). A draft whose body was never edited also needs no activity at all after the label (GitHub's `updated_at` not after it): any comment or label change after approval refuses it, because a deleted revision would leave no trace. An issue draft becomes one upstream issue with exactly its title and body; a comment draft becomes one comment on upstream `#N` with exactly its body. Before posting, an identical issue (open or closed) or comment by the owner upstream is recorded instead, so a lost record never posts twice. The approver then comments an owner record with the upstream link on the fork issue, closes it and tells the group the link. An owner record already on the draft means it was published: the draft is closed and nothing is posted; the record's link is never trusted or repeated. A refusal removes the `approved-upstream` label, which spends the approval, and is told once per approval and reason; adding the label again approves the current text. A label applied by anyone else is removed silently. Read errors retry on the next poll. A closed draft leaves the list, so a published draft costs nothing beyond the list read. Logs never contain the token, the owner number or the group.

| Variable | Meaning |
| --- | --- |
| `GITHUB_TOKEN` | The owner's classic PAT with only `public_repo`; reviews are submitted as its owner |
| `APPROVER_TOKEN` | Shared token signal-shim sends, at least 32 characters |
| `SIGNAL_REST_URL`, `SIGNAL_NUMBER`, `SIGNAL_RECIPIENT` | signal-cli-rest-api, the sender and the group (`group.<id>`) |
| `GITHUB_API_URL` | Default `https://api.github.com` |
| `LISTEN_ADDR` | Listen address, default `:8080` |

All except `GITHUB_API_URL` and `LISTEN_ADDR` are required.
