"""VM sandbox observability metrics — P7 dashboard/health export layer.

Wraps :class:`~general_ludd.security.sandboxes.vm.lifecycle.VMSandboxManager`
to provide structured metrics export for the daemon observability surface
(``/metrics/sandboxes``, ``/admin/vms``):

* :meth:`VMSandboxMetricsCollector.collect` builds a
  :class:`VMSandboxMetricsSnapshot` (per-state, per-backend counts, avg + p95
  boot time, dispatch totals, verify-findings totals).
* :meth:`VMSandboxMetricsCollector.export_dict` returns a JSON-serialisable
  dict embedding the snapshot plus a :class:`VMSandboxHealth` verdict.
* :meth:`VMSandboxMetricsCollector.export_prometheus` returns Prometheus text
  exposition format (one ``gludd_vm_*`` family per metric, with ``state=`` and
  ``backend=`` label series).
* :meth:`VMSandboxMetricsCollector.health` classifies the subsystem as
  ``empty`` / ``healthy`` / ``degraded`` / ``unhealthy`` based on the failed
  instance ratio.
* :meth:`VMSandboxMetricsCollector.record_snapshot` stores timestamped
  snapshots for time-series dashboards (bounded ring).

See ``docs/specs/FEATURE_UNIKERNEL_SANDBOX.md`` §4 P3 (observability) +
NF.2 P7.
"""

from __future__ import annotations

import logging
import math
import time
from collections import deque
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from general_ludd.security.sandboxes.vm.lifecycle import VMSandboxManager

logger = logging.getLogger(__name__)


_HEALTHY_BOOT_MS_THRESHOLD = 5000.0
_DEGRADED_FAILURE_RATIO = 0.0
_UNHEALTHY_FAILURE_RATIO = 0.5
_DEFAULT_MAX_SNAPSHOTS = 100


@dataclass
class VMSandboxMetricsSnapshot:
    """Point-in-time aggregate view of the VM sandbox subsystem.

    All count fields are non-negative integers; latency fields are
    milliseconds. ``state_breakdown`` / ``backend_breakdown`` are ``{label:
    count}`` dicts keyed by the :class:`VMLifecycleState` value string and the
    backend name respectively.
    """

    timestamp: float = field(default_factory=time.time)
    total_instances: int = 0
    running_instances: int = 0
    failed_instances: int = 0
    stopped_instances: int = 0
    total_dispatches: int = 0
    total_verify_findings: int = 0
    avg_boot_ms: float = 0.0
    p95_boot_ms: float = 0.0
    events_emitted: int = 0
    state_breakdown: dict[str, int] = field(default_factory=dict)
    backend_breakdown: dict[str, int] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "total_instances": self.total_instances,
            "running_instances": self.running_instances,
            "failed_instances": self.failed_instances,
            "stopped_instances": self.stopped_instances,
            "total_dispatches": self.total_dispatches,
            "total_verify_findings": self.total_verify_findings,
            "avg_boot_ms": self.avg_boot_ms,
            "p95_boot_ms": self.p95_boot_ms,
            "events_emitted": self.events_emitted,
            "state_breakdown": dict(self.state_breakdown),
            "backend_breakdown": dict(self.backend_breakdown),
        }


@dataclass
class VMSandboxHealth:
    """Health verdict for the VM sandbox subsystem.

    ``status`` is one of ``"empty"`` (no instances registered), ``"healthy"``
    (all running, no failures, acceptable boot latency), ``"degraded"`` (some
    failures or slow boots but minority), ``"unhealthy"`` (majority failed).
    ``issues`` is a list of human-readable strings explaining the verdict.
    """

    status: str
    issues: list[str] = field(default_factory=list)
    metrics: VMSandboxMetricsSnapshot | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "issues": list(self.issues),
            "metrics": self.metrics.as_dict() if self.metrics else None,
        }


def _percentile(values: list[float], pct: float) -> float:
    """Nearest-rank percentile (pct in [0,100]); 0 on empty input.

    Uses the nearest-rank method: ``ceil(pct/100 * n)``-th element of the
    sorted list (1-indexed). For ``pct=95`` with ``n=3`` that gives ``ceil(2.85)
    = 3`` → the max. For ``n=100`` it gives the 95th element.
    """
    if not values:
        return 0.0
    ordered = sorted(values)
    n = len(ordered)
    rank = max(1, math.ceil((pct / 100.0) * n))
    return ordered[min(rank, n) - 1]


class VMSandboxMetricsCollector:
    """Collect, aggregate, and export VM sandbox metrics for observability.

    Construct with an optional :class:`VMSandboxManager` (or :meth:`attach`
    later). All export methods are read-only against the manager's state and
    safe to call concurrently with the daemon's serialised mutations.
    """

    def __init__(
        self,
        manager: VMSandboxManager | None = None,
        max_snapshots: int = _DEFAULT_MAX_SNAPSHOTS,
    ) -> None:
        self._manager: VMSandboxManager | None = manager
        self._max_snapshots = max(1, int(max_snapshots))
        self._snapshots: deque[dict[str, Any]] = deque(maxlen=self._max_snapshots)

    def attach(self, manager: VMSandboxManager) -> None:
        """Attach (or replace) the :class:`VMSandboxManager` to collect from."""
        self._manager = manager

    def collect(self) -> VMSandboxMetricsSnapshot:
        """Build a snapshot of current VM sandbox aggregate metrics.

        Reads the attached manager's per-instance registry directly so the
        snapshot reflects state at call time (not a cached ``observe()`` view).
        Returns a zeroed snapshot when no manager is attached.
        """
        if self._manager is None:
            return VMSandboxMetricsSnapshot()

        from general_ludd.security.sandboxes.vm.lifecycle import VMLifecycleState

        instances = list(self._manager.instances.values())
        total = len(instances)
        boots = [i.metrics.boot_ms for i in instances if i.metrics.boot_ms > 0]
        avg_boot = sum(boots) / len(boots) if boots else 0.0
        p95_boot = _percentile(boots, 95.0)

        state_counts: dict[str, int] = {}
        backend_counts: dict[str, int] = {}
        failed = 0
        running = 0
        stopped = 0
        for inst in instances:
            key = inst.state.value
            state_counts[key] = state_counts.get(key, 0) + 1
            backend_counts[inst.backend_name] = (
                backend_counts.get(inst.backend_name, 0) + 1
            )
            if inst.state is VMLifecycleState.FAILED:
                failed += 1
            elif inst.state in (VMLifecycleState.RUNNING, VMLifecycleState.EXECUTING):
                running += 1
            elif inst.state is VMLifecycleState.STOPPED:
                stopped += 1

        total_dispatches = sum(i.metrics.dispatch_count for i in instances)
        total_findings = sum(i.metrics.last_verify_findings for i in instances)

        return VMSandboxMetricsSnapshot(
            timestamp=time.time(),
            total_instances=total,
            running_instances=running,
            failed_instances=failed,
            stopped_instances=stopped,
            total_dispatches=total_dispatches,
            total_verify_findings=total_findings,
            avg_boot_ms=avg_boot,
            p95_boot_ms=p95_boot,
            events_emitted=len(self._manager.events),
            state_breakdown=state_counts,
            backend_breakdown=backend_counts,
        )

    def health(self) -> VMSandboxHealth:
        """Classify subsystem health from the current snapshot.

        Verdict logic:
        - ``empty``    : no instances registered.
        - ``unhealthy``: failed instances make up >= 50% of the total.
        - ``degraded`` : any failed instance OR avg boot latency above the
                         healthy threshold.
        - ``healthy``  : otherwise.
        """
        snap = self.collect()
        if snap.total_instances == 0:
            return VMSandboxHealth(status="empty", metrics=snap)

        issues: list[str] = []
        failure_ratio = (
            snap.failed_instances / snap.total_instances
            if snap.total_instances
            else 0.0
        )

        if failure_ratio >= _UNHEALTHY_FAILURE_RATIO and snap.failed_instances > 0:
            issues.append(
                f"majority of instances failed ({snap.failed_instances}/"
                f"{snap.total_instances})"
            )
            return VMSandboxHealth(status="unhealthy", issues=issues, metrics=snap)

        if snap.failed_instances > 0:
            issues.append(
                f"{snap.failed_instances} instance(s) failed "
                f"of {snap.total_instances}"
            )

        if (
            snap.avg_boot_ms > _HEALTHY_BOOT_MS_THRESHOLD
            and snap.avg_boot_ms > 0.0
        ):
            issues.append(
                f"avg boot latency {snap.avg_boot_ms:.0f}ms exceeds "
                f"{_HEALTHY_BOOT_MS_THRESHOLD:.0f}ms threshold"
            )

        if issues:
            return VMSandboxHealth(status="degraded", issues=issues, metrics=snap)

        return VMSandboxHealth(status="healthy", metrics=snap)

    def export_dict(self) -> dict[str, Any]:
        """Return a JSON-serialisable metrics dict with embedded health.

        Suitable for ``GET /metrics/sandboxes``. Always includes the full set
        of keys listed in the test contract (zero values when empty).
        """
        snap = self.collect()
        data = snap.as_dict()
        data["health"] = self.health().as_dict()
        return data

    def export_prometheus(self) -> str:
        """Return Prometheus text exposition format for the current snapshot.

        Emits scalar families (``gludd_vm_total_instances`` etc.) plus label
        series for per-state and per-backend counts. Lines are newline-
        separated with no trailing HELP/TYPE repetition beyond one block per
        family.
        """
        snap = self.collect()
        lines: list[str] = []
        scalar_families: list[tuple[str, float]] = [
            ("gludd_vm_total_instances", float(snap.total_instances)),
            ("gludd_vm_running_instances", float(snap.running_instances)),
            ("gludd_vm_failed_instances", float(snap.failed_instances)),
            ("gludd_vm_stopped_instances", float(snap.stopped_instances)),
            ("gludd_vm_total_dispatches", float(snap.total_dispatches)),
            ("gludd_vm_total_verify_findings", float(snap.total_verify_findings)),
            ("gludd_vm_avg_boot_ms", snap.avg_boot_ms),
            ("gludd_vm_p95_boot_ms", snap.p95_boot_ms),
            ("gludd_vm_events_emitted", float(snap.events_emitted)),
        ]
        for name, value in scalar_families:
            lines.append(f"# TYPE {name} gauge")
            lines.append(f"{name} {value}")

        if snap.state_breakdown:
            lines.append("# TYPE gludd_vm_instances_by_state gauge")
            for state, count in sorted(snap.state_breakdown.items()):
                lines.append(f'gludd_vm_instances_by_state{{state="{state}"}} {count}')

        if snap.backend_breakdown:
            lines.append("# TYPE gludd_vm_instances_by_backend gauge")
            for backend, count in sorted(snap.backend_breakdown.items()):
                lines.append(
                    f'gludd_vm_instances_by_backend{{backend="{backend}"}} {count}'
                )

        return "\n".join(lines) + "\n"

    def record_snapshot(self) -> dict[str, Any]:
        """Collect a snapshot, store it in the bounded history, and return it.

        Stored snapshots power time-series dashboards. The ring is bounded at
        ``max_snapshots`` (constructor arg, default 100).
        """
        data = self.export_dict()
        self._snapshots.append(data)
        return data

    def history(self, limit: int = 10) -> list[dict[str, Any]]:
        """Return the ``limit`` most-recent recorded snapshots (newest last)."""
        if limit <= 0:
            return []
        items = list(self._snapshots)
        return items[-limit:]


__all__ = [
    "VMSandboxHealth",
    "VMSandboxMetricsCollector",
    "VMSandboxMetricsSnapshot",
]
