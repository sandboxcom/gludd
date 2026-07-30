"""Tests for ``src/general_ludd/security/sandboxes/vm/metrics.py``.

Covers VMSandboxMetricsSnapshot, VMSandboxHealth, _percentile, and
VMSandboxMetricsCollector basic operations.
"""
from __future__ import annotations

from types import SimpleNamespace

from general_ludd.security.sandboxes.vm.lifecycle import VMLifecycleState
from general_ludd.security.sandboxes.vm.metrics import (
    VMSandboxHealth,
    VMSandboxMetricsCollector,
    VMSandboxMetricsSnapshot,
    _percentile,
)


def _instance(
    state: VMLifecycleState,
    *,
    backend: str = "firecracker",
    boot_ms: float = 100.0,
    dispatches: int = 1,
    findings: int = 0,
) -> SimpleNamespace:
    return SimpleNamespace(
        state=state,
        backend_name=backend,
        metrics=SimpleNamespace(
            boot_ms=boot_ms,
            dispatch_count=dispatches,
            last_verify_findings=findings,
        ),
    )


class TestPercentile:
    def test_empty_values_returns_zero(self) -> None:
        assert _percentile([], 50.0) == 0.0

    def test_single_value(self) -> None:
        assert _percentile([10.0], 50.0) == 10.0
        assert _percentile([10.0], 95.0) == 10.0

    def test_median_of_odd_list(self) -> None:
        values = [5.0, 1.0, 3.0]
        assert _percentile(values, 50.0) == 3.0

    def test_p95_of_many(self) -> None:
        values = list(range(1, 101))
        result = _percentile([float(v) for v in values], 95.0)
        assert result == 95.0

    def test_p0_returns_min(self) -> None:
        result = _percentile([30.0, 10.0, 20.0], 0.0)
        assert result == 10.0

    def test_p100_returns_max(self) -> None:
        result = _percentile([30.0, 10.0, 20.0], 100.0)
        assert result == 30.0


class TestVMSandboxMetricsSnapshot:
    def test_defaults_are_zero(self) -> None:
        snap = VMSandboxMetricsSnapshot()
        assert snap.total_instances == 0
        assert snap.running_instances == 0
        assert snap.failed_instances == 0
        assert snap.avg_boot_ms == 0.0
        assert snap.p95_boot_ms == 0.0

    def test_timestamp_is_set(self) -> None:
        snap = VMSandboxMetricsSnapshot()
        assert isinstance(snap.timestamp, float)
        assert snap.timestamp > 0

    def test_custom_values(self) -> None:
        snap = VMSandboxMetricsSnapshot(
            total_instances=10,
            running_instances=7,
            failed_instances=2,
            stopped_instances=1,
            total_dispatches=42,
            avg_boot_ms=3500.0,
            p95_boot_ms=4800.0,
            state_breakdown={"running": 7, "failed": 2, "stopped": 1},
            backend_breakdown={"qemu": 10},
        )
        assert snap.total_instances == 10
        assert snap.running_instances == 7
        assert snap.failed_instances == 2
        assert snap.avg_boot_ms == 3500.0

    def test_as_dict(self) -> None:
        snap = VMSandboxMetricsSnapshot(
            total_instances=5,
            state_breakdown={"running": 3, "failed": 2},
        )
        d = snap.as_dict()
        assert d["total_instances"] == 5
        assert d["state_breakdown"] == {"running": 3, "failed": 2}
        assert "timestamp" in d


class TestVMSandboxHealth:
    def test_healthy_status(self) -> None:
        snap = VMSandboxMetricsSnapshot(total_instances=5, running_instances=5)
        health = VMSandboxHealth(status="healthy", metrics=snap)
        assert health.status == "healthy"
        assert health.issues == []

    def test_unhealthy_status_with_issues(self) -> None:
        snap = VMSandboxMetricsSnapshot(total_instances=5, failed_instances=4)
        health = VMSandboxHealth(
            status="unhealthy",
            issues=["majority of instances failed (4/5)"],
            metrics=snap,
        )
        assert health.status == "unhealthy"
        assert len(health.issues) == 1

    def test_as_dict(self) -> None:
        snap = VMSandboxMetricsSnapshot(total_instances=3)
        health = VMSandboxHealth(status="empty", metrics=snap)
        d = health.as_dict()
        assert d["status"] == "empty"
        assert d["metrics"]["total_instances"] == 3

    def test_as_dict_no_metrics(self) -> None:
        health = VMSandboxHealth(status="empty")
        d = health.as_dict()
        assert d["status"] == "empty"
        assert d["metrics"] is None


class TestVMSandboxMetricsCollector:
    def test_construct_and_attach(self) -> None:
        collector = VMSandboxMetricsCollector()
        assert collector._manager is None
        collector.attach(None)  # type: ignore[arg-type]
        assert collector._manager is None

    def test_collect_without_manager_returns_zeroed_snapshot(self) -> None:
        collector = VMSandboxMetricsCollector()
        snap = collector.collect()
        assert snap.total_instances == 0
        assert snap.running_instances == 0
        assert snap.failed_instances == 0

    def test_collect_aggregates_attached_manager_instances(self) -> None:
        manager = SimpleNamespace(
            instances={
                "running": _instance(
                    VMLifecycleState.RUNNING,
                    boot_ms=100.0,
                    dispatches=3,
                ),
                "executing": _instance(
                    VMLifecycleState.EXECUTING,
                    backend="gvisor",
                    boot_ms=300.0,
                    findings=2,
                ),
                "failed": _instance(
                    VMLifecycleState.FAILED,
                    boot_ms=0.0,
                ),
                "stopped": _instance(
                    VMLifecycleState.STOPPED,
                    boot_ms=200.0,
                ),
            },
            events=["created", "stopped"],
        )
        collector = VMSandboxMetricsCollector(manager)

        snap = collector.collect()

        assert snap.total_instances == 4
        assert snap.running_instances == 2
        assert snap.failed_instances == 1
        assert snap.stopped_instances == 1
        assert snap.total_dispatches == 6
        assert snap.total_verify_findings == 2
        assert snap.avg_boot_ms == 200.0
        assert snap.events_emitted == 2
        assert snap.state_breakdown == {
            "running": 1,
            "executing": 1,
            "failed": 1,
            "stopped": 1,
        }
        assert snap.backend_breakdown == {"firecracker": 3, "gvisor": 1}

        prometheus = collector.export_prometheus()
        assert 'gludd_vm_instances_by_state{state="failed"} 1' in prometheus
        assert (
            'gludd_vm_instances_by_backend{backend="firecracker"} 3'
            in prometheus
        )

    def test_health_classifies_unhealthy_degraded_and_healthy_managers(self) -> None:
        unhealthy_manager = SimpleNamespace(
            instances={
                "failed": _instance(VMLifecycleState.FAILED),
                "running": _instance(VMLifecycleState.RUNNING),
            },
            events=[],
        )
        unhealthy = VMSandboxMetricsCollector(unhealthy_manager).health()
        assert unhealthy.status == "unhealthy"
        assert "majority of instances failed" in unhealthy.issues[0]

        degraded_manager = SimpleNamespace(
            instances={
                "failed": _instance(VMLifecycleState.FAILED),
                "running-1": _instance(VMLifecycleState.RUNNING),
                "running-2": _instance(VMLifecycleState.RUNNING),
            },
            events=[],
        )
        degraded = VMSandboxMetricsCollector(degraded_manager).health()
        assert degraded.status == "degraded"
        assert "instance(s) failed" in degraded.issues[0]

        healthy_manager = SimpleNamespace(
            instances={"running": _instance(VMLifecycleState.RUNNING)},
            events=[],
        )
        assert VMSandboxMetricsCollector(healthy_manager).health().status == "healthy"

    def test_health_without_manager_returns_empty(self) -> None:
        collector = VMSandboxMetricsCollector()
        health = collector.health()
        assert health.status == "empty"
        assert health.metrics is not None
        assert health.metrics.total_instances == 0

    def test_export_dict_without_manager(self) -> None:
        collector = VMSandboxMetricsCollector()
        data = collector.export_dict()
        assert "health" in data
        assert data["health"]["status"] == "empty"
        assert data["total_instances"] == 0

    def test_export_prometheus_without_manager(self) -> None:
        collector = VMSandboxMetricsCollector()
        output = collector.export_prometheus()
        assert "gludd_vm_total_instances" in output
        assert "gludd_vm_total_instances 0" in output

    def test_record_snapshot(self) -> None:
        collector = VMSandboxMetricsCollector()
        data = collector.record_snapshot()
        assert data["total_instances"] == 0
        assert data["health"]["status"] == "empty"

    def test_history_empty(self) -> None:
        collector = VMSandboxMetricsCollector()
        assert collector.history() == []

    def test_history_with_recorded_snapshots(self) -> None:
        collector = VMSandboxMetricsCollector()
        collector.record_snapshot()
        collector.record_snapshot()
        hist = collector.history()
        assert len(hist) == 2
        assert hist[0]["total_instances"] == 0

    def test_history_respects_limit(self) -> None:
        collector = VMSandboxMetricsCollector()
        for _ in range(5):
            collector.record_snapshot()
        assert len(collector.history(limit=3)) == 3

    def test_history_limit_zero_returns_empty(self) -> None:
        collector = VMSandboxMetricsCollector()
        collector.record_snapshot()
        assert collector.history(limit=0) == []

    def test_max_snapshots_bounded(self) -> None:
        collector = VMSandboxMetricsCollector(max_snapshots=3)
        for _ in range(10):
            collector.record_snapshot()
        assert len(collector.history(limit=100)) == 3
