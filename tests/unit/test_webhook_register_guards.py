"""Unit tests for webhook registration guards.

Covers:
  - SSRF guard: rejects http://, loopback IPs, RFC-1918, and cloud-metadata URLs.
  - Header injection guard: rejects forbidden header names; caps key/value length.
  - Happy path: valid https URL + benign headers succeeds.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from general_ludd.daemon import create_daemon_app

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def app(monkeypatch):
    # /admin/hooks is an authenticated admin path. The daemon is fail-closed
    # by default (503) when GLUDD_AUTH_PSK is unset, which would short-circuit the
    # request before the SSRF/header validators run. Use the documented dev
    # opt-out so the request reaches the Pydantic guards under test. Mirrors
    # the pattern in tests/integration/test_g9_plan_critique_wiring.py et al.
    monkeypatch.setenv("GLUDD_ALLOW_NO_AUTH", "1")
    return create_daemon_app(tick_interval=0.01)


@pytest.fixture()
def transport(app):
    return ASGITransport(app=app)


def _mock_hooks(hook_id: str = "hook-abc"):
    """Return a mock subsystem dict that satisfies the /admin/hooks handler."""
    mock_hook_sys = MagicMock()
    mock_hook_sys.register_webhook.return_value = hook_id
    return {"hooks": mock_hook_sys}


# ---------------------------------------------------------------------------
# SSRF guard tests
# ---------------------------------------------------------------------------

class TestSSRFGuard:
    @pytest.mark.asyncio
    async def test_rejects_http_scheme(self, transport):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/admin/hooks",
                json={
                    "event_name": "deploy.started",
                    "url": "http://example.com/hook",
                },
            )
        assert resp.status_code == 422
        body = resp.json()
        assert "url" in str(body).lower() or "https" in str(body).lower()

    @pytest.mark.asyncio
    async def test_rejects_metadata_ip(self, transport):
        """169.254.169.254 is the AWS/GCP/Azure instance metadata endpoint."""
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/admin/hooks",
                json={
                    "event_name": "deploy.started",
                    "url": "http://169.254.169.254/latest/meta-data/",
                },
            )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_rejects_loopback_ip(self, transport):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/admin/hooks",
                json={
                    "event_name": "test",
                    "url": "https://127.0.0.1/hook",
                },
            )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_rejects_rfc1918_ip(self, transport):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/admin/hooks",
                json={
                    "event_name": "test",
                    "url": "https://10.0.0.1/hook",
                },
            )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_rejects_localhost_name(self, transport):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/admin/hooks",
                json={
                    "event_name": "test",
                    "url": "https://localhost/hook",
                },
            )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_rejects_ipv6_loopback(self, transport):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/admin/hooks",
                json={
                    "event_name": "test",
                    "url": "https://[::1]/hook",
                },
            )
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Header injection guard tests
# ---------------------------------------------------------------------------

class TestHeaderInjectionGuard:
    @pytest.mark.asyncio
    async def test_rejects_authorization_header(self, transport):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/admin/hooks",
                json={
                    "event_name": "test",
                    "url": "https://hooks.example.com/recv",
                    "headers": {"Authorization": "Bearer secret"},
                },
            )
        assert resp.status_code == 422
        body = resp.json()
        assert "authorization" in str(body).lower() or "headers" in str(body).lower()

    @pytest.mark.asyncio
    async def test_rejects_host_header(self, transport):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/admin/hooks",
                json={
                    "event_name": "test",
                    "url": "https://hooks.example.com/recv",
                    "headers": {"Host": "evil.com"},
                },
            )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_rejects_content_length_header(self, transport):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/admin/hooks",
                json={
                    "event_name": "test",
                    "url": "https://hooks.example.com/recv",
                    "headers": {"content-length": "999"},
                },
            )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_rejects_transfer_encoding_header(self, transport):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/admin/hooks",
                json={
                    "event_name": "test",
                    "url": "https://hooks.example.com/recv",
                    "headers": {"Transfer-Encoding": "chunked"},
                },
            )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_rejects_cookie_header(self, transport):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/admin/hooks",
                json={
                    "event_name": "test",
                    "url": "https://hooks.example.com/recv",
                    "headers": {"Cookie": "session=abc"},
                },
            )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_rejects_oversized_key(self, transport):
        long_key = "X-Custom-" + "A" * 1020
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/admin/hooks",
                json={
                    "event_name": "test",
                    "url": "https://hooks.example.com/recv",
                    "headers": {long_key: "value"},
                },
            )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_rejects_oversized_value(self, transport):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/admin/hooks",
                json={
                    "event_name": "test",
                    "url": "https://hooks.example.com/recv",
                    "headers": {"X-Custom": "V" * 1025},
                },
            )
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Happy-path test
# ---------------------------------------------------------------------------

class TestHappyPath:
    @pytest.mark.asyncio
    async def test_valid_https_url_and_benign_headers_succeeds(self, transport):
        """A safe https URL with benign custom headers should register successfully."""
        mock_subsys = _mock_hooks("hook-xyz")
        with patch(
            "general_ludd.routers.reload._get_or_create_subsystems",
            return_value=mock_subsys,
        ):
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post(
                    "/admin/hooks",
                    json={
                        "event_name": "deploy.completed",
                        "url": "https://hooks.example.com/recv",
                        "headers": {
                            "X-Signature-Secret": "abc123",  # pragma: allowlist secret
                            "X-Source": "gludd",
                        },
                        "retry_count": 3,
                        "timeout_seconds": 15,
                    },
                )
        assert resp.status_code == 200
        data = resp.json()
        assert data["hook_id"] == "hook-xyz"
        assert data["event_name"] == "deploy.completed"

    @pytest.mark.asyncio
    async def test_valid_https_url_no_headers_succeeds(self, transport):
        """A safe https URL with no headers at all should register successfully."""
        mock_subsys = _mock_hooks("hook-no-headers")
        with patch(
            "general_ludd.routers.reload._get_or_create_subsystems",
            return_value=mock_subsys,
        ):
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post(
                    "/admin/hooks",
                    json={
                        "event_name": "build.failed",
                        "url": "https://notify.example.org/webhook",
                    },
                )
        assert resp.status_code == 200
        data = resp.json()
        assert data["hook_id"] == "hook-no-headers"
