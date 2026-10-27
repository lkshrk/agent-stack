# cmd

Go programs, all built into the `agent-tools` image (`images/agent-tools/Dockerfile`). Tests: `go test -race ./...`.

## signal-shim

Serves the subset of signal-cli's HTTP daemon API that the Hermes Signal adapter uses (`GET /api/v1/check`, `GET /api/v1/events` as SSE, `POST /api/v1/rpc`) and translates it to signal-cli-rest-api in json-rpc mode.

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
