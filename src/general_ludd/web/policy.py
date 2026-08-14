"""Fail-closed destination and resource policy for web operations."""

from __future__ import annotations

import math
from dataclasses import dataclass

from general_ludd.security.url_fetch import FetchPolicy

_MAX_BODY_BYTES = 8 * 1024 * 1024
_MAX_REDIRECTS = 10
_MAX_PAGES = 100
_MAX_DEPTH = 5
_MAX_LINKS = 500
_MAX_SEARCH_RESULTS = 50
_MAX_FETCH_SECONDS = 60.0
_MAX_CRAWL_SECONDS = 120.0
_MAX_INTERVAL_SECONDS = 10.0


def _bounded_int(name: str, value: int, *, minimum: int, maximum: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise ValueError(f"{name} must be an integer between {minimum} and {maximum}")


def _bounded_float(name: str, value: float, *, minimum: float, maximum: float) -> None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError(f"{name} must be a finite number between {minimum:g} and {maximum:g}")
    numeric = float(value)
    if not math.isfinite(numeric) or not minimum <= numeric <= maximum:
        raise ValueError(f"{name} must be a finite number between {minimum:g} and {maximum:g}")


@dataclass(frozen=True, slots=True)
class WebPolicy:
    """Construction-time validated limits for fetch, gather, crawl, and render."""

    allowed_hosts: frozenset[str] = frozenset({"*"})
    max_bytes: int = 1024 * 1024
    timeout_seconds: float = 15.0
    dns_timeout_seconds: float = 2.0
    max_redirects: int = 3
    max_pages: int = 10
    max_depth: int = 2
    max_links_per_page: int = 100
    max_search_results: int = 10
    crawl_timeout_seconds: float = 30.0
    min_request_interval_seconds: float = 0.25
    respect_robots: bool = True
    robots_fail_closed: bool = True
    allow_render: bool = False
    render_timeout_seconds: float = 15.0
    max_render_bytes: int = 1024 * 1024
    user_agent: str = "GluddWebToolkit/1.0"

    def __post_init__(self) -> None:
        """Normalize destinations and reject invalid or excessive resource limits."""
        _bounded_int("max_bytes", self.max_bytes, minimum=1, maximum=_MAX_BODY_BYTES)
        _bounded_float(
            "timeout_seconds",
            self.timeout_seconds,
            minimum=0.001,
            maximum=_MAX_FETCH_SECONDS,
        )
        _bounded_float(
            "dns_timeout_seconds",
            self.dns_timeout_seconds,
            minimum=0.001,
            maximum=_MAX_FETCH_SECONDS,
        )
        _bounded_int("max_redirects", self.max_redirects, minimum=0, maximum=_MAX_REDIRECTS)
        _bounded_int("max_pages", self.max_pages, minimum=1, maximum=_MAX_PAGES)
        _bounded_int("max_depth", self.max_depth, minimum=0, maximum=_MAX_DEPTH)
        _bounded_int(
            "max_links_per_page",
            self.max_links_per_page,
            minimum=1,
            maximum=_MAX_LINKS,
        )
        _bounded_int(
            "max_search_results",
            self.max_search_results,
            minimum=1,
            maximum=_MAX_SEARCH_RESULTS,
        )
        _bounded_float(
            "crawl_timeout_seconds",
            self.crawl_timeout_seconds,
            minimum=0.001,
            maximum=_MAX_CRAWL_SECONDS,
        )
        _bounded_float(
            "min_request_interval_seconds",
            self.min_request_interval_seconds,
            minimum=0.0,
            maximum=_MAX_INTERVAL_SECONDS,
        )
        _bounded_float(
            "render_timeout_seconds",
            self.render_timeout_seconds,
            minimum=0.001,
            maximum=_MAX_FETCH_SECONDS,
        )
        _bounded_int(
            "max_render_bytes",
            self.max_render_bytes,
            minimum=1,
            maximum=_MAX_BODY_BYTES,
        )
        if not isinstance(self.respect_robots, bool) or not isinstance(self.robots_fail_closed, bool):
            raise ValueError("robots controls must be booleans")
        if not isinstance(self.allow_render, bool):
            raise ValueError("allow_render must be a boolean")
        if not isinstance(self.user_agent, str) or not self.user_agent.strip():
            raise ValueError("user_agent must be a non-empty string")
        if len(self.user_agent) > 256:
            raise ValueError("user_agent must not exceed 256 characters")

        network = self.fetch_policy()
        object.__setattr__(self, "allowed_hosts", network.allowed_hosts)
        object.__setattr__(self, "user_agent", self.user_agent.strip())

    def fetch_policy(self) -> FetchPolicy:
        """Build the maintained outbound-fetch policy for one request."""
        return FetchPolicy(
            allowed_hosts=frozenset(self.allowed_hosts),
            allowed_schemes=frozenset({"https"}),
            max_bytes=self.max_bytes,
            timeout_seconds=float(self.timeout_seconds),
            dns_timeout_seconds=float(self.dns_timeout_seconds),
            max_redirects=self.max_redirects,
        )


DEFAULT_POLICY = WebPolicy()

__all__ = ["DEFAULT_POLICY", "WebPolicy"]
