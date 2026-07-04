"""G12 Live web retrieval MCP tool."""

from __future__ import annotations

import logging
import os
import re
import urllib.request
from dataclasses import dataclass
from urllib.parse import urlparse

import diskcache

logger = logging.getLogger(__name__)

_MAX_CONTENT_BYTES = 1 * 1024 * 1024  # 1 MB default cap
_CACHE_TTL_SECONDS = 3600  # 1 hour

_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)
_TITLE_STRIP_RE = re.compile(r"<[^>]+>")


@dataclass
class WebPageResult:
    """Result of a web page fetch."""

    url: str
    status_code: int
    content: str
    title: str | None = None
    headers: dict[str, str] | None = None


def _extract_title(html: str) -> str | None:
    m = _TITLE_RE.search(html)
    if not m:
        return None
    raw = m.group(1).strip()
    return _TITLE_STRIP_RE.sub("", raw).strip() or None


def _normalise_domain(url: str) -> str:
    return urlparse(url).hostname or ""


class WebRetriever:
    """Fetches and processes live web pages for retrieval-augmented generation."""

    def __init__(self, *, timeout_seconds: int = 30) -> None:
        self._timeout = timeout_seconds
        self._cache = diskcache.Cache("web_retriever")

    @staticmethod
    def allowed_domains() -> list[str]:
        raw = os.environ.get("GLUDD_WEB_FETCH_ALLOWED_DOMAINS", "")
        return [d.strip() for d in raw.split(",") if d.strip()]

    def fetch_web_page(self, url: str) -> WebPageResult:
        """Fetch a web page and return its content.

        Args:
            url: The URL of the web page to fetch.

        Returns:
            A WebPageResult containing the page content and metadata.

        Raises:
            ValueError: If the domain is not in the allowlist.
        """
        allowed = self.allowed_domains()
        if allowed:
            domain = _normalise_domain(url)
            if domain not in allowed:
                raise ValueError(
                    f"Domain '{domain}' is not in the web fetch allowlist. "
                    f"Allowed domains: {allowed}"
                )

        with self._cache as cache:
            cached = cache.get(url)
            if cached is not None:
                logger.debug("cache hit for %s", url)
                return WebPageResult(**cached)

            req = urllib.request.Request(
                url,
                headers={"User-Agent": "gludd-web-retriever/1.0", "Accept": "text/html"},
            )
            try:
                resp = urllib.request.urlopen(req, timeout=self._timeout)
            except urllib.error.HTTPError as exc:
                logger.warning("HTTP %s fetching %s", exc.code, url)
                return WebPageResult(
                    url=url,
                    status_code=exc.code,
                    content="",
                    title=None,
                    headers=dict(exc.headers.items()),
                )
            except Exception as exc:
                logger.error("Failed to fetch %s: %s", url, exc)
                return WebPageResult(
                    url=url,
                    status_code=-1,
                    content=f"Fetch error: {exc}",
                    title=None,
                    headers=None,
                )

            status_code = resp.status
            headers = {k.lower(): v for k, v in dict(resp.headers).items()}

            content_bytes = resp.read(_MAX_CONTENT_BYTES + 1)
            if len(content_bytes) > _MAX_CONTENT_BYTES:
                content_bytes = content_bytes[:_MAX_CONTENT_BYTES]
            content = content_bytes.decode("utf-8", errors="replace")

            title = _extract_title(content)
            result = WebPageResult(
                url=url,
                status_code=status_code,
                content=content,
                title=title,
                headers=headers,
            )

            cache.set(
                url,
                {
                    "url": result.url,
                    "status_code": result.status_code,
                    "content": result.content,
                    "title": result.title,
                    "headers": result.headers,
                },
                expire=_CACHE_TTL_SECONDS,
            )
            return result
