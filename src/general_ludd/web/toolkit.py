"""SSRF-safe fetch, gather, crawl, and offline-render orchestration."""

from __future__ import annotations

import re
import time
from collections import deque
from collections.abc import Mapping, Sequence
from itertools import islice
from typing import Protocol
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from urllib.robotparser import RobotFileParser

import httpx
from pydantic import ValidationError

from general_ludd.security.url_fetch import (
    FetchPolicy,
    FetchResult,
    RedirectLimitExceeded,
    ResponseTooLarge,
    UnsafeURLError,
    URLFetchError,
    URLFetchTimeout,
    secure_fetch,
)
from general_ludd.web.parse import parse_html
from general_ludd.web.policy import DEFAULT_POLICY, WebPolicy
from general_ludd.web.types import (
    BlockSignal,
    GatheredPage,
    ParsedPage,
    SearchHit,
    WebError,
    WebResult,
)

_CHARSET_RE = re.compile(r"charset\s*=\s*[\"']?([^;\s\"']+)", re.IGNORECASE)
_CHALLENGE_SAMPLE_BYTES = 64 * 1024


class Fetcher(Protocol):
    """Callable shape supplied by the maintained outbound fetch boundary."""

    def __call__(
        self,
        url: str,
        *,
        policy: FetchPolicy,
        method: str = "GET",
        headers: Mapping[str, str] | None = None,
        content: bytes | str | None = None,
    ) -> FetchResult:
        """Fetch one resource under an explicit policy."""


class SearchProvider(Protocol):
    """Bounded operator-injected search seam; no provider is a valid state."""

    configured: bool

    def search(self, query: str, *, top_n: int) -> Sequence[SearchHit]:
        """Return no more than ``top_n`` provider hits."""


class OfflineRenderer(Protocol):
    """Renderer that accepts fetched HTML and has no navigation capability."""

    def render_offline(self, html: str, *, base_url: str, timeout_seconds: float) -> str:
        """Transform one securely fetched document within the supplied deadline."""


class NullSearchProvider:
    """Explicit offline provider, distinct from a configured zero-hit result."""

    configured = False

    def search(self, query: str, *, top_n: int) -> Sequence[SearchHit]:
        """Return no hits without attempting I/O."""
        del query, top_n
        return ()


def _bounded_detail(value: object) -> str:
    return str(value)[:512]


def _decode_body(result: FetchResult) -> str:
    content_type = result.headers.get("content-type", "")
    match = _CHARSET_RE.search(content_type)
    candidates = (match.group(1) if match else None, "utf-8")
    for encoding in candidates:
        if encoding is None:
            continue
        try:
            return result.content.decode(encoding, errors="strict")
        except (LookupError, UnicodeDecodeError):
            continue
    return result.content.decode("utf-8", errors="replace")


def _fetch_error(exc: BaseException) -> WebError:
    if isinstance(exc, UnsafeURLError):
        return WebError.SSRF_BLOCKED
    if isinstance(exc, URLFetchTimeout | httpx.TimeoutException):
        return WebError.TIMEOUT
    if isinstance(exc, RedirectLimitExceeded):
        return WebError.REDIRECT_LIMIT
    if isinstance(exc, ResponseTooLarge):
        return WebError.RESPONSE_TOO_LARGE
    if isinstance(exc, ValueError):
        return WebError.INVALID_URL
    return WebError.OFFLINE


def _challenge(status: int, headers: Mapping[str, str], body: str) -> BlockSignal | None:
    if status not in {403, 429, 503}:
        return None
    sample = body[:_CHALLENGE_SAMPLE_BYTES].lower()
    server = headers.get("server", "").lower()
    signatures = (
        ("cloudflare", "cloudflare", ("cf-mitigated", "just a moment")),
        ("recaptcha", "google", ("g-recaptcha", "recaptcha")),
        ("hcaptcha", "hcaptcha", ("hcaptcha", "h-captcha")),
    )
    for evidence, vendor, markers in signatures:
        if evidence in server or any(marker in sample for marker in markers):
            return BlockSignal(
                vendor=vendor,
                kind="rate_limited" if status == 429 else "captcha",
                status=status,
                evidence=evidence,
            )
    return None


def normalize_url(url: str) -> str | None:
    """Return a canonical HTTPS URL for crawl de-duplication."""
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except (TypeError, ValueError):
        return None
    if parsed.scheme.lower() != "https" or not parsed.hostname:
        return None
    if parsed.username is not None or parsed.password is not None:
        return None
    host = parsed.hostname.lower().rstrip(".")
    netloc = host if port in {None, 443} else f"{host}:{port}"
    path = parsed.path or "/"
    query = urlencode(sorted(parse_qsl(parsed.query, keep_blank_values=True)))
    return urlunsplit(("https", netloc, path, query, ""))


class WebToolkit:
    """Stateless facade over one hardened fetcher and optional offline seams."""

    def __init__(
        self,
        *,
        policy: WebPolicy = DEFAULT_POLICY,
        fetcher: Fetcher | None = None,
        search_provider: SearchProvider | None = None,
        renderer: OfflineRenderer | None = None,
    ) -> None:
        """Initialize bounded policy and optional provider/backend dependencies."""
        self.policy = policy
        self._fetcher = fetcher if fetcher is not None else secure_fetch
        self._search_provider = search_provider
        self._renderer = renderer

    def _failure(
        self,
        url: str,
        error: WebError,
        detail: str,
        *,
        status: int | None = None,
        elapsed_ms: float = 0.0,
        meta: dict[str, object] | None = None,
    ) -> WebResult:
        return WebResult(
            ok=False,
            url=url,
            final_url=url or None,
            status=status,
            error=error,
            detail=detail,
            elapsed_ms=elapsed_ms,
            meta=meta or {},
        )

    def fetch_raw(
        self,
        url: str,
        *,
        method: str = "GET",
        headers: Mapping[str, str] | None = None,
    ) -> WebResult:
        """Fetch one bounded resource and map expected failures to data."""
        started = time.monotonic()
        if not isinstance(url, str) or not url.strip():
            return self._failure("", WebError.INVALID_URL, "url must be a non-empty string")
        url = url.strip()
        if not isinstance(method, str) or method.strip().upper() not in {"GET", "HEAD"}:
            return self._failure(
                url,
                WebError.INVALID_INPUT,
                "web retrieval permits only GET or HEAD",
            )
        method = method.strip().upper()
        request_headers = dict(headers or {})
        request_headers.setdefault("User-Agent", self.policy.user_agent)
        try:
            fetched = self._fetcher(
                url,
                policy=self.policy.fetch_policy(),
                method=method,
                headers=request_headers,
            )
        except (URLFetchError, httpx.HTTPError, OSError, RuntimeError, ValueError) as exc:
            return self._failure(
                url,
                _fetch_error(exc),
                _bounded_detail(exc),
                elapsed_ms=(time.monotonic() - started) * 1000.0,
            )

        body = _decode_body(fetched)
        elapsed_ms = (time.monotonic() - started) * 1000.0
        signal = _challenge(fetched.status_code, fetched.headers, body)
        if signal is not None:
            return WebResult(
                ok=False,
                url=url,
                final_url=fetched.url,
                status=fetched.status_code,
                headers=dict(fetched.headers),
                body=body,
                error=WebError.CAPTCHA_DETECTED,
                detail="advisory bot challenge detected; no bypass attempted",
                elapsed_ms=elapsed_ms,
                meta={"blocked_by": signal.model_dump(mode="json")},
            )
        if fetched.status_code >= 500:
            error = WebError.HTTP_5XX
        elif fetched.status_code >= 400:
            error = WebError.HTTP_4XX
        else:
            error = None
        return WebResult(
            ok=error is None,
            url=url,
            final_url=fetched.url,
            status=fetched.status_code,
            headers=dict(fetched.headers),
            body=body,
            error=error,
            detail=f"HTTP {fetched.status_code}" if error is not None else None,
            elapsed_ms=elapsed_ms,
        )

    def fetch_parsed(self, url: str) -> WebResult:
        """Fetch then parse HTML, retaining non-HTML content as plain text."""
        result = self.fetch_raw(url)
        if not result.ok or result.body is None:
            return result
        content_type = result.headers.get("content-type", "").lower()
        if content_type and "html" not in content_type and "xml" not in content_type:
            parsed = ParsedPage(
                url=result.final_url or url,
                text=result.body,
                status=result.status,
            )
        else:
            try:
                parsed = parse_html(
                    result.body,
                    base_url=result.final_url or url,
                    status=result.status,
                    max_links=self.policy.max_links_per_page,
                )
            except (TypeError, ValueError) as exc:
                return result.model_copy(
                    update={
                        "ok": False,
                        "error": WebError.PARSE_ERROR,
                        "detail": _bounded_detail(exc),
                    }
                )
        return result.model_copy(update={"parsed": parsed})

    def search_gather(
        self,
        query: str,
        *,
        top_n: int = 5,
        fetch_results: bool = True,
    ) -> WebResult:
        """Search through an injected provider and gather bounded partial results."""
        started = time.monotonic()
        if not isinstance(query, str) or not query.strip():
            return self._failure(
                "search:",
                WebError.INVALID_INPUT,
                "query must be a non-empty string",
            )
        query = query.strip()
        if isinstance(top_n, bool) or not isinstance(top_n, int) or top_n < 1:
            return self._failure(
                f"search:{query}",
                WebError.INVALID_INPUT,
                "top_n must be a positive integer",
            )
        limit = min(top_n, self.policy.max_search_results)
        provider = self._search_provider
        if provider is None or not getattr(provider, "configured", True):
            return self._failure(
                f"search:{query}",
                WebError.PROVIDER_UNCONFIGURED,
                "no search provider is configured",
                meta={"provider_state": "unconfigured"},
            )
        try:
            raw_hits = provider.search(query, top_n=limit)
            hits = [
                hit if isinstance(hit, SearchHit) else SearchHit.model_validate(hit)
                for hit in islice(iter(raw_hits), limit)
            ]
        except (OSError, RuntimeError, TypeError, ValueError, ValidationError) as exc:
            return self._failure(
                f"search:{query}",
                WebError.PROVIDER_UNCONFIGURED,
                f"search provider failed: {_bounded_detail(exc)}",
                elapsed_ms=(time.monotonic() - started) * 1000.0,
                meta={"provider_state": "failed"},
            )

        if not fetch_results:
            return WebResult(
                ok=True,
                url=f"search:{query}",
                hits=hits,
                results=[],
                elapsed_ms=(time.monotonic() - started) * 1000.0,
                meta={"provider_state": "configured", "hit_count": len(hits)},
            )

        gathered: list[GatheredPage] = []
        pages: list[ParsedPage] = []
        errors: list[str] = []
        for hit in hits:
            page_result = self.fetch_parsed(hit.url)
            page = page_result.parsed
            if page_result.ok and page is not None:
                pages.append(page)
            else:
                errors.append(page_result.detail or (page_result.error or WebError.OFFLINE).value)
            gathered.append(
                GatheredPage(
                    url=hit.url,
                    ok=page_result.ok,
                    status=page_result.status,
                    title=(page.title if page is not None else None) or hit.title or None,
                    text=page.text if page is not None else None,
                    error=page_result.error,
                    detail=page_result.detail,
                )
            )
        succeeded = sum(page.ok for page in gathered)
        ok = succeeded > 0 or not hits
        first_error = next((page.error for page in gathered if page.error is not None), None)
        return WebResult(
            ok=ok,
            url=f"search:{query}",
            results=gathered,
            hits=hits,
            pages=pages,
            gathered=succeeded,
            failed=len(gathered) - succeeded,
            errors=errors,
            error=None if ok else first_error or WebError.OFFLINE,
            elapsed_ms=(time.monotonic() - started) * 1000.0,
            meta={"provider_state": "configured", "hit_count": len(hits)},
        )

    def _robots_for(self, seed_url: str) -> tuple[RobotFileParser | None, WebResult | None, str]:
        if not self.policy.respect_robots:
            return None, None, "disabled"
        parsed = urlsplit(seed_url)
        robots_url = urlunsplit((parsed.scheme, parsed.netloc, "/robots.txt", "", ""))
        result = self.fetch_raw(robots_url)
        if result.status in {404, 410}:
            return None, None, "missing"
        if not result.ok or result.status != 200 or result.body is None:
            if not self.policy.robots_fail_closed:
                return None, None, "unavailable_fail_open"
            failure = self._failure(
                seed_url,
                WebError.ROBOTS_DISALLOWED,
                "robots policy was unavailable; crawl refused",
                meta={"robots_state": "unavailable_fail_closed"},
            )
            return None, failure, "unavailable_fail_closed"
        parser = RobotFileParser()
        try:
            parser.set_url(robots_url)
            parser.parse(result.body.splitlines())
        except (UnicodeError, ValueError) as exc:
            if not self.policy.robots_fail_closed:
                return None, None, "invalid_fail_open"
            failure = self._failure(
                seed_url,
                WebError.ROBOTS_DISALLOWED,
                f"robots policy was invalid: {_bounded_detail(exc)}",
                meta={"robots_state": "invalid_fail_closed"},
            )
            return None, failure, "invalid_fail_closed"
        return parser, None, "loaded"

    def crawl_site(
        self,
        seed_url: str,
        *,
        max_pages: int | None = None,
        max_depth: int | None = None,
    ) -> WebResult:
        """Perform a sequential, same-host, robots-aware bounded BFS crawl."""
        started = time.monotonic()
        seed = normalize_url(seed_url)
        if seed is None:
            return self._failure(seed_url, WebError.INVALID_URL, "crawl seed must be an absolute HTTPS URL")
        if max_pages is not None and (isinstance(max_pages, bool) or not isinstance(max_pages, int)):
            return self._failure(seed, WebError.INVALID_INPUT, "max_pages must be an integer")
        if max_depth is not None and (isinstance(max_depth, bool) or not isinstance(max_depth, int)):
            return self._failure(seed, WebError.INVALID_INPUT, "max_depth must be an integer")
        page_limit = self.policy.max_pages if max_pages is None else max(1, min(max_pages, self.policy.max_pages))
        depth_limit = self.policy.max_depth if max_depth is None else max(0, min(max_depth, self.policy.max_depth))
        robots, robots_failure, robots_state = self._robots_for(seed)
        if robots_failure is not None:
            return robots_failure
        if robots is not None and not robots.can_fetch(self.policy.user_agent, seed):
            return self._failure(
                seed,
                WebError.ROBOTS_DISALLOWED,
                "robots policy disallows the crawl seed",
                meta={"robots_state": "denied"},
            )

        seed_host = urlsplit(seed).hostname
        queue: deque[tuple[str, int]] = deque([(seed, 0)])
        seen = {seed}
        visited: list[str] = []
        pages: list[ParsedPage] = []
        skipped: list[dict[str, str]] = []
        errors: list[str] = []
        off_domain = 0
        robots_denied = 0
        blocked = 0
        truncated = False
        deadline = started + self.policy.crawl_timeout_seconds
        last_request_started: float | None = None
        timeout_error = False

        while queue and len(visited) < page_limit:
            now = time.monotonic()
            if now >= deadline:
                timeout_error = True
                truncated = True
                break
            url, depth = queue.popleft()
            if robots is not None and not robots.can_fetch(self.policy.user_agent, url):
                robots_denied += 1
                skipped.append({"url": url, "reason": WebError.ROBOTS_DISALLOWED.value})
                continue
            if last_request_started is not None:
                wait_seconds = self.policy.min_request_interval_seconds - (now - last_request_started)
                if wait_seconds > 0:
                    remaining = deadline - now
                    if remaining <= wait_seconds:
                        timeout_error = True
                        truncated = True
                        break
                    time.sleep(wait_seconds)
            last_request_started = time.monotonic()
            visited.append(url)
            fetched = self.fetch_parsed(url)
            if not fetched.ok or fetched.parsed is None:
                reason = (fetched.error or WebError.OFFLINE).value
                skipped.append({"url": url, "reason": reason})
                errors.append(fetched.detail or reason)
                if fetched.error is WebError.SSRF_BLOCKED:
                    blocked += 1
                continue
            page = fetched.parsed
            pages.append(page)
            if depth >= depth_limit:
                continue
            for link in page.links:
                candidate = normalize_url(link.href)
                if candidate is None:
                    continue
                if urlsplit(candidate).hostname != seed_host:
                    off_domain += 1
                    continue
                if candidate in seen:
                    continue
                seen.add(candidate)
                if len(visited) + len(queue) >= page_limit:
                    truncated = True
                    continue
                queue.append((candidate, depth + 1))

        if queue:
            truncated = True
        if timeout_error:
            error = WebError.CRAWL_TIMEOUT
        elif not pages and errors:
            error = WebError.OFFLINE
        else:
            error = None
        return WebResult(
            ok=bool(pages) and not timeout_error,
            url=seed,
            final_url=seed,
            pages=pages,
            visited=visited,
            skipped=skipped,
            errors=errors,
            error=error,
            detail="crawl deadline exceeded" if timeout_error else None,
            elapsed_ms=(time.monotonic() - started) * 1000.0,
            truncated=truncated,
            stats={
                "pages": len(pages),
                "visited": len(visited),
                "skipped": len(skipped),
                "off_domain": off_domain,
                "robots_denied": robots_denied,
                "blocked": blocked,
                "page_limit": page_limit,
                "depth_limit": depth_limit,
            },
            meta={"robots_state": robots_state},
        )

    def render_js(self, url: str) -> WebResult:
        """Render already-fetched HTML through an explicitly offline backend."""
        started = time.monotonic()
        if not self.policy.allow_render:
            return self._failure(
                url,
                WebError.RENDERER_UNAVAILABLE,
                "rendering is disabled by policy",
                meta={"renderer_state": "disabled"},
            )
        if self._renderer is None:
            return self._failure(
                url,
                WebError.RENDERER_UNAVAILABLE,
                "no offline renderer is configured",
                meta={"renderer_state": "unavailable"},
            )
        fetched = self.fetch_raw(url)
        if not fetched.ok or fetched.body is None:
            return fetched.model_copy(update={"meta": {"renderer_state": "prefetch_failed"}})
        try:
            rendered = self._renderer.render_offline(
                fetched.body,
                base_url=fetched.final_url or url,
                timeout_seconds=self.policy.render_timeout_seconds,
            )
        except TimeoutError as exc:
            return self._failure(
                url,
                WebError.TIMEOUT,
                _bounded_detail(exc),
                elapsed_ms=(time.monotonic() - started) * 1000.0,
                meta={"renderer_state": "timeout"},
            )
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            return self._failure(
                url,
                WebError.RENDERER_UNAVAILABLE,
                _bounded_detail(exc),
                elapsed_ms=(time.monotonic() - started) * 1000.0,
                meta={"renderer_state": "failed"},
            )
        if not isinstance(rendered, str):
            return self._failure(
                url,
                WebError.RENDERER_UNAVAILABLE,
                "offline renderer returned a non-string document",
                meta={"renderer_state": "invalid_output"},
            )
        if len(rendered.encode("utf-8")) > self.policy.max_render_bytes:
            return self._failure(
                url,
                WebError.RESPONSE_TOO_LARGE,
                "rendered document exceeded the configured byte limit",
                meta={"renderer_state": "output_too_large"},
            )
        parsed = parse_html(
            rendered,
            base_url=fetched.final_url or url,
            status=fetched.status,
            max_links=self.policy.max_links_per_page,
        )
        return WebResult(
            ok=True,
            url=url,
            final_url=fetched.final_url,
            status=fetched.status,
            headers=fetched.headers,
            body=fetched.body,
            html=rendered,
            parsed=parsed,
            elapsed_ms=(time.monotonic() - started) * 1000.0,
            meta={"renderer_state": "offline"},
        )


__all__ = [
    "Fetcher",
    "NullSearchProvider",
    "OfflineRenderer",
    "SearchProvider",
    "WebToolkit",
    "normalize_url",
]
