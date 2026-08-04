"""Deep pool/allocator tests for VMSandboxPool.

Covers: acquire/release lifecycle, max pool size enforcement, warmup behaviour,
eviction (reap_idle), object validation (failed instances), and resource limits.
"""

from __future__ import annotations

import time
from unittest.mock import MagicMock

import pytest

from general_ludd.security.permissions import PermissionSpec
from general_ludd.security.sandboxes import SandboxHandle, SandboxTarget
from general_ludd.security.sandboxes.vm.lifecycle import (
    VMInstance,
    VMLifecycleState,
    VMSandboxManager,
)
from general_ludd.security.sandboxes.vm.pool import (
    PoolConfig,
    VMSandboxPool,
)

# ── helpers ──────────────────────────────────────────────────────────


def _make_spec() -> PermissionSpec:
    return PermissionSpec(agent_type="test")


def _make_target() -> SandboxTarget:
    return SandboxTarget()


def _make_handle() -> SandboxHandle:
    return SandboxHandle(backend="mock", token="mock-token", applied=True)


def _make_instance(
    instance_id: str = "vm-test",
    state: VMLifecycleState = VMLifecycleState.RUNNING,
) -> VMInstance:
    return VMInstance(
        instance_id=instance_id,
        backend_name="mock",
        spec=_make_spec(),
        handle=_make_handle(),
        state=state,
    )


def _boot_counter(manager: VMSandboxManager) -> int:
    return manager.boot.call_count  # type: ignore[no-any-expr]


_boot_seq: int = 0


def _next_boot_id() -> str:
    global _boot_seq
    _boot_seq += 1
    return f"vm-{_boot_seq}"


def _make_boot_side_effect():
    def boot(*a, **kw):  # type: ignore[no-untyped-def]
        inst = _make_instance(_next_boot_id())
        return inst

    return boot


# ── acquire / release lifecycle ──────────────────────────────────────


class TestAcquireRelease:
    def test_acquire_returns_prewarmed_instance(self) -> None:
        cfg = PoolConfig(min_idle=1, max_size=5, prewarm_count=2)
        manager = MagicMock(spec=VMSandboxManager)
        manager.instances = {}
        manager.boot.side_effect = _make_boot_side_effect()
        pool = VMSandboxPool("mock", _make_spec(), _make_target(), config=cfg, manager=manager)

        pool.prewarm()
        assert pool.available_count() == 2
        iid = pool.checkout()
        assert iid.startswith("vm-")
        assert pool.checked_out_count() == 1

    def test_acquire_drains_available_on_checkout(self) -> None:
        cfg = PoolConfig(min_idle=0, max_size=3, prewarm_count=0)
        manager = MagicMock(spec=VMSandboxManager)
        manager.instances = {}
        manager.boot.side_effect = _make_boot_side_effect()
        pool = VMSandboxPool("mock", _make_spec(), _make_target(), config=cfg, manager=manager)

        pool._available.append("vm-a")
        pool._available.append("vm-b")
        assert pool.available_count() == 2

        iid1 = pool.checkout()
        assert pool.available_count() == 1
        assert pool.checked_out_count() == 1
        iid2 = pool.checkout()
        assert pool.available_count() == 0
        assert pool.checked_out_count() == 2
        assert iid1 != iid2

    def test_release_returns_instance_to_available(self) -> None:
        cfg = PoolConfig(min_idle=0, max_size=3)
        manager = MagicMock(spec=VMSandboxManager)
        manager.instances = {}
        manager.boot.side_effect = _make_boot_side_effect()
        pool = VMSandboxPool("mock", _make_spec(), _make_target(), config=cfg, manager=manager)

        pool._available.append("vm-a")
        manager.instances["vm-a"] = _make_instance("vm-a")
        iid = pool.checkout()
        assert iid == "vm-a"
        assert pool.checked_out_count() == 1

        pool.return_instance("vm-a")
        assert pool.available_count() == 1
        assert pool.checked_out_count() == 0

    def test_double_release_is_noop(self) -> None:
        cfg = PoolConfig(min_idle=0, max_size=3)
        manager = MagicMock(spec=VMSandboxManager)
        manager.instances = {"vm-a": _make_instance("vm-a")}
        pool = VMSandboxPool("mock", _make_spec(), _make_target(), config=cfg, manager=manager)

        pool._available.append("vm-a")
        pool.return_instance("vm-a")
        assert pool.available_count() == 1
        pool.return_instance("vm-a")
        assert pool.available_count() == 1

    def test_checkout_updates_last_used_timestamp(self) -> None:
        cfg = PoolConfig(min_idle=0, max_size=3)
        manager = MagicMock(spec=VMSandboxManager)
        manager.instances = {}
        manager.boot.side_effect = _make_boot_side_effect()
        pool = VMSandboxPool("mock", _make_spec(), _make_target(), config=cfg, manager=manager)

        pool._available.append("vm-a")
        before = time.monotonic()
        pool.checkout()
        after = time.monotonic()
        assert before <= pool._last_used["vm-a"] <= after

    def test_return_instance_updates_last_used_timestamp(self) -> None:
        manager = MagicMock(spec=VMSandboxManager)
        manager.instances = {"vm-a": _make_instance("vm-a")}
        pool = VMSandboxPool(
            "mock",
            _make_spec(),
            _make_target(),
            config=PoolConfig(min_idle=0, max_size=3),
            manager=manager,
        )

        pool._available.append("vm-a")
        iid = pool.checkout()
        before = time.monotonic()
        pool.return_instance(iid)
        after = time.monotonic()
        assert before <= pool._last_used["vm-a"] <= after

    def test_checkout_empty_pool_triggers_auto_scale(self) -> None:
        cfg = PoolConfig(min_idle=1, max_size=3, prewarm_count=0)
        manager = MagicMock(spec=VMSandboxManager)
        manager.instances = {}
        manager.boot.side_effect = _make_boot_side_effect()
        pool = VMSandboxPool("mock", _make_spec(), _make_target(), config=cfg, manager=manager)

        iid = pool.checkout()
        assert iid.startswith("vm-")
        assert _boot_counter(manager) >= 1


# ── max pool size ────────────────────────────────────────────────────


class TestMaxPoolSize:
    def test_prewarm_respects_max_size(self) -> None:
        cfg = PoolConfig(prewarm_count=5, max_size=3, min_idle=1)
        manager = MagicMock(spec=VMSandboxManager)
        manager.instances = {}
        manager.boot.side_effect = _make_boot_side_effect()
        pool = VMSandboxPool("mock", _make_spec(), _make_target(), config=cfg, manager=manager)

        count = pool.prewarm()
        assert count == 3
        assert pool.available_count() == 3
        assert _boot_counter(manager) == 3

    def test_auto_scale_stops_at_max_size(self) -> None:
        cfg = PoolConfig(min_idle=3, max_size=3)
        manager = MagicMock(spec=VMSandboxManager)
        manager.instances = {}
        manager.boot.side_effect = _make_boot_side_effect()
        pool = VMSandboxPool("mock", _make_spec(), _make_target(), config=cfg, manager=manager)

        pool._checked_out.add("vm-c1")
        pool._checked_out.add("vm-c2")

        count = pool.auto_scale()
        assert count == 1
        assert pool.available_count() == 1
        assert pool._total_live() == 3

    def test_checkout_at_max_size_raises_when_none_available(self) -> None:
        cfg = PoolConfig(min_idle=0, max_size=2)
        manager = MagicMock(spec=VMSandboxManager)
        manager.instances = {}
        pool = VMSandboxPool("mock", _make_spec(), _make_target(), config=cfg, manager=manager)

        pool._available.append("vm-a")
        pool._available.append("vm-b")
        pool.checkout()
        pool.checkout()

        with pytest.raises(RuntimeError, match="no available VM"):
            pool.checkout()

    def test_auto_scale_does_nothing_when_filled(self) -> None:
        cfg = PoolConfig(min_idle=0, max_size=2)
        manager = MagicMock(spec=VMSandboxManager)
        manager.instances = {}
        manager.boot.side_effect = _make_boot_side_effect()
        pool = VMSandboxPool("mock", _make_spec(), _make_target(), config=cfg, manager=manager)

        pool._available.append("vm-a")
        pool._available.append("vm-b")
        assert pool.auto_scale() == 0

    def test_checkout_does_not_exceed_total_at_max_size(self) -> None:
        cfg = PoolConfig(min_idle=1, max_size=2, prewarm_count=2)
        manager = MagicMock(spec=VMSandboxManager)
        manager.instances = {}
        manager.boot.side_effect = _make_boot_side_effect()
        pool = VMSandboxPool("mock", _make_spec(), _make_target(), config=cfg, manager=manager)

        pool.prewarm()
        pool.checkout()
        pool.checkout()
        assert pool._total_live() == 2
        assert pool.checked_out_count() == 2


# ── warmup ───────────────────────────────────────────────────────────


class TestWarmup:
    def test_prewarm_boots_up_to_prewarm_count(self) -> None:
        cfg = PoolConfig(prewarm_count=3, max_size=10, min_idle=1)
        manager = MagicMock(spec=VMSandboxManager)
        manager.instances = {}
        manager.boot.side_effect = _make_boot_side_effect()
        pool = VMSandboxPool("mock", _make_spec(), _make_target(), config=cfg, manager=manager)

        assert pool.prewarm() == 3
        assert pool.available_count() == 3

    def test_prewarm_idempotent(self) -> None:
        cfg = PoolConfig(prewarm_count=3, max_size=10)
        manager = MagicMock(spec=VMSandboxManager)
        manager.instances = {}
        manager.boot.side_effect = _make_boot_side_effect()
        pool = VMSandboxPool("mock", _make_spec(), _make_target(), config=cfg, manager=manager)

        first = pool.prewarm()
        second = pool.prewarm()
        assert first == 3
        assert second == 0
        assert pool.available_count() == 3

    def test_prewarm_only_boots_deficit(self) -> None:
        cfg = PoolConfig(prewarm_count=4, max_size=10)
        manager = MagicMock(spec=VMSandboxManager)
        manager.instances = {}
        manager.boot.side_effect = _make_boot_side_effect()
        pool = VMSandboxPool("mock", _make_spec(), _make_target(), config=cfg, manager=manager)

        pool._available.append("vm-pre")
        assert pool.prewarm() == 3
        assert pool.available_count() == 4

    def test_prewarm_when_already_fully_warm(self) -> None:
        cfg = PoolConfig(prewarm_count=2, max_size=5)
        manager = MagicMock(spec=VMSandboxManager)
        manager.instances = {}
        pool = VMSandboxPool("mock", _make_spec(), _make_target(), config=cfg, manager=manager)

        pool._available.append("vm-a")
        pool._available.append("vm-b")
        assert pool.prewarm() == 0
        manager.boot.assert_not_called()


# ── eviction ─────────────────────────────────────────────────────────


class TestEviction:
    def test_reap_idle_releases_stale_instances(self) -> None:
        cfg = PoolConfig(min_idle=1, max_size=10, idle_timeout_seconds=0.01)
        manager = MagicMock(spec=VMSandboxManager)
        manager.instances = {
            "vm-a": _make_instance("vm-a"),
            "vm-b": _make_instance("vm-b"),
        }
        pool = VMSandboxPool("mock", _make_spec(), _make_target(), config=cfg, manager=manager)

        pool._available.append("vm-a")
        pool._available.append("vm-b")
        pool._last_used["vm-a"] = 0.0
        pool._last_used["vm-b"] = 0.0

        time.sleep(0.02)
        reaped = pool.reap_idle()
        assert reaped >= 1
        assert pool.available_count() <= 1

    def test_reap_idle_respects_min_idle_floor(self) -> None:
        cfg = PoolConfig(min_idle=2, max_size=5, idle_timeout_seconds=0.01)
        manager = MagicMock(spec=VMSandboxManager)
        manager.instances = {
            "vm-a": _make_instance("vm-a"),
            "vm-b": _make_instance("vm-b"),
            "vm-c": _make_instance("vm-c"),
        }
        pool = VMSandboxPool("mock", _make_spec(), _make_target(), config=cfg, manager=manager)

        pool._available.append("vm-a")
        pool._available.append("vm-b")
        pool._available.append("vm-c")
        pool._last_used["vm-a"] = 0.0
        pool._last_used["vm-b"] = 0.0
        pool._last_used["vm-c"] = 0.0

        time.sleep(0.02)
        pool.reap_idle()
        assert pool.available_count() >= 2

    def test_reap_idle_does_not_evict_recently_used(self) -> None:
        cfg = PoolConfig(min_idle=0, max_size=5, idle_timeout_seconds=3600.0)
        manager = MagicMock(spec=VMSandboxManager)
        manager.instances = {"vm-a": _make_instance("vm-a")}
        pool = VMSandboxPool("mock", _make_spec(), _make_target(), config=cfg, manager=manager)

        pool._available.append("vm-a")
        pool._last_used["vm-a"] = time.monotonic()

        assert pool.reap_idle() == 0
        assert pool.available_count() == 1

    def test_reap_idle_skips_checked_out_instances(self) -> None:
        cfg = PoolConfig(min_idle=0, max_size=5, idle_timeout_seconds=0.01)
        manager = MagicMock(spec=VMSandboxManager)
        manager.instances = {
            "vm-a": _make_instance("vm-a"),
            "vm-b": _make_instance("vm-b"),
        }
        pool = VMSandboxPool("mock", _make_spec(), _make_target(), config=cfg, manager=manager)

        pool._available.append("vm-a")
        pool._last_used["vm-a"] = 0.0
        pool._checked_out.add("vm-b")
        pool._last_used["vm-b"] = 0.0

        time.sleep(0.02)
        pool.reap_idle()
        assert "vm-b" in pool._checked_out

    def test_reap_idle_with_no_available_instances(self) -> None:
        cfg = PoolConfig(min_idle=1, max_size=5, idle_timeout_seconds=0.01)
        manager = MagicMock(spec=VMSandboxManager)
        manager.instances = {}
        pool = VMSandboxPool("mock", _make_spec(), _make_target(), config=cfg, manager=manager)

        assert pool.reap_idle() == 0


# ── object validation (failed instances) ─────────────────────────────


class TestObjectValidation:
    def test_failed_instance_is_quarantined_on_boot(self) -> None:
        cfg = PoolConfig(min_idle=0, max_size=5, prewarm_count=1)
        manager = MagicMock(spec=VMSandboxManager)
        manager.instances = {}
        manager.boot.return_value = _make_instance("vm-fail", state=VMLifecycleState.FAILED)
        pool = VMSandboxPool("mock", _make_spec(), _make_target(), config=cfg, manager=manager)

        assert pool.prewarm() == 0
        assert pool.failed_count() == 1
        assert pool.available_count() == 0

    def test_failed_instance_not_returned_on_checkout(self) -> None:
        cfg = PoolConfig(min_idle=0, max_size=5)
        manager = MagicMock(spec=VMSandboxManager)
        manager.instances = {}
        pool = VMSandboxPool("mock", _make_spec(), _make_target(), config=cfg, manager=manager)

        pool._available.append("vm-good")
        pool._failed.add("vm-bad")
        iid = pool.checkout()
        assert iid == "vm-good"
        assert pool.failed_count() == 1

    def test_return_failed_instance_is_noop(self) -> None:
        cfg = PoolConfig(min_idle=0, max_size=5)
        manager = MagicMock(spec=VMSandboxManager)
        manager.instances = {"vm-bad": _make_instance("vm-bad")}
        pool = VMSandboxPool("mock", _make_spec(), _make_target(), config=cfg, manager=manager)

        pool._failed.add("vm-bad")
        pool.return_instance("vm-bad")
        assert pool.available_count() == 0
        assert pool.failed_count() == 1

    def test_failed_instances_excluded_from_total_stats(self) -> None:
        cfg = PoolConfig(min_idle=0, max_size=5)
        manager = MagicMock(spec=VMSandboxManager)
        manager.instances = {}
        pool = VMSandboxPool("mock", _make_spec(), _make_target(), config=cfg, manager=manager)

        pool._available.append("vm-a")
        pool._checked_out.add("vm-b")
        pool._failed.add("vm-fail")
        assert pool.stats().total == 2
        assert pool.stats().failed == 1

    def test_mark_failed_during_checkout_excludes_from_pool(self) -> None:
        cfg = PoolConfig(min_idle=0, max_size=3)
        manager = MagicMock(spec=VMSandboxManager)
        manager.instances = {}
        pool = VMSandboxPool("mock", _make_spec(), _make_target(), config=cfg, manager=manager)

        pool._available.append("vm-x")
        pool._mark_failed("vm-x")
        assert pool.available_count() == 0
        assert pool.failed_count() == 1

        with pytest.raises(RuntimeError, match="no available VM"):
            pool.checkout()


# ── resource limits ──────────────────────────────────────────────────


class TestResourceLimits:
    def test_shutdown_blocks_prewarm(self) -> None:
        cfg = PoolConfig(prewarm_count=5, max_size=10)
        manager = MagicMock(spec=VMSandboxManager)
        manager.instances = {}
        pool = VMSandboxPool("mock", _make_spec(), _make_target(), config=cfg, manager=manager)

        pool.shutdown()
        assert pool.prewarm() == 0

    def test_shutdown_blocks_auto_scale(self) -> None:
        manager = MagicMock(spec=VMSandboxManager)
        manager.instances = {}
        pool = VMSandboxPool(
            "mock",
            _make_spec(),
            _make_target(),
            config=PoolConfig(min_idle=3, max_size=5),
            manager=manager,
        )

        pool.shutdown()
        assert pool.auto_scale() == 0

    def test_shutdown_blocks_checkout(self) -> None:
        manager = MagicMock(spec=VMSandboxManager)
        manager.instances = {}
        pool = VMSandboxPool(
            "mock",
            _make_spec(),
            _make_target(),
            config=PoolConfig(),
            manager=manager,
        )

        pool.shutdown()
        with pytest.raises(RuntimeError, match="shut down"):
            pool.checkout()

    def test_shutdown_blocks_reap_idle(self) -> None:
        cfg = PoolConfig(min_idle=1, max_size=5)
        manager = MagicMock(spec=VMSandboxManager)
        manager.instances = {}
        pool = VMSandboxPool("mock", _make_spec(), _make_target(), config=cfg, manager=manager)

        pool._available.append("vm-a")
        pool.shutdown()
        assert pool.reap_idle() == 0

    def test_shutdown_releases_all_instances(self) -> None:
        cfg = PoolConfig(min_idle=0, max_size=5)
        manager = MagicMock(spec=VMSandboxManager)
        manager.instances = {
            "vm-a": _make_instance("vm-a"),
            "vm-b": _make_instance("vm-b"),
        }
        pool = VMSandboxPool("mock", _make_spec(), _make_target(), config=cfg, manager=manager)

        pool._available.append("vm-a")
        pool._checked_out.add("vm-b")
        pool.shutdown()

        assert pool.available_count() == 0
        assert pool.checked_out_count() == 0
        assert manager.release.call_count == 2

    def test_min_idle_exceeds_max_size_raises(self) -> None:
        with pytest.raises(ValueError, match="min_idle"):
            PoolConfig(min_idle=3, max_size=2)

    def test_min_idle_zero_auto_scale_noop(self) -> None:
        cfg = PoolConfig(min_idle=0, max_size=5)
        manager = MagicMock(spec=VMSandboxManager)
        manager.instances = {}
        pool = VMSandboxPool("mock", _make_spec(), _make_target(), config=cfg, manager=manager)

        assert pool.auto_scale() == 0
        manager.boot.assert_not_called()


# ── stats and observability ──────────────────────────────────────────


class TestStatsObservability:
    def test_stats_total_excludes_failed(self) -> None:
        cfg = PoolConfig(min_idle=1, max_size=10)
        manager = MagicMock(spec=VMSandboxManager)
        manager.instances = {}
        pool = VMSandboxPool("mock", _make_spec(), _make_target(), config=cfg, manager=manager)

        pool._available.append("vm-a")
        pool._available.append("vm-b")
        pool._checked_out.add("vm-c")
        pool._failed.add("vm-d")
        pool._failed.add("vm-e")

        stats = pool.stats()
        assert stats.available == 2
        assert stats.checked_out == 1
        assert stats.failed == 2
        assert stats.total == 3

    def test_stats_reflects_config(self) -> None:
        cfg = PoolConfig(min_idle=3, max_size=7)
        manager = MagicMock(spec=VMSandboxManager)
        manager.instances = {}
        pool = VMSandboxPool("mock", _make_spec(), _make_target(), config=cfg, manager=manager)

        stats = pool.stats()
        assert stats.min_idle == 3
        assert stats.max_size == 7
