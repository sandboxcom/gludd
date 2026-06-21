"""SSRF guard tests for HookSystem webhook registration and firing.

HookSystem.register_webhook takes a caller-supplied URL. Without a guard it can
be pointed at internal/loopback/link-local/metadata addresses (SSRF), and
without follow_redirects=False a public URL can 30x-redirect into one. The guard
delegates the host/IP/scheme decision to the canonical
general_ludd.security.ssrf predicate (single source of truth) and raises
SSRFBlockedError; these tests pin both defences at the register_webhook seam.
"""
from __future__ import annotations

import httpx
import pytest

from general_ludd.events.hooks import HookSystem, SSRFBlockedError


class TestRegisterWebhookSSRF:
    @pytest.mark.parametrize(
        "url",
        [
            "http://127.0.0.1/x",  # loopback
            "http://localhost/x",  # loopback name
            "http://169.254.169.254/latest/meta-data/",  # cloud metadata / link-local
            "http://10.0.0.5/internal",  # RFC-1918
            "http://172.16.0.1/internal",
            "http://192.168.1.1/internal",
            "http://[::1]/x",  # IPv6 loopback
            "http://[fe80::1]/x",  # IPv6 link-local
            "https://198.51.100.7/test",  # TEST-NET-2 (non-global / documentation)
            "ftp://example.com/x",  # disallowed scheme
            "file:///etc/passwd",  # disallowed scheme
            "",  # empty
        ],
    )
    def test_rejects_unsafe(self, url):
        hs = HookSystem()
        with pytest.raises(SSRFBlockedError):
            hs.register_webhook("job.complete", url)

    @pytest.mark.parametrize(
        "url",
        [
            "https://hooks.example.com/notify",  # public hostname
            "http://8.8.8.8/webhook",  # public IP, http allowed for webhooks
            "http://example.com/hook",  # public hostname, http
        ],
    )
    def test_accepts_public(self, url):
        hs = HookSystem()
        hid = hs.register_webhook("job.complete", url)
        assert hid.startswith("hook-wh-")


class TestFireWebhookNoRedirect:
    def test_follow_redirects_false(self, monkeypatch):
        """_fire_webhook must pass follow_redirects=False so a 30x can't bounce
        the request into an internal address after the registration-time check."""
        captured: dict = {}

        def fake_post(url, **kwargs):
            captured["url"] = url
            captured["kwargs"] = kwargs

            class _Resp:
                status_code = 200

            return _Resp()

        monkeypatch.setattr(httpx, "post", fake_post)

        hs = HookSystem()
        hs.register_webhook("job.complete", "https://hooks.example.com/notify")
        hs.fire("job.complete", {"result": "ok"})

        assert captured, "httpx.post was never called"
        assert captured["kwargs"].get("follow_redirects") is False, (
            "follow_redirects must be explicitly False to block redirect-based SSRF"
        )
