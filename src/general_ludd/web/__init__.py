"""SSRF-safe bounded web toolkit integrated with current Gludd primitives."""

from general_ludd.web.parse import parse_html
from general_ludd.web.policy import DEFAULT_POLICY, WebPolicy
from general_ludd.web.toolkit import (
    Fetcher,
    NullSearchProvider,
    OfflineRenderer,
    SearchProvider,
    WebToolkit,
    normalize_url,
)
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
    CaptchaSignal,
    CrawlResult,
    GatheredPage,
    Link,
    ParsedPage,
    RawFetchResult,
    RenderResult,
    SearchHit,
    SearchResult,
    WebError,
    WebResult,
)

NullProvider = NullSearchProvider

__all__ = [
    "DEFAULT_POLICY",
    "TOOL_SPECS",
    "BlockSignal",
    "CaptchaSignal",
    "CrawlResult",
    "Fetcher",
    "GatheredPage",
    "Link",
    "NullProvider",
    "NullSearchProvider",
    "OfflineRenderer",
    "ParsedPage",
    "RawFetchResult",
    "RenderResult",
    "SearchHit",
    "SearchProvider",
    "SearchResult",
    "WebError",
    "WebPolicy",
    "WebResult",
    "WebToolkit",
    "crawl_site",
    "fetch_parsed",
    "fetch_raw",
    "normalize_url",
    "parse_html",
    "render_js",
    "search_gather",
]
