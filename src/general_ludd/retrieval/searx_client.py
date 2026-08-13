"""Async SearXNG JSON API client for agentic research."""

from __future__ import annotations

import asyncio
import logging
import math
import os
import time
from dataclasses import dataclass, field
from typing import Any, Literal

import httpx

from general_ludd.security.safe_diskcache import open_safe_diskcache

logger = logging.getLogger(__name__)

_CACHE_TTL_SECONDS = int(os.environ.get("GLUDD_SEARX_CACHE_TTL", "1800"))  # 30 min
_RATE_LIMIT_MIN_INTERVAL = float(os.environ.get("GLUDD_SEARX_RATE_LIMIT", "2.0"))  # seconds
_SEARXNG_BASE_URL = os.environ.get("GLUDD_SEARXNG_URL", "http://localhost:8080")
_SEARCH_TIMEOUT = float(os.environ.get("GLUDD_SEARX_TIMEOUT", "30.0"))

SearxCategory = Literal[
    "general",
    "science",
    "it",
    "news",
    "files",
    "images",
    "videos",
    "map",
    "music",
    "social media",
    "packages",
]

SearxTimeRange = Literal["day", "week", "month", "year"]
SearxSafeSearch = Literal[0, 1, 2]
SearxFormat = Literal["json", "html", "csv", "rss"]

_DEFAULT_CATEGORIES: list[SearxCategory] = ["science", "it", "general"]
_ENGINE_TIMEOUT = 10.0


@dataclass
class SearxResult:
    url: str
    title: str = ""
    content: str = ""
    engine: str = ""
    score: float = 0.0
    category: str = ""
    parsed_url: list[str] = field(default_factory=list)
    template: str = ""
    img_src: str = ""
    thumbnail_src: str = ""
    published_date: str | None = None


@dataclass
class SearxResponse:
    query: str
    results: list[SearxResult]
    number_of_results: int = 0
    suggestions: list[str] = field(default_factory=list)
    answers: list[str] = field(default_factory=list)
    corrections: list[str] = field(default_factory=list)
    infoboxes: list[dict[str, Any]] = field(default_factory=list)
    unresponsive_engines: list[list[str]] = field(default_factory=list)
    elapsed_ms: float = 0.0


def _cache_key(
    query: str,
    categories: tuple[SearxCategory, ...],
    time_range: str | None,
    page: int,
    language: str,
) -> str:
    return f"searx:{query}:{','.join(sorted(categories))}:{time_range}:{page}:{language}"


def _raw_result_to_searx(raw: dict[str, Any]) -> SearxResult:
    return SearxResult(
        url=str(raw.get("url", "")),
        title=str(raw.get("title", "")),
        content=str(raw.get("content", "")),
        engine=str(raw.get("engine", "")),
        score=float(raw.get("score", 0.0)),
        category=str(raw.get("category", "")),
        parsed_url=raw.get("parsed_url", []) or [],
        template=str(raw.get("template", "")),
        img_src=str(raw.get("img_src", "")),
        thumbnail_src=str(raw.get("thumbnail_src", "")),
        published_date=raw.get("publishedDate") or raw.get("published_date"),
    )


class SearxNGClient:
    """Async client wrapping the SearXNG JSON API with caching and rate limiting.

    Configurable via environment variables:
        GLUDD_SEARXNG_URL — base URL (default http://localhost:8080)
        GLUDD_SEARX_CACHE_TTL — result cache TTL in seconds (default 1800)
        GLUDD_SEARX_RATE_LIMIT — minimum seconds between requests (default 2.0)
        GLUDD_SEARX_TIMEOUT — HTTP request timeout in seconds (default 30.0)
    """

    def __init__(
        self,
        *,
        base_url: str | None = None,
        cache_dir: str | None = None,
    ) -> None:
        self._base_url = (base_url or _SEARXNG_BASE_URL).rstrip("/")
        cache_path = os.path.expanduser(os.path.expandvars(
            str(cache_dir or ".gludd/searx_cache")
        ))
        self._cache = open_safe_diskcache(cache_path)
        self._last_request_time: float = 0.0
        self._client: httpx.AsyncClient | None = None

    @staticmethod
    def base_url() -> str:
        return _SEARXNG_BASE_URL

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(_SEARCH_TIMEOUT, connect=10.0),
                limits=httpx.Limits(max_connections=5, max_keepalive_connections=2),
            )
        return self._client

    async def close(self) -> None:
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()
        self._cache.close()

    async def _rate_limit(self) -> None:
        elapsed = time.monotonic() - self._last_request_time
        if elapsed < _RATE_LIMIT_MIN_INTERVAL:
            wait = _RATE_LIMIT_MIN_INTERVAL - elapsed
            logger.debug("rate limit: waiting %.2fs", wait)
            await asyncio.sleep(wait)
        self._last_request_time = time.monotonic()

    async def search(
        self,
        query: str,
        *,
        categories: list[SearxCategory] | None = None,
        engines: list[str] | None = None,
        language: str = "en",
        time_range: SearxTimeRange | None = None,
        safe_search: SearxSafeSearch = 0,
        page: int = 1,
        bypass_cache: bool = False,
    ) -> SearxResponse:
        """Execute a search query against SearXNG.

        Args:
            query: The search query string.
            categories: Engine categories to search. Defaults to science, it, general.
            engines: Specific engines to use (overrides categories).
            language: Language code (default 'en').
            time_range: Time filter: day, week, month, year.
            safe_search: 0=off, 1=moderate, 2=strict.
            page: Page number (1-indexed).
            bypass_cache: If True, skip the disk cache.

        Returns:
            SearxResponse with results, metadata, and engine status.

        Raises:
            httpx.HTTPError: On network or HTTP errors.
        """
        selected_categories = categories or list(_DEFAULT_CATEGORIES)
        cat_tuple = tuple(selected_categories)
        key = _cache_key(query, cat_tuple, time_range, page, language)

        if not bypass_cache:
            with self._cache as cache:
                cached = cache.get(key)
                if isinstance(cached, dict) and all(
                    isinstance(cache_key, str) for cache_key in cached
                ):
                    logger.debug("cache hit for query %r", query)
                    return SearxResponse(**cached)

        await self._rate_limit()

        params: dict[str, Any] = {
            "q": query,
            "format": "json",
            "pageno": page,
            "language": language,
            "safesearch": safe_search,
        }
        if engines:
            params["engines"] = ",".join(engines)
        else:
            params["categories"] = ",".join(selected_categories)

        if time_range:
            params["time_range"] = time_range

        client = await self._get_client()
        start = time.monotonic()

        try:
            resp = await client.get(
                f"{self._base_url}/search",
                params=params,
                headers={"Accept": "application/json"},
            )
            resp.raise_for_status()
            elapsed = (time.monotonic() - start) * 1000
        except httpx.TimeoutException:
            logger.error("SearXNG request timed out for query %r", query)
            raise
        except httpx.HTTPStatusError as exc:
            logger.error(
                "SearXNG returned HTTP %d for query %r: %s",
                exc.response.status_code,
                query,
                exc.response.text[:200],
            )
            raise

        raw = resp.json()
        results = [
            _raw_result_to_searx(r)
            for r in raw.get("results", [])
        ]

        deduped = _deduplicate(results)

        response = SearxResponse(
            query=raw.get("query", query),
            results=deduped,
            number_of_results=raw.get("number_of_results", 0),
            suggestions=raw.get("suggestions", []) or [],
            answers=raw.get("answers", []) or [],
            corrections=raw.get("corrections", []) or [],
            infoboxes=raw.get("infoboxes", []) or [],
            unresponsive_engines=raw.get("unresponsive_engines", []) or [],
            elapsed_ms=math.ceil(elapsed),
        )

        self._cache.set(
            key,
            {
                "query": response.query,
                "results": [
                    {
                        "url": r.url,
                        "title": r.title,
                        "content": r.content,
                        "engine": r.engine,
                        "score": r.score,
                        "category": r.category,
                        "parsed_url": r.parsed_url,
                        "template": r.template,
                        "img_src": r.img_src,
                        "thumbnail_src": r.thumbnail_src,
                        "published_date": r.published_date,
                    }
                    for r in response.results
                ],
                "number_of_results": response.number_of_results,
                "suggestions": response.suggestions,
                "answers": response.answers,
                "corrections": response.corrections,
                "infoboxes": response.infoboxes,
                "unresponsive_engines": response.unresponsive_engines,
                "elapsed_ms": response.elapsed_ms,
            },
            expire=_CACHE_TTL_SECONDS,
        )

        logger.info(
            "search %r returned %d results in %.0fms (engines unresponsive: %d)",
            query,
            len(response.results),
            elapsed,
            len(response.unresponsive_engines),
        )
        return response

    async def health(self) -> dict[str, Any]:
        """Health check: query SearXNG with a minimal test search."""
        try:
            client = await self._get_client()
            resp = await client.get(
                f"{self._base_url}/search",
                params={"q": "test", "format": "json", "pageno": 1},
                timeout=httpx.Timeout(10.0),
            )
            if resp.status_code == 200:
                data = resp.json()
                return {
                    "ok": True,
                    "detail": f"SearXNG reachable, engines: {len(data.get('results', []))} results",
                    "base_url": self._base_url,
                }
            return {"ok": False, "detail": f"HTTP {resp.status_code}"}
        except Exception as exc:
            return {"ok": False, "detail": f"SearXNG unreachable: {type(exc).__name__}"}

    async def multi_search(
        self,
        query: str,
        *,
        time_ranges: list[SearxTimeRange | None] | None = None,
        categories: list[SearxCategory] | None = None,
        **kwargs: Any,
    ) -> list[SearxResponse]:
        """Run the same query across multiple time ranges in parallel.

        Useful for agents that need both recent and historical context.
        """
        from asyncio import gather

        ranges: list[SearxTimeRange | None] = time_ranges or [None, "year", "month"]
        tasks = [
            self.search(query, time_range=tr, categories=categories, **kwargs)
            for tr in ranges
        ]
        return list(await gather(*tasks))


def _deduplicate(results: list[SearxResult]) -> list[SearxResult]:
    """Remove duplicate results by URL, keeping the highest-scoring instance."""
    seen: dict[str, SearxResult] = {}
    for r in results:
        url = r.url.strip().lower()
        if not url:
            continue
        if url not in seen or r.score > seen[url].score:
            seen[url] = r
    deduped = list(seen.values())
    deduped.sort(key=lambda r: r.score, reverse=True)
    return deduped
