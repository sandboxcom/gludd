"""
Shared SearXNG search client for general_ludd collections.

Extracts the generic HTTP search infrastructure from the travel collection's
searxng_search module so other collections can query SearXNG without
re-implementing URL building, HTTP transport, or result extraction.

Usage in a module
-----------------
    from ansible_collections.general_ludd.agent.plugins.module_utils.searxng import (
        SearXNGClient,
        SearxResponse,
        SearxResult,
        build_search_url,
        execute_search,
        extract_price,
        extract_stars,
        normalise_url,
    )

    client = SearXNGClient(base_url="http://localhost:8080")
    resp = client.search("flights to Paris", categories=["general"])
"""

from __future__ import annotations

import dataclasses as _dc
import json as _json
import re as _re
from typing import Any
from urllib import parse as _urlparse
from urllib import request as _urllib_request
from urllib.error import HTTPError as _HTTPError
from urllib.error import URLError as _URLError

_PRICE_RE = _re.compile(r"\$\s*(\d{1,6}(?:[.,]\d{1,2})?)")
_STAR_RE = _re.compile(r"(\d(?:[.,]\d)?)[\s/]*(?:star|⭐|out of 5)")

_VALID_CATEGORIES: set[str] = {
    "general",
    "news",
    "images",
    "videos",
    "music",
    "it",
    "science",
    "files",
    "social_media",
    "map",
}

DEFAULT_BASE_URL: str = "http://localhost:8080"
DEFAULT_TIMEOUT: int = 30
DEFAULT_RETRIES: int = 2


def normalise_url(base: str) -> str:
    """Strip trailing slashes and add ``http://`` prefix if missing."""
    url = base.rstrip("/")
    if not url.startswith(("http://", "https://")):
        url = f"http://{url}"
    return url


def build_search_url(
    searxng_url: str,
    query: str,
    category: str,
    engines: str = "",
    max_results: int = 10,
    safe_search: int = 0,
    language: str = "en",
) -> str:
    """Build a SearXNG JSON API search URL.

    Parameters
    ----------
    searxng_url:
        Base URL of the SearXNG instance.
    query:
        Free-text search query.
    category:
        Search category (``flights``, ``hotels``, ``general``, etc.).
        Controls which engines are used via ``engines_per_category``.
    engines:
        Comma-separated override list; when empty the category default is used.
    max_results:
        Result page size.
    safe_search:
        Safe search level (0, 1, 2).
    language:
        Language code for results.
    """
    params: dict[str, str] = {
        "q": query,
        "format": "json",
        "categories": "general",
        "engines": engines,
        "language": language,
        "safesearch": str(safe_search),
        "pageno": "1",
    }
    base = normalise_url(searxng_url)
    return f"{base}/search?{_urlparse.urlencode(params)}"


def engines_per_category(category: str) -> str:
    """Return default SearXNG engine list for a search category."""
    _ENGINE_MAP: dict[str, str] = {
        "flights": "google_flights,google_travel",
        "hotels": "booking,hotelscombined,tripadvisor",
        "events": "google_events,ticketmaster,eventbrite",
        "activities": "tripadvisor,wikivoyage,google_maps",
        "restaurants": "yelp,tripadvisor,google_maps",
        "general": "google,wikipedia,duckduckgo",
    }
    return _ENGINE_MAP.get(category, "google,wikipedia")


def execute_search(
    search_url: str,
    timeout: int = 10,
    user_agent: str = "gludd-searxng/1.0",
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], str]:
    """Execute a SearXNG JSON API search and return structured results.

    Parameters
    ----------
    search_url:
        Full search URL (as built by :func:`build_search_url`).
    timeout:
        HTTP request timeout in seconds.
    user_agent:
        User-Agent header value.

    Returns
    -------
    ``(structured_results, raw_results, search_url)``.
    On HTTP error, returns ``([], [], search_url)``.
    """
    req = _urllib_request.Request(
        search_url,
        headers={
            "User-Agent": user_agent,
            "Accept": "application/json",
        },
    )

    try:
        with _urllib_request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8")
    except (_HTTPError, _URLError):
        return [], [], search_url

    data = _json.loads(body)
    raw_results: list[dict[str, Any]] = data.get("results", [])
    return list(raw_results), list(raw_results), search_url


def extract_price(text: str) -> float | None:
    """Extract a USD price from text (e.g. ``$ 150.00`` → ``150.0``)."""
    match = _PRICE_RE.search(text)
    if match:
        value = match.group(1).replace(",", "")
        return float(value)
    return None


def extract_stars(text: str) -> float | None:
    """Extract a star rating from text (e.g. ``4.5 stars`` → ``4.5``)."""
    match = _STAR_RE.search(text)
    if match:
        return float(match.group(1))
    return None


# ---------------------------------------------------------------------------
# SearxResult — typed result model
# ---------------------------------------------------------------------------


@_dc.dataclass
class SearxResult:
    """A single search result from a SearXNG response."""

    url: str
    title: str = ""
    snippet: str = ""
    engine: str = ""
    score: float = 0.0
    category: str = ""
    img_src: str = ""
    thumbnail_src: str = ""
    published_date: str | None = None

    @classmethod
    def from_raw(cls, data: dict[str, Any]) -> SearxResult:
        """Build a SearxResult from a raw SearXNG result dict."""
        score = data.get("score", 0.0)
        try:
            score = float(score)
        except (TypeError, ValueError):
            score = 0.0

        published_date = data.get("publishedDate") or data.get("published_date")

        return cls(
            url=str(data.get("url", "")),
            title=str(data.get("title", "")),
            snippet=str(data.get("content", "")),
            engine=str(data.get("engine", "")),
            score=score,
            category=str(data.get("category", "")),
            img_src=str(data.get("img_src", "")),
            thumbnail_src=str(data.get("thumbnail_src", "")),
            published_date=published_date,
        )


# ---------------------------------------------------------------------------
# SearxResponse — aggregated search response
# ---------------------------------------------------------------------------


@_dc.dataclass
class SearxResponse:
    """Wraps raw SearXNG JSON results into typed result objects."""

    query: str
    results: list[SearxResult] = _dc.field(default_factory=list)
    number_of_results: int = 0
    suggestions: list[str] = _dc.field(default_factory=list)
    answers: list[str] = _dc.field(default_factory=list)
    unresponsive_engines: list[list[str]] = _dc.field(default_factory=list)

    @property
    def urls(self) -> list[str]:
        return [r.url for r in self.results if r.url]

    @property
    def titles(self) -> list[str]:
        return [r.title for r in self.results]

    @property
    def snippets(self) -> list[str]:
        return [r.snippet for r in self.results]

    @property
    def engines(self) -> list[str]:
        seen: set[str] = set()
        result: list[str] = []
        for r in self.results:
            if r.engine and r.engine not in seen:
                seen.add(r.engine)
                result.append(r.engine)
        return result


# ---------------------------------------------------------------------------
# SearXNGClient — OOP client
# ---------------------------------------------------------------------------


class SearXNGClient:
    """HTTP client for a SearXNG instance.

    Parameters
    ----------
    base_url:
        Base URL of the SearXNG instance. Defaults to ``http://localhost:8080``.
    timeout:
        HTTP request timeout in seconds (default 30).
    retries:
        Number of retry attempts on 5xx / transient errors (default 2).
    """

    def __init__(
        self,
        base_url: str = DEFAULT_BASE_URL,
        timeout: int = DEFAULT_TIMEOUT,
        retries: int = DEFAULT_RETRIES,
    ) -> None:
        self.base_url = normalise_url(base_url)
        self.timeout = timeout
        self.retries = retries
        self._indices: dict[str, list[str]] = {}

    # -- search ---------------------------------------------------------------

    def search(
        self,
        query: str,
        max_results: int = 10,
        categories: list[str] | None = None,
        engines: list[str] | None = None,
        language: str = "en",
        safe_search: int = 0,
        page: int = 1,
    ) -> SearxResponse:
        """Run a search query against SearXNG.

        Parameters
        ----------
        query:
            Free-text search query.
        max_results:
            Cap on returned results (applied after deduplication).
        categories:
            One or more search categories. Defaults to ``["general"]``.
        engines:
            Comma-separated engine list override. When ``None``, the
            category default is used.
        language:
            Language code (default ``"en"``).
        safe_search:
            Safe search level 0-2 (default 0).
        page:
            Results page number (default 1).

        Raises
        ------
        ValueError:
            If an unknown category is supplied.
        urllib.error.HTTPError, URLError:
            After retries are exhausted.
        """
        cats = categories or ["general"]
        for c in cats:
            if c not in _VALID_CATEGORIES:
                raise ValueError(f"Unknown category: {c}")
        cat_str = ",".join(cats)

        engines_str = ",".join(engines) if engines else ""

        params: dict[str, str] = {
            "q": query,
            "format": "json",
            "categories": cat_str,
            "engines": engines_str,
            "language": language,
            "safesearch": str(safe_search),
            "pageno": str(page),
        }
        url = f"{self.base_url}/search?{_urlparse.urlencode(params)}"

        data = _search_with_retries(url, self.timeout, self.retries)

        raw_results = data.get("results", [])
        parsed = [SearxResult.from_raw(r) for r in raw_results]
        parsed = _deduplicate(parsed)
        if max_results < len(parsed):
            parsed = parsed[:max_results]

        num_results = data.get("number_of_results", len(parsed))
        suggestions = data.get("suggestions", [])
        answers = data.get("answers", [])
        unresponsive = data.get("unresponsive_engines", [])

        return SearxResponse(
            query=query,
            results=parsed,
            number_of_results=num_results,
            suggestions=suggestions,
            answers=answers,
            unresponsive_engines=unresponsive,
        )

    def web_search(self, query: str, max_results: int = 10) -> SearxResponse:
        """Convenience search restricted to the ``general`` category."""
        return self.search(query, max_results=max_results, categories=["general"])

    def news_search(self, query: str, max_results: int = 10) -> SearxResponse:
        """Convenience search restricted to the ``news`` category."""
        return self.search(query, max_results=max_results, categories=["news"])

    def image_search(self, query: str, max_results: int = 10) -> SearxResponse:
        """Convenience search restricted to the ``images`` category."""
        return self.search(query, max_results=max_results, categories=["images"])

    # -- index management -----------------------------------------------------

    def create_index(self, name: str, engines: list[str]) -> dict[str, Any]:
        """Create a named engine index stored client-side."""
        if not name:
            raise ValueError("Index name must be non-empty")
        if not engines:
            raise ValueError("Index must contain at least one engine")
        self._indices[name] = list(engines)
        return {"name": name, "engines": engines, "created": True}

    def index_status(self, name: str) -> dict[str, Any]:
        """Check the health of a named engine index."""
        if name not in self._indices:
            return {"exists": False, "error": f"Index '{name}' not found"}

        engines = self._indices[name]
        engines_str = ",".join(engines)
        params: dict[str, str] = {
            "q": "health check",
            "format": "json",
            "categories": "general",
            "engines": engines_str,
            "language": "en",
            "safesearch": "0",
            "pageno": "1",
        }
        url = f"{self.base_url}/search?{_urlparse.urlencode(params)}"

        try:
            raw = _search_with_retries(url, self.timeout, retries=0)
        except (_URLError, _HTTPError) as exc:
            return {
                "exists": True,
                "name": name,
                "engines_total": len(engines),
                "healthy": False,
                "error": str(exc),
            }

        unresponsive_engines: list[list[str]] = raw.get("unresponsive_engines", [])
        unresponsive_names = [e[0] for e in unresponsive_engines if e]
        responsive = [e for e in engines if e not in set(unresponsive_names)]

        return {
            "exists": True,
            "name": name,
            "engines_total": len(engines),
            "engines_responsive": len(responsive),
            "engines_unresponsive": len(unresponsive_names),
            "responsive": responsive,
            "unresponsive": unresponsive_names,
            "healthy": len(responsive) >= 1,
        }

    def search_with_index(self, query: str, index_name: str, max_results: int = 10) -> SearxResponse:
        """Search using a previously-created engine index."""
        if index_name not in self._indices:
            raise ValueError(f"Index '{index_name}' not found")
        return self.search(
            query,
            max_results=max_results,
            engines=self._indices[index_name],
        )

    # -- health ---------------------------------------------------------------

    def health(self) -> dict[str, Any]:
        """Check if the SearXNG instance is reachable."""
        try:
            req = _urllib_request.Request(
                f"{self.base_url}/healthz",
                headers={
                    "User-Agent": "gludd-searxng/1.0",
                    "Accept": "text/plain",
                },
            )
            with _urllib_request.urlopen(req, timeout=5) as resp:
                resp.read()
            return {"ok": True, "detail": "SearXNG reachable", "base_url": self.base_url}
        except ConnectionError as exc:
            return {"ok": False, "detail": f"SearXNG unreachable: {exc}"}
        except (_URLError, _HTTPError, OSError) as exc:
            return {"ok": False, "detail": str(exc)}


# -- internal helpers ----------------------------------------------------------


def _search_with_retries(url: str, timeout: int, retries: int) -> dict[str, Any]:
    """Execute a SearXNG JSON API search with retry logic.

    Returns the full JSON response dict.
    4xx errors are NOT retried.  5xx, URLError, and OSError are.
    """
    last_exc: Exception | None = None
    for _attempt in range(retries + 1):
        try:
            req = _urllib_request.Request(
                url,
                headers={
                    "User-Agent": "gludd-searxng/1.0",
                    "Accept": "application/json",
                },
            )
            with _urllib_request.urlopen(req, timeout=timeout) as resp:
                body = resp.read().decode("utf-8")
            return _json.loads(body)
        except _HTTPError as exc:
            if exc.code is not None and 400 <= exc.code < 500:
                raise
            last_exc = exc
        except (_URLError, OSError) as exc:
            last_exc = exc

    if last_exc is not None:
        raise last_exc
    return {}


def _deduplicate(results: list[SearxResult]) -> list[SearxResult]:
    """Remove duplicate URLs, keeping the highest-score entry for each URL.

    Results are returned sorted by score descending.
    """
    seen: dict[str, SearxResult] = {}
    for r in results:
        if not r.url:
            continue
        if r.url not in seen or r.score > seen[r.url].score:
            seen[r.url] = r
    return sorted(seen.values(), key=lambda x: x.score, reverse=True)


def parse_urls(raw: list[dict[str, Any]]) -> list[str]:
    """Extract URLs from raw SearXNG result dicts."""
    return [r["url"] for r in raw if "url" in r]


def parse_titles(raw: list[dict[str, Any]]) -> list[str]:
    """Extract titles from raw SearXNG result dicts."""
    return [r["title"] for r in raw if "title" in r]


def parse_snippets(raw: list[dict[str, Any]]) -> list[str]:
    """Extract content/snippets from raw SearXNG result dicts."""
    return [r["content"] for r in raw if "content" in r]


def parse_engines(raw: list[dict[str, Any]]) -> list[str]:
    """Extract deduplicated, sorted engine names from raw result dicts."""
    engines = sorted({r["engine"] for r in raw if "engine" in r})
    return engines
