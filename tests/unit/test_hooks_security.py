"""Security tests for hooks.py — written before fixes (TDD red phase)."""
from __future__ import annotations

import asyncio
from typing import Any, cast
from unittest.mock import MagicMock, patch

import httpx
import pytest

from general_ludd.events.hooks import HookSystem


@pytest.fixture(autouse=True)
def _isolate_webhook_dns(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep hook tests hermetic while dedicated SSRF tests exercise DNS policy."""
    monkeypatch.setattr(
        "general_ludd.events.hooks.resolve_and_pin",
        lambda _host, *, port=443, timeout=2.0: None,
    )

# ---------------------------------------------------------------------------
# Helpers — fake httpx.AsyncClient for use in tests
# ---------------------------------------------------------------------------

class _FakeResponse:
    """Minimal httpx.Response stand-in that supports raise_for_status()."""
    status_code: int = 200

    def raise_for_status(self) -> None:
        pass

    def json(self) -> dict:
        return {}


def _make_fake_async_client(callback=None):
    """Return an async context manager whose ``post`` method records calls
    to *callback* and returns a _FakeResponse."""

    async def _post(self, url, **kwargs):
        if callback:
            callback(url, **kwargs)
        return _FakeResponse()

    class _FakeAsyncClient:
        post = _post

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

    return _FakeAsyncClient()


# ---------------------------------------------------------------------------
# Credential redaction tests
# ---------------------------------------------------------------------------


class TestCredentialRedaction:
    """A) Payload allowlist — no secret keys leak to webhook."""

    def test_secret_key_stripped_from_outgoing_body(self):
        """api_key in payload must not appear in the POST body."""
        captured = []

        def record(url, **kwargs):
            captured.append(kwargs.get("json", {}))

        hs = HookSystem()
        hs.register_webhook("evt", "http://example.com", retry_count=1)

        payload = {"api_key": "SEKRIT", "name": "ok", "type": "model_added"}  # pragma: allowlist secret
        with patch(
            "general_ludd.events.hooks.httpx.AsyncClient",
            return_value=_make_fake_async_client(callback=record),
        ):
            hs.fire("evt", payload)

        assert len(captured) == 1
        sent_payload = captured[0].get("payload", {})
        assert "api_key" not in sent_payload
        assert "SEKRIT" not in str(sent_payload)
        assert sent_payload.get("name") == "ok"

    def test_token_stripped(self):
        captured = []

        def record(url, **kwargs):
            captured.append(kwargs.get("json", {}))

        hs = HookSystem()
        hs.register_webhook("evt2", "http://example.com", retry_count=1)
        payload = {"token": "abc123", "user_id": "u1"}
        with patch(
            "general_ludd.events.hooks.httpx.AsyncClient",
            return_value=_make_fake_async_client(callback=record),
        ):
            hs.fire("evt2", payload)
        sent_payload = captured[0].get("payload", {})
        assert "token" not in sent_payload
        assert sent_payload.get("user_id") == "u1"

    def test_safe_keys_preserved(self):
        captured = []

        def record(url, **kwargs):
            captured.append(kwargs.get("json", {}))

        hs = HookSystem()
        hs.register_webhook("evt3", "http://example.com", retry_count=1)
        payload = {"name": "x", "type": "y", "id": "123", "timestamp": "t", "status": "ok"}
        with patch(
            "general_ludd.events.hooks.httpx.AsyncClient",
            return_value=_make_fake_async_client(callback=record),
        ):
            hs.fire("evt3", payload)
        sent_payload = captured[0].get("payload", {})
        assert sent_payload == payload


# ---------------------------------------------------------------------------
# Retry count clamp tests
# ---------------------------------------------------------------------------


class TestRetryCountClamp:
    """C) retry_count must be clamped to max 5."""

    def test_retry_count_999_clamped_to_5(self):
        """retry_count=999 must result in at most 5 attempts."""
        attempt_count = []

        async def always_fail(self, url, **kwargs):
            attempt_count.append(1)
            raise httpx.ConnectError("fail")

        class FailingClient:
            post = always_fail

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                pass

        hs = HookSystem()
        hs.register_webhook("retryevt", "http://example.com", retry_count=999)

        with patch(
            "general_ludd.events.hooks.httpx.AsyncClient",
            return_value=FailingClient(),
        ):
            hs.fire("retryevt", {"name": "x"})

        assert len(attempt_count) <= 5, f"Expected <=5 attempts, got {len(attempt_count)}"

    def test_retry_count_0_clamped_to_1(self):
        """retry_count=0 must result in at least 1 attempt."""
        attempt_count = []

        def record(url, **kwargs):
            attempt_count.append(1)

        hs = HookSystem()
        hs.register_webhook("retryevt2", "http://example.com", retry_count=0)

        with patch(
            "general_ludd.events.hooks.httpx.AsyncClient",
            return_value=_make_fake_async_client(callback=record),
        ):
            hs.fire("retryevt2", {"name": "x"})

        assert len(attempt_count) >= 1


# ---------------------------------------------------------------------------
# Async context: ensure_future is used instead of run_in_executor
# ---------------------------------------------------------------------------


class TestNoBlockingCallInAsyncContext:
    """B) Async dispatch must use asyncio.ensure_future, not run_in_executor."""

    def test_ensure_future_used_in_async_context(self):
        """When fire() is called from async context, ensure_future must be used."""
        ensure_future_calls: list = []

        def fake_ensure_future(coro):
            ensure_future_calls.append(coro)
            coro.close()
            return MagicMock()

        hs = HookSystem()
        hs.register_webhook("asyncevt", "http://example.com", retry_count=1)

        with (
            patch(
                "general_ludd.events.hooks.httpx.AsyncClient",
                return_value=_make_fake_async_client(),
            ),
            patch(
                "asyncio.get_running_loop", return_value=MagicMock(),
            ),
            patch(
                "asyncio.ensure_future", side_effect=fake_ensure_future,
            ),
        ):
            hs.fire("asyncevt", {"name": "safe"})

        assert len(ensure_future_calls) >= 1, (
            "asyncio.ensure_future must be called in async context"
        )


# ---------------------------------------------------------------------------
# Nested redaction tests
# ---------------------------------------------------------------------------


class TestNestedRedaction:
    """D) Nested dict/list values containing secret keys must not reach the webhook."""

    def _fire_and_capture(self, payload: dict) -> dict:
        captured = []

        def record(url, **kwargs):
            captured.append(kwargs.get("json", {}))

        hs = HookSystem()
        hs.register_webhook("nestevt", "http://example.com", retry_count=1)
        with patch(
            "general_ludd.events.hooks.httpx.AsyncClient",
            return_value=_make_fake_async_client(callback=record),
        ):
            hs.fire("nestevt", payload)
        assert len(captured) == 1
        return captured[0].get("payload", {})

    def test_nested_dict_secret_redacted(self):
        """Secret in a sub-dict must not appear in the outgoing payload."""
        payload = {"meta": {"api_key": "SEKRIT", "env": "prod"}}  # pragma: allowlist secret
        sent = self._fire_and_capture(payload)
        assert "SEKRIT" not in str(sent), "Nested api_key value leaked to webhook"
        assert sent.get("meta", {}).get("env") == "prod"

    def test_nested_list_of_dicts_secret_redacted(self):
        """Secret in a list-of-dicts element must not appear in the outgoing payload."""
        payload = {"items": [{"token": "T_SECRET"}, {"name": "ok"}]}  # pragma: allowlist secret
        sent = self._fire_and_capture(payload)
        assert "T_SECRET" not in str(sent), "token value in list element leaked to webhook"
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
        assert sent.get("a", {}).get("b", {}).get("safe") == "keep"


# ---------------------------------------------------------------------------
# D-07: Async dispatch does not block the event loop
# ---------------------------------------------------------------------------


class TestAsyncWebhookNonBlocking:
    """D-07 — _fire_webhook called from inside a running loop must not stall it."""

    def test_ensure_future_called_in_async_context(self):
        """In async context, asyncio.ensure_future must be used."""
        ensure_future_calls: list = []

        def fake_ensure_future(coro):
            ensure_future_calls.append(coro)
            coro.close()
            return MagicMock()

        hs = HookSystem()
        hs.register_webhook("asyncevt2", "http://slow.example.com", retry_count=1, timeout_seconds=30)

        with (
            patch(
                "general_ludd.events.hooks.httpx.AsyncClient",
                return_value=_make_fake_async_client(),
            ),
            patch("asyncio.get_running_loop", return_value=MagicMock()),
            patch("asyncio.ensure_future", side_effect=fake_ensure_future),
        ):
            hs.fire("asyncevt2", {"event_type": "model_added", "profile": {}})

        assert len(ensure_future_calls) >= 1, (
            "ensure_future was never called in async context"
        )

    def test_async_context_schedules_before_dns_resolution(self):
        """DNS pinning must happen in the scheduled coroutine, not fire()."""
        captured_coros: list = []
        resolve_calls: list[str] = []

        def fake_ensure_future(coro):
            captured_coros.append(coro)
            task = MagicMock()
            task.add_done_callback = lambda cb: None
            return task

        def fake_resolve_and_pin(host, *, port=443, timeout=2.0):
            resolve_calls.append(host)
            return None

        hs = HookSystem()
        hs.register_webhook("asyncevt-dns", "http://slow.example.com", retry_count=1)

        with (
            patch(
                "general_ludd.events.hooks.httpx.AsyncClient",
                return_value=_make_fake_async_client(),
            ),
            patch("general_ludd.events.hooks.resolve_and_pin", side_effect=fake_resolve_and_pin),
            patch("asyncio.get_running_loop", return_value=MagicMock()),
            patch("asyncio.ensure_future", side_effect=fake_ensure_future),
        ):
            hs.fire("asyncevt-dns", {"event_type": "model_added", "profile": {}})

        assert len(captured_coros) == 1
        assert resolve_calls == []

        with (
            patch(
                "general_ludd.events.hooks.httpx.AsyncClient",
                return_value=_make_fake_async_client(),
            ),
            patch("general_ludd.events.hooks.resolve_and_pin", side_effect=fake_resolve_and_pin),
        ):
            asyncio.run(captured_coros[0])
        assert resolve_calls == ["slow.example.com"]

    def test_sync_context_uses_async_client(self):
        """When there is NO running loop, async client is used via asyncio.run."""
        called = []

        def record(url, **kwargs):
            called.append(url)

        hs = HookSystem()
        hs.register_webhook("syncevt", "http://example.com", retry_count=1)

        with (
            patch(
                "general_ludd.events.hooks.httpx.AsyncClient",
                return_value=_make_fake_async_client(callback=record),
            ),
            patch("asyncio.get_running_loop", side_effect=RuntimeError("no loop")),
        ):
            hs.fire("syncevt", {"data": "ok"})

        assert len(called) == 1, "AsyncClient should be used in sync context"


# ---------------------------------------------------------------------------
# D-07b: Async dispatch + redaction — both concerns verified together
# ---------------------------------------------------------------------------


class TestAsyncWebhookRedactionCombined:
    """D-07b — async dispatch AND credential redaction are both enforced."""

    def test_ensure_future_used_and_secret_redacted(self):
        """Async dispatch schedules a task AND credentials are redacted.

        Strategy:
        1. Patch asyncio.ensure_future to capture the scheduled coroutine.
        2. Run the coroutine separately (with AsyncClient mocked) to verify the
           body sent via httpx has sensitive keys stripped.
        """
        captured_coros: list = []

        def fake_ensure_future(coro):
            captured_coros.append(coro)
            task = MagicMock()
            task.add_done_callback = lambda cb: None
            return task

        posted_bodies: list[dict] = []

        def record(url, **kwargs):
            posted_bodies.append(kwargs.get("json", {}))

        hs = HookSystem()
        hs.register_webhook("secure_async_evt", "http://example.com", retry_count=1)

        payload = {"api_key": "SEKRIT", "name": "ok", "type": "model_added"}  # pragma: allowlist secret

        with (
            patch(
                "general_ludd.events.hooks.httpx.AsyncClient",
                return_value=_make_fake_async_client(),
            ),
            patch("asyncio.get_running_loop", return_value=MagicMock()),
            patch("asyncio.ensure_future", side_effect=fake_ensure_future),
        ):
            hs.fire("secure_async_evt", payload)

        # Phase 1: ensure_future must have been called
        assert len(captured_coros) == 1, (
            "ensure_future must be called exactly once in async context"
        )
        assert len(posted_bodies) == 0, (
            "httpx must not be invoked on the same call stack as fire()"
        )

        # Phase 2: run the captured coroutine with AsyncClient mocked to verify redaction
        with patch(
            "general_ludd.events.hooks.httpx.AsyncClient",
            return_value=_make_fake_async_client(callback=record),
        ):
            asyncio.run(captured_coros[0])

        assert len(posted_bodies) == 1, "httpx post must be called inside the coroutine"
        sent_payload = posted_bodies[0].get("payload", {})
        assert "api_key" not in sent_payload, "api_key must be redacted before dispatch"
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

        def record(url, **kwargs):
            captured.append(kwargs.get("json", {}))

        hs = HookSystem()
        hs.register_webhook("model_added", "https://webhook.example.com", retry_count=1)

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

        with (
            patch(
                "general_ludd.events.hooks.httpx.AsyncClient",
                return_value=_make_fake_async_client(callback=record),
            ),
            patch("general_ludd.events.hooks.resolve_and_pin", return_value=None),
        ):
            hs.fire("model_added", payload)
        assert len(captured) == 1
        body = captured[0]
        body_str = str(body)
        assert "sk-abc123" not in body_str, "api_key value leaked in outgoing body"
        assert "bearer-xyz" not in body_str, "token value leaked in outgoing body"
        assert body.get("payload", {}).get("event") == "model_added"


# ---------------------------------------------------------------------------
# D-34: retry_count is clamped AT REGISTRATION, stored value == min(max(1,n), 5)
# ---------------------------------------------------------------------------


class TestRetryCountClampAtRegistration:
    """D-34 — retry_count must be clamped to [1, 5] when register_webhook is called."""

    def test_retry_count_10000_stored_as_5(self):
        """register_webhook(retry_count=10000) must store retry_count=5 on the config."""
        hs = HookSystem()
        hs.register_webhook("evt_clamp", "http://example.com", retry_count=10000)
        hooks = hs.list_hooks()
        assert len(hooks) == 1
        stored = cast(Any, hooks[0]).webhook_config.retry_count
        assert stored == 5, (
            f"Expected retry_count=5 (clamped), got {stored}. "
            "Clamp must happen at register_webhook, not only at fire time."
        )

    def test_retry_count_0_stored_as_1(self):
        """register_webhook(retry_count=0) must store retry_count=1."""
        hs = HookSystem()
        hs.register_webhook("evt_zero", "http://example.com", retry_count=0)
        stored = cast(Any, hs.list_hooks()[0]).webhook_config.retry_count
        assert stored == 1, f"Expected retry_count=1 for input 0, got {stored}"

    def test_retry_count_negative_stored_as_1(self):
        """register_webhook(retry_count=-99) must store retry_count=1."""
        hs = HookSystem()
        hs.register_webhook("evt_neg", "http://example.com", retry_count=-99)
        stored = cast(Any, hs.list_hooks()[0]).webhook_config.retry_count
        assert stored == 1, f"Expected retry_count=1 for input -99, got {stored}"

    def test_retry_count_in_range_stored_unchanged(self):
        """retry_count=3 is within range and must be stored as-is."""
        hs = HookSystem()
        hs.register_webhook("evt_ok", "http://example.com", retry_count=3)
        stored = cast(Any, hs.list_hooks()[0]).webhook_config.retry_count
        assert stored == 3, f"Expected retry_count=3 unchanged, got {stored}"


# ---------------------------------------------------------------------------
# Real-loop non-blocking proof: fire() must NOT hold the event-loop thread
# ---------------------------------------------------------------------------


class TestEventLoopNotBlocked:
    """Prove with a REAL asyncio event loop that fire-and-forget webhook
    dispatch via ``asyncio.ensure_future`` never blocks the loop thread."""

    async def test_slow_webhook_does_not_block_real_loop(self):
        """T1 — a 0.5s async post must not stall a real event loop.

        Strategy:
        * Inside ``asyncio.run`` (pytest-asyncio auto mode), register a webhook
          whose mocked AsyncClient.post sleeps 0.5s.
        * Schedule a sentinel coroutine that sets an ``asyncio.Event`` after 10ms.
        * Call ``hs.fire(...)`` (fire-and-forget).
        * ``await asyncio.wait_for(sentinel_task, timeout=0.3)`` must succeed.
        """

        async def slow_post(self, url, **kwargs):
            await asyncio.sleep(0.5)

        class SlowClient:
            post = slow_post

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                pass

        hs = HookSystem()
        hs.register_webhook(
            "realloop_evt", "http://example.com", retry_count=1, timeout_seconds=5
        )

        sentinel = asyncio.Event()

        async def sentinel_coro():
            await asyncio.sleep(0.01)
            sentinel.set()

        sentinel_task = asyncio.ensure_future(sentinel_coro())

        with patch(
            "general_ludd.events.hooks.httpx.AsyncClient",
            return_value=SlowClient(),
        ):
            hs.fire("realloop_evt", {"name": "ok", "type": "model_added"})
            await asyncio.wait_for(sentinel_task, timeout=0.3)

        assert sentinel.is_set(), (
            "Sentinel coroutine did not complete within 0.3s — the event loop "
            "thread was blocked by the fire-and-forget webhook dispatch"
        )

        # Drain the pending webhook task so its done-callback doesn't leak
        if hs._pending_webhooks:
            await asyncio.gather(*hs._pending_webhooks, return_exceptions=True)
        await sentinel_task

    async def test_multiple_webhooks_tracked_in_pending_set(self):
        """T2 — firing multiple webhooks populates _pending_webhooks."""

        async def slow_post(self, url, **kwargs):
            await asyncio.sleep(0.3)

        class SlowClient:
            post = slow_post

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                pass

        hs = HookSystem()
        for i in range(3):
            hs.register_webhook(
                f"multi_evt_{i}", "http://example.com", retry_count=1, timeout_seconds=5
            )

        with patch(
            "general_ludd.events.hooks.httpx.AsyncClient",
            return_value=SlowClient(),
        ):
            for i in range(3):
                hs.fire(f"multi_evt_{i}", {"i": i})
            observed = len(hs._pending_webhooks)

        assert observed == 3, (
            f"Expected 3 in-flight webhook tasks while slow POSTs pending, "
            f"saw {observed}"
        )

        if hs._pending_webhooks:
            await asyncio.gather(*hs._pending_webhooks, return_exceptions=True)
        assert len(hs._pending_webhooks) == 0, (
            "_pending_webhooks was not drained after deliveries completed"
        )
