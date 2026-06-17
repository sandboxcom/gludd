"""Per-host robots.txt cache, fetched THROUGH the SSRF client.

Uses ``urllib.robotparser.RobotFileParser`` for parsing but fetches the
``robots.txt`` body via :class:`SsrfSafeClient` (so the robots fetch is itself
SSRF-guarded and bounded), NOT via robotparser's own ``urlopen``.

POLICY: the client is HTTPS-only, so robots is only fetched over https.  On an
http-only host robots is left unfetched and the crawl proceeds per the documented
default (``allow_when_unfetched=True``) rather than fail-closed-blocking.
"""

from __future__ import annotations

from urllib.parse import urlsplit, urlunsplit
from urllib.robotparser import RobotFileParser

from general_ludd.web.results import WebError
from general_ludd.web.ssrf_client import SsrfSafeClient


class RobotsCache:
    """Caches one :class:`RobotFileParser` per ``scheme://host[:port]``."""

    def __init__(
        self,
        client: SsrfSafeClient,
        *,
        allow_when_unfetched: bool = True,
    ) -> None:
        self._client = client
        self._allow_when_unfetched = allow_when_unfetched
        self._parsers: dict[str, RobotFileParser | None] = {}
        self._delays: dict[str, float | None] = {}

    @staticmethod
    def _origin(url: str) -> tuple[str, str]:
        parts = urlsplit(url)
        netloc = parts.netloc
        origin = urlunsplit((parts.scheme, netloc, "/robots.txt", "", ""))
        key = f"{parts.scheme}://{netloc}"
        return key, origin

    def _load(self, url: str) -> RobotFileParser | None:
        key, robots_url = self._origin(url)
        if key in self._parsers:
            return self._parsers[key]
        parser: RobotFileParser | None
        # Only https is fetchable (client invariant); http robots stays unfetched.
        if not robots_url.lower().startswith("https://"):
            self._parsers[key] = None
            self._delays[key] = None
            return None
        result = self._client.fetch(robots_url)
        if result.ok and result.status == 200 and result.body:
            parser = RobotFileParser()
            parser.parse(result.body.splitlines())
            try:
                self._delays[key] = parser.crawl_delay("*")  # type: ignore[assignment]
            except Exception:
                self._delays[key] = None
        elif result.error in (WebError.SSRF_BLOCKED,):
            # An SSRF-blocked robots fetch must NOT silently open the host.
            parser = RobotFileParser()
            parser.parse(["User-agent: *", "Disallow: /"])
            self._delays[key] = None
        else:
            # 404 / offline / non-200: no robots policy -> treat as allow-all per
            # the robots.txt convention (absence => permitted), bounded by caller.
            parser = None
            self._delays[key] = None
        self._parsers[key] = parser
        return parser

    def can_fetch(self, user_agent: str, url: str) -> bool:
        """True iff ``user_agent`` may fetch ``url`` per the host's robots.txt."""
        parser = self._load(url)
        if parser is None:
            return self._allow_when_unfetched
        try:
            return parser.can_fetch(user_agent, url)
        except Exception:
            return self._allow_when_unfetched

    def crawl_delay(self, url: str) -> float | None:
        """Return the host's Crawl-delay (seconds) if any, else None."""
        key, _ = self._origin(url)
        if key not in self._parsers:
            self._load(url)
        return self._delays.get(key)
