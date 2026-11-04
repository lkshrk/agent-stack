import json
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit


class FakeGitHub:
    """An in-process GitHub REST API: routes are (method, path regex) -> handler(match, query, body) -> (status, json)."""

    def __init__(self, token: str):
        self.token = token
        self.requests: list[dict] = []
        self.routes: list[tuple[str, re.Pattern, object]] = []
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _serve(self):
                url = urlsplit(self.path)
                length = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(length)) if length else None
                fake.requests.append(
                    {"method": self.command, "path": url.path, "query": url.query, "body": body, "auth": self.headers.get("Authorization")}
                )
                status, payload = fake.dispatch(self.command, url.path, parse_qs(url.query), body, self.headers.get("Authorization"))
                data = json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            do_GET = do_POST = do_PATCH = do_PUT = do_DELETE = _serve

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.server.shutdown()
        self.server.server_close()

    def route(self, method: str, pattern: str, handler) -> None:
        self.routes.insert(0, (method, re.compile(pattern), handler))

    def dispatch(self, method, path, query, body, auth):
        if auth != f"Bearer {self.token}":
            return 401, {"message": "Bad credentials"}
        for route_method, pattern, handler in self.routes:
            match = pattern.fullmatch(path)
            if route_method == method and match:
                return handler(match, query, body)
        return 404, {"message": "Not Found"}

    def writes(self) -> list[tuple[str, str, object]]:
        return [(r["method"], r["path"], r["body"]) for r in self.requests if r["method"] != "GET"]
