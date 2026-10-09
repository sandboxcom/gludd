"""Collection-native DNS-pinned, no-redirect, size-bounded HTTPS transport."""

from __future__ import annotations

import asyncio
import ipaddress
import re
from collections.abc import Mapping
from dataclasses import dataclass
from urllib.parse import urlsplit

import httpx
import safehttpx

_METHOD = re.compile(r"^(?:GET|HEAD)$")


class SecureFetchError(RuntimeError):
    """A bounded transport or destination-policy failure."""


class FetchPolicy:
    """Exact destination and resource limits for one controller request."""

    __slots__ = (
        "allowed_hosts",
        "allowed_schemes",
        "dns_timeout_seconds",
        "max_bytes",
        "max_redirects",
        "timeout_seconds",
    )

    def __init__(
        self,
        *,
        allowed_hosts: frozenset[str],
        allowed_schemes: frozenset[str] = frozenset({"https"}),
        max_bytes: int = 1024 * 1024,
        timeout_seconds: float = 15.0,
        dns_timeout_seconds: float = 2.0,
        max_redirects: int = 0,
    ) -> None:
        """Normalize the allowlist and enforce the no-redirect budget."""
        hosts = frozenset(host.strip().lower().rstrip(".") for host in allowed_hosts)
        schemes = frozenset(scheme.strip().lower() for scheme in allowed_schemes)
        if not hosts or any(not host or "*" in host or "/" in host for host in hosts):
            raise ValueError("allowed_hosts must contain only explicit DNS hosts")
        if schemes != frozenset({"https"}):
            raise ValueError("only HTTPS transport is allowed")
        if not (1 <= max_bytes <= 4 * 1024 * 1024):
            raise ValueError("max_bytes must be between 1 byte and 4 MiB")
        if timeout_seconds <= 0 or dns_timeout_seconds <= 0:
            raise ValueError("transport timeouts must be positive")
        if max_redirects != 0:
            raise ValueError("redirects are forbidden")
        self.allowed_hosts = hosts
        self.allowed_schemes = schemes
        self.max_bytes = max_bytes
        self.timeout_seconds = timeout_seconds
        self.dns_timeout_seconds = dns_timeout_seconds
        self.max_redirects = max_redirects


@dataclass(frozen=True, slots=True)
class FetchResult:
    """Fully consumed response below the byte ceiling."""

    url: str
    status_code: int
    headers: dict[str, str]
    content: bytes


def _validated_url(url: str, policy: FetchPolicy) -> tuple[str, str, int]:
    try:
        split = urlsplit(url)
        parsed = httpx.URL(url)
    except (TypeError, ValueError, httpx.InvalidURL) as exc:
        raise SecureFetchError("invalid outbound URL") from exc
    if split.username is not None or split.password is not None:
        raise SecureFetchError("URL credentials are forbidden")
    scheme = parsed.scheme.lower()
    host = parsed.host.lower().rstrip(".")
    if scheme not in policy.allowed_schemes or not parsed.is_absolute_url:
        raise SecureFetchError("outbound URL must be absolute HTTPS")
    if not host or host not in policy.allowed_hosts:
        raise SecureFetchError("outbound URL host is not explicitly allowed")
    if "." not in host:
        raise SecureFetchError("single-label outbound hosts are forbidden")
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        if not literal.is_global:
            raise SecureFetchError("outbound IP literal is not globally routable")
    return str(parsed.copy_with(fragment=None)), host, parsed.port or 443


async def _resolve_public(host: str, port: int, timeout: float) -> str:
    loop = asyncio.get_running_loop()
    try:
        async with asyncio.timeout(timeout):
            infos = await loop.getaddrinfo(host, port, type=0)
    except (OSError, TimeoutError) as exc:
        raise SecureFetchError("outbound host resolution failed") from exc
    if not infos:
        raise SecureFetchError("outbound host resolved to no addresses")
    selected: str | None = None
    for info in infos:
        raw = str(info[4][0]).split("%", 1)[0]
        try:
            address = ipaddress.ip_address(raw)
        except ValueError as exc:
            raise SecureFetchError("outbound host resolved to an invalid address") from exc
        if not address.is_global:
            raise SecureFetchError("outbound host resolved to a non-public address")
        if selected is None:
            selected = str(address)
    if selected is None:
        raise SecureFetchError("outbound host resolved to no usable address")
    return selected


async def _secure_fetch_async(
    url: str,
    *,
    policy: FetchPolicy,
    method: str,
    headers: Mapping[str, str],
) -> FetchResult:
    method = method.strip().upper()
    if _METHOD.fullmatch(method) is None:
        raise ValueError("secure transport accepts only GET or HEAD")
    validated, host, port = _validated_url(url, policy)
    try:
        async with asyncio.timeout(policy.timeout_seconds):
            address = await _resolve_public(host, port, policy.dns_timeout_seconds)
            transport = safehttpx.AsyncSecureTransport(address)
            timeout = httpx.Timeout(policy.timeout_seconds)
            async with (
                httpx.AsyncClient(
                    transport=transport,
                    timeout=timeout,
                    follow_redirects=False,
                    trust_env=False,
                ) as client,
                client.stream(method, validated, headers=headers) as response,
            ):
                response_headers = {key.lower(): value for key, value in response.headers.items()}
                if response.status_code in {301, 302, 303, 307, 308}:
                    raise SecureFetchError("redirect responses are forbidden")
                length = response_headers.get("content-length", "")
                if length.isdigit() and int(length) > policy.max_bytes:
                    raise SecureFetchError("response exceeds the byte limit")
                chunks: list[bytes] = []
                received = 0
                async for chunk in response.aiter_bytes():
                    received += len(chunk)
                    if received > policy.max_bytes:
                        raise SecureFetchError("response exceeds the byte limit")
                    chunks.append(chunk)
                return FetchResult(validated, response.status_code, response_headers, b"".join(chunks))
    except (TimeoutError, httpx.TimeoutException) as exc:
        raise SecureFetchError("bounded HTTPS request timed out") from exc
    except (httpx.HTTPError, OSError) as exc:
        raise SecureFetchError("bounded HTTPS request failed") from exc


def secure_fetch(
    url: str,
    *,
    policy: FetchPolicy,
    method: str = "GET",
    headers: Mapping[str, str] | None = None,
) -> FetchResult:
    """Execute one synchronous request without an ambient loop or persistent state."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        pass
    else:
        raise RuntimeError("secure_fetch cannot run inside an event loop")
    return asyncio.run(
        _secure_fetch_async(
            url,
            policy=policy,
            method=method,
            headers=headers or {},
        )
    )


__all__ = ["FetchPolicy", "FetchResult", "SecureFetchError", "secure_fetch"]
