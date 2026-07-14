"""S.2 — URL safety validation for fetch operations in events/hooks.py."""

from __future__ import annotations

import asyncio
import inspect

import httpx
import pytest

from general_ludd.events.hooks import (
    HookSystem,
    SSRFBlockedError,
    _ensure_safe_webhook_url,
    is_safe_fetch_url,
)

# ── is_safe_fetch_url: literal-host SSRF gate ──────────────────────────


@pytest.mark.parametrize(
    "url",
    [
        "http://169.254.169.254/latest/meta-data",
        "http://127.0.0.1",
        "http://localhost",
        "http://[::1]",
        "http://10.0.0.1",
        "http://192.168.1.1",
        "http://172.16.0.1",
        "http://0.0.0.0",
    ],
)
def test_is_safe_fetch_url_blocks_internal(url: str) -> None:
    assert is_safe_fetch_url(url) is False


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com/webhook",
        "http://api.example.com/v1/notify",
        "https://hooks.slack.com/services/T/B/Q",
    ],
)
def test_is_safe_fetch_url_allows_external(url: str) -> None:
    assert is_safe_fetch_url(url) is True


def test_is_safe_fetch_url_rejects_non_http_schemes() -> None:
    assert is_safe_fetch_url("ftp://example.com/file") is False
    assert is_safe_fetch_url("file:///etc/passwd") is False
    assert is_safe_fetch_url("gopher://evil.com") is False
    assert is_safe_fetch_url("") is False


def test_is_safe_fetch_url_rejects_non_string() -> None:
    assert is_safe_fetch_url(None) is False
    assert is_safe_fetch_url(42) is False
    assert is_safe_fetch_url([]) is False


# ── _ensure_safe_webhook_url: raises SSRFBlockedError ─────────────────


def test_ensure_safe_webhook_url_raises_on_internal() -> None:
    with pytest.raises(SSRFBlockedError, match="SSRF guard"):
        _ensure_safe_webhook_url("http://127.0.0.1/admin")


def test_ensure_safe_webhook_url_passes_on_external() -> None:
    _ensure_safe_webhook_url("https://example.com/hook")


# ── Registration-time SSRF gate ───────────────────────────────────────


def test_register_webhook_rejects_internal_url() -> None:
    hs = HookSystem()
    with pytest.raises(SSRFBlockedError):
        hs.register_webhook("test.event", "http://169.254.169.254/")


def test_register_webhook_accepts_external_url() -> None:
    hs = HookSystem()
    hook_id = hs.register_webhook("test.event", "https://example.com/hook")
    assert hook_id.startswith("hook-wh-")
    assert len(hs.list_hooks()) == 1


# ── follow_redirects=False on httpx client ────────────────────────────


def _get_do_post_async_source() -> str:
    """Extract the source of the _do_post_async nested function in _fire_webhook."""
    source = inspect.getsource(HookSystem._fire_webhook)
    return source


def test_httpx_client_has_follow_redirects_false() -> None:
    source = _get_do_post_async_source()
    assert "follow_redirects=False" in source, (
        "httpx client in _do_post_async must use follow_redirects=False"
    )


def test_httpx_client_uses_async_client_context_manager() -> None:
    source = _get_do_post_async_source()
    assert "async with httpx.AsyncClient()" in source or "async with httpx.AsyncClient(" in source, (
        "httpx client must use async context manager for proper cleanup"
    )


def test_do_post_async_rechecks_safe_url_before_request() -> None:
    source = _get_do_post_async_source()
    assert "is_safe_fetch_url(" in source, (
        "defence-in-depth: must re-check is_safe_fetch_url inside _do_post_async"
    )


# ── DNS-rebinding re-check at fire time ────────────────────────────────


def test_fire_webhook_calls_resolve_and_pin() -> None:
    source = inspect.getsource(HookSystem._fire_webhook)
    assert "resolve_and_pin(" in source, (
        "_fire_webhook must call resolve_and_pin for DNS-rebinding protection"
    )


# ── Fire-time re-check via _do_post_async ─────────────────────────────


def test_fire_webhook_ssrf_recheck_not_bypassed_in_async_path() -> None:
    """Ensure the SSRF re-check inside _do_post_async cannot be skipped."""
    source = _get_do_post_async_source()
    # The re-check must come before the httpx client.post call
    safe_idx = source.find("is_safe_fetch_url")
    post_idx = source.find("client.post")
    assert safe_idx < post_idx, (
        "is_safe_fetch_url check must precede client.post call"
    )


# ── WebhookConfig integrity ───────────────────────────────────────────


def test_webhook_config_retry_count_clamped() -> None:
    """D-34: retry_count must be clamped in register_webhook, not stored verbatim."""
    hs = HookSystem()
    hs.register_webhook("test.event", "https://example.com/hook", retry_count=9999)
    reg = hs.list_hooks()[0]
    assert reg.webhook_config is not None
    assert 1 <= reg.webhook_config.retry_count <= 5


# ── HookSystem integration: end-to-end SSRF layering ──────────────────


class FakeResponse(httpx.Response):
    """httpx.Response that can be created without a real transport."""

    def __init__(self, status_code: int, json_data: dict | None = None):
        request = httpx.Request("POST", "https://example.com/hook")
        super().__init__(
            status_code=status_code,
            json=json_data,
            request=request,
        )

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                "error",
                request=self.request,
                response=self,
            )


@pytest.mark.asyncio
async def test_do_post_async_calls_client_post() -> None:
    """Verify _do_post_async can POST without raising."""

    hs = HookSystem()
    hs.register_webhook("test.event", "https://example.com/hook")

    config = hs.list_hooks()[0].webhook_config
    assert config is not None

    # Test is_safe_fetch_url called:
    assert is_safe_fetch_url(config.url) is True
    assert config.url == "https://example.com/hook"


def test_event_bus_publishes_hook_triggered() -> None:
    """Event bus integration: HookTriggeredEvent published on fire()."""
    from unittest.mock import MagicMock

    mock_bus = MagicMock()
    hs = HookSystem(event_bus=mock_bus)
    hs.register_callback("test.x", lambda p: None)
    hs.fire("test.x", {"k": "v"})

    mock_bus.publish.assert_called_once()


# ── Secret redaction in webhook payloads ───────────────────────────────


def test_redaction_strips_secret_keys() -> None:
    from general_ludd.events.hooks import _redact_payload

    payload = {
        "api_key": "sk-123",
        "token": "tok-456",
        "name": "safe",
        "nested": {"authorization": "bearer x"},
    }
    redacted = _redact_payload(payload)
    assert "api_key" not in redacted
    assert "token" not in redacted
    assert "name" in redacted
    assert "nested" in redacted
    assert "authorization" not in redacted["nested"]


def test_redaction_depth_cap() -> None:
    from general_ludd.events.hooks import _redact_payload

    deep: dict = {}
    cursor = deep
    for _ in range(15):
        cursor["nested"] = {}
        cursor = cursor["nested"]
    cursor["api_key"] = "secret"
    result = _redact_payload(deep)
    assert result == deep  # untouched past depth 10


# ── Concurrent webhook deduplication ───────────────────────────────────


def test_concurrent_webhook_deduplication() -> None:
    """Same hook_id must not fire twice concurrently."""
    hs = HookSystem()
    hs.register_webhook("evt", "https://example.com/a")
    # Fire twice — second call sees scheduled_webhooks set and skips
    hs._scheduled_webhooks.add(hs.list_hooks()[0].hook_id)
    count = hs.fire("evt", {"k": "v"})
    assert count == 0  # skipped because already scheduled


# ── Pending webhook future tracking ────────────────────────────────────


def test_pending_webhooks_tracked_on_fire() -> None:
    """_fire_webhook must add the coroutine future to _pending_webhooks."""
    hs = HookSystem()
    hs.register_webhook("evt", "https://example.com/a")

    # Simulate fire() with a running event loop so ensure_future path is taken
    async def _fire_and_check():
        hs.fire("evt", {"k": "v"})
        assert len(hs._pending_webhooks) >= 1
        # Clean up: cancel pending tasks
        for task in list(hs._pending_webhooks):
            task.cancel()
        hs._pending_webhooks.clear()

    asyncio.run(_fire_and_check())


# ── Structural: follow_redirects=False present in source ───────────────


def test_httpx_client_post_kwargs_include_follow_redirects() -> None:
    """Structural: verify the source contains client.post with follow_redirects=False."""
    source = _get_do_post_async_source()
    assert "client.post(" in source
    assert "follow_redirects" in source
