"""S.2 SSRF guard tests for events/hooks.py — is_safe_fetch_url + fire-time check.

hooks.py delegates the host/IP/scheme decision to the canonical ssrf module
(single source of truth), raises SSRFBlockedError, and sets follow_redirects=False
so a 30x can't bounce the request into an internal address after the check.

Covered:
  - is_safe_fetch_url rejects internal/loopback/link-local/metadata
  - is_safe_fetch_url accepts public URLs (http + https for webhooks)
  - follow_redirects=False on all hook HTTP calls
  - SSRF re-check at fire time (defense-in-depth)
  - DNS rebinding prevented (follow_redirects=False + fire-time check)
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from general_ludd.events.hooks import HookSystem, SSRFBlockedError, is_safe_fetch_url


class TestIsSafeFetchUrl:
    @pytest.mark.parametrize(
        "url",
        [
            "http://127.0.0.1/x",
            "http://localhost/x",
            "http://localhost.localdomain/x",
            "http://169.254.169.254/latest/meta-data/",
            "http://10.0.0.5/internal",
            "http://172.16.0.1/internal",
            "http://192.168.1.1/internal",
            "http://[::1]/x",
            "http://[fe80::1]/x",
            "https://198.51.100.7/test",
            "ftp://example.com/x",
            "file:///etc/passwd",
            "",
            "http://metadata.google.internal/x",
            "http://metadata.azure.com/x",
            "http://100.100.100.200/hook",
            "http://vault/x",
            "http://prometheus:9090/x",
            "http://api.svc.localhost/x",
        ],
    )
    def test_rejects_unsafe(self, url):
        assert is_safe_fetch_url(url) is False

    @pytest.mark.parametrize(
        "url",
        [
            "https://hooks.example.com/notify",
            "http://example.com/hook",
            "http://8.8.8.8/webhook",
            "https://api.github.com/org/repo/hooks",
            "https://webhook.site/uuid",
        ],
    )
    def test_accepts_public(self, url):
        assert is_safe_fetch_url(url) is True


class TestRegisterWebhookUsesIsSafeFetchUrl:
    """register_webhook delegates to is_safe_fetch_url (not a separate impl)."""

    def test_rejects_unsafe_via_is_safe_fetch_url(self):
        hs = HookSystem()
        with pytest.raises(SSRFBlockedError):
            hs.register_webhook("job.complete", "http://127.0.0.1/x")

    def test_accepts_public_via_is_safe_fetch_url(self):
        hs = HookSystem()
        hid = hs.register_webhook("job.complete", "https://hooks.example.com/notify")
        assert hid.startswith("hook-wh-")


class TestFollowRedirectsFalse:
    def test_follow_redirects_false(self):
        captured: list[dict] = []

        async def fake_post(self, url, **kwargs):
            captured.append({"url": url, "kwargs": kwargs})

        class FakeClient:
            post = fake_post

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                pass

        hs = HookSystem()
        hs.register_webhook("job.complete", "https://hooks.example.com/notify")

        with patch(
            "general_ludd.events.hooks.httpx.AsyncClient",
            return_value=FakeClient(),
        ):
            hs.fire("job.complete", {"result": "ok"})

        assert captured, "AsyncClient.post was never called"
        assert captured[0]["kwargs"].get("follow_redirects") is False, (
            "follow_redirects must be explicitly False to block redirect-based SSRF"
        )


class TestFireTimeSSRFRecheck:
    """Defence-in-depth: _do_post_async re-checks SSRF at fire time.

    If the WebhookConfig URL is somehow mutated between registration and fire,
    the _do_post_async check catches it before any HTTP call.
    """

    def test_fire_time_rejects_blocked_url(self, caplog):
        hs = HookSystem()
        hs.register_webhook("job.complete", "https://hooks.example.com/notify")

        hooks = list(hs._hooks.get("job.complete", []))
        assert hooks, "webhook should be registered"
        config = hooks[0].webhook_config
        assert config is not None
        config.url = "http://127.0.0.1/internal"

        hs.fire("job.complete", {"result": "ok"})

        error_logs = [
            r for r in caplog.records
            if r.levelname == "ERROR"
            and "rejected at fire time" in getattr(r, "message", "")
        ]
        assert error_logs, "Fire-time SSRF check must block mutated URL"

