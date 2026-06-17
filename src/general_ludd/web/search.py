"""search_gather — pluggable provider search + multi-page gather.

No search SDK is bundled.  An operator injects a :class:`SearchProvider` (e.g. a
licensed search API client); the default :class:`NullProvider` returns a
structured ``NO_PROVIDER`` error.  Each result URL is fetched THROUGH the SSRF
client, so every gathered page inherits the SSRF guard + timeout + breaker.
Partial success is allowed: a failed page is recorded with its structured error
and never aborts the gather.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

import httpx

from general_ludd.web.breaker import HostCircuitBreaker
from general_ludd.web.parse import fetch_parsed
from general_ludd.web.results import SearchHit, SearchResult, WebError
from general_ludd.web.ssrf_client import SsrfSafeClient


@runtime_checkable
class SearchProvider(Protocol):
    """An operator-injected search backend."""

    def search(self, query: str, *, top_n: int) -> list[SearchHit]:
        ...


def _err_str(error: object) -> str:
    """Render a WebError (enum or its dumped string value) as a stable string."""
    if error is None:
        return "error"
    return getattr(error, "value", str(error))


class NullProvider:
    """Default provider: no backend configured."""

    def search(self, query: str, *, top_n: int) -> list[SearchHit]:
        return []


def search_gather(
    query: str,
    *,
    provider: SearchProvider | None = None,
    top_n: int = 5,
    fetch_results: bool = True,
    transport: httpx.BaseTransport | None = None,
    client: SsrfSafeClient | None = None,
    breaker: HostCircuitBreaker | None = None,
) -> SearchResult:
    """Run a provider search and (optionally) gather each result page.

    Returns a :class:`SearchResult`; ``ok`` is True when at least one page was
    gathered (or, when ``fetch_results=False``, when at least one hit was
    returned).  Never raises.
    """
    prov = provider if provider is not None else NullProvider()
    if isinstance(prov, NullProvider):
        return SearchResult(
            ok=False, error=WebError.NO_PROVIDER, query=query,
            detail="no SearchProvider configured",
        )

    try:
        raw_hits = prov.search(query, top_n=top_n) or []
    except Exception as exc:  # a provider must never crash the gather
        return SearchResult(
            ok=False, error=WebError.NO_PROVIDER, query=query,
            detail=f"provider raised: {type(exc).__name__}",
        )

    hits = [h for h in raw_hits if isinstance(h, SearchHit)][:top_n]
    if not hits:
        return SearchResult(ok=False, error=WebError.NO_PROVIDER, query=query,
                            detail="provider returned no hits")

    if not fetch_results:
        return SearchResult(ok=True, query=query, hits=hits, gathered=0)

    # Share ONE SsrfSafeClient (one breaker + pool) across the gather.
    cli = client or SsrfSafeClient(transport=transport, breaker=breaker)

    pages = []
    errors: list[str] = []
    gathered = 0
    failed = 0
    for hit in hits:
        page = fetch_parsed(hit.url, client=cli)
        pages.append(page)
        if page.ok:
            gathered += 1
        else:
            failed += 1
            errors.append(f"{hit.url}: {_err_str(page.error)}")

    return SearchResult(
        ok=gathered >= 1,
        error=None if gathered >= 1 else WebError.OFFLINE,
        query=query,
        hits=hits,
        pages=pages,
        gathered=gathered,
        failed=failed,
        errors=errors,
    )
