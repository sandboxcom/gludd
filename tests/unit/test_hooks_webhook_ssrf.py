"""Tests for SSRF guard, redirect blocking, and retry clamp in _fire_webhook."""
from __future__ import annotations

import pytest

from general_ludd.events.hooks import MAX_WEBHOOK_RETRIES, HookSystem


class TestWebhookSsrfBlock:
    """Webhook to a link-local/metadata address must raise ValueError immediately."""

    def test_metadata_url_is_blocked(self) -> None:
        hs = HookSystem()
        hs.register_webhook("test.event", url="http://169.254.169.254/")
        with pytest.raises(ValueError, match="blocked by security policy"):
            hs.fire("test.event", {"data": "x"})

    def test_loopback_url_is_blocked(self) -> None:
        hs = HookSystem()
        hs.register_webhook("test.event", url="http://127.0.0.1/secret")
        with pytest.raises(ValueError, match="blocked by security policy"):
            hs.fire("test.event", {})

    def test_private_rfc1918_is_blocked(self) -> None:
        hs = HookSystem()
        hs.register_webhook("test.event", url="http://192.168.1.1/hook")
        with pytest.raises(ValueError, match="blocked by security policy"):
            hs.fire("test.event", {})


class TestWebhookRetryClamp:
    """retry_count beyond MAX_WEBHOOK_RETRIES must be clamped to MAX_WEBHOOK_RETRIES."""

    def test_excessive_retry_count_is_clamped(self, monkeypatch: pytest.MonkeyPatch) -> None:
        call_count = 0

        def fake_post(*args: object, **kwargs: object) -> None:
            nonlocal call_count
            call_count += 1
            raise OSError("simulated network failure")

        import httpx
        monkeypatch.setattr(httpx, "post", fake_post)

        hs = HookSystem()
        # Use a URL that passes the SSRF guard (https, public host literal that
        # is_safe_fetch_url allows).  We monkeypatch httpx.post so no real I/O
        # occurs; the guard only inspects the URL string.
        hs.register_webhook(
            "test.event",
            url="https://example.com/webhook",
            retry_count=10_000,
        )

        with pytest.raises(OSError, match="simulated network failure"):
            hs.fire("test.event", {"k": "v"})

        assert call_count <= MAX_WEBHOOK_RETRIES, (
            f"Expected at most {MAX_WEBHOOK_RETRIES} attempts, got {call_count}"
        )

    def test_retry_count_zero_still_attempts_once(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """min(max(1, retry_count), MAX) means retry_count=0 still fires once."""
        call_count = 0

        def fake_post(*args: object, **kwargs: object) -> None:
            nonlocal call_count
            call_count += 1
            raise OSError("fail")

        import httpx
        monkeypatch.setattr(httpx, "post", fake_post)

        hs = HookSystem()
        hs.register_webhook(
            "test.event",
            url="https://example.com/webhook",
            retry_count=0,
        )

        with pytest.raises(OSError):
            hs.fire("test.event", {})

        assert call_count == 1
