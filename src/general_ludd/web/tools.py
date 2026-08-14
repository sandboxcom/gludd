"""Flat, injectable functions and self-description for model-facing web tools."""

from __future__ import annotations

from collections.abc import Mapping

from general_ludd.web.policy import DEFAULT_POLICY, WebPolicy
from general_ludd.web.toolkit import Fetcher, OfflineRenderer, SearchProvider, WebToolkit
from general_ludd.web.types import WebResult

TOOL_SPECS: dict[str, dict[str, object]] = {
    "web_fetch": {
        "description": "Fetch one HTTPS resource through the DNS-pinned bounded client.",
        "params": {"url": "str", "method": "GET|HEAD"},
    },
    "web_fetch_parsed": {
        "description": "Fetch and extract bounded visible text, metadata, and links.",
        "params": {"url": "str"},
    },
    "web_search": {
        "description": "Search through an operator provider and gather bounded partial pages.",
        "params": {"query": "str", "top_n": "int"},
    },
    "web_crawl": {
        "description": "Run a robots-aware same-host breadth-first crawl under hard limits.",
        "params": {"seed_url": "str", "max_pages": "int?", "max_depth": "int?"},
    },
    "web_render": {
        "description": "Process securely prefetched HTML through an optional offline renderer.",
        "params": {"url": "str"},
    },
}


def _toolkit(
    toolkit: WebToolkit | None,
    *,
    policy: WebPolicy,
    fetcher: Fetcher | None,
    search_provider: SearchProvider | None = None,
    renderer: OfflineRenderer | None = None,
) -> WebToolkit:
    if toolkit is not None:
        return toolkit
    return WebToolkit(
        policy=policy,
        fetcher=fetcher,
        search_provider=search_provider,
        renderer=renderer,
    )


def fetch_raw(
    url: str,
    *,
    method: str = "GET",
    headers: Mapping[str, str] | None = None,
    policy: WebPolicy = DEFAULT_POLICY,
    fetcher: Fetcher | None = None,
    toolkit: WebToolkit | None = None,
) -> WebResult:
    """Fetch one resource through an injected or default hardened toolkit."""
    return _toolkit(toolkit, policy=policy, fetcher=fetcher).fetch_raw(
        url,
        method=method,
        headers=headers,
    )


def fetch_parsed(
    url: str,
    *,
    policy: WebPolicy = DEFAULT_POLICY,
    fetcher: Fetcher | None = None,
    toolkit: WebToolkit | None = None,
) -> WebResult:
    """Fetch and parse one resource without adding another network path."""
    return _toolkit(toolkit, policy=policy, fetcher=fetcher).fetch_parsed(url)


def search_gather(
    query: str,
    *,
    top_n: int = 5,
    fetch_results: bool = True,
    provider: SearchProvider | None = None,
    policy: WebPolicy = DEFAULT_POLICY,
    fetcher: Fetcher | None = None,
    toolkit: WebToolkit | None = None,
) -> WebResult:
    """Gather a bounded provider result set with per-hit partial failures."""
    return _toolkit(
        toolkit,
        policy=policy,
        fetcher=fetcher,
        search_provider=provider,
    ).search_gather(query, top_n=top_n, fetch_results=fetch_results)


def crawl_site(
    seed_url: str,
    *,
    max_pages: int | None = None,
    max_depth: int | None = None,
    policy: WebPolicy = DEFAULT_POLICY,
    fetcher: Fetcher | None = None,
    toolkit: WebToolkit | None = None,
) -> WebResult:
    """Crawl one host sequentially within robots, time, and memory limits."""
    return _toolkit(toolkit, policy=policy, fetcher=fetcher).crawl_site(
        seed_url,
        max_pages=max_pages,
        max_depth=max_depth,
    )


def render_js(
    url: str,
    *,
    policy: WebPolicy = DEFAULT_POLICY,
    fetcher: Fetcher | None = None,
    renderer: OfflineRenderer | None = None,
    toolkit: WebToolkit | None = None,
) -> WebResult:
    """Render a securely prefetched document without browser navigation."""
    return _toolkit(
        toolkit,
        policy=policy,
        fetcher=fetcher,
        renderer=renderer,
    ).render_js(url)


__all__ = [
    "TOOL_SPECS",
    "crawl_site",
    "fetch_parsed",
    "fetch_raw",
    "render_js",
    "search_gather",
]
