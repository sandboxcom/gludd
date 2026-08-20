"""
SearXNG search client for general_ludd collections.

Thin Ansible-compatible wrapper using the collection's shared stdlib HTTP
transport. SearXNG is an operator-configured external service, not a reason to
import the Gludd source checkout into the controller process.

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
    )

    client = SearXNGClient(base_url="http://localhost:8080")
    resp = client.search("flights to Paris", categories=["general"])
"""

from __future__ import annotations

import dataclasses as _dc
import re as _re
from typing import Any
from urllib.parse import parse_qs as _parse_qs
from urllib.parse import urlencode as _urlencode
from urllib.parse import urlparse as _urlparse

from ansible_collections.general_ludd.agent.plugins.module_utils.gludd import GluddClient

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

_ENGINE_MAP: dict[str, str] = {
    "flights": "google_flights,google_travel",
    "hotels": "booking,hotelscombined,tripadvisor",
    "events": "google_events,ticketmaster,eventbrite",
    "activities": "tripadvisor,wikivoyage,google_maps",
    "restaurants": "yelp,tripadvisor,google_maps",
    "general": "google,wikipedia,duckduckgo",
}

DEFAULT_BASE_URL: str = "http://localhost:8080"
DEFAULT_TIMEOUT: int = 30
DEFAULT_RETRIES: int = 2


# ============================================================================
# Standalone utilities — pure functions, zero HTTP
# ============================================================================


def normalise_url(base: str) -> str:
    """Strip trailing slashes and add ``http://`` prefix if missing."""
    url = base.rstrip("/")
    if not url.startswith(("http://", "https://")):
        url = f"http://{url}"
    return url


def engines_per_category(category: str) -> str:
    """Return default SearXNG engine list for a search category."""
    return _ENGINE_MAP.get(category, "google,wikipedia")


def extract_price(text: str) -> float | None:
    """Extract a USD price from text (e.g. ``$ 150.00`` -> ``150.0``)."""
    match = _PRICE_RE.search(text)
    if match:
        value = match.group(1).replace(",", "")
        return float(value)
    return None


def extract_stars(text: str) -> float | None:
    """Extract a star rating from text (e.g. ``4.5 stars`` -> ``4.5``)."""
    match = _STAR_RE.search(text)
    if match:
        return float(match.group(1))
    return None


# ============================================================================
# Connector helper
# ============================================================================


def _connector(base_url: str, timeout: float) -> GluddClient:
    """Build the shared stdlib transport for an operator-selected instance."""
    return GluddClient(base_url=normalise_url(base_url), timeout=int(timeout))


# ============================================================================
# URL building  (kept for consumers that need the URL string separately)
# ============================================================================


def build_search_url(
    searxng_url: str,
    query: str,
    category: str,  # kept for backwards-compatible signature
    engines: str = "",
    max_results: int = 10,  # URL carries no per-result cap
    safe_search: int = 0,
    language: str = "en",
) -> str:
    """Build a SearXNG JSON API search URL.

    .. note::

        This function is retained so callers can inspect the composed URL.
        Actual HTTP execution is delegated to
        :class:`~general_ludd.connectors.searx.SearXConnector` via
        :func:`execute_search`.
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
    return f"{base}/search?{_urlencode(params)}"


# ============================================================================
# execute_search — delegates HTTP to SearXConnector
# ============================================================================


def execute_search(
    search_url: str,
    timeout: int = 10,
    user_agent: str = "gludd-searxng/1.0",  # delegate handles headers
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], str]:
    """Execute a SearXNG JSON API search.

    **Delegates to SearXConnector** for SSRF-guarded HTTP transport,
    TLS verification, and JSON deserialisation.

    Returns ``(structured_results, raw_results, search_url)``.
    On error returns ``([], [], search_url)``.
    """
    try:
        parsed = _urlparse(search_url)
        base_url = f"{parsed.scheme}://{parsed.netloc}"
        flat_params: dict[str, str | int] = {}
        for key, vals in _parse_qs(parsed.query).items():
            flat_params[key] = vals[0] if len(vals) == 1 else ",".join(vals)
    except Exception:
        return [], [], search_url

    try:
        conn = _connector(base_url, float(timeout))
        body = conn.get("/search", params=flat_params)
        status = int(body.get("_status", 0))
    except Exception:
        return [], [], search_url

    if not (200 <= status < 300) or not isinstance(body, dict):
        return [], [], search_url

    raw_results: list[dict[str, Any]] = list(body.get("results", []))
    return raw_results, raw_results, search_url


# ============================================================================
# SearxResult — typed result model
# ============================================================================


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


# ============================================================================
# SearxResponse — aggregated search response
# ============================================================================


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


# ============================================================================
# SearXNGClient — thin OOP wrapper around SearXConnector
# ============================================================================


def _deduplicate(results: list[SearxResult]) -> list[SearxResult]:
    """Remove duplicate URLs, keeping the highest-score entry for each URL."""
    seen: dict[str, SearxResult] = {}
    for r in results:
        if not r.url:
            continue
        if r.url not in seen or r.score > seen[r.url].score:
            seen[r.url] = r
    return sorted(seen.values(), key=lambda x: x.score, reverse=True)


class SearXNGClient:
    """HTTP client for a SearXNG instance — thin wrapper around SearXConnector.

    All HTTP transport, URL building, SSRF protection, TLS verification,
    and JSON parsing are delegated to the :class:`~general_ludd.connectors.searx.SearXConnector`
    held in ``self._connector``.
    """

    def __init__(
        self,
        base_url: str = DEFAULT_BASE_URL,
        timeout: int = DEFAULT_TIMEOUT,
        retries: int = DEFAULT_RETRIES,  # connector handles transport
    ) -> None:
        self.base_url = normalise_url(base_url)
        self.timeout = timeout
        self.retries = retries
        self._connector = _connector(base_url, float(timeout))
        self._indices: dict[str, list[str]] = {}

    # -- search (delegates HTTP to SearXConnector) ----------------------------

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
        """Run a search query — delegates HTTP to SearXConnector.

        Deduplication and max_results capping are applied locally on the
        typed results.
        """
        cats = categories or ["general"]
        for c in cats:
            if c not in _VALID_CATEGORIES:
                raise ValueError(f"Unknown category: {c}")

        flat_params: dict[str, str | int] = {
            "q": query,
            "format": "json",
            "categories": ",".join(cats),
            "engines": ",".join(engines) if engines else "",
            "language": language,
            "safesearch": str(safe_search),
            "pageno": str(page),
        }

        body = self._connector.get("/search", params=flat_params)
        status = int(body.get("_status", 0))

        if not (200 <= status < 300) or not isinstance(body, dict):
            return SearxResponse(query=query)

        raw_results = body.get("results", [])
        parsed = [SearxResult.from_raw(r) for r in raw_results]
        parsed = _deduplicate(parsed)
        if max_results < len(parsed):
            parsed = parsed[:max_results]

        return SearxResponse(
            query=query,
            results=parsed,
            number_of_results=body.get("number_of_results", len(parsed)),
            suggestions=body.get("suggestions", []),
            answers=body.get("answers", []),
            unresponsive_engines=body.get("unresponsive_engines", []),
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

    # -- index management (in-memory bookkeeping, HTTP delegated) ------------

    def create_index(self, name: str, engines: list[str]) -> dict[str, Any]:
        """Create a named engine index stored client-side."""
        if not name:
            raise ValueError("Index name must be non-empty")
        if not engines:
            raise ValueError("Index must contain at least one engine")
        self._indices[name] = list(engines)
        return {"name": name, "engines": engines, "created": True}

    def index_status(self, name: str) -> dict[str, Any]:
        """Check the health of a named engine index — HTTP delegates to SearXConnector."""
        if name not in self._indices:
            return {"exists": False, "error": f"Index '{name}' not found"}

        engines = self._indices[name]
        params: dict[str, str | int] = {
            "q": "health check",
            "format": "json",
            "categories": "general",
            "engines": ",".join(engines),
            "language": "en",
            "safesearch": "0",
            "pageno": "1",
        }

        try:
            raw = self._connector.get("/search", params=params)
            status = int(raw.get("_status", 0))
        except Exception as exc:
            return {
                "exists": True,
                "name": name,
                "engines_total": len(engines),
                "healthy": False,
                "error": str(exc),
            }

        if not (200 <= status < 300) or not isinstance(raw, dict):
            return {
                "exists": True,
                "name": name,
                "engines_total": len(engines),
                "healthy": False,
                "error": f"HTTP {status}",
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

    # -- health (delegates to SearXConnector) --------------------------------

    def health(self) -> dict[str, Any]:
        """Check if the SearXNG instance is reachable — delegates to SearXConnector."""
        try:
            result = self._connector.get("/healthz")
            ok = result.get("_status") == 200
            if ok:
                return {
                    "ok": True,
                    "detail": "SearXNG reachable",
                    "base_url": self.base_url,
                }
            return {"ok": False, "detail": f"HTTP error: {result.get('error', 'unknown')}"}
        except Exception as exc:
            return {"ok": False, "detail": str(exc)}


# ============================================================================
# Module-level raw-result parsers (kept for backwards compatibility)
# ============================================================================


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
