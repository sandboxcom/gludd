"""Regression tests for SF-fire-webhook-sync: webhook delivery Futures must be
tracked (not discarded) and their failures surfaced, not silently swallowed.

These tests patch the module-level SSRF guard to a no-op so they exercise only
the delivery-tracking behavior (the SSRF guard has its own tests), and patch
httpx.AsyncClient so no real network call is made.
"""

from __future__ import annotations

import asyncio
import logging

import pytest

from general_ludd.events import hooks as hooks_mod
from general_ludd.events.hooks import HookSystem


class _FakeResponse:
    status_code: int = 200

    def raise_for_status(self) -> None:
        pass


def _make_client(callback=None):
    async def _post(self, url, **kwargs):
        if callback:
            callback(url, **kwargs)
        return _FakeResponse()

    class _C:
        post = _post

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

    return _C()


def _make_failing_client():
    async def _boom(self, *a, **k):
        raise RuntimeError("connection refused")

    class _C:
        post = _boom

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

    return _C()


@pytest.fixture(autouse=True)
def _no_ssrf_check(monkeypatch):
    # Isolate from the SSRF/DNS guard so the test is hermetic; we only care
    # about delivery-Future tracking here.
    monkeypatch.setattr(hooks_mod, "_ensure_safe_webhook_url", lambda url: None)


@pytest.mark.asyncio
async def test_failed_webhook_is_tracked_then_cleaned_up_and_logged(monkeypatch, caplog):
    monkeypatch.setattr(hooks_mod.httpx, "AsyncClient", lambda: _make_failing_client())

    hs = HookSystem()
    hs.register_webhook("evt", "http://example.com/hook", retry_count=1)

    logging.getLogger(hooks_mod.logger.name).propagate = True
    with caplog.at_level(logging.WARNING, logger=hooks_mod.logger.name):
        hs.fire("evt", {"k": "v"})
        # Let the async task run the (failing) retry loop and the
        # done-callback fire on the loop.
        for _ in range(50):
            await asyncio.sleep(0.01)
            if not hs._pending_webhooks:
                break

    # The Future was tracked and then removed by the done-callback.
    assert hs._pending_webhooks == set()
    # The failure was surfaced to operators, not swallowed.
    assert any("Webhook delivery failed" in r.getMessage() for r in caplog.records)


@pytest.mark.asyncio
async def test_successful_webhook_future_is_tracked_then_cleared(monkeypatch):
    monkeypatch.setattr(hooks_mod.httpx, "AsyncClient", lambda: _make_client())

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
    # With no running loop (pure sync caller / CLI / unit test), _do_post_async
    # runs via asyncio.run and the pending set stays empty.
    calls: list[str] = []

    def record(url, **kwargs):
        calls.append(url)

    monkeypatch.setattr(hooks_mod.httpx, "AsyncClient", lambda: _make_client(callback=record))

    hs = HookSystem()
    hs.register_webhook("evt", "http://example.com/hook", retry_count=1)
    hs.fire("evt", {"k": "v"})

    assert calls == ["http://example.com/hook"]
    assert hs._pending_webhooks == set()
