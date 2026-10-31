from collections.abc import Callable

from agent_stack_hermes.omnigent import TRANSPORT_STATUS, OmnigentError
from agent_stack_hermes.postrun import GitHubError

OUTAGE_NOTE = "omnigent-outage: "
OUTAGE_OVER = "over"
RATE_LIMITED = 429


def unreachable(exc: BaseException) -> str | None:
    if isinstance(exc, OmnigentError):
        source = "omnigent"
    elif isinstance(exc, GitHubError):
        source = "github"
    else:
        return None
    retryable = exc.status in (TRANSPORT_STATUS, RATE_LIMITED) or 500 <= exc.status < 600
    return source if retryable and not exc.malformed else None


class OutageClock:
    def __init__(self, comments: tuple[str, ...], run_id: int, note: Callable[[str], None], now):
        self._run, self._note, self._now = str(run_id), note, now
        self._since = self._open_since(comments)
        self._success: float | None = None

    def _open_since(self, comments: tuple[str, ...]) -> float | None:
        since = None
        for comment in comments:
            if not comment.startswith(OUTAGE_NOTE):
                continue
            run, _, value = comment[len(OUTAGE_NOTE):].strip().partition(" ")
            if run == self._run:
                try:
                    since = float(value)
                except ValueError:
                    since = None
        return since

    def ok(self) -> None:
        self._success = self._now()
        if self._since is not None:
            self._note(f"{OUTAGE_NOTE}{self._run} {OUTAGE_OVER}")
            self._since = None

    def elapsed(self) -> float:
        t = self._now()
        if self._since is None:
            self._since = t if self._success is None else self._success
            self._note(f"{OUTAGE_NOTE}{self._run} {self._since}")
        return t - self._since
