"""Security contract for the single outbound URL fetch path."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable, Coroutine
from pathlib import Path
from typing import Any

import httpx
import pytest

from general_ludd.security.ssrf import PinnedTarget, SSRFError
from general_ludd.security.url_fetch import (
    FetchPolicy,
    RedirectLimitExceeded,
    ResponseTooLarge,
    UnsafeURLError,
    URLFetchTimeout,
    secure_fetch,
    secure_fetch_async,
)

Handler = Callable[[httpx.Request], httpx.Response] | Callable[
    [httpx.Request], Coroutine[None, None, httpx.Response]
]


def test_safehttpx_stub_uses_a_package_shape_visible_to_mypy() -> None:
    """Stub-only distributions must mirror the imported package layout."""

    repo_root = Path(__file__).resolve().parents[2]
    assert (repo_root / "typings/safehttpx/__init__.pyi").is_file()
    assert not (repo_root / "src/safehttpx/__init__.pyi").exists()
    assert not (repo_root / "src/safehttpx.pyi").exists()


def _install_transport(
    monkeypatch: pytest.MonkeyPatch,
    handler: Handler,
    pinned_ips: list[str],
) -> None:
    """Replace only the network edge while retaining policy/redirect logic."""

    def factory(verified_ip: str) -> httpx.MockTransport:
        pinned_ips.append(verified_ip)
        return httpx.MockTransport(handler)

    monkeypatch.setattr(
        "general_ludd.security.url_fetch.safehttpx.AsyncSecureTransport",
        factory,
    )


def _public_target(host: str, *, port: int = 443) -> PinnedTarget:
    return PinnedTarget(host=host, ip="93.184.216.34", port=port)


@pytest.mark.asyncio
async def test_fetch_pins_the_vetted_ip_and_returns_public_https(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pinned_ips: list[str] = []
    seen_urls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_urls.append(str(request.url))
        return httpx.Response(200, content=b"ok", headers={"X-Test": "yes"})

    monkeypatch.setattr(
        "general_ludd.security.url_fetch.resolve_and_pin",
        lambda host, *, port, timeout: _public_target(host, port=port),
    )
    _install_transport(monkeypatch, handler, pinned_ips)

    result = await secure_fetch_async(
        "https://example.com/data",
        policy=FetchPolicy(allowed_hosts=frozenset({"example.com"})),
    )

    assert result.status_code == 200
    assert result.content == b"ok"
    assert result.headers["x-test"] == "yes"
    assert seen_urls == ["https://example.com/data"]
    assert pinned_ips == ["93.184.216.34"]


@pytest.mark.asyncio
async def test_dns_rebinding_answer_fails_closed_before_transport(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pinned_ips: list[str] = []

    def blocked_resolution(host: str, *, port: int, timeout: float) -> PinnedTarget:
        raise SSRFError(f"{host} rebound to 127.0.0.1")

    monkeypatch.setattr(
        "general_ludd.security.url_fetch.resolve_and_pin", blocked_resolution
    )
    _install_transport(
        monkeypatch,
        lambda request: httpx.Response(200, content=b"should not run"),
        pinned_ips,
    )

    with pytest.raises(UnsafeURLError, match="rebound"):
        await secure_fetch_async(
            "https://rebind.example/resource",
            policy=FetchPolicy(allowed_hosts=frozenset({"rebind.example"})),
        )

    assert pinned_ips == []


@pytest.mark.asyncio
async def test_redirect_destination_is_resolved_and_blocked_before_second_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[str] = []
    pinned_ips: list[str] = []

    def resolve(host: str, *, port: int, timeout: float) -> PinnedTarget:
        if host == "redirect.example":
            return _public_target(host, port=port)
        raise SSRFError(f"{host} resolved to link-local 169.254.169.254")

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(str(request.url))
        return httpx.Response(
            302,
            headers={"Location": "https://landing.example/private"},
        )

    monkeypatch.setattr("general_ludd.security.url_fetch.resolve_and_pin", resolve)
    _install_transport(monkeypatch, handler, pinned_ips)

    policy = FetchPolicy(
        allowed_hosts=frozenset({"redirect.example", "landing.example"}),
        max_redirects=2,
    )
    with pytest.raises(UnsafeURLError, match="link-local"):
        await secure_fetch_async("https://redirect.example/start", policy=policy)

    assert requests == ["https://redirect.example/start"]
    assert pinned_ips == ["93.184.216.34"]


@pytest.mark.asyncio
async def test_redirect_to_metadata_literal_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pinned_ips: list[str] = []
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(
            302,
            headers={"Location": "http://169.254.169.254/latest/meta-data"},
        )

    monkeypatch.setattr(
        "general_ludd.security.url_fetch.resolve_and_pin",
        lambda host, *, port, timeout: _public_target(host, port=port),
    )
    _install_transport(monkeypatch, handler, pinned_ips)

    with pytest.raises(UnsafeURLError, match="blocked"):
        await secure_fetch_async(
            "https://public.example/start",
            policy=FetchPolicy(
                allowed_hosts=frozenset({"*"}),
                allowed_schemes=frozenset({"http", "https"}),
            ),
        )

    assert calls == 1


@pytest.mark.asyncio
async def test_response_body_is_streamed_with_hard_size_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pinned_ips: list[str] = []
    monkeypatch.setattr(
        "general_ludd.security.url_fetch.resolve_and_pin",
        lambda host, *, port, timeout: _public_target(host, port=port),
    )
    _install_transport(
        monkeypatch,
        lambda request: httpx.Response(200, content=b"0123456789"),
        pinned_ips,
    )

    with pytest.raises(ResponseTooLarge, match="8-byte"):
        await secure_fetch_async(
            "https://large.example/file",
            policy=FetchPolicy(
                allowed_hosts=frozenset({"large.example"}), max_bytes=8
            ),
        )


@pytest.mark.asyncio
async def test_whole_operation_timeout_is_enforced(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pinned_ips: list[str] = []

    async def slow_handler(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(0.1)
        return httpx.Response(200, content=b"late")

    monkeypatch.setattr(
        "general_ludd.security.url_fetch.resolve_and_pin",
        lambda host, *, port, timeout: _public_target(host, port=port),
    )
    _install_transport(monkeypatch, slow_handler, pinned_ips)

    with pytest.raises(URLFetchTimeout, match=r"0\.01"):
        await secure_fetch_async(
            "https://slow.example/",
            policy=FetchPolicy(
                allowed_hosts=frozenset({"slow.example"}), timeout_seconds=0.01
            ),
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("url", "policy", "message"),
    [
        (
            "file:///etc/passwd",
            FetchPolicy(allowed_hosts=frozenset({"*"})),
            "scheme",
        ),
        (
            "https://user:secret@example.com/",
            FetchPolicy(allowed_hosts=frozenset({"example.com"})),
            "credentials",
        ),
        (
            "https://other.example/",
            FetchPolicy(allowed_hosts=frozenset({"allowed.example"})),
            "allowlist",
        ),
        (
            "https://metadata.google.internal/computeMetadata/v1/",
            FetchPolicy(allowed_hosts=frozenset({"*"})),
            "blocked",
        ),
    ],
)
async def test_unsafe_url_shapes_are_rejected_without_dns(
    monkeypatch: pytest.MonkeyPatch,
    url: str,
    policy: FetchPolicy,
    message: str,
) -> None:
    resolver_called = False

    def unexpected_resolver(host: str, *, port: int, timeout: float) -> PinnedTarget:
        nonlocal resolver_called
        resolver_called = True
        return _public_target(host, port=port)

    monkeypatch.setattr(
        "general_ludd.security.url_fetch.resolve_and_pin", unexpected_resolver
    )

    with pytest.raises(UnsafeURLError, match=message):
        await secure_fetch_async(url, policy=policy)

    assert resolver_called is False


@pytest.mark.asyncio
async def test_safe_relative_redirect_is_followed_and_revalidated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[str] = []
    pinned_ips: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(str(request.url))
        if request.url.path == "/old":
            return httpx.Response(301, headers={"Location": "/new"})
        return httpx.Response(200, content=b"done")

    monkeypatch.setattr(
        "general_ludd.security.url_fetch.resolve_and_pin",
        lambda host, *, port, timeout: _public_target(host, port=port),
    )
    _install_transport(monkeypatch, handler, pinned_ips)

    result = await secure_fetch_async(
        "https://example.com/old",
        policy=FetchPolicy(
            allowed_hosts=frozenset({"example.com"}), max_redirects=1
        ),
    )

    assert result.url == "https://example.com/new"
    assert result.content == b"done"
    assert requests == ["https://example.com/old", "https://example.com/new"]
    assert pinned_ips == ["93.184.216.34", "93.184.216.34"]


@pytest.mark.parametrize(
    "kwargs",
    [
        {"allowed_hosts": frozenset(), "allowed_schemes": frozenset({"https"})},
        {"allowed_hosts": frozenset({"example.com"}), "allowed_schemes": frozenset()},
        {"allowed_hosts": frozenset({"example.com"}), "max_bytes": -1},
        {"allowed_hosts": frozenset({"example.com"}), "timeout_seconds": 0},
        {"allowed_hosts": frozenset({"example.com"}), "dns_timeout_seconds": 0},
        {"allowed_hosts": frozenset({"example.com"}), "max_redirects": -1},
        {"allowed_hosts": frozenset({"bad/host"})},
        {"allowed_hosts": frozenset({"\ud800.example"})},
    ],
)
def test_invalid_policy_fails_closed(kwargs: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        FetchPolicy(**kwargs)


@pytest.mark.asyncio
@pytest.mark.parametrize("url", ["https://[::1", "https:/missing-host"])
async def test_malformed_or_non_absolute_url_is_rejected(url: str) -> None:
    with pytest.raises(UnsafeURLError):
        await secure_fetch_async(
            url,
            policy=FetchPolicy(allowed_hosts=frozenset({"*"})),
        )


@pytest.mark.asyncio
async def test_wildcard_host_matches_only_a_subdomain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pinned_ips: list[str] = []
    monkeypatch.setattr(
        "general_ludd.security.url_fetch.resolve_and_pin",
        lambda host, *, port, timeout: _public_target(host, port=port),
    )
    _install_transport(
        monkeypatch,
        lambda request: httpx.Response(200, content=b"ok"),
        pinned_ips,
    )

    result = await secure_fetch_async(
        "https://api.example.com/",
        policy=FetchPolicy(allowed_hosts=frozenset({"*.example.com"})),
    )
    assert result.content == b"ok"

    with pytest.raises(UnsafeURLError, match="allowlist"):
        await secure_fetch_async(
            "https://example.com/",
            policy=FetchPolicy(allowed_hosts=frozenset({"*.example.com"})),
        )


@pytest.mark.asyncio
async def test_stream_without_content_length_is_still_size_bounded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Chunks(httpx.AsyncByteStream):
        async def __aiter__(self) -> AsyncIterator[bytes]:
            yield b"1234"
            yield b"5678"

    pinned_ips: list[str] = []
    monkeypatch.setattr(
        "general_ludd.security.url_fetch.resolve_and_pin",
        lambda host, *, port, timeout: _public_target(host, port=port),
    )
    _install_transport(
        monkeypatch,
        lambda request: httpx.Response(200, stream=Chunks()),
        pinned_ips,
    )

    with pytest.raises(ResponseTooLarge, match="6-byte"):
        await secure_fetch_async(
            "https://stream.example/",
            policy=FetchPolicy(
                allowed_hosts=frozenset({"stream.example"}), max_bytes=6
            ),
        )


@pytest.mark.asyncio
async def test_cross_origin_303_drops_credentials_and_post_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str, bool, bytes]] = []
    pinned_ips: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(
            (
                request.method,
                request.url.host,
                "authorization" in request.headers,
                request.content,
            )
        )
        if request.url.host == "first.example":
            return httpx.Response(
                303, headers={"Location": "https://second.example/done"}
            )
        return httpx.Response(200, content=b"ok")

    monkeypatch.setattr(
        "general_ludd.security.url_fetch.resolve_and_pin",
        lambda host, *, port, timeout: _public_target(host, port=port),
    )
    _install_transport(monkeypatch, handler, pinned_ips)

    result = await secure_fetch_async(
        "https://first.example/start",
        method="POST",
        headers={"Authorization": "Bearer secret", "Content-Type": "text/plain"},
        content=b"payload",
        policy=FetchPolicy(
            allowed_hosts=frozenset({"first.example", "second.example"})
        ),
    )

    assert result.content == b"ok"
    assert calls == [
        ("POST", "first.example", True, b"payload"),
        ("GET", "second.example", False, b""),
    ]


@pytest.mark.asyncio
async def test_redirect_limit_and_invalid_method_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pinned_ips: list[str] = []
    monkeypatch.setattr(
        "general_ludd.security.url_fetch.resolve_and_pin",
        lambda host, *, port, timeout: _public_target(host, port=port),
    )
    _install_transport(
        monkeypatch,
        lambda request: httpx.Response(302, headers={"Location": "/again"}),
        pinned_ips,
    )

    with pytest.raises(RedirectLimitExceeded, match="0-redirect"):
        await secure_fetch_async(
            "https://loop.example/start",
            policy=FetchPolicy(
                allowed_hosts=frozenset({"loop.example"}), max_redirects=0
            ),
        )
    with pytest.raises(ValueError, match="method"):
        await secure_fetch_async(
            "https://loop.example/start",
            method="GET /internal",
            policy=FetchPolicy(allowed_hosts=frozenset({"loop.example"})),
        )


def test_synchronous_adapter_runs_the_same_policy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pinned_ips: list[str] = []
    monkeypatch.setattr(
        "general_ludd.security.url_fetch.resolve_and_pin",
        lambda host, *, port, timeout: _public_target(host, port=port),
    )
    _install_transport(
        monkeypatch,
        lambda request: httpx.Response(200, content=b"sync"),
        pinned_ips,
    )

    result = secure_fetch(
        "https://sync.example/",
        policy=FetchPolicy(allowed_hosts=frozenset({"sync.example"})),
    )
    assert result.content == b"sync"


@pytest.mark.asyncio
async def test_synchronous_adapter_refuses_a_running_event_loop() -> None:
    with pytest.raises(RuntimeError, match="event loop"):
        secure_fetch(
            "https://example.com/",
            policy=FetchPolicy(allowed_hosts=frozenset({"example.com"})),
        )
