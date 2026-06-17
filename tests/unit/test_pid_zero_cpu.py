"""Regression: LoadController must not ZeroDivisionError when logical_cpu_count==0.

The HYBRID profile computes `penalty = excess / max(1, snapshot.logical_cpu_count)`.
Before the max(1, ...) guard, a snapshot reporting 0 logical CPUs (seen on some
containerised hosts) with a positive load average raised ZeroDivisionError and
crashed the throttle evaluation.
"""
from __future__ import annotations

from general_ludd.controllers.load_scrape import LoadSnapshot
from general_ludd.controllers.pid import LoadController
from general_ludd.schemas.queue import Queue


def test_hybrid_zero_cpu_does_not_raise_zero_division() -> None:
    snapshot = LoadSnapshot(
        loadavg_1m=0.5,
        loadavg_5m=0.5,
        loadavg_10m=1.5,  # > logical_cpu_count (0): enters the penalty branch
        logical_cpu_count=0,
        cpu_percent=50.0,
        memory_available_percent=50.0,
        disk_free_percent=50.0,
        active_jobs=0,
    )
    queue = Queue(queue_name="worker", resource_profile="hybrid", soft_cap=5)
    controller = LoadController(cpu_count=1)

    # The whole point: this call must not raise ZeroDivisionError.
    outputs = controller.evaluate_snapshot(snapshot, [queue])
    assert outputs is not None
    assert "worker" in outputs.desired_active_buckets_by_queue
