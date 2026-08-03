"""Policy-bound outbound HTTP fetching with DNS pinning.

All network destinations handled here are validated before a socket is opened.
The connection itself uses safehttpx's audited pinned-IP transport so the HTTP
client cannot perform a second, attacker-controlled DNS lookup.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Mapping
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit

import httpx

import safehttpx
from general_ludd.security.ssrf import PinnedTarget, SSRFError, host_is_blocked, resolve_and_pin

_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
_SENSITIVE_HEADERS = frozenset({"authorization", "cookie", "proxy-authorization"})
_CONTENT_HEADERS = frozenset({"content-length", "content-type", "transfer-encoding"})
_METHOD_RE = re.compile(r"^[A-Z]+$")


class URLFetchError(RuntimeError):
    """Base class for bounded outbound-fetch failures."""


class UnsafeURLError(URLFetchError, ValueError):
    """The requested destination violates the outbound policy."""


class ResponseTooLarge(URLFetchError):
    """The response exceeded the configured byte limit."""


class URLFetchTimeout(URLFetchError):
    """The complete request, including DNS and redirects, exceeded its deadline."""


class RedirectLimitExceeded(URLFetchError):
    """The response exceeded the configured redirect-hop limit."""


@dataclass(frozen=True, slots=True)
class FetchPolicy:
    """Explicit destination and resource limits for one outbound request."""

    allowed_hosts: frozenset[str]
    allowed_schemes: frozenset[str] = frozenset({"https"})
    max_bytes: int = 1024 * 1024
    timeout_seconds: float = 15.0
    dns_timeout_seconds: float = 2.0
    max_redirects: int = 3

    def __post_init__(self) -> None:
        schemes = frozenset(s.strip().lower() for s in self.allowed_schemes if s.strip())
        hosts = frozenset(_normalise_host_pattern(h) for h in self.allowed_hosts if h.strip())
        if not schemes:
            raise ValueError("allowed_schemes must not be empty")
        if not hosts:
            raise ValueError("allowed_hosts must not be empty")
        if self.max_bytes < 0:
            raise ValueError("max_bytes must be non-negative")
        if self.timeout_seconds <= 0 or self.dns_timeout_seconds <= 0:
            raise ValueError("timeouts must be positive")
        if self.max_redirects < 0:
            raise ValueError("max_redirects must be non-negative")
        object.__setattr__(self, "allowed_schemes", schemes)
        object.__setattr__(self, "allowed_hosts", hosts)


@dataclass(frozen=True, slots=True)
class FetchResult:
    """Fully-read, size-bounded response returned by :func:`secure_fetch`."""

    url: str
    status_code: int
    headers: dict[str, str]
    content: bytes


@dataclass(frozen=True, slots=True)
class _HopResult:
    status_code: int
    headers: dict[str, str]
    content: bytes


def _normalise_host_pattern(pattern: str) -> str:
    pattern = pattern.strip().lower().rstrip(".")
    if pattern == "*":
        return pattern
    wildcard = pattern.startswith("*.")
    bare = pattern[2:] if wildcard else pattern
    if not bare or "/" in bare or "@" in bare:
        raise ValueError(f"invalid allowed-host pattern: {pattern!r}")
    try:
        bare = bare.encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise ValueError(f"invalid allowed-host pattern: {pattern!r}") from exc
    return f"*.{bare}" if wildcard else bare


def _host_matches(host: str, allowed_hosts: frozenset[str]) -> bool:
    if "*" in allowed_hosts:
        return True
    for pattern in allowed_hosts:
        if pattern.startswith("*."):
            suffix = pattern[2:]
            if host.endswith(f".{suffix}"):
                return True
        elif host == pattern:
            return True
    return False


def _validate_url(url: str, policy: FetchPolicy) -> tuple[str, str, int]:
    """Parse with the HTTP client's URL model and enforce static policy."""

    try:
        split = urlsplit(url)
        parsed = httpx.URL(url)
    except (TypeError, ValueError, httpx.InvalidURL) as exc:
        raise UnsafeURLError(f"invalid outbound URL: {url!r}") from exc

    if split.username is not None or split.password is not None:
        raise UnsafeURLError("URL credentials are forbidden")
    scheme = parsed.scheme.lower()
    if scheme not in policy.allowed_schemes:
        raise UnsafeURLError(f"URL scheme {scheme!r} is not allowed")
    if not parsed.is_absolute_url:
        raise UnsafeURLError("outbound URL must be absolute")

    host = parsed.host.lower().rstrip(".")
    if not host or host_is_blocked(host):
        raise UnsafeURLError(f"URL host {host!r} is blocked")
    if not _host_matches(host, policy.allowed_hosts):
        raise UnsafeURLError(f"URL host {host!r} is not in the allowlist")

    port = parsed.port or (443 if scheme == "https" else 80)
    return str(parsed.copy_with(fragment=None)), host, port


async def _resolve_destination(
    host: str,
    port: int,
    policy: FetchPolicy,
) -> PinnedTarget:
    try:
        return await asyncio.to_thread(
            resolve_and_pin,
            host,
            port=port,
            timeout=min(policy.dns_timeout_seconds, policy.timeout_seconds),
        )
    except SSRFError as exc:
        raise UnsafeURLError(str(exc)) from exc


async def _send_once(
    method: str,
    url: str,
    *,
    headers: Mapping[str, str],
    content: bytes | str | None,
    target: PinnedTarget,
    policy: FetchPolicy,
) -> _HopResult:
    """Send one no-redirect hop through safehttpx's pinned-IP transport."""

    transport = safehttpx.AsyncSecureTransport(target.ip)
    timeout = httpx.Timeout(policy.timeout_seconds)
    async with (
        httpx.AsyncClient(
            transport=transport,
            timeout=timeout,
            follow_redirects=False,
            trust_env=False,
        ) as client,
        client.stream(
            method,
            url,
            headers=headers,
            content=content,
        ) as response,
    ):
        response_headers = {key.lower(): value for key, value in response.headers.items()}
        if response.status_code in _REDIRECT_STATUSES and response_headers.get("location"):
            return _HopResult(response.status_code, response_headers, b"")

        content_length = response_headers.get("content-length", "")
        if content_length.isdigit() and int(content_length) > policy.max_bytes:
            raise ResponseTooLarge(f"response exceeded the {policy.max_bytes}-byte limit")

        chunks: list[bytes] = []
        received = 0
        async for chunk in response.aiter_bytes():
            received += len(chunk)
            if received > policy.max_bytes:
                raise ResponseTooLarge(f"response exceeded the {policy.max_bytes}-byte limit")
            chunks.append(chunk)
        return _HopResult(response.status_code, response_headers, b"".join(chunks))


def _origin(url: str) -> tuple[str, str, int]:
    parsed = httpx.URL(url)
    return (
        parsed.scheme.lower(),
        parsed.host.lower().rstrip("."),
        parsed.port or (443 if parsed.scheme.lower() == "https" else 80),
    )


def _redirect_request(
    status_code: int,
    old_url: str,
    new_url: str,
    method: str,
    headers: dict[str, str],
    content: bytes | str | None,
) -> tuple[str, dict[str, str], bytes | str | None]:
    next_headers = dict(headers)
    next_method = method
    next_content = content

    if _origin(old_url) != _origin(new_url):
        next_headers = {key: value for key, value in next_headers.items() if key.lower() not in _SENSITIVE_HEADERS}
    if status_code == 303 or (status_code in {301, 302} and method == "POST"):
        next_method = "GET"
        next_content = None
        next_headers = {key: value for key, value in next_headers.items() if key.lower() not in _CONTENT_HEADERS}
    return next_method, next_headers, next_content


async def _fetch_with_redirects(
    url: str,
    *,
    method: str,
    headers: Mapping[str, str],
    content: bytes | str | None,
    policy: FetchPolicy,
) -> FetchResult:
    current_url = url
    current_method = method
    current_headers = dict(headers)
    current_headers.setdefault("Accept-Encoding", "identity")
    current_content = content

    for redirect_count in range(policy.max_redirects + 1):
        vetted_url, host, port = _validate_url(current_url, policy)
        target = await _resolve_destination(host, port, policy)
        hop = await _send_once(
            current_method,
            vetted_url,
            headers=current_headers,
            content=current_content,
            target=target,
            policy=policy,
        )
        location = hop.headers.get("location")
        if hop.status_code not in _REDIRECT_STATUSES or not location:
            return FetchResult(vetted_url, hop.status_code, hop.headers, hop.content)
        if redirect_count >= policy.max_redirects:
            raise RedirectLimitExceeded(f"response exceeded the {policy.max_redirects}-redirect limit")

        next_url = urljoin(vetted_url, location)
        current_method, current_headers, current_content = _redirect_request(
            hop.status_code,
            vetted_url,
            next_url,
            current_method,
            current_headers,
            current_content,
        )
        current_url = next_url

    raise RedirectLimitExceeded(f"response exceeded the {policy.max_redirects}-redirect limit")


async def secure_fetch_async(
    url: str,
    *,
    policy: FetchPolicy,
    method: str = "GET",
    headers: Mapping[str, str] | None = None,
    content: bytes | str | None = None,
) -> FetchResult:
    """Fetch one URL under an explicit, fail-closed outbound policy."""

    method = method.strip().upper()
    if not _METHOD_RE.fullmatch(method):
        raise ValueError(f"invalid HTTP method: {method!r}")
    try:
        async with asyncio.timeout(policy.timeout_seconds):
            return await _fetch_with_redirects(
                url,
                method=method,
                headers=headers or {},
                content=content,
                policy=policy,
            )
    except (TimeoutError, httpx.TimeoutException) as exc:
        raise URLFetchTimeout(f"outbound fetch exceeded {policy.timeout_seconds:g}s") from exc


def secure_fetch(
    url: str,
    *,
    policy: FetchPolicy,
    method: str = "GET",
    headers: Mapping[str, str] | None = None,
    content: bytes | str | None = None,
) -> FetchResult:
    """Synchronous adapter for callers that do not own an event loop."""

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        pass
    else:
        raise RuntimeError("secure_fetch() cannot run inside an event loop; use secure_fetch_async()")
    return asyncio.run(
        secure_fetch_async(
            url,
            policy=policy,
            method=method,
            headers=headers,
            content=content,
        )
    )


__all__ = [
    "FetchPolicy",
    "FetchResult",
    "RedirectLimitExceeded",
    "ResponseTooLarge",
    "URLFetchError",
    "URLFetchTimeout",
    "UnsafeURLError",
    "secure_fetch",
    "secure_fetch_async",
]
