"""fetch_raw — the thin public wrapper over :class:`SsrfSafeClient`.

A single call site that constructs (or reuses) an SsrfSafeClient and returns its
structured :class:`RawFetchResult`.  Inherits the SSRF guard, explicit timeout,
tenacity retry, per-host circuit breaker, captcha detection and structured
offline fallback — and NEVER raises and NEVER hangs.
"""

from __future__ import annotations

import httpx

from general_ludd.web.breaker import HostCircuitBreaker
from general_ludd.web.results import RawFetchResult
from general_ludd.web.ssrf_client import DEFAULT_UA, SsrfSafeClient


def fetch_raw(
    url: str,
    *,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    timeout: httpx.Timeout | None = None,
    transport: httpx.BaseTransport | None = None,
    client: SsrfSafeClient | None = None,
    breaker: HostCircuitBreaker | None = None,
    user_agent: str = DEFAULT_UA,
) -> RawFetchResult:
    """Fetch ``url`` and return a structured :class:`RawFetchResult`.

    ``client`` lets a caller (e.g. the Crawler) share one SsrfSafeClient — and so
    one circuit breaker + connection pool — across many fetches.  Otherwise a
    fresh client is built from ``transport`` / ``timeout`` / ``breaker``.
    """
    cli = client or SsrfSafeClient(
        timeout=timeout,
        transport=transport,
        breaker=breaker,
        user_agent=user_agent,
    )
    return cli.fetch(url, method=method, headers=headers)
