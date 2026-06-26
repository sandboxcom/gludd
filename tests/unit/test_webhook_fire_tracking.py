"""Regression tests for SF-fire-webhook-sync: webhook delivery Futures must be
tracked (not discarded) and their failures surfaced, not silently swallowed.

The async path offloads the blocking httpx.post via loop.run_in_executor. The
returned Future was previously dropped on the floor, so (a) it could be garbage
collected mid-delivery and (b) an exception in the worker thread was never
logged. The fix stores each Future in self._pending_webhooks with a done-callback
that logs failures and removes the entry on completion.

These tests patch the module-level SSRF guard to a no-op so they exercise only
the delivery-tracking behavior (the SSRF guard has its own tests), and patch
httpx.post so no real network call is made.
"""

from __future__ import annotations

import asyncio
import logging

import pytest

from general_ludd.events import hooks as hooks_mod
from general_ludd.events.hooks import HookSystem


@pytest.fixture(autouse=True)
def _no_ssrf_check(monkeypatch):
    # Isolate from the SSRF/DNS guard so the test is hermetic; we only care
    # about delivery-Future tracking here.
    monkeypatch.setattr(hooks_mod, "_ensure_safe_webhook_url", lambda url: None)


@pytest.mark.asyncio
async def test_failed_webhook_is_tracked_then_cleaned_up_and_logged(monkeypatch, caplog):
    def _boom(*_a, **_k):
        raise RuntimeError("connection refused")

    monkeypatch.setattr(hooks_mod.httpx, "post", _boom)

    hs = HookSystem()
    hs.register_webhook("evt", "http://example.com/hook", retry_count=1)

    with caplog.at_level(logging.WARNING, logger=hooks_mod.logger.name):
        hs.fire("evt", {"k": "v"})
        # Let the executor thread run the (failing) retry loop and the
        # done-callback fire on the loop.
        for _ in range(50):
            await asyncio.sleep(0.01)
            if not hs._pending_webhooks:
                break

    # The Future was tracked and then removed by the done-callback.
    assert hs._pending_webhooks == set()
    # The failure was surfaced to operators, not swallowed.
    assert any("Webhook delivery failed" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_successful_webhook_future_is_tracked_then_cleared(monkeypatch):
    class _Resp:
        def raise_for_status(self) -> None:
            return None

    monkeypatch.setattr(hooks_mod.httpx, "post", lambda *a, **k: _Resp())

    hs = HookSystem()
    hs.register_webhook("evt", "http://example.com/hook", retry_count=1)

    hs.fire("evt", {"k": "v"})
    # The done-callback removes the entry once delivery completes.
    for _ in range(50):
        await asyncio.sleep(0.01)
        if not hs._pending_webhooks:
            break
    assert hs._pending_webhooks == set()


def test_sync_context_still_delivers_without_event_loop(monkeypatch):
    # With no running loop (pure sync caller / CLI / unit test), _do_post runs
    # inline and the pending set stays empty.
    calls: list[str] = []

    class _Resp:
        def raise_for_status(self) -> None:
            return None

    def _post(url, *a, **k):
        calls.append(url)
        return _Resp()

    monkeypatch.setattr(hooks_mod.httpx, "post", _post)

    hs = HookSystem()
    hs.register_webhook("evt", "http://example.com/hook", retry_count=1)
    hs.fire("evt", {"k": "v"})

    assert calls == ["http://example.com/hook"]
    assert hs._pending_webhooks == set()
