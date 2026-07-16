"""Unit tests for VM sandbox observability metrics — P7 dashboard/health layer.

Covers: VMSandboxMetricsCollector structured export (dict + Prometheus text),
VMSandboxMetricsSnapshot aggregation (per-state, per-backend, p95 boot time),
VMSandboxHealth verdicts (empty/healthy/degraded/unhealthy), and time-series
snapshot history for the daemon observability surface.
"""

from __future__ import annotations

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
    return PermissionSpec(agent_type="test-agent")


@pytest.fixture()
def sample_target():
    return SandboxTarget(pid=99999)


def _make_handle(applied: bool = True, backend: str = "firecracker") -> SandboxHandle:
    return SandboxHandle(
        backend=backend,
        token="gludd-test",
        applied=applied,
        extra={"stub": True} if applied else {"reason": "absent"},
    )


def _boot_one(mgr, backend_name, spec, target):
    """Boot one instance via mocked backend, return the instance."""
    patch_avail = mock.patch(
        f"general_ludd.security.sandboxes.vm.{backend_name}_backend."
        f"{'Firecracker' if backend_name == 'firecracker' else 'Gvisor'}Backend.available",
        return_value=True,
    )
    cls = "Firecracker" if backend_name == "firecracker" else "Gvisor"
    patch_apply = mock.patch(
        f"general_ludd.security.sandboxes.vm.{backend_name}_backend.{cls}Backend.apply",
        return_value=_make_handle(applied=True, backend=backend_name),
    )
    with patch_avail, patch_apply:
        return mgr.boot(backend_name, spec, target)


# ---------------------------------------------------------------------------
# Module exports / structural
# ---------------------------------------------------------------------------


def test_metrics_module_exports_required_names():
    from general_ludd.security.sandboxes.vm import metrics as mod

    for name in (
        "VMSandboxMetricsCollector",
        "VMSandboxMetricsSnapshot",
        "VMSandboxHealth",
    ):
        assert hasattr(mod, name), f"metrics module missing {name}"


def test_snapshot_defaults_are_zeroed():
    from general_ludd.security.sandboxes.vm.metrics import VMSandboxMetricsSnapshot

    snap = VMSandboxMetricsSnapshot()
    assert snap.total_instances == 0
    assert snap.running_instances == 0
    assert snap.failed_instances == 0
    assert snap.total_dispatches == 0
    assert snap.total_verify_findings == 0
    assert snap.avg_boot_ms == 0.0
    assert snap.p95_boot_ms == 0.0
    assert snap.events_emitted == 0
    assert snap.state_breakdown == {}
    assert snap.backend_breakdown == {}
    assert snap.timestamp > 0.0


def test_health_status_field_accepts_known_values():
    from general_ludd.security.sandboxes.vm.metrics import VMSandboxHealth

    for status in ("healthy", "degraded", "unhealthy", "empty"):
        h = VMSandboxHealth(status=status, issues=[], metrics=None)
        assert h.status == status
        assert h.issues == []
        assert h.metrics is None


# ---------------------------------------------------------------------------
# Collector with no manager attached
# ---------------------------------------------------------------------------


def test_collector_no_manager_collect_returns_empty_snapshot():
    from general_ludd.security.sandboxes.vm.metrics import VMSandboxMetricsCollector

    collector = VMSandboxMetricsCollector()
    snap = collector.collect()
    assert snap.total_instances == 0
    assert snap.running_instances == 0


def test_collector_no_manager_export_dict_has_required_keys():
    from general_ludd.security.sandboxes.vm.metrics import VMSandboxMetricsCollector

    collector = VMSandboxMetricsCollector()
    data = collector.export_dict()
    for key in (
        "timestamp",
        "total_instances",
        "running_instances",
        "failed_instances",
        "stopped_instances",
        "total_dispatches",
        "total_verify_findings",
        "avg_boot_ms",
        "p95_boot_ms",
        "events_emitted",
        "state_breakdown",
        "backend_breakdown",
        "health",
    ):
        assert key in data, f"export_dict missing {key}"


def test_collector_no_manager_health_is_empty():
    from general_ludd.security.sandboxes.vm.metrics import (
        VMSandboxMetricsCollector,
    )

    collector = VMSandboxMetricsCollector()
    health = collector.health()
    assert health.status == "empty"
    assert health.issues == []


# ---------------------------------------------------------------------------
# Collector with a live manager (boot/dispatch/verify)
# ---------------------------------------------------------------------------


def test_collector_collect_reads_manager_state(sample_spec, sample_target):
    from general_ludd.security.sandboxes.vm.lifecycle import VMSandboxManager
    from general_ludd.security.sandboxes.vm.metrics import VMSandboxMetricsCollector

    mgr = VMSandboxManager()
    _boot_one(mgr, "firecracker", sample_spec, sample_target)
    _boot_one(mgr, "firecracker", sample_spec, sample_target)

    collector = VMSandboxMetricsCollector(manager=mgr)
    snap = collector.collect()
    assert snap.total_instances == 2
    assert snap.running_instances == 2
    assert snap.failed_instances == 0
    assert snap.avg_boot_ms >= 0.0
    assert snap.backend_breakdown.get("firecracker") == 2


def test_collector_export_dict_reflects_dispatches(sample_spec, sample_target):
    from general_ludd.security.sandboxes.vm.lifecycle import VMSandboxManager
    from general_ludd.security.sandboxes.vm.metrics import VMSandboxMetricsCollector

    mgr = VMSandboxManager()
    inst = _boot_one(mgr, "firecracker", sample_spec, sample_target)
    with mock.patch(
        "general_ludd.security.sandboxes.vm.agent_executor.AgentExecutor.receive_and_execute",
        return_value={"exit_code": 0},
    ):
        mgr.dispatch(inst.instance_id, sample_target)
        mgr.dispatch(inst.instance_id, sample_target)

    collector = VMSandboxMetricsCollector(manager=mgr)
    data = collector.export_dict()
    assert data["total_dispatches"] == 2
    assert data["total_instances"] == 1
    assert data["running_instances"] == 1


def test_collector_export_dict_reflects_verify_findings(
    sample_spec, sample_target
):
    from general_ludd.security.sandboxes import Finding
    from general_ludd.security.sandboxes.vm.lifecycle import VMSandboxManager
    from general_ludd.security.sandboxes.vm.metrics import VMSandboxMetricsCollector

    mgr = VMSandboxManager()
    inst = _boot_one(mgr, "firecracker", sample_spec, sample_target)
    with mock.patch(
        "general_ludd.security.sandboxes.vm.firecracker_backend.FirecrackerBackend.verify",
        return_value=[
            Finding(severity="warn", message="a", capability=None),
            Finding(severity="fail", message="b", capability=None),
        ],
    ):
        mgr.verify(inst.instance_id)

    collector = VMSandboxMetricsCollector(manager=mgr)
    data = collector.export_dict()
    assert data["total_verify_findings"] == 2


def test_collector_state_breakdown_counts_each_state(
    sample_spec, sample_target
):
    from general_ludd.security.sandboxes.vm.lifecycle import VMSandboxManager
    from general_ludd.security.sandboxes.vm.metrics import VMSandboxMetricsCollector

    mgr = VMSandboxManager()
    inst1 = _boot_one(mgr, "firecracker", sample_spec, sample_target)
    _boot_one(mgr, "firecracker", sample_spec, sample_target)
    with mock.patch(
        "general_ludd.security.sandboxes.vm.firecracker_backend.FirecrackerBackend.release",
    ):
        mgr.release(inst1.instance_id)

    collector = VMSandboxMetricsCollector(manager=mgr)
    data = collector.export_dict()
    assert data["state_breakdown"]["stopped"] == 1
    assert data["state_breakdown"]["running"] == 1
    assert data["stopped_instances"] == 1


def test_collector_failed_instances_counted(sample_spec, sample_target):
    from general_ludd.security.sandboxes.vm.lifecycle import (
        VMSandboxManager,
    )
    from general_ludd.security.sandboxes.vm.metrics import VMSandboxMetricsCollector

    mgr = VMSandboxManager()
    with mock.patch(
        "general_ludd.security.sandboxes.vm.firecracker_backend.FirecrackerBackend.available",
        return_value=False,
    ):
        mgr.boot("firecracker", sample_spec, sample_target)

    collector = VMSandboxMetricsCollector(manager=mgr)
    data = collector.export_dict()
    assert data["failed_instances"] == 1
    assert data["state_breakdown"]["failed"] == 1


def test_collector_p95_boot_ms_computed_from_instances(
    sample_spec, sample_target
):
    from general_ludd.security.sandboxes.vm.lifecycle import VMSandboxManager
    from general_ludd.security.sandboxes.vm.metrics import VMSandboxMetricsCollector

    mgr = VMSandboxManager()
    inst1 = _boot_one(mgr, "firecracker", sample_spec, sample_target)
    inst2 = _boot_one(mgr, "firecracker", sample_spec, sample_target)
    inst3 = _boot_one(mgr, "firecracker", sample_spec, sample_target)
    # Directly poke boot_ms to deterministic values for p95 check.
    mgr.instances[inst1.instance_id].metrics.boot_ms = 10.0
    mgr.instances[inst2.instance_id].metrics.boot_ms = 20.0
    mgr.instances[inst3.instance_id].metrics.boot_ms = 100.0

    collector = VMSandboxMetricsCollector(manager=mgr)
    snap = collector.collect()
    # p95 of [10, 20, 100] — nearest-rank gives the max (100.0) for small n.
    assert snap.p95_boot_ms == pytest.approx(100.0, rel=0.01)


def test_collector_backend_breakdown_multi_backend(sample_spec, sample_target):
    from general_ludd.security.sandboxes.vm.lifecycle import VMSandboxManager
    from general_ludd.security.sandboxes.vm.metrics import VMSandboxMetricsCollector

    mgr = VMSandboxManager()
    _boot_one(mgr, "firecracker", sample_spec, sample_target)
    _boot_one(mgr, "gvisor", sample_spec, sample_target)
    _boot_one(mgr, "firecracker", sample_spec, sample_target)

    collector = VMSandboxMetricsCollector(manager=mgr)
    snap = collector.collect()
    assert snap.backend_breakdown.get("firecracker") == 2
    assert snap.backend_breakdown.get("gvisor") == 1


# ---------------------------------------------------------------------------
# Health verdicts
# ---------------------------------------------------------------------------


def test_health_healthy_when_all_running(sample_spec, sample_target):
    from general_ludd.security.sandboxes.vm.lifecycle import VMSandboxManager
    from general_ludd.security.sandboxes.vm.metrics import VMSandboxMetricsCollector

    mgr = VMSandboxManager()
    _boot_one(mgr, "firecracker", sample_spec, sample_target)

    collector = VMSandboxMetricsCollector(manager=mgr)
    health = collector.health()
    assert health.status == "healthy"
    assert health.issues == []


def test_health_degraded_when_some_failed(sample_spec, sample_target):
    from general_ludd.security.sandboxes.vm.lifecycle import VMSandboxManager
    from general_ludd.security.sandboxes.vm.metrics import VMSandboxMetricsCollector

    mgr = VMSandboxManager()
    _boot_one(mgr, "firecracker", sample_spec, sample_target)
    _boot_one(mgr, "firecracker", sample_spec, sample_target)
    with mock.patch(
        "general_ludd.security.sandboxes.vm.firecracker_backend.FirecrackerBackend.available",
        return_value=False,
    ):
        mgr.boot("firecracker", sample_spec, sample_target)

    collector = VMSandboxMetricsCollector(manager=mgr)
    health = collector.health()
    assert health.status == "degraded"
    assert any("failed" in i for i in health.issues)


def test_health_unhealthy_when_majority_failed(sample_spec, sample_target):
    from general_ludd.security.sandboxes.vm.lifecycle import VMSandboxManager
    from general_ludd.security.sandboxes.vm.metrics import VMSandboxMetricsCollector

    mgr = VMSandboxManager()
    _boot_one(mgr, "firecracker", sample_spec, sample_target)
    with mock.patch(
        "general_ludd.security.sandboxes.vm.firecracker_backend.FirecrackerBackend.available",
        return_value=False,
    ):
        mgr.boot("firecracker", sample_spec, sample_target)
        mgr.boot("firecracker", sample_spec, sample_target)

    collector = VMSandboxMetricsCollector(manager=mgr)
    health = collector.health()
    assert health.status == "unhealthy"
    assert len(health.issues) >= 1


def test_health_export_dict_embeds_status(sample_spec, sample_target):
    from general_ludd.security.sandboxes.vm.lifecycle import VMSandboxManager
    from general_ludd.security.sandboxes.vm.metrics import VMSandboxMetricsCollector

    mgr = VMSandboxManager()
    _boot_one(mgr, "firecracker", sample_spec, sample_target)
    collector = VMSandboxMetricsCollector(manager=mgr)
    data = collector.export_dict()
    assert data["health"]["status"] == "healthy"


# ---------------------------------------------------------------------------
# Prometheus text export
# ---------------------------------------------------------------------------


def test_export_prometheus_has_metric_families(sample_spec, sample_target):
    from general_ludd.security.sandboxes.vm.lifecycle import VMSandboxManager
    from general_ludd.security.sandboxes.vm.metrics import VMSandboxMetricsCollector

    mgr = VMSandboxManager()
    inst = _boot_one(mgr, "firecracker", sample_spec, sample_target)
    with mock.patch(
        "general_ludd.security.sandboxes.vm.agent_executor.AgentExecutor.receive_and_execute",
        return_value={"exit_code": 0},
    ):
        mgr.dispatch(inst.instance_id, sample_target)

    collector = VMSandboxMetricsCollector(manager=mgr)
    text = collector.export_prometheus()
    for name in (
        "gludd_vm_total_instances",
        "gludd_vm_running_instances",
        "gludd_vm_failed_instances",
        "gludd_vm_total_dispatches",
        "gludd_vm_total_verify_findings",
        "gludd_vm_avg_boot_ms",
        "gludd_vm_p95_boot_ms",
    ):
        assert name in text, f"prometheus export missing {name}"


def test_export_prometheus_empty_when_no_manager():
    from general_ludd.security.sandboxes.vm.metrics import VMSandboxMetricsCollector

    text = VMSandboxMetricsCollector().export_prometheus()
    assert "gludd_vm_total_instances 0" in text
    assert "gludd_vm_running_instances 0" in text


def test_export_prometheus_includes_state_series(sample_spec, sample_target):
    from general_ludd.security.sandboxes.vm.lifecycle import VMSandboxManager
    from general_ludd.security.sandboxes.vm.metrics import VMSandboxMetricsCollector

    mgr = VMSandboxManager()
    _boot_one(mgr, "firecracker", sample_spec, sample_target)
    collector = VMSandboxMetricsCollector(manager=mgr)
    text = collector.export_prometheus()
    assert 'gludd_vm_instances_by_state{state="running"}' in text


# ---------------------------------------------------------------------------
# Snapshot history (time-series)
# ---------------------------------------------------------------------------


def test_record_snapshot_stores_history(sample_spec, sample_target):
    from general_ludd.security.sandboxes.vm.lifecycle import VMSandboxManager
    from general_ludd.security.sandboxes.vm.metrics import VMSandboxMetricsCollector

    mgr = VMSandboxManager()
    _boot_one(mgr, "firecracker", sample_spec, sample_target)
    collector = VMSandboxMetricsCollector(manager=mgr)

    collector.record_snapshot()
    collector.record_snapshot()
    history = collector.history()
    assert len(history) == 2
    assert history[0]["total_instances"] == 1


def test_history_limit_bounds_return(sample_spec, sample_target):
    from general_ludd.security.sandboxes.vm.lifecycle import VMSandboxManager
    from general_ludd.security.sandboxes.vm.metrics import VMSandboxMetricsCollector

    mgr = VMSandboxManager()
    _boot_one(mgr, "firecracker", sample_spec, sample_target)
    collector = VMSandboxMetricsCollector(manager=mgr)
    for _ in range(5):
        collector.record_snapshot()
    recent = collector.history(limit=2)
    assert len(recent) == 2


def test_record_snapshot_caps_total_history(sample_spec, sample_target):
    from general_ludd.security.sandboxes.vm.lifecycle import VMSandboxManager
    from general_ludd.security.sandboxes.vm.metrics import VMSandboxMetricsCollector

    mgr = VMSandboxManager()
    collector = VMSandboxMetricsCollector(manager=mgr, max_snapshots=3)
    for _ in range(10):
        collector.record_snapshot()
    assert len(collector.history(limit=100)) == 3


def test_attach_manager_after_construction(sample_spec, sample_target):
    from general_ludd.security.sandboxes.vm.lifecycle import VMSandboxManager
    from general_ludd.security.sandboxes.vm.metrics import VMSandboxMetricsCollector

    mgr = VMSandboxManager()
    collector = VMSandboxMetricsCollector()
    assert collector.collect().total_instances == 0
    _boot_one(mgr, "firecracker", sample_spec, sample_target)
    collector.attach(mgr)
    assert collector.collect().total_instances == 1


# ---------------------------------------------------------------------------
# Event surface (events_emitted passthrough)
# ---------------------------------------------------------------------------


def test_export_dict_events_emitted_passthrough(sample_spec, sample_target):
    from general_ludd.security.sandboxes.vm.lifecycle import VMSandboxManager
    from general_ludd.security.sandboxes.vm.metrics import VMSandboxMetricsCollector

    mgr = VMSandboxManager()
    _boot_one(mgr, "firecracker", sample_spec, sample_target)
    collector = VMSandboxMetricsCollector(manager=mgr)
    data = collector.export_dict()
    assert data["events_emitted"] >= 1
