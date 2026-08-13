"""Focused lifecycle tests for the event-loop compute phase."""

from __future__ import annotations

import inspect
from unittest.mock import AsyncMock, patch

import pytest

from general_ludd.event_loop.loop import EventLoop


def test_compute_utilization_phase_is_awaitable() -> None:
    assert inspect.iscoroutinefunction(EventLoop._phase_check_compute_utilization)


@pytest.mark.asyncio
async def test_compute_phase_enforces_persisted_hard_ttl_without_tracker() -> None:
    loop = object.__new__(EventLoop)
    loop.config = {"compute_idle_check_interval_ticks": 1}
    loop._total_ticks = 1
    loop._deployment_manager = AsyncMock()
    loop._deployment_manager.cleanup_expired.return_value = ["azure-expired"]
    loop._floor_controller = None
    loop._utilization_tracker = None
    loop._daemon_state = {}
    loop._todo_repo = None
    loop._tick_state = {}
    loop._tick_metrics = {}

    with patch(
        "general_ludd.infra.gpu_metrics.GPUMetricsCollector.collect_all_gpu_metrics",
        return_value=[],
    ):
        await loop._phase_check_compute_utilization()

    loop._deployment_manager.cleanup_expired.assert_awaited_once_with()
    assert loop._daemon_state["_last_gpu_metrics"] == []
    assert loop._daemon_state["_last_gpu_metrics_at"] > 0
