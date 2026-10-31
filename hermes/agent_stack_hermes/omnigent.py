import base64
import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Iterable, Iterator
from dataclasses import dataclass

TOKEN_SKEW_SECONDS = 300
TRANSPORT_STATUS = 0
BAD_BODY_STATUS = 502
LIST_PAGE_SIZE = 100
MAX_LIST_PAGES = 10


class OmnigentError(Exception):
    def __init__(self, status: int, message: str = "", malformed: bool = False):
        super().__init__(f"omnigent: HTTP {status} {message}".strip())
        self.status = status
        self.malformed = malformed


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


def _transport_error(exc: Exception) -> OmnigentError:
    return OmnigentError(TRANSPORT_STATUS, f"{type(exc).__name__}: {exc}")


def _bad_body(message: str) -> OmnigentError:
    return OmnigentError(BAD_BODY_STATUS, message, malformed=True)


def _decode_object(raw: bytes) -> dict:
    try:
        value = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        raise _bad_body("response body is not JSON") from None
    if not isinstance(value, dict):
        raise _bad_body("response body is not a JSON object")
    return value


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
                raw = response.read()
        except urllib.error.HTTPError as exc:
            raise _error_from(exc) from None
        except OSError as exc:
            raise _transport_error(exc) from None
        payload = _decode_object(raw)
        token = payload.get("access_token")
        expires_in = payload.get("expires_in")
        if not isinstance(token, str) or not token:
            raise _bad_body("token response has no access_token")
        if isinstance(expires_in, bool) or not isinstance(expires_in, (int, float)):
            raise _bad_body("token response has no numeric expires_in")
        self._token = token
        self._expires_at = self._now() + expires_in - TOKEN_SKEW_SECONDS


def _frame(name: str, data: list[str]) -> StreamEvent:
    payload = "\n".join(data)
    if payload == "[DONE]":
        return StreamEvent(name, None)
    try:
        value = json.loads(payload)
    except ValueError:
        raise _bad_body(f"malformed SSE data in {name!r} frame") from None
    if not isinstance(value, dict):
        raise _bad_body(f"SSE data in {name!r} frame is not an object")
    return StreamEvent(name, value)


def parse_sse(lines: Iterable[bytes]) -> Iterator[StreamEvent]:
    name = "message"
    data: list[str] = []
    for raw in lines:
        try:
            line = raw.decode("utf-8").rstrip("\r\n")
        except UnicodeDecodeError:
            raise _bad_body("SSE line is not UTF-8") from None
        if line == "":
            if data:
                yield _frame(name, data)
            name = "message"
            data = []
        elif line.startswith(":"):
            continue
        elif line.startswith("event:"):
            name = line[6:].strip()
        elif line.startswith("data:"):
            data.append(line[5:].removeprefix(" "))


class OmnigentClient:
    def __init__(self, base_url: str, tokens: TokenSource, opener=urllib.request.urlopen, timeout: float = 30.0):
        self._base = base_url.rstrip("/")
        self._tokens = tokens
        self._opener = opener
        self._timeout = timeout

    def create_session(self, body: dict) -> str:
        session_id = self._json("POST", "/v1/sessions", body).get("id")
        if not isinstance(session_id, str) or not session_id:
            raise _bad_body("session response has no id")
        return session_id

    def snapshot(self, session_id: str) -> dict:
        return self._json("GET", f"/v1/sessions/{session_id}?include_items=true&include_usage=true")

    def pull_requests(self, session_id: str) -> list[dict]:
        data = self._json("GET", f"/v1/sessions/{session_id}/pull-requests").get("data")
        if not isinstance(data, list):
            raise _bad_body("pull-requests response has no data list")
        return data

    def list_sessions(
        self, agent_id: str | None = None, visibility: str = "mine", max_pages: int = MAX_LIST_PAGES
    ) -> list[dict]:
        params = {"limit": LIST_PAGE_SIZE, "order": "desc", "sort_by": "created_at", "visibility": visibility}
        if agent_id:
            params["agent_id"] = agent_id
        return self._pages("/v1/sessions", params, max_pages)

    def child_sessions(self, session_id: str, max_pages: int = MAX_LIST_PAGES) -> list[dict]:
        params = {"limit": LIST_PAGE_SIZE, "order": "desc"}
        return self._pages(f"/v1/sessions/{session_id}/child_sessions", params, max_pages)

    def _pages(self, path: str, params: dict, max_pages: int) -> list[dict]:
        params = dict(params)
        sessions: list[dict] = []
        for _ in range(max_pages):
            page = self._json("GET", path + "?" + urllib.parse.urlencode(params))
            data = page.get("data")
            if not isinstance(data, list) or not all(isinstance(item, dict) for item in data):
                raise _bad_body("session list has no data list of objects")
            sessions.extend(data)
            if page.get("has_more") is not True:
                return sessions
            last_id = page.get("last_id")
            if not isinstance(last_id, str) or not last_id:
                raise _bad_body("session list has more pages but no last_id")
            params["after"] = last_id
        raise _bad_body("too many session list pages")

    def interrupt(self, session_id: str) -> None:
        self._json("POST", f"/v1/sessions/{session_id}/events", {"type": "interrupt"}, allow_empty=True)

    def share(self, session_id: str, user_id: str, level: int = 1) -> None:
        self._json(
            "PUT", f"/v1/sessions/{session_id}/permissions", {"user_id": user_id, "level": level}, allow_empty=True
        )

    def stream(self, session_id: str, read_timeout: float) -> Iterator[StreamEvent]:
        with self._open("GET", f"/v1/sessions/{session_id}/stream", None, read_timeout, "text/event-stream") as response:
            try:
                yield from parse_sse(response)
            except OSError as exc:
                raise _transport_error(exc) from None

    def _json(self, method: str, path: str, body: dict | None = None, allow_empty: bool = False) -> dict:
        with self._open(method, path, body, self._timeout, "application/json") as response:
            try:
                raw = response.read()
            except OSError as exc:
                raise _transport_error(exc) from None
        if not raw.strip():
            if allow_empty:
                return {}
            raise _bad_body("empty response body")
        return _decode_object(raw)

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
            except OSError as exc:
                raise _transport_error(exc) from None
