# cmd

Go programs, all built into the `agent-tools` image (`images/agent-tools/Dockerfile`). Tests: `go test -race ./...`.

## signal-shim

Serves the subset of signal-cli's HTTP daemon API that the Hermes Signal adapter uses (`GET /api/v1/check`, `GET /api/v1/events` as SSE, `POST /api/v1/rpc`) and translates it to signal-cli-rest-api in json-rpc mode.

`send` keeps the adapter's formatting: the `textStyle`/`textStyles` ranges (bold, italic, strikethrough, monospace, spoiler) become signal-cli-rest-api's `text_mode: "styled"` markers, with literal `*`, `` ` ``, `~` and `|` escaped. A range the styled parser can't express is dropped and its text sent unstyled: ranges that cross, a style overlapping itself, or a marker directly after a literal `\`.

Polls: `sendPollCreate` (`question`, `options`, a `groupId` or one `recipient`) and `sendPollTerminate` (`pollTimestamp`) map to `/v1/polls`. Without an `account`, the shim uses the only registered account. The adapter reads only a message's text, so the shim gives a vote on the event stream a text of its own: `🗳️ Poll "<question>": <options> (poll <timestamp>)`. It remembers the last 256 polls it created to name the options; after a restart it falls back to option numbers. Hermes sends polls with `signal-poll` (`images/hermes/bin/signal-poll`).

| Variable | Meaning |
| --- | --- |
| `SIGNAL_REST_URL` | signal-cli-rest-api base URL (required) |
| `LISTEN_ADDR` | Listen address, default `127.0.0.1:8090` |

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
