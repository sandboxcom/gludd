"""
Shared SearXNG search client for general_ludd collections.

Extracts the generic HTTP search infrastructure from the travel collection's
searxng_search module so other collections can query SearXNG without
re-implementing URL building, HTTP transport, or result extraction.

Usage in a module
-----------------
    from ansible_collections.general_ludd.agent.plugins.module_utils.searxng import (
        build_search_url,
        execute_search,
        extract_price,
        extract_stars,
        normalise_url,
    )

    url = build_search_url("http://localhost:8080", "flights to Paris", "flights")
    results, raw, search_url = execute_search(url, timeout=10)
"""

from __future__ import annotations

import json as _json
import re as _re
from typing import Any
from urllib import parse as _urlparse
from urllib import request as _urllib_request
from urllib.error import HTTPError as _HTTPError
from urllib.error import URLError as _URLError

_PRICE_RE = _re.compile(r"\$\s*(\d{1,6}(?:[.,]\d{1,2})?)")
_STAR_RE = _re.compile(r"(\d(?:[.,]\d)?)[\s/]*(?:star|⭐|out of 5)")


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
