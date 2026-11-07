# cmd

Go programs, all built into the `agent-tools` image (`images/agent-tools/Dockerfile`). Tests: `go test -race ./...`.

## signal-shim

Serves the subset of signal-cli's HTTP daemon API that the Hermes Signal adapter uses (`GET /api/v1/check`, `GET /api/v1/events` as SSE, `POST /api/v1/rpc`) and translates it to signal-cli-rest-api in json-rpc mode.

`send` keeps the adapter's formatting: the `textStyle`/`textStyles` ranges (bold, italic, strikethrough, monospace, spoiler) become signal-cli-rest-api's `text_mode: "styled"` markers, with literal `*`, `` ` ``, `~` and `|` escaped. A range the styled parser can't express is dropped and its text sent unstyled: ranges that cross, a style overlapping itself, or a marker directly after a literal `\`.

Polls: `sendPollCreate` (`question`, `options`, a `groupId` or one `recipient`) and `sendPollTerminate` (`pollTimestamp`) map to `/v1/polls`. Without an `account`, the shim uses the only registered account. The adapter reads only a message's text, so the shim gives a vote on the event stream a text of its own: `🗳️ Poll "<question>": <options> (poll <timestamp>)`. It remembers the last 256 polls it created to name the options; after a restart it falls back to option numbers. Hermes sends polls with `signal-poll` (`images/hermes/bin/signal-poll`).

Contribution approvals: when `APPROVER_URL` is set, a new message from the owner (`sourceNumber`, or `source` when it is a number, equal to `OWNER_NUMBER`; or `sourceUuid`, or `source` when it is a UUID, equal to `OWNER_UUID`, in any case) whose whole text is `approve <code>` or `reject <code>` goes to `contribution-approver` as `POST /v1/decisions` with the shared token (30 s timeout, above the approver's 20 s decision budget), and Hermes never sees it. The text is trimmed and the word may be in any case; the code is exactly 8 digits and nothing else may follow. The message may be a quote reply and may carry signal-cli's `hasProfileKey` flag; other set flags (`viewOnce`, `isExpirationUpdate`, `isProfileKeyUpdate`, `isEndSession`) and any content besides the text and quote make it not a decision. A quote reply whose whole text is just `approve` or `reject` takes the code from the quoted message, but only if the shim's own account wrote it (`authorNumber`, or `author`, equal to the event's `account`; or `authorUuid`, `authorAci`, `authorServiceId` or `author` equal to `SHIM_ACCOUNT_UUID`) and it has exactly one announcement code line (`Reply "approve <code>" ...`). A code in the text wins over the quote. If the approver is unreachable or refuses the call, the shim tells the sender in the same chat that nothing was submitted. Anything else, including edits, reactions, sync messages, attachments, mentions and other senders, reaches Hermes as before. A message from the shim's own account (its number, or its UUID when `SHIM_ACCOUNT_UUID` is set) is never a decision, even if it also carries the owner's number. When a message that reaches Hermes has `approve` or `reject` as its whole first word, the shim logs `approve/reject message not routed as a decision: <reason>`, the first check that failed (for example `sender is not the owner (number present: false, uuid present: true)`, `unknown dataMessage key mentions` or `quote is not by this account`); the line holds key names only, never text, codes, numbers or UUIDs. The shim only receives while Hermes holds the event stream open.

| Variable | Meaning |
| --- | --- |
| `SIGNAL_REST_URL` | signal-cli-rest-api base URL (required) |
| `LISTEN_ADDR` | Listen address, default `127.0.0.1:8090` |
| `APPROVER_URL`, `APPROVER_TOKEN`, `OWNER_NUMBER` | contribution-approver base URL, shared token (at least 32 characters) and the owner's E.164 number; set all three or none. If `OWNER_NUMBER` is not exactly one E.164 number (for example a list), approvals are disabled with a log line and everything still reaches Hermes |
| `OWNER_UUID` | The owner's Signal account UUID (ACI), optional. Required when the owner's number is hidden ("Who can see my number: Nobody"): Signal then delivers the owner's messages without `sourceNumber`. If it is not exactly one UUID, approvals are disabled with a log line |
| `SHIM_ACCOUNT_UUID` | The bot account's Signal UUID, optional. A message from this UUID is never a decision. It also lets a bare `approve`/`reject` quote reply count when the quote names the announcement's author only by UUID. If it is not exactly one UUID, approvals are disabled with a log line |

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

Every 30 s it also reads the Kanban gate's event stream (`hermes/agent_stack_hermes/gate.py`) and projects cards with a Linear link onto their issue: status, agent activity, an evidence comment, and a 🔴 Signal alert for a post-run check violation. A card for `omnigent-runner` without a Linear link, such as an infra sweep, has no issue: when a batch holds its `completed`, `blocked`, `gave_up` or `block_loop_detected` event, the owner gets one Signal report instead (`report.go`). Every card field in it is reduced to one clean, clipped line, PR links are printed only as `https://github.com/<owner>/<repo>/pull/<n>`, and the whole message stays under 2000 characters. Reports wait in an in-memory queue that never holds the cursor: an unsent report is retried on the next ticks and, after 10 failed attempts, dropped with one alert attempt. A restart loses queued reports.

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

Publishes owner-approved contributions from the contribution forks upstream, and turns the owner's Signal replies into reviews (`docs/contribution-fork.md`). It is the only cluster component that holds the owner's PAT.

Fork settings come from `forks.json`. It is embedded in the binary and rendered from `config/public-contributions.yaml` by `templates/render_approver_config.py` (`go generate`; a test fails on drift), so a registry change ships with the next release.

**Poll loop.** Every 60 s, and early after a Signal decision, the approver polls each fork with a 45 s timeout:

- It checks that the token belongs to the fork owner, and that GitHub reports the fork as owned by that owner with the registry upstream as its parent.
- It lists open pull requests on the default branch. A pull request is ready when it is labelled `upstream-ready`, its head is a fork branch other than `upstream/*`, and its base is the fork's default branch. Each ready pull request gets 20 s.
- It then polls issue drafts in their own 45 s window, 20 s per draft, so a stalled fork can hold one iteration for up to 90 s.
- Only the poll loop publishes. Every list is read to the end; when publishing, more pages than the cap refuse.

**Announcements.** A ready pull request is announced once per head SHA to the Signal group: title, the first 300 runes of the body, head SHA, files/additions/deletions, CI result, link and a single-use 8-digit code that expires after 30 minutes.

- It is announced only when every CI run on the head completed with `success`. A title that doesn't follow `title_format`, a changed base, or an upstream diff that is empty, too large, touches `.github/` or differs from the fork-relative diff is logged once per head and not announced.
- A new announcement of the same pull request, such as for a new head SHA, replaces the code. A pull request that stops being ready loses it.
- A head the owner already reviewed is not announced, unless the approval can't be bound to an announcement (after a restart, or when the text changed). Then it is announced once more.
- Codes live in memory, so a restart announces again with new codes. That skips a pull request whose approved commit is already on its upstream pull request, or was carried by a closed or merged one.
- If listing the reviews after an announcement fails, the announcement is retried on the next poll, then at most every 30 minutes, doubling up to a day. A new head is announced at once.

**Decisions.** `POST /v1/decisions` (`{"action":"approve"|"reject","code":"<8 digits>"}`, `Authorization: Bearer <APPROVER_TOKEN>`, compared in constant time):

- It consumes the code and re-reads the pull request. If its head, title, body or `upstream-ready` label changed since the announcement, it answers 409 and announces again if the pull request is still ready.
- Otherwise it submits the owner's review pinned to the announced SHA (`APPROVE` or `REQUEST_CHANGES`) and wakes the poll loop. If GitHub refuses the review, the pull request is announced again with a new code.
- Unknown, used, replaced and expired codes make no GitHub call. An expired code stays recognisable for 24 hours: replying with it once makes the next poll announce the pull request again, if it is still ready on the same head. A used code never brings the same head back.
- A replaced code also stays recognisable for 24 hours after it would have expired, once: the reply (410) says the announcement is outdated because the pull request has a newer one, and nothing is announced again. Used and unknown codes answer 404 "That code is unknown or already used".
- The reply goes to the Signal group. An approval says publishing follows.

**Publishing pull requests.** On every poll the owner's newest `APPROVED`, `CHANGES_REQUESTED` or `DISMISSED` review decides. Only `APPROVED` publishes, exactly its `commit_id`. It publishes only when all of these hold:

- the approval came after the announcement of that commit, and the title and body are unchanged since (the review ID is greater than the newest review when the announcement went out);
- the title wasn't renamed and the body wasn't edited at or after the review, by the GraphQL edit history;
- the pull request is open, labelled and was never closed and reopened after the review, and its base was never changed;
- every CI run and check run on the commit completed with `success`; any other conclusion refuses;
- the fork-relative and upstream-relative diffs list the same files and blobs, and neither touches `.github/`.

An approval before the announcement fails the first rule, so the owner approves after the announcement, which comes once CI is green. CI re-run after the announcement is a transient refusal: the approval waits for the run. A hard refusal dismisses the owner's approval with the reason, which spends it. Transient refusals retry on the next poll.

The write points `upstream/pr-<N>` at the commit, then opens the upstream pull request (`maintainer_can_modify: false`), or, if one is open, updates its title and body after the branch move. Once the branch matches, the text is never written again.

- Upstream pull requests count only when their author is the owner and their head repository is the fork; they are listed with `state=all`.
- A commit that a closed or merged upstream pull request carried is never republished. A different commit needs an owner approval newer than the close.
- A 422 "A pull request already exists" or `head invalid` on opening waits; other 422s refuse.
- A branch move that GitHub refuses "without `workflow` scope" waits, and the group is told once per approval to sync the fork first.
- Once a commit is upstream, later changes to the fork pull request never dismiss its approval; the group is told once that later changes are not published.
- A settled pull request costs its reviews list and the pull request read; GETs revalidate with `If-None-Match`.

**Issue drafts.** Open fork issues labelled `approved-upstream` (pull requests are ignored). A draft publishes only when all of these hold:

- the newest `approved-upstream` label event is the owner's; a label applied by anyone else is removed silently;
- it has `upstream-issue` and not `upstream-comment`, and its body is not blank;
- its title and body don't contain `contribution-fork:` in any case;
- no rename, body edit, deleted body revision or kind-label change happened at or after the label;
- its three labels match a replay of its label events, and none of the three repository labels was edited at or after the label;
- a draft whose body was never edited had no activity after the label (`updated_at` not after it).

The draft becomes one upstream issue: its title, and its body followed by a blank line and `<!-- contribution-fork:<fork>#<n> -->`. Then the approver comments an owner record with the link on the fork issue, removes `approved-upstream` (spending the approval) and closes the issue.

- Before posting and before refusing, it lists the owner's upstream issues updated since the draft was created, every page. An owner issue carrying this draft's exact marker is its publish and is recorded instead of posted again.
- Within one process, a ledger keyed by draft remembers every upstream write until its record exists.
- An unedited owner record on the draft (`updated_at` equal to `created_at`) means it was published. An edited one refuses the draft.
- A hard refusal removes `approved-upstream`. Upstream 404, 410, 422 and a 403 that isn't a rate limit refuse; other errors retry.
- The upstream write runs on its own 8 s deadline, and its link is told at once. Record, label and close share another 8 s deadline.

**Comment drafts are off.** A draft carrying `upstream-comment` at the approval or now is refused, and the group is told to file it as an issue draft or post it by hand.

**Owner notices.** Notices hold only fixed text, the fork, the number, a checked SHA and a shape-checked upstream link. Each is told once per approval and reason, and waits until Signal accepts it.

**Shutdown.** On SIGTERM the approver starts no new draft, finishes the one in flight and exits within 25 s. This needs one replica, `strategy: Recreate` and a termination grace of at least 30 s.

`GET /healthz` for probes. Logs never contain the token, the owner number or the group.

| Variable | Meaning |
| --- | --- |
| `GITHUB_TOKEN` | The owner's classic PAT with only `public_repo`; reviews, dismissals and upstream writes are made as its owner |
| `APPROVER_TOKEN` | Shared token signal-shim sends, at least 32 characters |
| `SIGNAL_REST_URL`, `SIGNAL_NUMBER`, `SIGNAL_RECIPIENT` | signal-cli-rest-api, the sender and the group (`group.<id>`) |
| `GITHUB_API_URL` | Default `https://api.github.com` |
| `LISTEN_ADDR` | Listen address, default `:8080` |

All except `GITHUB_API_URL` and `LISTEN_ADDR` are required.
