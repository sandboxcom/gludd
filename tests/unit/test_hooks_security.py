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


# ---------------------------------------------------------------------------
# D-07: Async dispatch does not block the event loop
# ---------------------------------------------------------------------------

class TestAsyncWebhookNonBlocking:
    """D-07 — _fire_webhook called from inside a running loop must not stall it.

    A slow webhook (e.g. 0.5 s) dispatched via fire() from an async context
    must not prevent a concurrent coroutine from completing first.  The slow
    network call must be offloaded via run_in_executor so the loop stays free.
    """

    def test_slow_webhook_does_not_stall_concurrent_task(self):
        """Slow webhook dispatched in async context must not block the event loop.

        Strategy: patch asyncio.get_running_loop() to return a spy object that
        records run_in_executor calls.  Then assert the executor was used (not
        a direct blocking call), so a concurrent task could have progressed.
        """
        executor_calls: list[tuple] = []

        class SpyLoop:
            """Minimal loop stand-in that records run_in_executor calls."""

            def run_in_executor(self, executor, fn, *args):
                executor_calls.append((executor, fn, args))
                # Return None — the impl fires-and-forgets (returns immediately)
                return None

        spy_loop = SpyLoop()
        hs = HookSystem()
        hs.register_webhook("asyncevt2", "http://slow.example.com", retry_count=1, timeout_seconds=30)

        with (
            patch("general_ludd.events.hooks.httpx.post") as mock_post,
            patch("asyncio.get_running_loop", return_value=spy_loop),
        ):
            hs.fire("asyncevt2", {"event_type": "model_added", "profile": {}})

        # The blocking httpx.post must NOT be called directly — offloaded to executor
        mock_post.assert_not_called()
        assert len(executor_calls) >= 1, (
            "run_in_executor was never called — webhook dispatch blocked the loop"
        )

    def test_sync_context_uses_direct_httpx_post(self):
        """When there is NO running loop, direct httpx.post is acceptable."""
        called = []

        def fake_post(url, **kwargs):
            called.append(url)
            return MagicMock(status_code=200)

        hs = HookSystem()
        hs.register_webhook("syncevt", "http://example.com", retry_count=1)

        # asyncio.get_running_loop raises RuntimeError when no loop is running
        with (
            patch("general_ludd.events.hooks.httpx.post", side_effect=fake_post),
            patch("asyncio.get_running_loop", side_effect=RuntimeError("no loop")),
        ):
            hs.fire("syncevt", {"data": "ok"})

        assert len(called) == 1, "Direct httpx.post should be used in sync context"


# ---------------------------------------------------------------------------
# D-07b: Async dispatch + redaction — both concerns verified together
# ---------------------------------------------------------------------------

class TestAsyncWebhookRedactionCombined:
    """D-07b — async offload AND credential redaction are both enforced.

    When fire() is called from inside a running event loop the webhook body
    must reach httpx.post via run_in_executor (not a direct blocking call), AND
    any sensitive keys must have been stripped by _redact_payload before the
    body is handed off to the thread.
    """

    def test_executor_used_and_secret_redacted_in_async_context(self):
        """run_in_executor is used in async context AND credentials are redacted.

        Strategy:
        1. Patch asyncio.get_running_loop() to return a capturing stand-in that
           records the callable passed to run_in_executor without executing it —
           this proves httpx.post is NOT called directly on the event-loop thread.
        2. Manually invoke the captured callable (with httpx.post still mocked)
           to confirm the body seen by httpx.post has sensitive keys stripped.
        """
        posted_bodies: list[dict] = []

        def fake_post(url, **kwargs):
            posted_bodies.append(kwargs.get("json", {}))
            mock_resp = MagicMock()
            mock_resp.raise_for_status = lambda: None
            return mock_resp

        executor_callables: list = []

        class CapturingLoop:
            """Stand-in loop that records run_in_executor calls without running them."""

            def run_in_executor(self, executor, fn, *args):
                executor_callables.append(fn)
                return None  # fire-and-forget contract; caller does not await

        hs = HookSystem()
        hs.register_webhook("secure_async_evt", "http://example.com", retry_count=1)

        payload = {"api_key": "SEKRIT", "name": "ok", "type": "model_added"}  # pragma: allowlist secret

        with (
            patch("general_ludd.events.hooks.httpx.post", side_effect=fake_post),
            patch("asyncio.get_running_loop", return_value=CapturingLoop()),
        ):
            hs.fire("secure_async_evt", payload)

        # Phase 1: httpx.post must NOT be called directly on the loop thread
        assert len(executor_callables) == 1, (
            "run_in_executor must be called exactly once in async context"
        )
        assert len(posted_bodies) == 0, (
            "httpx.post must not be invoked directly while an event loop is running"
        )

        # Phase 2: execute the captured callable to verify redaction was applied
        # before the body was handed to the thread.
        executor_callables[0]()

        assert len(posted_bodies) == 1, "httpx.post must be called inside _do_post"
        sent_payload = posted_bodies[0].get("payload", {})
        assert "api_key" not in sent_payload, "api_key key must be redacted before thread dispatch"
        assert "SEKRIT" not in str(sent_payload), "secret value must not appear in forwarded body"
        assert sent_payload.get("name") == "ok", "non-secret key must survive redaction"


# ---------------------------------------------------------------------------
# D-08: ModelAddedEvent-style profile payload strips credentials at registration
# ---------------------------------------------------------------------------

class TestModelAddedEventCredentialStrip:
    """D-08 — ModelAddedEvent.profile can carry api_key / token; must be stripped."""

    def test_model_profile_api_key_not_forwarded(self):
        """api_key inside a model profile sub-dict must not reach the webhook URL."""
        captured = []

        def fake_post(url, **kwargs):
            captured.append(kwargs.get("json", {}))
            return MagicMock(status_code=200)

        hs = HookSystem()
        hs.register_webhook("model_added", "https://webhook.example.com", retry_count=1)

        # Simulate what ModelAddedEvent.profile might look like
        payload = {
            "event": "model_added",
            "profile": {
                "name": "gpt-4o",
                "provider": "openai",
                "api_key": "sk-abc123",  # pragma: allowlist secret
                "endpoint": "https://api.openai.com/v1",
                "token": "bearer-xyz",  # pragma: allowlist secret
            },
        }

        with patch("general_ludd.events.hooks.httpx.post", side_effect=fake_post):
            hs.fire("model_added", payload)

        assert len(captured) == 1
        body = captured[0]
        body_str = str(body)
        assert "sk-abc123" not in body_str, "api_key value leaked in outgoing body"
        assert "bearer-xyz" not in body_str, "token value leaked in outgoing body"
        # Safe fields must be preserved
        assert body.get("payload", {}).get("event") == "model_added"


# ---------------------------------------------------------------------------
# D-34: retry_count is clamped AT REGISTRATION, stored value == min(max(1,n), 5)
# ---------------------------------------------------------------------------

class TestRetryCountClampAtRegistration:
    """D-34 — retry_count must be clamped to [1, 5] when register_webhook is called.

    The stored WebhookConfig.retry_count must equal 5 for any input > 5,
    and 1 for any input < 1 (including 0 and negatives).  This prevents a
    caller from configuring a 10000-iteration retry loop.
    """

    def test_retry_count_10000_stored_as_5(self):
        """register_webhook(retry_count=10000) must store retry_count=5 on the config."""
        hs = HookSystem()
        hs.register_webhook("evt_clamp", "http://example.com", retry_count=10000)
        hooks = hs.list_hooks()
        assert len(hooks) == 1
        stored = hooks[0].webhook_config.retry_count  # type: ignore[union-attr]
        assert stored == 5, (
            f"Expected retry_count=5 (clamped), got {stored}. "
            "Clamp must happen at register_webhook, not only at fire time."
        )

    def test_retry_count_0_stored_as_1(self):
        """register_webhook(retry_count=0) must store retry_count=1."""
        hs = HookSystem()
        hs.register_webhook("evt_zero", "http://example.com", retry_count=0)
        stored = hs.list_hooks()[0].webhook_config.retry_count  # type: ignore[union-attr]
        assert stored == 1, f"Expected retry_count=1 for input 0, got {stored}"

    def test_retry_count_negative_stored_as_1(self):
        """register_webhook(retry_count=-99) must store retry_count=1."""
        hs = HookSystem()
        hs.register_webhook("evt_neg", "http://example.com", retry_count=-99)
        stored = hs.list_hooks()[0].webhook_config.retry_count  # type: ignore[union-attr]
        assert stored == 1, f"Expected retry_count=1 for input -99, got {stored}"

    def test_retry_count_in_range_stored_unchanged(self):
        """retry_count=3 is within range and must be stored as-is."""
        hs = HookSystem()
        hs.register_webhook("evt_ok", "http://example.com", retry_count=3)
        stored = hs.list_hooks()[0].webhook_config.retry_count  # type: ignore[union-attr]
        assert stored == 3, f"Expected retry_count=3 unchanged, got {stored}"
