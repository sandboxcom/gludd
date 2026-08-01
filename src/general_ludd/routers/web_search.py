"""GET /admin/web/search?q=... — live web search backed by WebRetriever."""

from __future__ import annotations

import threading
import time
from collections import deque

from fastapi import FastAPI, HTTPException, Query

from general_ludd.security.url_fetch import FetchPolicy, secure_fetch

_DEFAULT_MAX_REQUESTS = 10
_DEFAULT_WINDOW_SECONDS = 60.0


class SlidingWindowRateLimiter:
    """Simple sliding-window rate limiter using a deque of timestamps."""

    def __init__(self, max_requests: int, window_seconds: float) -> None:
        self._max_requests = max_requests
        self._window = window_seconds
        self._timestamps: deque[float] = deque()
        self._lock = threading.Lock()

    def allow(self) -> bool:
        now = time.monotonic()
        with self._lock:
            cutoff = now - self._window
            while self._timestamps and self._timestamps[0] <= cutoff:
                self._timestamps.popleft()
            if len(self._timestamps) < self._max_requests:
                self._timestamps.append(now)
                return True
            return False


_RATE_LIMITER = SlidingWindowRateLimiter(
    max_requests=_DEFAULT_MAX_REQUESTS,
    window_seconds=_DEFAULT_WINDOW_SECONDS,
)


def _web_search(query: str) -> list[dict[str, str]]:
    """Perform a web search via DuckDuckGo's HTML interface.

    Returns a list of result dicts with 'title', 'url', and 'snippet' keys.
    Falls back to an empty list when the search engine is unreachable.
    """
    import re
    import urllib.parse

    encoded_q = urllib.parse.quote_plus(query)
    url = f"https://html.duckduckgo.com/html/?q={encoded_q}"

    try:
        response = secure_fetch(
            url,
            headers={"User-Agent": "gludd-web-retriever/1.0"},
            policy=FetchPolicy(
                allowed_hosts=frozenset({"html.duckduckgo.com"}),
                max_bytes=1024 * 1024,
                timeout_seconds=15,
                max_redirects=2,
            ),
        )
    except Exception:
        return []

    html = response.content.decode("utf-8", errors="replace")

    results: list[dict[str, str]] = []
    # Extract result blocks: each <a class="result__snippet">...<a class="result__url">...
    # Simplified extraction using regex for the HTML structure
    snippet_re = re.compile(
        r'<a[^>]*class="result__snippet"[^>]*>((?:(?!</a>).)*)</a>',
        re.DOTALL | re.IGNORECASE,
    )
    url_re = re.compile(
        r'<a[^>]*class="result__url"[^>]*>(.*?)</a>',
        re.DOTALL | re.IGNORECASE,
    )
    title_re = re.compile(
        r'<a[^>]*class="result__a"[^>]*>(.*?)</a>',
        re.DOTALL | re.IGNORECASE,
    )

    snippets = snippet_re.findall(html)
    urls = url_re.findall(html)
    titles = title_re.findall(html)

    _TAG_RE = re.compile(r"<[^>]+>")
    for i in range(min(len(titles), len(urls))):
        title = _TAG_RE.sub("", titles[i]).strip()
        url_str = urls[i].strip()
        if url_str and not url_str.startswith("http"):
            url_str = "https:" + url_str if url_str.startswith("//") else url_str
        snippet = ""
        if i < len(snippets):
            snippet = _TAG_RE.sub("", snippets[i]).strip()
        if title and url_str:
            results.append({"title": title, "url": url_str, "snippet": snippet})

    return results


def register(app: FastAPI, _daemon_state: dict[str, object]) -> None:

    @app.get("/admin/web/search")
    async def admin_web_search(
        q: str = Query(..., min_length=1, max_length=512),
    ) -> dict[str, object]:
        if not _RATE_LIMITER.allow():
            raise HTTPException(
                status_code=429,
                detail="Rate limit exceeded — max 10 requests per minute",
            )

        import asyncio
        results = await asyncio.to_thread(_web_search, q)
        return {"query": q, "results": results}
