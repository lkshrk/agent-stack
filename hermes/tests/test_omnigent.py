import base64
import io
import json
import unittest
import urllib.error
from pathlib import Path
from urllib.parse import parse_qs

from agent_stack_hermes.omnigent import (
    MachineCredentials,
    OmnigentClient,
    OmnigentError,
    StreamEvent,
    TokenSource,
    parse_sse,
)

TESTDATA = Path(__file__).parent / "testdata" / "omnigent"
CREDS = MachineCredentials("https://idp.test/token", "cid", "secret")


class FakeResponse(io.BytesIO):
    def __init__(self, body=b"", status=200):
        super().__init__(body)
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def json_response(obj, status=200):
    return FakeResponse(json.dumps(obj).encode(), status)


class FakeOpener:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []
        self.timeouts = []

    def __call__(self, request, timeout=None):
        self.requests.append(request)
        self.timeouts.append(timeout)
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def http_error(status, body=b"{}"):
    return urllib.error.HTTPError("http://x", status, "err", {}, io.BytesIO(body))


def token_response(token="tok", expires_in=3600):
    return json_response({"access_token": token, "expires_in": expires_in})


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


class TokenSourceTest(unittest.TestCase):
    def test_token_form_and_cache(self):
        opener = FakeOpener([token_response("a")])
        source = TokenSource(CREDS, opener=opener, now=Clock())
        self.assertEqual(source.token(), "a")
        self.assertEqual(source.token(), "a")
        self.assertEqual(len(opener.requests), 1)
        request = opener.requests[0]
        self.assertEqual(request.get_method(), "POST")
        self.assertEqual(request.full_url, CREDS.token_url)
        self.assertEqual(parse_qs(request.data.decode()), {"grant_type": ["client_credentials"]})
        expected = "Basic " + base64.b64encode(b"cid:secret").decode()
        self.assertEqual(request.get_header("Authorization"), expected)

    def test_expiry_refreshes_before_deadline(self):
        clock = Clock()
        opener = FakeOpener([token_response("a", 600), token_response("b", 600)])
        source = TokenSource(CREDS, opener=opener, now=clock)
        self.assertEqual(source.token(), "a")
        clock.t += 299
        self.assertEqual(source.token(), "a")
        clock.t += 2
        self.assertEqual(source.token(), "b")

    def test_token_endpoint_failure_raises(self):
        source = TokenSource(CREDS, opener=FakeOpener([http_error(401)]), now=Clock())
        with self.assertRaises(OmnigentError) as ctx:
            source.token()
        self.assertEqual(ctx.exception.status, 401)


def client(responses, token_count=1):
    tokens = TokenSource(
        CREDS,
        opener=FakeOpener([token_response(f"t{i}") for i in range(token_count)]),
        now=Clock(),
    )
    opener = FakeOpener(responses)
    return OmnigentClient("https://omni.test/", tokens, opener=opener), opener


class ClientTest(unittest.TestCase):
    def test_retry_once_on_401(self):
        c, opener = client([http_error(401), json_response({"id": "s1"}, 201)], token_count=2)
        self.assertEqual(c.create_session({"a": 1}), "s1")
        auth = [r.get_header("Authorization") for r in opener.requests]
        self.assertEqual(auth, ["Bearer t0", "Bearer t1"])

    def test_second_401_raises(self):
        c, _ = client([http_error(401), http_error(401)], token_count=2)
        with self.assertRaises(OmnigentError) as ctx:
            c.create_session({})
        self.assertEqual(ctx.exception.status, 401)

    def test_create_session_returns_id(self):
        c, opener = client([json_response({"id": "s1", "status": "idle"}, 201)])
        self.assertEqual(c.create_session({"agent": "polly"}), "s1")
        request = opener.requests[0]
        self.assertEqual(request.get_method(), "POST")
        self.assertEqual(request.full_url, "https://omni.test/v1/sessions")
        self.assertEqual(json.loads(request.data), {"agent": "polly"})
        self.assertEqual(request.get_header("Content-type"), "application/json")
        self.assertFalse(request.has_header("Origin"))
        self.assertEqual(request.get_header("Authorization"), "Bearer t0")

    def test_non_2xx_raises(self):
        c, _ = client([http_error(422, b'{"detail": "bad"}')])
        with self.assertRaises(OmnigentError) as ctx:
            c.create_session({})
        self.assertEqual(ctx.exception.status, 422)

    def test_snapshot_query(self):
        c, opener = client([json_response({"id": "s1"})])
        self.assertEqual(c.snapshot("s1"), {"id": "s1"})
        self.assertEqual(
            opener.requests[0].full_url,
            "https://omni.test/v1/sessions/s1?include_items=true&include_usage=true",
        )

    def test_pull_requests_returns_data(self):
        c, opener = client([json_response({"object": "list", "data": [{"url": "u"}], "synced": True})])
        self.assertEqual(c.pull_requests("s1"), [{"url": "u"}])
        self.assertEqual(opener.requests[0].full_url, "https://omni.test/v1/sessions/s1/pull-requests")

    def test_interrupt_posts_event(self):
        c, opener = client([json_response({}, 202)])
        self.assertIsNone(c.interrupt("s1"))
        request = opener.requests[0]
        self.assertEqual(request.full_url, "https://omni.test/v1/sessions/s1/events")
        self.assertEqual(json.loads(request.data), {"type": "interrupt"})

    def test_share_puts_permission(self):
        c, opener = client([json_response({})])
        self.assertIsNone(c.share("s1", "u1", level=2))
        request = opener.requests[0]
        self.assertEqual(request.get_method(), "PUT")
        self.assertEqual(request.full_url, "https://omni.test/v1/sessions/s1/permissions")
        self.assertEqual(json.loads(request.data), {"user_id": "u1", "level": 2})

    def test_stream_uses_read_timeout_and_yields_events(self):
        body = b'event: session.status\ndata: {"status": "idle"}\n\ndata: [DONE]\n\n'
        c, opener = client([FakeResponse(body)])
        events = list(c.stream("s1", read_timeout=90.0))
        self.assertEqual(events, [StreamEvent("session.status", {"status": "idle"}), StreamEvent("message", None)])
        self.assertEqual(opener.timeouts, [90.0])
        request = opener.requests[0]
        self.assertEqual(request.full_url, "https://omni.test/v1/sessions/s1/stream")
        self.assertEqual(request.get_header("Accept"), "text/event-stream")


class ParseSseTest(unittest.TestCase):
    def test_parse_sse_frames(self):
        chunks = [
            b": connected\n",
            b"\n",
            b"event: a\n",
            b'data: {"x": 1}\n',
            b": keepalive\n",
            b"\n",
            b"event: b\n",
            b'data: {"y": 2}\n',
            b"\n",
            b"data: [DONE]\n",
            b"\n",
        ]
        self.assertEqual(
            list(parse_sse(chunks)),
            [StreamEvent("a", {"x": 1}), StreamEvent("b", {"y": 2}), StreamEvent("message", None)],
        )

    def test_crlf_lines(self):
        events = list(parse_sse([b"event: a\r\n", b'data: {"x": 1}\r\n', b"\r\n"]))
        self.assertEqual(events, [StreamEvent("a", {"x": 1})])

    def test_parse_sse_fixture(self):
        with open(TESTDATA / "stream_polly.txt", "rb") as f:
            events = list(parse_sse(f))
        self.assertIsNone(events[-1].data)
        statuses = [e for e in events if e.name == "session.status"]
        self.assertTrue(statuses)
        self.assertEqual(statuses[-1].data["status"], "idle")


if __name__ == "__main__":
    unittest.main()
