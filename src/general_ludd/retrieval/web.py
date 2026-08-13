"""G12 Live web retrieval MCP tool."""

from __future__ import annotations

import logging
import os
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from email.message import Message
from typing import IO
from urllib.parse import urlparse

from general_ludd.security.safe_diskcache import open_safe_diskcache
from general_ludd.security.ssrf import is_url_blocked

logger = logging.getLogger(__name__)

_MAX_CONTENT_BYTES = 1 * 1024 * 1024  # 1 MB default cap
_CACHE_TTL_SECONDS = 3600  # 1 hour

_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)
_TITLE_STRIP_RE = re.compile(r"<[^>]+>")


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Opener handler that refuses to follow any HTTP redirect.

    ``fetch_web_page``'s ``is_url_blocked`` guard only inspects the ORIGINAL
    url, before any network I/O. ``urllib.request``'s default opener
    auto-follows 3xx redirects, so a public URL that later 302s to a
    loopback / link-local / RFC-1918 / cloud-metadata host (e.g.
    ``http://169.254.169.254/``) would bypass the guard entirely — the
    SERVER, not the caller, would pick the final destination. Raising
    ``HTTPError`` here (mirroring
    ``general_ludd.issue_sources.monday._NoRedirectHandler``) means the
    redirect response itself is returned to the caller instead of silently
    followed — no internal host is ever fetched via a redirect chain.
    """

    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: IO[bytes] | None,
        code: int,
        msg: str,
        headers: Message,
        newurl: str,
    ) -> None:
        raise urllib.error.HTTPError(
            url=newurl,
            code=code,
            msg=f"redirect blocked (SSRF guard): {msg}",
            hdrs=headers,
            fp=None,
        )


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
        self._cache_path = "web_retriever"

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
            ValueError: If the domain is not in the allowlist, or if the URL
                fails the SSRF guard (unsafe scheme, or a loopback / link-local
                / RFC-1918 / cloud-metadata host).
        """
        # SSRF guard (G12 hardening): reject a URL whose scheme isn't
        # http(s), or whose LITERAL host is loopback / link-local / RFC-1918 /
        # a cloud metadata name-or-IP (e.g. 169.254.169.254), BEFORE any
        # network I/O. Uses the canonical no-DNS host_is_blocked/is_url_blocked
        # primitives from security.ssrf — the same ones every other egress
        # path in the repo (skills/fetcher.py, connectors/*) delegates to —
        # so this can never hang on a hostile/slow resolver. This check is
        # ADDITIONAL to (not a replacement for) the optional
        # GLUDD_WEB_FETCH_ALLOWED_DOMAINS allowlist below: it always applies,
        # allowlisted or not, because an operator-configured domain allowlist
        # says nothing about whether that domain's DNS/IP is internal.
        if is_url_blocked(url):
            raise ValueError(
                f"Refusing to fetch unsafe URL (SSRF guard): {url!r}"
            )

        allowed = self.allowed_domains()
        if allowed:
            domain = _normalise_domain(url)
            if domain not in allowed:
                raise ValueError(
                    f"Domain '{domain}' is not in the web fetch allowlist. "
                    f"Allowed domains: {allowed}"
                )

        # Open the disk-backed cache only after validation and close it before
        # returning.  Keeping a Cache instance on every retriever leaks an idle
        # SQLite connection when callers construct a retriever for dependency
        # injection or reject a URL before the first cache operation.
        with open_safe_diskcache(self._cache_path) as cache:
            cached = cache.get(url)
            if isinstance(cached, dict) and all(
                isinstance(key, str) for key in cached
            ):
                logger.debug("cache hit for %s", url)
                return WebPageResult(**cached)

            req = urllib.request.Request(
                url,
                headers={"User-Agent": "gludd-web-retriever/1.0", "Accept": "text/html"},
            )
            # Build a fresh opener with the no-follow redirect handler for
            # EVERY request (rather than installing it as the process-wide
            # default opener) so this SSRF hardening never changes redirect
            # behavior for unrelated urllib.request.urlopen() call sites
            # elsewhere in the codebase.
            opener = urllib.request.build_opener(_NoRedirectHandler())
            try:
                resp = opener.open(req, timeout=self._timeout)
            except urllib.error.HTTPError as exc:
                try:
                    logger.warning("HTTP %s fetching %s", exc.code, url)
                    return WebPageResult(
                        url=url,
                        status_code=exc.code,
                        content="",
                        title=None,
                        headers=dict(exc.headers.items()),
                    )
                finally:
                    exc.close()
            except Exception as exc:
                logger.error("Failed to fetch %s: %s", url, exc)
                return WebPageResult(
                    url=url,
                    status_code=-1,
                    content=f"Fetch error: {exc}",
                    title=None,
                    headers=None,
                )

            try:
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
            finally:
                resp.close()
