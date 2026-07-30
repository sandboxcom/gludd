"""Tests for ``src/general_ludd/security/sandboxes/vm/pool.py``.

Covers PoolConfig validation, PoolStats, and VMSandboxPool basic operations.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from general_ludd.security.permissions import PermissionSpec
from general_ludd.security.sandboxes import SandboxTarget
from general_ludd.security.sandboxes.vm.lifecycle import VMLifecycleState
from general_ludd.security.sandboxes.vm.pool import (
    PoolConfig,
    PoolStats,
    VMSandboxPool,
)


class TestPoolConfig:
    def test_defaults(self) -> None:
        cfg = PoolConfig()
        assert cfg.min_idle == 1
        assert cfg.max_size == 5
        assert cfg.prewarm_count == 1
        assert cfg.idle_timeout_seconds == 300.0

    def test_max_size_below_1_raises(self) -> None:
        with pytest.raises(ValueError, match="max_size"):
            PoolConfig(max_size=0)

    def test_min_idle_negative_raises(self) -> None:
        with pytest.raises(ValueError, match="min_idle"):
            PoolConfig(min_idle=-1)

    def test_min_idle_exceeds_max_size_raises(self) -> None:
        with pytest.raises(ValueError, match="min_idle"):
            PoolConfig(min_idle=10, max_size=5)

    def test_prewarm_count_negative_raises(self) -> None:
        with pytest.raises(ValueError, match="prewarm_count"):
            PoolConfig(prewarm_count=-1)

    def test_prewarm_count_clamped_to_max_size(self) -> None:
        cfg = PoolConfig(prewarm_count=10, max_size=3)
        assert cfg.prewarm_count == 3

    def test_idle_timeout_negative_raises(self) -> None:
        with pytest.raises(ValueError, match="idle_timeout_seconds"):
            PoolConfig(idle_timeout_seconds=-1)

    def test_valid_custom_config(self) -> None:
        cfg = PoolConfig(
            min_idle=2, max_size=10, prewarm_count=4, idle_timeout_seconds=600.0
        )
        assert cfg.min_idle == 2
        assert cfg.max_size == 10
        assert cfg.prewarm_count == 4
        assert cfg.idle_timeout_seconds == 600.0

    def test_min_idle_zero_allowed(self) -> None:
        cfg = PoolConfig(min_idle=0, max_size=5)
        assert cfg.min_idle == 0


class TestPoolStats:
    def test_defaults(self) -> None:
        stats = PoolStats()
        assert stats.available == 0
        assert stats.checked_out == 0
        assert stats.failed == 0
        assert stats.total == 0
        assert stats.min_idle == 0
        assert stats.max_size == 0

    def test_custom_values(self) -> None:
        stats = PoolStats(
            available=3,
            checked_out=2,
            failed=1,
            total=6,
            min_idle=2,
            max_size=10,
        )
        assert stats.available == 3
        assert stats.checked_out == 2
        assert stats.failed == 1
        assert stats.total == 6

    def test_as_dict(self) -> None:
        stats = PoolStats(available=3, checked_out=1, min_idle=2, max_size=10)
        d = stats.as_dict()
        assert d == {
            "available": 3,
            "checked_out": 1,
            "failed": 0,
            "total": 0,
            "min_idle": 2,
            "max_size": 10,
        }


def _make_spec() -> PermissionSpec:
    return PermissionSpec(agent_type="test")


def _make_target() -> SandboxTarget:
    return SandboxTarget()


class TestVMSandboxPoolConstruction:
    def test_construct_pool_defaults(self) -> None:
        pool = VMSandboxPool(
            backend_name="qemu",
            spec=_make_spec(),
            target=_make_target(),
        )
        assert pool.available_count() == 0
        assert pool.checked_out_count() == 0
        assert pool.failed_count() == 0
        assert pool.config.min_idle == 1
        assert pool.config.max_size == 5

    def test_construct_with_custom_config(self) -> None:
        cfg = PoolConfig(min_idle=0, max_size=3, prewarm_count=0)
        pool = VMSandboxPool(
            backend_name="qemu",
            spec=_make_spec(),
            target=_make_target(),
            config=cfg,
        )
        assert pool.config.min_idle == 0

    def test_stats_empty_pool(self) -> None:
        pool = VMSandboxPool(
            backend_name="qemu",
            spec=_make_spec(),
            target=_make_target(),
        )
        stats = pool.stats()
        assert stats.available == 0
        assert stats.checked_out == 0
        assert stats.total == 0
        assert stats.as_dict()["max_size"] == 5

    def test_active_pool_prewarms_scales_returns_reaps_and_shuts_down(self) -> None:
        manager = MagicMock()
        manager.instances = {}
        sequence = iter(("vm-1", "vm-2", "vm-3"))

        def boot(*_args):
            instance_id = next(sequence)
            instance = SimpleNamespace(
                instance_id=instance_id,
                state=VMLifecycleState.RUNNING,
            )
            manager.instances[instance_id] = instance
            return instance

        manager.boot.side_effect = boot
        pool = VMSandboxPool(
            backend_name="qemu",
            spec=_make_spec(),
            target=_make_target(),
            config=PoolConfig(
                min_idle=2,
                max_size=3,
                prewarm_count=2,
                idle_timeout_seconds=0,
            ),
            manager=manager,
        )

        assert pool.prewarm() == 2
        assert pool.prewarm() == 0
        leased = pool.checkout()
        assert leased == "vm-1"
        assert pool.available_count() == 2
        assert pool.checked_out_count() == 1

        pool.return_instance(leased)
        assert pool.available_count() == 3
        assert pool.checked_out_count() == 0

        assert pool.reap_idle() == 1
        assert pool.available_count() == 2
        assert manager.release.call_count == 1

        pool.shutdown()
        assert manager.release.call_count == 3
        assert pool.available_count() == 0

    def test_prewarm_shutdown_pool_returns_zero(self) -> None:
        pool = VMSandboxPool(
            backend_name="qemu",
            spec=_make_spec(),
            target=_make_target(),
        )
        pool._shutdown = True
        assert pool.prewarm() == 0

    def test_auto_scale_shutdown_pool_returns_zero(self) -> None:
        pool = VMSandboxPool(
            backend_name="qemu",
            spec=_make_spec(),
            target=_make_target(),
        )
        pool._shutdown = True
        assert pool.auto_scale() == 0

    def test_checkout_shutdown_pool_raises(self) -> None:
        pool = VMSandboxPool(
            backend_name="qemu",
            spec=_make_spec(),
            target=_make_target(),
        )
        pool._shutdown = True
        with pytest.raises(RuntimeError, match="shut down"):
            pool.checkout()

    def test_checkout_empty_pool_raises(self) -> None:
        pool = VMSandboxPool(
            backend_name="qemu",
            spec=_make_spec(),
            target=_make_target(),
        )
        with pytest.raises(RuntimeError, match="no available VM"):
            pool.checkout()

    def test_return_unregistered_instance_raises(self) -> None:
        pool = VMSandboxPool(
            backend_name="qemu",
            spec=_make_spec(),
            target=_make_target(),
        )
        with pytest.raises(KeyError, match="not registered"):
            pool.return_instance("nonexistent-id")

    def test_return_instance_not_checked_out_is_noop(self) -> None:
        pool = VMSandboxPool(
            backend_name="qemu",
            spec=_make_spec(),
            target=_make_target(),
        )
        pool.manager.instances["inst-x"] = None  # type: ignore[index]
        pool.return_instance("inst-x")
        assert pool.checked_out_count() == 0
        assert pool.available_count() == 0

    def test_reap_idle_empty_pool(self) -> None:
        pool = VMSandboxPool(
            backend_name="qemu",
            spec=_make_spec(),
            target=_make_target(),
        )
        assert pool.reap_idle() == 0

    def test_reap_idle_shutdown_pool(self) -> None:
        pool = VMSandboxPool(
            backend_name="qemu",
            spec=_make_spec(),
            target=_make_target(),
        )
        pool._shutdown = True
        assert pool.reap_idle() == 0

    def test_shutdown_idempotent(self) -> None:
        pool = VMSandboxPool(
            backend_name="qemu",
            spec=_make_spec(),
            target=_make_target(),
        )
        pool.shutdown()
        pool.shutdown()

    def test_mark_failed_quarantines_instance(self) -> None:
        pool = VMSandboxPool(
            backend_name="qemu",
            spec=_make_spec(),
            target=_make_target(),
        )
        pool._available.append("inst-1")
        pool._checked_out.add("inst-2")
        pool._mark_failed("inst-1")
        pool._mark_failed("inst-2")
        assert pool._failed == {"inst-1", "inst-2"}
        assert pool.available_count() == 0
        assert pool.checked_out_count() == 0
        assert pool.failed_count() == 2

    def test_return_failed_instance_returns_early(self) -> None:
        pool = VMSandboxPool(
            backend_name="qemu",
            spec=_make_spec(),
            target=_make_target(),
        )
        pool.manager.instances["inst-1"] = None  # type: ignore[index]
        pool._failed.add("inst-1")
        pool.return_instance("inst-1")
        assert "inst-1" in pool._failed
        assert pool.checked_out_count() == 0

    def test_stats_reflects_pool_state(self) -> None:
        pool = VMSandboxPool(
            backend_name="qemu",
            spec=_make_spec(),
            target=_make_target(),
        )
        pool._available.append("a1")
        pool._available.append("a2")
        pool._checked_out.add("c1")
        pool._failed.add("f1")
        stats = pool.stats()
        assert stats.available == 2
        assert stats.checked_out == 1
        assert stats.failed == 1
        assert stats.total == 3
