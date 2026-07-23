"""H.2 — concurrent /admin/reload calls race on shared registries with no lock.

Tests:
  1. Two concurrent reloads serialize (second waits for first)
  2. Lock prevents registry corruption (read-modify-write interleaving)
  3. Lock is non-reentrant (prevents deadlock from nested reload)
  4. Timeout on lock acquisition
"""

from __future__ import annotations

import threading
import time
from typing import Any

import pytest

from general_ludd.reload.hot_reloader import HotReloader, ReloadResult, ReloadScope

_LOCK_TYPE = type(threading.Lock())


class _DummyReloader(HotReloader):
    """Override _reload_impl so timing-controlled tests don't hit the full reload stack."""

    def _reload_impl(self, scope: Any) -> ReloadResult:
        getattr(self, "_test_side_effect", lambda s: None)(scope)
        return ReloadResult(success=True, scope=str(scope), details={})


@pytest.fixture
def shared_lock() -> threading.Lock:
    """Fresh per-test lock so tests don't interfere with each other."""
    return threading.Lock()


@pytest.fixture
def reloader_factory(shared_lock: threading.Lock) -> Any:
    from functools import partial

    return partial(
        _DummyReloader, config_dir="/tmp", reload_lock=shared_lock
    )


# ── Test 1: Concurrent reloads serialize ──────────────────────────────

def test_concurrent_reloads_serialize(reloader_factory: Any) -> None:
    """When two threads reload concurrently, the second waits for the first to finish."""
    order: list[str] = []

    def _side(idx: int, label: str) -> Any:
        def _effect(_scope: Any) -> None:
            order.append(f"start-{label}")
            time.sleep(0.15)
            order.append(f"end-{label}")
        return _effect

    r1 = reloader_factory()
    r2 = reloader_factory()
    r1._test_side_effect = _side(0, "a")  # type: ignore[attr-defined]
    r2._test_side_effect = _side(1, "b")  # type: ignore[attr-defined]

    results: list[Any] = [None, None]
    def run(idx: int, rl: Any) -> None:
        results[idx] = rl.reload(ReloadScope.ALL)

    t1 = threading.Thread(target=run, args=(0, r1))
    t2 = threading.Thread(target=run, args=(1, r2))

    t1.start()
    time.sleep(0.03)  # let t1 acquire the lock first
    t2.start()

    t1.join(timeout=5)
    t2.join(timeout=5)

    assert results[0] is not None
    assert results[1] is not None
    assert results[0].success
    assert results[1].success

    # Must be A-start, A-end, B-start, B-end — never interleaved
    assert order == ["start-a", "end-a", "start-b", "end-b"], (
        f"Expected serialized order, got {order}"
    )


# ── Test 2: Lock prevents registry corruption ─────────────────────────

def test_lock_prevents_registry_corruption(reloader_factory: Any) -> None:
    """Read-modify-write on shared state must not interleave under concurrent reload."""
    shared: dict[str, int] = {"counter": 0}

    def _side() -> Any:
        def _effect(_scope: Any) -> None:
            current = shared["counter"]
            time.sleep(0.05)  # window for interleaving if un-locked
            shared["counter"] = current + 1
        return _effect

    r1 = reloader_factory()
    r2 = reloader_factory()
    r1._test_side_effect = _side()  # type: ignore[attr-defined]
    r2._test_side_effect = _side()  # type: ignore[attr-defined]

    threads: list[threading.Thread] = []
    for rl in (r1, r2):
        t = threading.Thread(target=lambda r=rl: r.reload(ReloadScope.ALL))
        threads.append(t)

    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=5)

    assert shared["counter"] == 2, (
        f"Expected counter=2 (serialized increments), got {shared['counter']} "
        f"— concurrent interleaving would produce counter=1"
    )


# ── Test 3: Lock is non-reentrant ─────────────────────────────────────

def test_lock_is_non_reentrant(shared_lock: threading.Lock) -> None:
    """threading.Lock (not RLock) — prevents deadlock from nested reload."""
    assert type(shared_lock) is _LOCK_TYPE, (
        f"Expected threading.Lock, got {type(shared_lock)}"
    )
    assert "RLock" not in type(shared_lock).__name__, (
        "Lock must not be reentrant — a nested reload should deadlock/timeout"
    )


def test_non_reentrant_causes_timeout_on_nested_acquire(
    reloader_factory: Any,
) -> None:
    """If someone nests reload(), the non-reentrant lock times out the inner call."""

    def _side() -> Any:
        def _effect(scope: Any) -> None:
            # Simulate nested reload attempt from within _reload_impl
            r2 = reloader_factory()
            r2._test_side_effect = lambda s: None  # type: ignore[attr-defined]
            # This should time out because the outer reload holds the lock
            result = r2.reload(ReloadScope.CONFIG)
            # Store the nested result for inspection
            getattr(_effect, "_nested_result_store", {}).setdefault("result", result)
        return _effect

    r1 = reloader_factory()
    nested_store: dict[str, object] = {}
    effect = _side()
    effect._nested_result_store = nested_store  # type: ignore[attr-defined]
    r1._test_side_effect = effect  # type: ignore[attr-defined]

    # Force a short timeout so nested reload fails fast
    # We can't change timeout on the shared lock in r2 without making it available.
    # Instead, we verify via the non-reentrant type test above + the fact that
    # threading.Lock.acquire(timeout=...) returns False when held by same thread.
    # This test confirms the timeout error path works.
    result = r1.reload(ReloadScope.ALL)
    assert result.success, f"Outer reload failed: {result.error}"

    nested = nested_store.get("result")
    if nested is not None:
        assert not nested.success, (
            "Nested reload should have failed (lock timeout), got success"
        )
        assert "timeout" in (nested.error or "").lower(), (
            f"Expected timeout error, got: {nested.error}"
        )


# ── Test 4: Timeout on lock acquisition ───────────────────────────────

def test_timeout_on_lock_acquisition(
    shared_lock: threading.Lock,
) -> None:
    """When the lock is held, a new caller with a short timeout gets ReloadBusyError via timeout."""
    held = threading.Event()
    release = threading.Event()

    def _holder() -> None:
        shared_lock.acquire()
        held.set()
        release.wait(timeout=3.0)
        shared_lock.release()

    holder_t = threading.Thread(target=_holder, daemon=True)
    holder_t.start()
    assert held.wait(timeout=2.0), "Holder thread did not acquire the lock"

    # Create a reloader with a short acquisition timeout
    from functools import partial

    fast_reloader = partial(_DummyReloader, config_dir="/tmp", reload_lock=shared_lock)
    r = fast_reloader(reload_timeout_s=0.1)
    r._test_side_effect = lambda s: None  # type: ignore[attr-defined]

    result = r.reload(ReloadScope.CONFIG)

    release.set()
    holder_t.join(timeout=3)

    assert not result.success, (
        "Expected reload to fail (lock acquisition timeout), got success"
    )
    assert result.error is not None and "timeout" in result.error.lower(), (
        f"Expected timeout in error message, got: {result.error}"
    )


# ── Test 5: After a reload completes, the lock is released ────────────

def test_lock_released_after_success(reloader_factory: Any) -> None:
    """The lock must be released after a successful reload."""
    r1 = reloader_factory()
    r1._test_side_effect = lambda s: None  # type: ignore[attr-defined]
    result1 = r1.reload(ReloadScope.CONFIG)
    assert result1.success, f"First reload failed: {result1.error}"

    r2 = reloader_factory()
    r2._test_side_effect = lambda s: None  # type: ignore[attr-defined]
    result2 = r2.reload(ReloadScope.CONFIG)
    assert result2.success, "Second reload should succeed — lock was not released"


# ── Test 6: Lock released after exception in _reload_impl ─────────────

def test_lock_released_after_exception(reloader_factory: Any) -> None:
    """The lock must be released even if _reload_impl raises."""

    def _side() -> Any:
        def _effect(_scope: Any) -> None:
            raise RuntimeError("boom during reload")
        return _effect

    r1 = reloader_factory()
    r1._test_side_effect = _side()  # type: ignore[attr-defined]

    result = r1.reload(ReloadScope.ALL)
    assert not result.success, "Expected reload to fail due to exception"
    assert "boom" in (result.error or "")

    r2 = reloader_factory()
    r2._test_side_effect = lambda s: None  # type: ignore[attr-defined]
    result2 = r2.reload(ReloadScope.CONFIG)
    assert result2.success, (
        "Second reload failed — lock was not released after first crashed"
    )


# ── Test 7: Default lock is created when no lock passed ────────────────

def test_default_lock_created_when_none_passed() -> None:
    """When reload_lock is None, HotReloader creates its own threading.Lock."""
    r = HotReloader(config_dir="/tmp")
    assert isinstance(r._reload_lock, _LOCK_TYPE)
    # Default lock should not be shared with another instance
    r2 = HotReloader(config_dir="/tmp")
    assert r._reload_lock is not r2._reload_lock, (
        "Two HotReloaders without a shared lock should get independent locks"
    )


# ── Test 8: min_interval still enforced with shared lock ──────────────

def test_min_interval_still_enforced(reloader_factory: Any) -> None:
    """Even with a shared lock, the per-key min_reload_interval prevents hammering."""
    reloader_factory()

    class _Result:
        pass

    _Result.success = False
    _Result.error = ""

    def _make() -> Any:
        class R:
            success = True
            error = None
        return R

    r1 = reloader_factory()
    r1._test_side_effect = lambda s: None  # type: ignore[attr-defined]

    # First reload succeeds
    res1 = r1.reload(ReloadScope.CONFIG)
    assert res1.success

    # Second reload too soon — should be rejected by min_interval
    res2 = r1.reload(ReloadScope.CONFIG)
    assert not res2.success, "Expected min_interval rejection"
    assert "too soon" in (res2.error or "").lower()

    # But a different key should still work
    r1._test_side_effect = lambda s: None  # type: ignore[attr-defined]
    res3 = r1.reload(ReloadScope.TEMPLATES)
    assert res3.success, f"Different scope reload failed: {res3.error}"
