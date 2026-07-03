"""TDD: _fire_webhook must use httpx.AsyncClient, not sync httpx.post.

These tests verify that _fire_webhook in hooks.py uses native async I/O
(httpx.AsyncClient) instead of the sync httpx.post + run_in_executor workaround.
The sync workaround, while non-blocking, still consumes a thread-pool thread and
is not the intended long-term fix.  Native async I/O avoids both event-loop
freezing AND thread-pool exhaustion.
"""
from __future__ import annotations

import asyncio
from unittest.mock import patch

from general_ludd.events.hooks import HookSystem


class _FakeResponse:
    """Minimal httpx.Response stand-in that supports raise_for_status()."""
    status_code: int = 200

    def raise_for_status(self) -> None:
        pass

    def json(self) -> dict:
        return {}


def _make_fake_client(callback=None):
    """Return a class-based async context manager whose ``post`` method
    records calls to *callback* (if given) and returns a _FakeResponse.

    Using a real class instead of ``unittest.mock.AsyncMock`` avoids the
    edge-cases AsyncMock exhibits when patched in place of an async context
    manager used inside a ``patch``-wrapped call.
    """

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
# Test 1: AsyncClient is used in async context (NOT sync httpx.post)
# ---------------------------------------------------------------------------


class TestAsyncClientUsed:
    """TDD-1 — _fire_webhook from async context must use AsyncClient."""

    async def test_async_client_post_is_called_sync_post_is_not(self):
        """httpx.AsyncClient.post must be called; sync httpx.post must NOT.

        Strategy:
        - Patch BOTH httpx.AsyncClient and httpx.post.
        - Fire a webhook from inside a real asyncio event loop.
        - Assert AsyncClient.post received the call, and sync httpx.post did NOT.
        """
        async_post_calls: list[str] = []
        sync_post_calls: list[str] = []

        def record_async(url, **kwargs):
            async_post_calls.append(url)

        def record_sync(url, **kwargs):
            sync_post_calls.append(url)
            from unittest.mock import MagicMock
            return MagicMock(status_code=200)

        fake_client = _make_fake_client(callback=record_async)

        hs = HookSystem()
        hs.register_webhook(
            "evt_async", "http://example.com", retry_count=1, timeout_seconds=5
        )

        # Keep patches alive across the full async lifecycle: fire() schedules
        # the coroutine, and the subsequent awaits yield to the loop so the
        # coroutine actually executes — all inside the with block.
        with (
            patch(
                "general_ludd.events.hooks.httpx.AsyncClient",
                return_value=fake_client,
            ),
            patch(
                "general_ludd.events.hooks.httpx.post",
                side_effect=record_sync,
            ),
        ):
            hs.fire("evt_async", {"name": "test", "type": "model_added"})

            # Give the async task time to schedule and run
            await asyncio.sleep(0.3)

            # Drain pending tasks so the async webhook completes
            if hs._pending_webhooks:
                await asyncio.gather(*hs._pending_webhooks, return_exceptions=True)

        assert len(async_post_calls) >= 1, (
            f"httpx.AsyncClient.post must be called in async context; "
            f"saw {len(async_post_calls)} AsyncClient calls, "
            f"{len(sync_post_calls)} sync httpx.post calls"
        )
        assert len(sync_post_calls) == 0, (
            "sync httpx.post must NOT be called from async context"
        )


# ---------------------------------------------------------------------------
# Test 2: Non-blocking proof with a real event loop
# ---------------------------------------------------------------------------


class TestAsyncWebhookNonBlocking:
    """TDD-2 — _fire_webhook must not block the event loop."""

    async def test_slow_webhook_does_not_block_real_loop(self):
        """A slow webhook (0.5 s) must not stall a real event loop.

        Strategy:
        - Mock httpx.AsyncClient.post to sleep 0.5s (simulating a slow network).
        - Schedule a sentinel coroutine that sets an Event after 10ms.
        - Fire the webhook (fire-and-forget).
        - ``await asyncio.wait_for(sentinel_task, timeout=0.3)`` must succeed.
          If the loop thread were held by the slow POST, the sentinel could
          not have completed before the 0.3 s deadline.
        """

        async def slow_post(self, url, **kwargs):
            await asyncio.sleep(0.5)

        fake_client = _make_fake_client()
        fake_client.post = slow_post  # override with slow version

        hs = HookSystem()
        hs.register_webhook(
            "slow_evt", "http://example.com", retry_count=1, timeout_seconds=5
        )

        sentinel = asyncio.Event()

        async def sentinel_coro():
            await asyncio.sleep(0.01)
            sentinel.set()

        sentinel_task = asyncio.ensure_future(sentinel_coro())

        with patch(
            "general_ludd.events.hooks.httpx.AsyncClient",
            return_value=fake_client,
        ):
            hs.fire("slow_evt", {"name": "ok", "type": "model_added"})
            await asyncio.wait_for(sentinel_task, timeout=0.3)

        assert sentinel.is_set(), (
            "Sentinel coroutine did not complete within 0.3 s — the event loop "
            "thread was blocked by the webhook dispatch"
        )

        # Drain pending webhook tasks
        if hs._pending_webhooks:
            await asyncio.gather(*hs._pending_webhooks, return_exceptions=True)


# ---------------------------------------------------------------------------
# Test 3: Sync context (no running loop) also uses AsyncClient via asyncio.run
# ---------------------------------------------------------------------------


class TestSyncContextAsyncClient:
    """TDD-3 — sync context (no loop) uses AsyncClient via asyncio.run."""

    def test_sync_context_uses_async_client(self):
        """When no event loop is running, _fire_webhook calls asyncio.run
        with an async function that uses httpx.AsyncClient."""
        async_post_calls: list[str] = []

        def record_async(url, **kwargs):
            async_post_calls.append(url)

        fake_client = _make_fake_client(callback=record_async)

        hs = HookSystem()
        hs.register_webhook(
            "sync_evt", "http://example.com", retry_count=1, timeout_seconds=5
        )

        with (
            patch(
                "general_ludd.events.hooks.httpx.AsyncClient",
                return_value=fake_client,
            ),
            patch(
                "general_ludd.events.hooks.httpx.post",
            ) as mock_sync_post,
            patch(
                "asyncio.get_running_loop",
                side_effect=RuntimeError("no running loop"),
            ),
        ):
            hs.fire("sync_evt", {"name": "test"})

        assert len(async_post_calls) >= 1, (
            "AsyncClient.post must be called even in sync context"
        )
        mock_sync_post.assert_not_called()
