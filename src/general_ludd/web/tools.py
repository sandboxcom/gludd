"""Model-facing flat tool functions — the ONLY module dispatch imports.

Each function is a pure function over an injectable ``SafeFetcher`` + ``WebResilience``
and ALWAYS returns a :class:`WebResult` (``ok: bool``) — it NEVER raises across
the boundary and NEVER hangs (every network call is timeout + retry + breaker
guarded). ``TOOL_SPECS`` lets the tools self-describe for ``/api/dispatch/available``.
"""

from __future__ import annotations

import socket
import time
from collections.abc import Mapping
from urllib.parse import urlsplit

import httpx

from general_ludd.web.captcha import detect_block
from general_ludd.web.parse import parse_html
from general_ludd.web.policy import DEFAULT_POLICY, WebPolicy
from general_ludd.web.resilience import (
    CircuitOpenError,
    RetryableStatusError,
    WebResilience,
    web_error_for,
)
from general_ludd.web.safe_fetch import SafeFetcher, SafeFetchError, _RawResponse
from general_ludd.web.search import NullSearchProvider, SearchProvider
from general_ludd.web.types import BlockSignal, GatheredPage, WebError, WebResult


def _host_of(url: str) -> str:
    return urlsplit(url).netloc or url


def _retry_after_header(headers: Mapping[str, str]) -> float | None:
    """Parse a numeric ``Retry-After`` (seconds form) off a transient response."""
    raw = headers.get("retry-after") or headers.get("Retry-After")
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def _decode_body(raw: _RawResponse) -> str:
    """Decode bytes -> text via header charset, then meta, then utf-8/replace."""
    enc = raw.encoding
    if not enc:
        ctype = raw.headers.get("content-type", "")
        if "charset=" in ctype:
            enc = ctype.split("charset=", 1)[1].split(";", 1)[0].strip() or None
    for candidate in (enc, "utf-8"):
        if not candidate:
            continue
        try:
            return raw.body.decode(candidate, errors="strict")
        except (LookupError, UnicodeDecodeError):
            continue
    return raw.body.decode("utf-8", errors="replace")


def _build_defaults(
    policy: WebPolicy,
    fetcher: SafeFetcher | None,
    resilience: WebResilience | None,
) -> tuple[SafeFetcher, WebResilience]:
    f = fetcher if fetcher is not None else SafeFetcher(policy=policy)
    r = resilience if resilience is not None else WebResilience(policy=policy)
    return f, r


def fetch_raw(
    url: str,
    *,
    method: str = "GET",
    headers: Mapping[str, str] | None = None,
    policy: WebPolicy = DEFAULT_POLICY,
    fetcher: SafeFetcher | None = None,
    resilience: WebResilience | None = None,
) -> WebResult:
    """Fetch a URL through the hardened client + resilience. Always a WebResult.

    On success: ``ok=True`` with status/headers/decoded-body/final_url. A captcha/
    bot-block marker flips it to ``ok=False, error=CAPTCHA_DETECTED`` (not
    retried). On SSRF block / redirect cap / offline / exhaustion: ``ok=False``
    with the mapped :class:`WebError` and any partial status — never raises.
    """
    fetcher, resilience = _build_defaults(policy, fetcher, resilience)
    host = _host_of(url)
    t0 = time.monotonic()

    def _attempt() -> tuple[_RawResponse, str, BlockSignal | None]:
        # One fetch attempt INSIDE the resilience loop. A transient status (5xx or
        # a 429 WITHOUT a captcha/bot-block marker) is raised as a
        # RetryableStatusError so tenacity retries it with backoff AND the per-host
        # breaker records the failure — closing the "retry transient 5xx/429"
        # contract that a bare structured-return would silently violate. A
        # marker-bearing challenge is a PERSISTENT block (retrying won't help), so
        # it is returned terminally for the CAPTCHA_DETECTED path below.
        attempt_raw = fetcher.fetch(url, method=method, headers=headers)
        attempt_body = _decode_body(attempt_raw)
        attempt_signal = detect_block(
            attempt_raw.status, attempt_raw.headers, attempt_body
        )
        if attempt_signal is None and (
            attempt_raw.status >= 500 or attempt_raw.status == 429
        ):
            raise RetryableStatusError(
                attempt_raw.status,
                retry_after=_retry_after_header(attempt_raw.headers),
            )
        return attempt_raw, attempt_body, attempt_signal

    try:
        raw, body, signal = resilience.run(host, _attempt)
    except Exception as exc:  # boundary: nothing escapes to the model
        return _error_result(url, exc, time.monotonic() - t0)

    elapsed = (time.monotonic() - t0) * 1000.0
    meta = {"redirect_chain": raw.redirect_chain}

    if signal is not None:
        return WebResult(
            ok=False,
            url=url,
            final_url=raw.final_url,
            status=raw.status,
            headers=raw.headers,
            body=body,
            error=WebError.CAPTCHA_DETECTED,
            detail=f"{signal.vendor} {signal.kind} challenge detected",
            elapsed_ms=elapsed,
            meta={**meta, "blocked_by": signal.model_dump()},
        )

    if raw.status >= 500:
        return WebResult(
            ok=False, url=url, final_url=raw.final_url, status=raw.status,
            headers=raw.headers, body=body, error=WebError.HTTP_5XX,
            detail=f"server error {raw.status}", elapsed_ms=elapsed, meta=meta,
        )
    if raw.status >= 400:
        return WebResult(
            ok=False, url=url, final_url=raw.final_url, status=raw.status,
            headers=raw.headers, body=body, error=WebError.HTTP_4XX,
            detail=f"client error {raw.status}", elapsed_ms=elapsed, meta=meta,
        )

    return WebResult(
        ok=True, url=url, final_url=raw.final_url, status=raw.status,
        headers=raw.headers, body=body, elapsed_ms=elapsed, meta=meta,
    )


def fetch_parsed(
    url: str,
    *,
    rich: bool = False,
    policy: WebPolicy = DEFAULT_POLICY,
    fetcher: SafeFetcher | None = None,
    resilience: WebResilience | None = None,
) -> WebResult:
    """Fetch then stdlib-parse: title/text/links/meta/lang. Always a WebResult.

    A parse failure NEVER nukes a successful fetch — the raw body is carried and
    ``error=PARSE_ERROR`` only when the body is genuinely unparseable.
    """
    result = fetch_raw(url, policy=policy, fetcher=fetcher, resilience=resilience)
    if not result.ok or result.body is None:
        return result
    try:
        parsed = parse_html(
            result.body, base_url=result.final_url or url, status=result.status, rich=rich
        )
    except Exception as exc:  # parse must NEVER nuke a successful fetch
        return result.model_copy(
            update={"parsed": None, "detail": f"parse error: {exc}"}
        )
    return result.model_copy(update={"parsed": parsed})


def search_gather(
    query: str,
    *,
    top_n: int = 5,
    rich: bool = False,
    provider: SearchProvider | None = None,
    policy: WebPolicy = DEFAULT_POLICY,
    fetcher: SafeFetcher | None = None,
    resilience: WebResilience | None = None,
) -> WebResult:
    """Search via a pluggable provider, fetch+parse the top hits, aggregate.

    With the default :class:`NullSearchProvider`: ``ok=False,
    error=PROVIDER_UNCONFIGURED`` (DISTINCT from a genuine zero-hit search, which
    is ``ok=True, results=[]``). Per-hit failures are captured into their
    :class:`GatheredPage` and are NOT fatal.
    """
    provider = provider if provider is not None else NullSearchProvider()
    fetcher, resilience = _build_defaults(policy, fetcher, resilience)
    t0 = time.monotonic()

    if isinstance(provider, NullSearchProvider) or getattr(provider, "configured", True) is False:
        return WebResult(
            ok=False, url=f"search:{query}", error=WebError.PROVIDER_UNCONFIGURED,
            detail="no search provider wired", results=[],
            elapsed_ms=(time.monotonic() - t0) * 1000.0,
            meta={"provider_state": "unconfigured"},
        )

    try:
        hits = provider.search(query, top_n)
    except Exception as exc:  # provider failure is returned structured, not raised
        return WebResult(
            ok=False, url=f"search:{query}", error=WebError.RETRY_EXHAUSTED,
            detail=f"search provider error: {exc}", results=[],
            elapsed_ms=(time.monotonic() - t0) * 1000.0,
        )

    gathered: list[GatheredPage] = []
    for hit in hits[:top_n]:
        page_result = fetch_parsed(
            hit.url, rich=rich, policy=policy, fetcher=fetcher, resilience=resilience
        )
        gathered.append(_to_gathered(hit.url, page_result, fallback_title=hit.title))

    any_ok = any(g.ok for g in gathered)
    return WebResult(
        ok=any_ok or len(hits) == 0,
        url=f"search:{query}",
        results=gathered,
        elapsed_ms=(time.monotonic() - t0) * 1000.0,
        meta={"provider_state": "configured", "hit_count": len(hits)},
    )


def render_js(
    url: str,
    *,
    wait_selector: str | None = None,
    policy: WebPolicy = DEFAULT_POLICY,
    fetcher: SafeFetcher | None = None,
    resilience: WebResilience | None = None,
) -> WebResult:
    """Render a JS page via the optional Playwright backend. Always a WebResult.

    Delegates to :mod:`general_ludd.web.render`, which lazy-imports playwright and
    returns ``RENDERER_UNAVAILABLE`` if the extra/browser is missing — so this
    import is always safe and a missing renderer never raises.
    """
    from general_ludd.web.render import render_js as _render

    return _render(url, wait_selector=wait_selector, policy=policy, fetcher=fetcher)


def crawl_site(
    seed_url: str,
    *,
    policy: WebPolicy = DEFAULT_POLICY,
    fetcher: SafeFetcher | None = None,
    resilience: WebResilience | None = None,
) -> WebResult:
    """Politely crawl from ``seed_url`` (BFS). Delegates to :class:`PoliteCrawler`."""
    from general_ludd.web.crawl import PoliteCrawler

    crawler = PoliteCrawler(policy=policy, fetcher=fetcher, resilience=resilience)
    return crawler.crawl(seed_url)


def _to_gathered(url: str, result: WebResult, *, fallback_title: str = "") -> GatheredPage:
    title = (result.parsed.title if result.parsed else None) or (fallback_title or None)
    text = result.parsed.text if result.parsed else None
    return GatheredPage(
        url=url,
        ok=result.ok,
        status=result.status,
        title=title,
        text=text,
        error=result.error,
        detail=result.detail,
    )


def _error_result(url: str, exc: BaseException, elapsed_s: float) -> WebResult:
    """Map ANY caught exception to a structured WebResult (never re-raise)."""
    error = web_error_for(exc)
    status: int | None = None
    final_url: str | None = None
    if isinstance(exc, SafeFetchError):
        status = exc.partial_status
        final_url = exc.url
    elif isinstance(exc, RetryableStatusError):
        # Retries exhausted on a transient 5xx/429 — preserve the last status so
        # the structured result still carries it.
        status = exc.status
    elif isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
    elif isinstance(exc, CircuitOpenError | socket.gaierror):
        pass
    return WebResult(
        ok=False, url=url, final_url=final_url, status=status,
        error=error, detail=str(exc), elapsed_ms=elapsed_s * 1000.0,
    )


#: Self-describing tool specs for GET /api/dispatch/available.
TOOL_SPECS: dict[str, dict[str, object]] = {
    "fetch_raw": {
        "description": "Fetch a URL (SSRF-hardened, https-only) and return raw "
        "status/headers/body with timeout+retry+breaker fallback.",
        "params": {"url": "str (https)", "method": "str=GET", "headers": "dict?"},
    },
    "fetch_parsed": {
        "description": "Fetch a URL then extract title/text/links/meta via the "
        "stdlib HTML parser.",
        "params": {"url": "str (https)", "rich": "bool=false"},
    },
    "search_gather": {
        "description": "Search via a pluggable provider and fetch+parse the top "
        "N hits (provider must be operator-wired).",
        "params": {"query": "str", "top_n": "int=5"},
    },
    "crawl_site": {
        "description": "Politely BFS-crawl a site (robots, rate limit, depth/page "
        "caps, same-domain confinement, SSRF per hop).",
        "params": {"seed_url": "str (https)"},
    },
    "render_js": {
        "description": "Render a JS page via the optional Playwright backend "
        "(returns renderer_unavailable if the 'web' extra is not installed).",
        "params": {"url": "str (https)", "wait_selector": "str?"},
    },
}
