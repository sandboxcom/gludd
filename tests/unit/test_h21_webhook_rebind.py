"""TDD tests for H.21 — H-WEBHOOK-DELIVERY-REBIND.

Webhook URLs SSRF-checked only at registration (literal host check, no DNS),
never re-checked with DNS resolution at delivery. This allows DNS rebinding:
evil.com → 1.2.3.4 at register time, evil.com → 127.0.0.1 at fire time.

The fix: _fire_webhook must call the DNS-resolving SSRF guard
(resolve_and_pin / resolved_host_is_blocked) at delivery time so a hostname
that re-binds to an internal IP after registration is caught.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from general_ludd.events.hooks import HookSystem, SSRFBlockedError
from general_ludd.security.ssrf import SSRFError, resolve_and_pin


class TestWebhookUrlCheckedAtRegistration:
    """Literal-host SSRF check at registration time (existing behaviour, pin)."""

    def test_registration_rejects_internal_ip(self) -> None:
        hs = HookSystem()
        with pytest.raises(SSRFBlockedError):
            hs.register_webhook("test.event", "http://127.0.0.1/notify")

    def test_registration_rejects_metadata_ip(self) -> None:
        hs = HookSystem()
        with pytest.raises(SSRFBlockedError):
            hs.register_webhook("test.event", "http://169.254.169.254/webhook")

    def test_registration_rejects_localhost(self) -> None:
        hs = HookSystem()
        with pytest.raises(SSRFBlockedError):
            hs.register_webhook("test.event", "http://localhost/webhook")

    def test_registration_accepts_public_hostname(self) -> None:
        hs = HookSystem()
        hid = hs.register_webhook("test.event", "https://hooks.example.com/notify")
        assert hid.startswith("hook-wh-")

    def test_registration_accepts_public_ip(self) -> None:
        hs = HookSystem()
        hid = hs.register_webhook("test.event", "http://8.8.8.8/webhook")
        assert hid.startswith("hook-wh-")


class TestWebhookUrlRecheckedAtDelivery:
    """DNS-resolving SSRF check at delivery time (new behaviour for H.21)."""

    def test_fire_resolves_hostname_and_checks_blocked_ips(self) -> None:
        """At fire time the webhook URL host must be DNS-resolved and every
        resolved IP vetted against the SSRF blocklist."""
        hs = HookSystem()
        hs.register_webhook("test.event", "https://hooks.example.com/notify")

        resolve_calls: list[str] = []

        def fake_resolve_and_pin(host, *, port=443, timeout=2.0):
            resolve_calls.append(host)
            from general_ludd.security.ssrf import PinnedTarget
            return PinnedTarget(host=host, ip="203.0.113.1", port=port)

        # Patch httpx to prevent real HTTP call
        async def fake_post(self, url, **kwargs):
            pass

        class FakeResponse:
            status_code = 200
            def raise_for_status(self):
                pass

        async def fake_client_post(self, url, **kwargs):
            return FakeResponse()

        with patch("general_ludd.events.hooks.resolve_and_pin", fake_resolve_and_pin), \
             patch("httpx.AsyncClient.post", fake_client_post):
            hs.fire("test.event", {"result": "ok"})

        assert resolve_calls, (
            "fire() must call resolve_and_pin on the webhook URL host to re-check "
            "for DNS rebinding between registration and delivery"
        )
        assert "hooks.example.com" in resolve_calls


class TestDnsRebindingBetweenRegistrationAndDelivery:
    """Hostname rebinds to a blocked IP after registration — must be caught."""

    def test_rebind_to_loopback_blocked_at_fire(self) -> None:
        """evil.com resolves publicly at registration, rebinds to 127.0.0.1 after.
        The fire-time DNS-resolving check must block delivery."""
        hs = HookSystem()
        hs.register_webhook("test.event", "https://evil.com/webhook")

        def fake_resolve_and_pin_malicious(host, *, port=443, timeout=2.0):
            raise SSRFError(
                f"host {host!r} resolves to blocked address 127.0.0.1 (denied for SSRF)"
            )

        async def fake_post(self, url, **kwargs):
            raise AssertionError("HTTP POST must never happen for a blocked URL")

        with patch("general_ludd.events.hooks.resolve_and_pin", fake_resolve_and_pin_malicious), \
             patch("httpx.AsyncClient.post", fake_post):
            result = hs.fire("test.event", {"result": "ok"})

        assert result == 0, (
            f"fire() should report 0 successful deliveries when webhook is blocked. "
            f"Got count={result}"
        )

    def test_rebind_to_rfc1918_blocked_at_fire(self) -> None:
        """evil.com resolves publicly at registration, rebinds to 10.0.0.5 after."""
        hs = HookSystem()
        hs.register_webhook("test.event", "https://evil.com/hook")

        def fake_resolve_and_pin_malicious(host, *, port=443, timeout=2.0):
            raise SSRFError(
                f"host {host!r} resolves to blocked address 10.0.0.5 (denied for SSRF)"
            )

        async def fake_post(self, url, **kwargs):
            raise AssertionError("HTTP POST must never happen for a blocked URL")

        with patch("general_ludd.events.hooks.resolve_and_pin", fake_resolve_and_pin_malicious):
            result = hs.fire("test.event", {"data": "secret"})

        assert result == 0

    def test_rebind_to_link_local_blocked_at_fire(self) -> None:
        """evil.com rebinds to 169.254.169.254 (cloud metadata) — must be blocked."""
        hs = HookSystem()
        hs.register_webhook("test.event", "https://evil.com/hook")

        def fake_resolve_and_pin_metadata(host, *, port=443, timeout=2.0):
            raise SSRFError(
                f"host {host!r} resolves to blocked address 169.254.169.254 (denied for SSRF)"
            )

        with patch("general_ludd.events.hooks.resolve_and_pin", fake_resolve_and_pin_metadata):
            result = hs.fire("test.event", {"data": "secret"})

        assert result == 0


class TestPublicUrlStillDeliversAfterRecheck:
    """After the fix, legitimate public webhooks must still deliver."""

    def test_public_hostname_still_delivers(self) -> None:
        hs = HookSystem()
        hs.register_webhook("test.event", "https://hooks.example.com/notify")

        posted_urls: list[str] = []

        class FakeResponse:
            status_code = 200
            def raise_for_status(self):
                pass

        async def fake_post(self, url, **kwargs):
            posted_urls.append(url)
            return FakeResponse()

        from general_ludd.security.ssrf import PinnedTarget

        def fake_resolve_and_pin(host, *, port=443, timeout=2.0):
            return PinnedTarget(host=host, ip="203.0.113.1", port=port)

        with patch("general_ludd.events.hooks.resolve_and_pin", fake_resolve_and_pin), \
             patch("httpx.AsyncClient.post", fake_post):
            result = hs.fire("test.event", {"result": "ok"})

        assert result >= 1
        assert posted_urls, "Public webhook must still be delivered after re-check"
        assert "https://hooks.example.com/notify" in posted_urls

    def test_public_ip_url_still_delivers(self) -> None:
        hs = HookSystem()
        hs.register_webhook("test.event", "http://8.8.8.8/webhook")

        posted_urls: list[str] = []

        class FakeResponse:
            status_code = 200
            def raise_for_status(self):
                pass

        async def fake_post(self, url, **kwargs):
            posted_urls.append(url)
            return FakeResponse()

        with patch("httpx.AsyncClient.post", fake_post):
            result = hs.fire("test.event", {"data": "ok"})

        assert result >= 1
        assert posted_urls, "Public IP webhook must still deliver"


class TestSequenceOfEvents:
    """Multiple webhooks — some safe, some re-bound — at delivery time."""

    def test_safe_and_malicious_mixed(self) -> None:
        """One hook re-binds to internal, the other stays safe. The safe one
        must still deliver after the malicious one is blocked."""
        hs = HookSystem()
        hs.register_webhook("test.event", "https://safe.example.com/hook")
        hs.register_webhook("test.event", "https://evil.com/hook")

        posted_urls: list[str] = []

        class FakeResponse:
            status_code = 200
            def raise_for_status(self):
                pass

        from general_ludd.security.ssrf import PinnedTarget

        def selective_resolve(host, *, port=443, timeout=2.0):
            if host == "evil.com":
                raise SSRFError(
                    f"host {host!r} resolves to blocked address 127.0.0.1"
                )
            return PinnedTarget(host=host, ip="203.0.113.1", port=port)

        async def fake_post(self, url, **kwargs):
            posted_urls.append(url)
            return FakeResponse()

        with patch("general_ludd.events.hooks.resolve_and_pin", selective_resolve), \
             patch("httpx.AsyncClient.post", fake_post):
            hs.fire("test.event", {"data": "ok"})

        safe_posts = [u for u in posted_urls if "safe.example.com" in u]
        evil_posts = [u for u in posted_urls if "evil.com" in u]
        assert safe_posts, "Safe webhook must still deliver after re-bind check"
        assert not evil_posts, "Re-bound webhook must NOT deliver"


class TestResolveAndPinIntegration:
    """Verify resolve_and_pin itself blocks internal IPs as expected."""

    def test_loopback_rejected(self) -> None:
        with pytest.raises(SSRFError):
            resolve_and_pin("127.0.0.1")

    def test_localhost_name_blocked_at_literal_check(self) -> None:
        with pytest.raises(SSRFError):
            resolve_and_pin("localhost")

    def test_metadata_ip_blocked(self) -> None:
        with pytest.raises(SSRFError):
            resolve_and_pin("169.254.169.254")

    def test_public_ip_passes(self) -> None:
        target = resolve_and_pin("8.8.8.8")
        assert target.ip == "8.8.8.8"


class TestLiteralIpPassthrough:
    """Literal IP URLs at registration pass literal check, and resolve_and_pin
    at fire time acts as the re-check. The fix must handle this correctly."""

    def test_literal_public_ip_fire_passes_resolve(self) -> None:
        hs = HookSystem()
        hs.register_webhook("test.event", "http://8.8.8.8/webhook")

        resolve_calls: list[str] = []

        def track_resolve(host, *, port=443, timeout=2.0):
            resolve_calls.append(host)
            from general_ludd.security.ssrf import PinnedTarget
            return PinnedTarget(host=host, ip=host, port=port)

        async def fake_post(self, url, **kwargs):
            pass

        class FakeResponse:
            status_code = 200
            def raise_for_status(self):
                pass

        async def fake_client_post(self, url, **kwargs):
            return FakeResponse()

        with patch("general_ludd.events.hooks.resolve_and_pin", track_resolve), \
             patch("httpx.AsyncClient.post", fake_client_post):
            hs.fire("test.event", {"data": "ok"})

        assert "8.8.8.8" in resolve_calls
