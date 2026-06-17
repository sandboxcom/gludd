"""robots.txt handling — fetched THROUGH SafeFetcher, per-host TTL cached.

The robots fetch is itself SSRF-checked (it goes through the same SafeFetcher),
parsed with the stdlib ``urllib.robotparser``, and cached per host with a TTL.
When robots.txt cannot be fetched the decision forks on policy
(``robots_on_error``: ``fail_open`` => allow, ``fail_closed`` => deny). A
``Crawl-delay`` is surfaced so the crawler can feed it into the token bucket.
"""

from __future__ import annotations

import threading
import time
import urllib.robotparser
from dataclasses import dataclass
from urllib.parse import urlsplit

from general_ludd.web.policy import WebPolicy
from general_ludd.web.safe_fetch import SafeFetcher


@dataclass
class _Entry:
    parser: urllib.robotparser.RobotFileParser | None
    fetched_at: float
    reachable: bool
    crawl_delay: float | None


class RobotsCache:
    """Per-host robots.txt cache with SSRF-checked fetch + TTL + policy fork."""

    def __init__(self, fetcher: SafeFetcher, policy: WebPolicy) -> None:
        self._fetcher = fetcher
        self._policy = policy
        self._cache: dict[str, _Entry] = {}
        self._lock = threading.Lock()
        self._clock = time.monotonic

    def _robots_url(self, url: str) -> tuple[str, str]:
        parts = urlsplit(url)
        host = parts.netloc
        scheme = parts.scheme or "https"
        return host, f"{scheme}://{host}/robots.txt"

    def _load(self, url: str) -> _Entry:
        _host, robots_url = self._robots_url(url)
        rp = urllib.robotparser.RobotFileParser()
        rp.set_url(robots_url)
        try:
            raw = self._fetcher.fetch(robots_url)
        except Exception:
            # Unreachable / blocked robots.txt (SafeFetchError, socket/transport
            # error, anything) -> reachable=False; the decision fork happens in
            # can_fetch per policy.robots_on_error.
            return _Entry(parser=None, fetched_at=self._clock(), reachable=False, crawl_delay=None)
        if raw.status >= 400:
            # 404 robots == allow-all per the standard; 5xx == unreachable.
            if raw.status == 404:
                rp.parse([])
                return _Entry(parser=rp, fetched_at=self._clock(), reachable=True, crawl_delay=None)
            return _Entry(parser=None, fetched_at=self._clock(), reachable=False, crawl_delay=None)
        text = raw.body.decode("utf-8", errors="replace")
        rp.parse(text.splitlines())
        delay = rp.crawl_delay(self._policy.user_agent)
        return _Entry(
            parser=rp,
            fetched_at=self._clock(),
            reachable=True,
            crawl_delay=float(delay) if delay is not None else None,
        )

    def _entry(self, url: str) -> _Entry:
        host, _ = self._robots_url(url)
        with self._lock:
            entry = self._cache.get(host)
            if entry is not None and (
                self._clock() - entry.fetched_at < self._policy.robots_ttl_seconds
            ):
                return entry
        entry = self._load(url)
        with self._lock:
            self._cache[host] = entry
        return entry

    def can_fetch(self, url: str) -> bool:
        """True iff ``policy.user_agent`` may fetch ``url`` per robots.txt.

        ``obey_robots=False`` short-circuits to allow. An unreachable robots.txt
        forks on ``policy.robots_on_error``.
        """
        if not self._policy.obey_robots:
            return True
        entry = self._entry(url)
        if not entry.reachable or entry.parser is None:
            return self._policy.robots_on_error == "fail_open"
        return entry.parser.can_fetch(self._policy.user_agent, url)

    def crawl_delay(self, url: str) -> float | None:
        """The robots ``Crawl-delay`` for this host, if any."""
        entry = self._entry(url)
        return entry.crawl_delay
