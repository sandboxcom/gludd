"""Unit tests for VM sandbox pool manager — NF.2 P8.

Covers: pre-warm, checkout/return, auto-scale up/down, idle reaping,
config validation, metrics, and integration with VMSandboxManager.
"""

from __future__ import annotations

import time
from unittest import mock

import pytest

from general_ludd.security.permissions import PermissionSpec
from general_ludd.security.sandboxes import SandboxHandle, SandboxTarget


@pytest.fixture(autouse=True)
def _isolate_cache_dir(tmp_path, monkeypatch):
    cache = tmp_path / "sandbox-cache"
    cache.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(
        "general_ludd.security.sandboxes.vm.image_builder.CACHE_DIR", cache
    )
    return cache


@pytest.fixture()
def sample_spec():
    return PermissionSpec(agent_type="pool-agent")


@pytest.fixture()
def sample_target():
    return SandboxTarget(pid=99999)


def _make_handle(applied: bool = True, backend: str = "firecracker") -> SandboxHandle:
    return SandboxHandle(
        backend=backend,
        token="gludd-pool",
        applied=applied,
        extra={"stub": True} if applied else {"reason": "absent"},
    )


def _patch_fc_apply(applied: bool = True):
    """Context manager pair patching FirecrackerBackend.available + apply."""
    return (
        mock.patch(
            "general_ludd.security.sandboxes.vm.firecracker_backend.FirecrackerBackend.available",
            return_value=True,
        ),
        mock.patch(
            "general_ludd.security.sandboxes.vm.firecracker_backend.FirecrackerBackend.apply",
            return_value=_make_handle(applied=applied),
        ),
    )


# ---------------------------------------------------------------------------
# Module / exports
# ---------------------------------------------------------------------------


def test_pool_module_exports_required_names():
    from general_ludd.security.sandboxes.vm import pool

    for name in ("VMSandboxPool", "PoolConfig", "PoolStats"):
        assert hasattr(pool, name), f"pool missing {name}"


def test_pool_re_exported_from_vm_init():
    from general_ludd.security.sandboxes.vm import VMSandboxPool

    assert VMSandboxPool is not None


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


def test_pool_config_defaults():
    from general_ludd.security.sandboxes.vm.pool import PoolConfig

    cfg = PoolConfig()
    assert cfg.min_idle == 1
    assert cfg.max_size == 5
    assert cfg.idle_timeout_seconds == 300.0
    assert cfg.prewarm_count == 1


def test_pool_config_rejects_min_idle_gt_max_size():
    from general_ludd.security.sandboxes.vm.pool import PoolConfig

    with pytest.raises(ValueError, match=r"min_idle.*max_size"):
        PoolConfig(min_idle=10, max_size=5)


def test_pool_config_rejects_non_positive_sizes():
    from general_ludd.security.sandboxes.vm.pool import PoolConfig

    with pytest.raises(ValueError, match="max_size"):
        PoolConfig(max_size=0)
    with pytest.raises(ValueError, match="min_idle"):
        PoolConfig(min_idle=-1)


# ---------------------------------------------------------------------------
# Pre-warm
# ---------------------------------------------------------------------------


def test_prewarm_boots_n_instances(sample_spec, sample_target):
    from general_ludd.security.sandboxes.vm.lifecycle import VMLifecycleState
    from general_ludd.security.sandboxes.vm.pool import PoolConfig, VMSandboxPool

    pool = VMSandboxPool(
        backend_name="firecracker",
        spec=sample_spec,
        target=sample_target,
        config=PoolConfig(prewarm_count=3, min_idle=3, max_size=5),
    )
    av, ap = _patch_fc_apply()
    with av, ap as fake_apply:
        pool.prewarm()
    assert fake_apply.call_count == 3
    assert pool.available_count() == 3
    assert pool.checked_out_count() == 0
    for inst in pool.manager.list_instances():
        assert inst.state is VMLifecycleState.RUNNING


def test_prewarm_idempotent_does_not_exceed_prewarm_count(sample_spec, sample_target):
    from general_ludd.security.sandboxes.vm.pool import PoolConfig, VMSandboxPool

    pool = VMSandboxPool(
        backend_name="firecracker",
        spec=sample_spec,
        target=sample_target,
        config=PoolConfig(prewarm_count=2, min_idle=2, max_size=5),
    )
    av, ap = _patch_fc_apply()
    with av, ap as fake_apply:
        pool.prewarm()
        pool.prewarm()
    assert fake_apply.call_count == 2
    assert pool.available_count() == 2


def test_prewarm_zero_is_noop(sample_spec, sample_target):
    from general_ludd.security.sandboxes.vm.pool import PoolConfig, VMSandboxPool

    pool = VMSandboxPool(
        backend_name="firecracker",
        spec=sample_spec,
        target=sample_target,
        config=PoolConfig(prewarm_count=0, min_idle=0, max_size=5),
    )
    av, ap = _patch_fc_apply()
    with av, ap as fake_apply:
        pool.prewarm()
    assert fake_apply.call_count == 0
    assert pool.available_count() == 0


# ---------------------------------------------------------------------------
# Checkout / return
# ---------------------------------------------------------------------------


def test_checkout_returns_instance_id(sample_spec, sample_target):
    from general_ludd.security.sandboxes.vm.pool import PoolConfig, VMSandboxPool

    pool = VMSandboxPool(
        backend_name="firecracker",
        spec=sample_spec,
        target=sample_target,
        config=PoolConfig(prewarm_count=1, min_idle=0, max_size=3),
    )
    av, ap = _patch_fc_apply()
    with av, ap:
        pool.prewarm()
        iid = pool.checkout()
    assert isinstance(iid, str)
    assert iid in pool.manager.instances
    assert pool.available_count() == 0
    assert pool.checked_out_count() == 1


def test_checkout_raises_when_pool_empty(sample_spec, sample_target):
    from general_ludd.security.sandboxes.vm.pool import PoolConfig, VMSandboxPool

    pool = VMSandboxPool(
        backend_name="firecracker",
        spec=sample_spec,
        target=sample_target,
        config=PoolConfig(prewarm_count=0, min_idle=0, max_size=2),
    )
    av, ap = _patch_fc_apply()
    with av, ap:
        pool.prewarm()
        with pytest.raises(RuntimeError, match="no available"):
            pool.checkout()


def test_checkout_auto_boots_when_under_min_idle(sample_spec, sample_target):
    from general_ludd.security.sandboxes.vm.pool import PoolConfig, VMSandboxPool

    pool = VMSandboxPool(
        backend_name="firecracker",
        spec=sample_spec,
        target=sample_target,
        config=PoolConfig(prewarm_count=1, min_idle=1, max_size=3),
    )
    av, ap = _patch_fc_apply()
    with av, ap as fake_apply:
        pool.prewarm()
        assert fake_apply.call_count == 1
        pool.checkout()
    # min_idle=1, pool now has 0 available → auto-boot one more
    assert fake_apply.call_count == 2
    assert pool.available_count() == 1
    assert pool.checked_out_count() == 1


def test_checkout_does_not_exceed_max_size(sample_spec, sample_target):
    from general_ludd.security.sandboxes.vm.pool import PoolConfig, VMSandboxPool

    pool = VMSandboxPool(
        backend_name="firecracker",
        spec=sample_spec,
        target=sample_target,
        config=PoolConfig(prewarm_count=2, min_idle=2, max_size=2),
    )
    av, ap = _patch_fc_apply()
    with av, ap as fake_apply:
        pool.prewarm()
        pool.checkout()
        pool.checkout()
    # max_size=2, both checked out, cannot auto-boot more
    assert fake_apply.call_count == 2
    assert pool.available_count() == 0
    assert pool.checked_out_count() == 2
    with pytest.raises(RuntimeError, match="no available"):
        pool.checkout()


def test_return_makes_instance_available_again(sample_spec, sample_target):
    from general_ludd.security.sandboxes.vm.pool import PoolConfig, VMSandboxPool

    pool = VMSandboxPool(
        backend_name="firecracker",
        spec=sample_spec,
        target=sample_target,
        config=PoolConfig(prewarm_count=1, min_idle=0, max_size=3),
    )
    av, ap = _patch_fc_apply()
    with av, ap:
        pool.prewarm()
        iid = pool.checkout()
        assert pool.available_count() == 0
        pool.return_instance(iid)
    assert pool.available_count() == 1
    assert pool.checked_out_count() == 0


def test_return_unknown_instance_raises(sample_spec, sample_target):
    from general_ludd.security.sandboxes.vm.pool import PoolConfig, VMSandboxPool

    pool = VMSandboxPool(
        backend_name="firecracker",
        spec=sample_spec,
        target=sample_target,
        config=PoolConfig(prewarm_count=0, min_idle=0, max_size=2),
    )
    av, ap = _patch_fc_apply()
    with av, ap, pytest.raises(KeyError, match="not registered"):
        pool.return_instance("nonexistent")


def test_return_idempotent_on_already_available(sample_spec, sample_target):
    from general_ludd.security.sandboxes.vm.pool import PoolConfig, VMSandboxPool

    pool = VMSandboxPool(
        backend_name="firecracker",
        spec=sample_spec,
        target=sample_target,
        config=PoolConfig(prewarm_count=1, min_idle=1, max_size=3),
    )
    av, ap = _patch_fc_apply()
    with av, ap:
        pool.prewarm()
        iid = pool.manager.list_instances()[0].instance_id
        # Returning an instance that was never checked out is idempotent
        pool.return_instance(iid)
    assert pool.available_count() == 1


# ---------------------------------------------------------------------------
# Auto-scale
# ---------------------------------------------------------------------------


def test_auto_scale_up_boots_to_min_idle(sample_spec, sample_target):
    from general_ludd.security.sandboxes.vm.pool import PoolConfig, VMSandboxPool

    pool = VMSandboxPool(
        backend_name="firecracker",
        spec=sample_spec,
        target=sample_target,
        config=PoolConfig(prewarm_count=0, min_idle=2, max_size=5),
    )
    av, ap = _patch_fc_apply()
    with av, ap as fake_apply:
        pool.auto_scale()
    assert fake_apply.call_count == 2
    assert pool.available_count() == 2


def test_auto_scale_noop_when_already_at_min(sample_spec, sample_target):
    from general_ludd.security.sandboxes.vm.pool import PoolConfig, VMSandboxPool

    pool = VMSandboxPool(
        backend_name="firecracker",
        spec=sample_spec,
        target=sample_target,
        config=PoolConfig(prewarm_count=2, min_idle=2, max_size=5),
    )
    av, ap = _patch_fc_apply()
    with av, ap as fake_apply:
        pool.prewarm()
        pool.auto_scale()
    assert fake_apply.call_count == 2


def test_auto_scale_respects_max_size_ceiling(sample_spec, sample_target):
    from general_ludd.security.sandboxes.vm.pool import PoolConfig, VMSandboxPool

    pool = VMSandboxPool(
        backend_name="firecracker",
        spec=sample_spec,
        target=sample_target,
        config=PoolConfig(prewarm_count=0, min_idle=3, max_size=3),
    )
    av, ap = _patch_fc_apply()
    with av, ap as fake_apply:
        pool.auto_scale()
    assert fake_apply.call_count == 3
    assert pool.available_count() == 3


# ---------------------------------------------------------------------------
# Idle reaping
# ---------------------------------------------------------------------------


def test_reap_idle_releases_stale_instances(sample_spec, sample_target):
    from general_ludd.security.sandboxes.vm.lifecycle import VMLifecycleState
    from general_ludd.security.sandboxes.vm.pool import PoolConfig, VMSandboxPool

    pool = VMSandboxPool(
        backend_name="firecracker",
        spec=sample_spec,
        target=sample_target,
        config=PoolConfig(prewarm_count=3, min_idle=1, max_size=5,
                          idle_timeout_seconds=1.0),
    )
    av, ap = _patch_fc_apply()
    with av, ap, \
         mock.patch(
             "general_ludd.security.sandboxes.vm.firecracker_backend.FirecrackerBackend.release"
         ) as fake_release:
        pool.prewarm()
        assert pool.available_count() == 3
        # Force all instances to look 10s old
        for iid in list(pool._available):
            pool._last_used[iid] = time.monotonic() - 10.0
        reaped = pool.reap_idle()
    assert reaped == 2  # 3 total - min_idle(1) = 2 reaped
    assert pool.available_count() == 1
    assert fake_release.call_count == 2
    reaped_insts = [i for i in pool.manager.list_instances()
                    if i.state is VMLifecycleState.STOPPED]
    assert len(reaped_insts) == 2


def test_reap_idle_never_below_min_idle(sample_spec, sample_target):
    from general_ludd.security.sandboxes.vm.pool import PoolConfig, VMSandboxPool

    pool = VMSandboxPool(
        backend_name="firecracker",
        spec=sample_spec,
        target=sample_target,
        config=PoolConfig(prewarm_count=2, min_idle=2, max_size=5,
                          idle_timeout_seconds=1.0),
    )
    av, ap = _patch_fc_apply()
    with av, ap, \
         mock.patch(
             "general_ludd.security.sandboxes.vm.firecracker_backend.FirecrackerBackend.release"
         ):
        pool.prewarm()
        for iid in list(pool._available):
            pool._last_used[iid] = time.monotonic() - 100.0
        reaped = pool.reap_idle()
    assert reaped == 0
    assert pool.available_count() == 2


def test_reap_idle_skips_fresh_instances(sample_spec, sample_target):
    from general_ludd.security.sandboxes.vm.pool import PoolConfig, VMSandboxPool

    pool = VMSandboxPool(
        backend_name="firecracker",
        spec=sample_spec,
        target=sample_target,
        config=PoolConfig(prewarm_count=3, min_idle=1, max_size=5,
                          idle_timeout_seconds=600.0),
    )
    av, ap = _patch_fc_apply()
    with av, ap, \
         mock.patch(
             "general_ludd.security.sandboxes.vm.firecracker_backend.FirecrackerBackend.release"
         ) as fake_release:
        pool.prewarm()
        reaped = pool.reap_idle()
    assert reaped == 0
    assert fake_release.call_count == 0
    assert pool.available_count() == 3


def test_reap_idle_does_not_touch_checked_out(sample_spec, sample_target):
    from general_ludd.security.sandboxes.vm.pool import PoolConfig, VMSandboxPool

    pool = VMSandboxPool(
        backend_name="firecracker",
        spec=sample_spec,
        target=sample_target,
        config=PoolConfig(prewarm_count=2, min_idle=0, max_size=5,
                          idle_timeout_seconds=1.0),
    )
    av, ap = _patch_fc_apply()
    with av, ap, \
         mock.patch(
             "general_ludd.security.sandboxes.vm.firecracker_backend.FirecrackerBackend.release"
         ) as fake_release:
        pool.prewarm()
        checked_iid = pool.checkout()
        # Age the remaining available
        for iid in list(pool._available):
            pool._last_used[iid] = time.monotonic() - 100.0
        reaped = pool.reap_idle()
    assert reaped == 1
    # checked-out instance is untouched
    assert checked_iid in pool._checked_out
    assert fake_release.call_count == 1


# ---------------------------------------------------------------------------
# Stats / observability
# ---------------------------------------------------------------------------


def test_pool_stats_reflects_state(sample_spec, sample_target):
    from general_ludd.security.sandboxes.vm.pool import PoolConfig, PoolStats, VMSandboxPool

    pool = VMSandboxPool(
        backend_name="firecracker",
        spec=sample_spec,
        target=sample_target,
        config=PoolConfig(prewarm_count=2, min_idle=2, max_size=5),
    )
    av, ap = _patch_fc_apply()
    with av, ap:
        pool.prewarm()
        pool.checkout()
    stats = pool.stats()
    assert isinstance(stats, PoolStats)
    assert stats.available == 2  # auto-scaled back to min_idle
    assert stats.checked_out == 1
    assert stats.total == 3
    assert stats.max_size == 5
    assert stats.min_idle == 2


def test_pool_stats_empty_pool():
    from general_ludd.security.sandboxes.vm.pool import PoolConfig, VMSandboxPool

    pool = VMSandboxPool(
        backend_name="firecracker",
        spec=PermissionSpec(agent_type="x"),
        target=SandboxTarget(pid=1),
        config=PoolConfig(prewarm_count=0, min_idle=0, max_size=3),
    )
    s = pool.stats()
    assert s.available == 0
    assert s.checked_out == 0
    assert s.total == 0


# ---------------------------------------------------------------------------
# Shutdown
# ---------------------------------------------------------------------------


def test_shutdown_releases_all(sample_spec, sample_target):
    from general_ludd.security.sandboxes.vm.lifecycle import VMLifecycleState
    from general_ludd.security.sandboxes.vm.pool import PoolConfig, VMSandboxPool

    pool = VMSandboxPool(
        backend_name="firecracker",
        spec=sample_spec,
        target=sample_target,
        config=PoolConfig(prewarm_count=2, min_idle=0, max_size=5),
    )
    av, ap = _patch_fc_apply()
    with av, ap, \
         mock.patch(
             "general_ludd.security.sandboxes.vm.firecracker_backend.FirecrackerBackend.release"
         ) as fake_release:
        pool.prewarm()
        pool.checkout()
        pool.shutdown()
    assert fake_release.call_count == 2
    assert pool.available_count() == 0
    assert pool.checked_out_count() == 0
    for inst in pool.manager.list_instances():
        assert inst.state is VMLifecycleState.STOPPED


def test_shutdown_idempotent(sample_spec, sample_target):
    from general_ludd.security.sandboxes.vm.pool import PoolConfig, VMSandboxPool

    pool = VMSandboxPool(
        backend_name="firecracker",
        spec=sample_spec,
        target=sample_target,
        config=PoolConfig(prewarm_count=1, min_idle=1, max_size=3),
    )
    av, ap = _patch_fc_apply()
    with av, ap, \
         mock.patch(
             "general_ludd.security.sandboxes.vm.firecracker_backend.FirecrackerBackend.release"
         ) as fake_release:
        pool.prewarm()
        pool.shutdown()
        pool.shutdown()
    assert fake_release.call_count == 1


# ---------------------------------------------------------------------------
# Failed instances
# ---------------------------------------------------------------------------


def test_checkout_skips_failed_instances(sample_spec, sample_target):
    from general_ludd.security.sandboxes.vm.lifecycle import VMLifecycleState
    from general_ludd.security.sandboxes.vm.pool import PoolConfig, VMSandboxPool

    pool = VMSandboxPool(
        backend_name="firecracker",
        spec=sample_spec,
        target=sample_target,
        config=PoolConfig(prewarm_count=2, min_idle=0, max_size=5),
    )
    av, ap = _patch_fc_apply()
    with av, ap:
        pool.prewarm()
    # Force one instance to FAILED via the public quarantine helper
    insts = pool.manager.list_instances()
    insts[0].state = VMLifecycleState.FAILED
    pool._mark_failed(insts[0].instance_id)
    with av, ap:
        iid = pool.checkout()
    assert iid != insts[0].instance_id
    assert pool.checked_out_count() == 1


def test_failed_instance_not_counted_as_available(sample_spec, sample_target):
    from general_ludd.security.sandboxes.vm.lifecycle import VMLifecycleState
    from general_ludd.security.sandboxes.vm.pool import PoolConfig, VMSandboxPool

    pool = VMSandboxPool(
        backend_name="firecracker",
        spec=sample_spec,
        target=sample_target,
        config=PoolConfig(prewarm_count=2, min_idle=2, max_size=5),
    )
    av, ap = _patch_fc_apply()
    with av, ap:
        pool.prewarm()
    insts = pool.manager.list_instances()
    insts[0].state = VMLifecycleState.FAILED
    pool._mark_failed(insts[0].instance_id)
    assert pool.available_count() == 1
    assert insts[0].instance_id in pool._failed
