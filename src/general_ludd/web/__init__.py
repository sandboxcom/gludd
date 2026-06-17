"""gludd web toolkit — SSRF-hardened fetch/parse/search/crawl + optional JS render.

Self-contained package with ZERO new HARD deps for v1: httpx (core) for
transport, stdlib (html.parser, urllib.robotparser, ipaddress, socket) for
parsing/robots/SSRF/normalization, tenacity (core) for retry. The package imports
cleanly with NOTHING extra installed — nothing here imports playwright/trafilatura
at module top level (those are the optional ``[web]`` extra, lazy-imported inside
``render_js`` / rich parse).

Load-bearing invariant: NOTHING in ``web/`` touches the network except through the
one hardened client (:class:`SafeFetcher`). Every public entrypoint returns a
frozen :class:`WebResult` (``ok: bool``) and never raises / never hangs.
"""

from __future__ import annotations

from general_ludd.web.captcha import NullSolver, SolverHook, detect_block
from general_ludd.web.crawl import PoliteCrawler
from general_ludd.web.policy import DEFAULT_POLICY, WebPolicy
from general_ludd.web.resilience import WebResilience
from general_ludd.web.safe_fetch import SafeFetcher, SafeFetchError
from general_ludd.web.search import NullSearchProvider, SearchHit, SearchProvider
from general_ludd.web.tools import (
    TOOL_SPECS,
    crawl_site,
    fetch_parsed,
    fetch_raw,
    render_js,
    search_gather,
)
from general_ludd.web.types import (
    BlockSignal,
    GatheredPage,
    Link,
    ParsedPage,
    WebError,
    WebResult,
)

__all__ = [
    "DEFAULT_POLICY",
    "TOOL_SPECS",
    "BlockSignal",
    "GatheredPage",
    "Link",
    "NullSearchProvider",
    "NullSolver",
    "ParsedPage",
    "PoliteCrawler",
    "SafeFetchError",
    "SafeFetcher",
    "SearchHit",
    "SearchProvider",
    "SolverHook",
    "WebError",
    "WebPolicy",
    "WebResilience",
    "WebResult",
    "crawl_site",
    "detect_block",
    "fetch_parsed",
    "fetch_raw",
    "render_js",
    "search_gather",
]
