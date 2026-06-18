"""Security tests for hooks.py — written before fixes (TDD red phase)."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from general_ludd.events.hooks import HookSystem


class TestCredentialRedaction:
    """A) Payload allowlist — no secret keys leak to webhook."""

    def test_secret_key_stripped_from_outgoing_body(self):
        """api_key in payload must not appear in the POST body."""
        captured = []

        def fake_post(url, **kwargs):
            captured.append(kwargs.get("json", {}))
            return MagicMock(status_code=200)

        hs = HookSystem()
        hs.register_webhook("evt", "http://example.com", retry_count=1)

        payload = {"api_key": "SEKRIT", "name": "ok", "type": "model_added"}  # pragma: allowlist secret
        with patch("general_ludd.events.hooks.httpx.post", side_effect=fake_post):
            hs.fire("evt", payload)

        assert len(captured) == 1
        sent_payload = captured[0].get("payload", {})
        assert "api_key" not in sent_payload
        assert "SEKRIT" not in str(sent_payload)
        assert sent_payload.get("name") == "ok"

    def test_token_stripped(self):
        captured = []
        def fake_post(url, **kwargs):
            captured.append(kwargs.get("json", {}))
            return MagicMock(status_code=200)
        hs = HookSystem()
        hs.register_webhook("evt2", "http://example.com", retry_count=1)
        payload = {"token": "abc123", "user_id": "u1"}
        with patch("general_ludd.events.hooks.httpx.post", side_effect=fake_post):
            hs.fire("evt2", payload)
        sent_payload = captured[0].get("payload", {})
        assert "token" not in sent_payload
        assert sent_payload.get("user_id") == "u1"

    def test_safe_keys_preserved(self):
        captured = []
        def fake_post(url, **kwargs):
            captured.append(kwargs.get("json", {}))
            return MagicMock(status_code=200)
        hs = HookSystem()
        hs.register_webhook("evt3", "http://example.com", retry_count=1)
        payload = {"name": "x", "type": "y", "id": "123", "timestamp": "t", "status": "ok"}
        with patch("general_ludd.events.hooks.httpx.post", side_effect=fake_post):
            hs.fire("evt3", payload)
        sent_payload = captured[0].get("payload", {})
        assert sent_payload == payload


class TestRetryCountClamp:
    """C) retry_count must be clamped to max 5."""

    def test_retry_count_999_clamped_to_5(self):
        """retry_count=999 must result in at most 5 attempts."""
        attempt_count = []

        def fake_post(url, **kwargs):
            attempt_count.append(1)
            raise httpx_error()

        import httpx
        def httpx_error():
            return httpx.ConnectError("fail")

        hs = HookSystem()
        hs.register_webhook("retryevt", "http://example.com", retry_count=999)

        with patch("general_ludd.events.hooks.httpx.post", side_effect=fake_post):
            hs.fire("retryevt", {"name": "x"})

        assert len(attempt_count) <= 5, f"Expected <=5 attempts, got {len(attempt_count)}"

    def test_retry_count_0_clamped_to_1(self):
        """retry_count=0 must result in at least 1 attempt."""
        attempt_count = []

        def fake_post(url, **kwargs):
            attempt_count.append(1)
            return MagicMock(status_code=200)

        hs = HookSystem()
        hs.register_webhook("retryevt2", "http://example.com", retry_count=0)

        with patch("general_ludd.events.hooks.httpx.post", side_effect=fake_post):
            hs.fire("retryevt2", {"name": "x"})

        assert len(attempt_count) >= 1


class TestNoBlockingCallInAsyncContext:
    """B) Blocking httpx.post must not be called directly inside a running event loop."""

    def test_blocking_post_not_called_when_loop_running(self):
        """When fire() is called from async context, httpx.post should go via run_in_executor."""
        blocking_calls = []

        MagicMock(return_value=MagicMock(status_code=200))

        def capture_blocking(url, **kwargs):
            blocking_calls.append(url)
            return MagicMock(status_code=200)

        hs = HookSystem()
        hs.register_webhook("asyncevt", "http://example.com", retry_count=1)

        mock_loop = MagicMock()
        mock_loop.run_in_executor = MagicMock(return_value=None)

        with (
            patch("general_ludd.events.hooks.httpx.post", side_effect=capture_blocking),
            patch("asyncio.get_running_loop", return_value=mock_loop),
        ):
            hs.fire("asyncevt", {"name": "safe"})

        # When a running loop exists, httpx.post must NOT be called directly
        assert len(blocking_calls) == 0, (
            f"httpx.post was called directly {len(blocking_calls)} time(s) "
            "inside a running event loop — this blocks the loop"
        )
        assert mock_loop.run_in_executor.called, "run_in_executor was not called"


class TestNestedRedaction:
    """D) Nested dict/list values containing secret keys must not reach the webhook."""

    def _fire_and_capture(self, payload: dict) -> dict:
        captured = []

        def fake_post(url, **kwargs):
            captured.append(kwargs.get("json", {}))
            return MagicMock(status_code=200)

        hs = HookSystem()
        hs.register_webhook("nestevt", "http://example.com", retry_count=1)
        with patch("general_ludd.events.hooks.httpx.post", side_effect=fake_post):
            hs.fire("nestevt", payload)
        assert len(captured) == 1
        return captured[0].get("payload", {})

    def test_nested_dict_secret_redacted(self):
        """Secret in a sub-dict must not appear in the outgoing payload."""
        payload = {"meta": {"api_key": "SEKRIT", "env": "prod"}}  # pragma: allowlist secret
        sent = self._fire_and_capture(payload)
        assert "SEKRIT" not in str(sent), "Nested api_key value leaked to webhook"
        # non-secret sibling key is preserved
        assert sent.get("meta", {}).get("env") == "prod"

    def test_nested_list_of_dicts_secret_redacted(self):
        """Secret in a list-of-dicts element must not appear in the outgoing payload."""
        payload = {"items": [{"token": "T_SECRET"}, {"name": "ok"}]}  # pragma: allowlist secret
        sent = self._fire_and_capture(payload)
        assert "T_SECRET" not in str(sent), "token value in list element leaked to webhook"
        # non-secret element key is preserved
        items = sent.get("items", [])
        assert any(item.get("name") == "ok" for item in items)

    def test_top_level_still_redacted(self):
        """Regression: top-level secret keys remain redacted after the recursion refactor."""
        payload = {"password": "toplevel_secret", "user": "alice"}  # pragma: allowlist secret
        sent = self._fire_and_capture(payload)
        assert "password" not in sent
        assert "toplevel_secret" not in str(sent)
        assert sent.get("user") == "alice"

    def test_three_levels_deep_redacted(self):
        """Secret 3 levels deep must be stripped."""
        payload = {"a": {"b": {"authorization": "bearer xyz123", "safe": "keep"}}}  # pragma: allowlist secret
        sent = self._fire_and_capture(payload)
        assert "xyz123" not in str(sent), "3-level nested authorization value leaked"
        # safe sibling preserved
        assert sent.get("a", {}).get("b", {}).get("safe") == "keep"
