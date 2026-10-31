import base64
import json
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Iterable, Iterator
from dataclasses import dataclass

TOKEN_SKEW_SECONDS = 300


class OmnigentError(Exception):
    def __init__(self, status: int, message: str = ""):
        super().__init__(f"omnigent: HTTP {status} {message}".strip())
        self.status = status


@dataclass(frozen=True)
class MachineCredentials:
    token_url: str
    client_id: str
    client_secret: str


@dataclass(frozen=True)
class StreamEvent:
    name: str
    data: dict | None


def _error_from(exc: urllib.error.HTTPError) -> OmnigentError:
    try:
        detail = exc.read().decode("utf-8", "replace")[:500]
    except Exception:
        detail = ""
    finally:
        exc.close()
    return OmnigentError(exc.code, detail)


class TokenSource:
    def __init__(self, creds: MachineCredentials, opener=urllib.request.urlopen, now=time.time):
        self._creds = creds
        self._opener = opener
        self._now = now
        self._lock = threading.Lock()
        self._token: str | None = None
        self._expires_at = 0.0

    def token(self) -> str:
        with self._lock:
            if self._token is None or self._now() >= self._expires_at:
                self._fetch()
            return self._token

    def invalidate(self) -> None:
        with self._lock:
            self._token = None

    def _fetch(self) -> None:
        basic = base64.b64encode(f"{self._creds.client_id}:{self._creds.client_secret}".encode()).decode()
        request = urllib.request.Request(
            self._creds.token_url,
            data=b"grant_type=client_credentials",
            method="POST",
            headers={
                "Authorization": f"Basic {basic}",
                "Content-Type": "application/x-www-form-urlencoded",
                "Accept": "application/json",
            },
        )
        try:
            with self._opener(request, timeout=30.0) as response:
                payload = json.load(response)
        except urllib.error.HTTPError as exc:
            raise _error_from(exc) from None
        self._token = payload["access_token"]
        self._expires_at = self._now() + float(payload.get("expires_in", 0)) - TOKEN_SKEW_SECONDS


def parse_sse(lines: Iterable[bytes]) -> Iterator[StreamEvent]:
    name = "message"
    data: list[str] = []
    for raw in lines:
        line = raw.decode("utf-8").rstrip("\r\n")
        if line == "":
            if data:
                payload = "\n".join(data)
                yield StreamEvent(name, None if payload == "[DONE]" else json.loads(payload))
            name = "message"
            data = []
        elif line.startswith(":"):
            continue
        elif line.startswith("event:"):
            name = line[6:].strip()
        elif line.startswith("data:"):
            data.append(line[5:].removeprefix(" "))
    if data:
        payload = "\n".join(data)
        yield StreamEvent(name, None if payload == "[DONE]" else json.loads(payload))


class OmnigentClient:
    def __init__(self, base_url: str, tokens: TokenSource, opener=urllib.request.urlopen, timeout: float = 30.0):
        self._base = base_url.rstrip("/")
        self._tokens = tokens
        self._opener = opener
        self._timeout = timeout

    def create_session(self, body: dict) -> str:
        return self._json("POST", "/v1/sessions", body)["id"]

    def snapshot(self, session_id: str) -> dict:
        return self._json("GET", f"/v1/sessions/{session_id}?include_items=true&include_usage=true")

    def pull_requests(self, session_id: str) -> list[dict]:
        return self._json("GET", f"/v1/sessions/{session_id}/pull-requests")["data"]

    def interrupt(self, session_id: str) -> None:
        self._json("POST", f"/v1/sessions/{session_id}/events", {"type": "interrupt"})

    def share(self, session_id: str, user_id: str, level: int = 1) -> None:
        self._json("PUT", f"/v1/sessions/{session_id}/permissions", {"user_id": user_id, "level": level})

    def stream(self,session_id: str, read_timeout: float) -> Iterator[StreamEvent]:
        with self._open("GET", f"/v1/sessions/{session_id}/stream", None, read_timeout, "text/event-stream") as response:
            yield from parse_sse(response)

    def _json(self, method: str, path: str, body: dict | None = None) -> dict:
        with self._open(method, path, body, self._timeout, "application/json") as response:
            raw = response.read()
        return json.loads(raw) if raw else {}

    def _open(self, method: str, path: str, body: dict | None, timeout: float, accept: str):
        for attempt in (0, 1):
            headers = {"Authorization": f"Bearer {self._tokens.token()}", "Accept": accept}
            data = None
            if body is not None:
                data = json.dumps(body).encode()
                headers["Content-Type"] = "application/json"
            request = urllib.request.Request(self._base + path, data=data, method=method, headers=headers)
            try:
                return self._opener(request, timeout=timeout)
            except urllib.error.HTTPError as exc:
                if exc.code == 401 and attempt == 0:
                    exc.close()
                    self._tokens.invalidate()
                    continue
                raise _error_from(exc) from None
