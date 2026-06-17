"""general_ludd.web — SSRF-hardened web fetch / parse / search / crawl toolkit.

A layered toolkit whose keystone is a single hardened :class:`SsrfSafeClient`
that closes the DNS-rebinding / TOCTOU hole left by the literal-host-only
``security.auth.is_safe_fetch_url``.  Every component routes through that one
client, so the SSRF guard + explicit timeout + tenacity retry + per-host circuit
breaker + structured offline fallback are enforced in exactly one place.

This module imports ONLY base dependencies (httpx + stdlib + tenacity + pydantic)
and NEVER imports playwright/selenium/trafilatura at module scope, so
``import general_ludd.web`` succeeds with nothing extra installed and the test
suite's collection-check stays green.  Those JS-render / rich-extraction backends
are behind the optional ``[web]`` extra and lazy-imported inside ``render_page``.
"""

from __future__ import annotations

from general_ludd.web.breaker import HostCircuitBreaker
from general_ludd.web.captcha import (
    CaptchaSolver,
    SolveOutcome,
    UnconfiguredSolver,
    detect_captcha,
)
from general_ludd.web.crawl import Crawler, CrawlPolicy, registrable_domain
from general_ludd.web.fetch import fetch_raw
from general_ludd.web.parse import fetch_parsed
from general_ludd.web.render import RenderConfig, render_page
from general_ludd.web.results import (
    CaptchaSignal,
    CrawlResult,
    ParsedPage,
    RawFetchResult,
    RenderResult,
    SearchHit,
    SearchResult,
    WebError,
)
from general_ludd.web.search import NullProvider, SearchProvider, search_gather
from general_ludd.web.ssrf_client import DEFAULT_UA, SsrfSafeClient

__all__ = [
    "DEFAULT_UA",
    "CaptchaSignal",
    "CaptchaSolver",
    "CrawlPolicy",
    "CrawlResult",
    "Crawler",
    "HostCircuitBreaker",
    "NullProvider",
    "ParsedPage",
    "RawFetchResult",
    "RenderConfig",
    "RenderResult",
    "SearchHit",
    "SearchProvider",
    "SearchResult",
    "SolveOutcome",
    "SsrfSafeClient",
    "UnconfiguredSolver",
    "WebError",
    "detect_captcha",
    "fetch_parsed",
    "fetch_raw",
    "registrable_domain",
    "render_page",
    "search_gather",
]
