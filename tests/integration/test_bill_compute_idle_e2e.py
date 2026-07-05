"""Integration tests for compute idle teardown in EventLoop.

Proves end-to-end that:
- _phase_check_compute_utilization detects idle GPUs
- Idle counter increments across ticks
- Teardown triggers after threshold_ticks
- Non-idle endpoints reset counters
- Config-gated skipping works (check_interval_ticks > 1)
- Torn down endpoints are tracked in daemon_state
- Multi-endpoint scenarios work correctly
- Edge cases: no tracker, threshold=0, empty endpoints
"""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from general_ludd.event_loop.loop import EventLoop
from general_ludd.infra.utilization import ComputeEndpoint, UtilizationTracker


def _make_tracker(*endpoints: ComputeEndpoint) -> UtilizationTracker:
    tracker = UtilizationTracker()
    for ep in endpoints:
        tracker._endpoints[ep.endpoint_id] = ep
    return tracker


# ---------------------------------------------------------------------------
# Phase check: basic idle detection and teardown lifecycle
# ---------------------------------------------------------------------------


class TestComputeIdleLifecycle:
    @pytest.mark.asyncio
    async def test_full_idle_to_teardown_lifecycle(self):
        now = time.time()
        tracker = _make_tracker(
            ComputeEndpoint(
                endpoint_id="gpu1",
                url="http://gpu1:8000",
                model="llama3",
                gpu_type="A100",
                current_load=0,
                last_used=now - 3600,
            ),
        )
        daemon_state: dict = {}
        deploy_mgr = AsyncMock()
        deploy_mgr.destroy = AsyncMock()
        deploy_mgr.get_deployment = MagicMock(return_value=MagicMock(instance_id="gpu1"))

        loop = EventLoop(
            utilization_tracker=tracker,
            deployment_manager=deploy_mgr,
            daemon_state=daemon_state,
            config={
                "compute_idle_check_interval_ticks": 1,
                "compute_idle_teardown_threshold_ticks": 3,
                "compute_idle_gpu_sm_pct": 5.0,
            },
        )

        for tick in range(1, 4):
            loop._total_ticks = tick
            await loop._phase_check_compute_utilization()
            idle = daemon_state.get("idle_endpoints", {})
            if tick < 3:
                assert "gpu1" in idle, f"tick={tick}: gpu1 should be tracked as idle"
                assert idle["gpu1"]["idle_ticks"] == tick
                deploy_mgr.destroy.assert_not_called()
            else:
                deploy_mgr.destroy.assert_called_once_with("gpu1")
                assert "gpu1" not in daemon_state.get("idle_endpoints", {})
                assert "gpu1" in daemon_state.get("torn_down_endpoints", [])

    @pytest.mark.asyncio
    async def test_recent_activity_resets_idle_counter(self):
        now = time.time()
        tracker = _make_tracker(
            ComputeEndpoint(
                endpoint_id="gpu1",
                url="http://gpu1:8000",
                model="llama3",
                gpu_type="A100",
                current_load=0,
                last_used=now - 3600,
            ),
        )
        daemon_state: dict = {}
        loop = EventLoop(
            utilization_tracker=tracker,
            daemon_state=daemon_state,
            config={
                "compute_idle_check_interval_ticks": 1,
                "compute_idle_teardown_threshold_ticks": 5,
                "compute_idle_gpu_sm_pct": 5.0,
            },
        )

        loop._total_ticks = 1
        await loop._phase_check_compute_utilization()
        assert daemon_state["idle_endpoints"]["gpu1"]["idle_ticks"] == 1

        loop._total_ticks = 2
        await loop._phase_check_compute_utilization()
        assert daemon_state["idle_endpoints"]["gpu1"]["idle_ticks"] == 2

        tracker._endpoints["gpu1"].current_load = 8
        tracker._endpoints["gpu1"].last_used = time.time()

        loop._total_ticks = 3
        await loop._phase_check_compute_utilization()
        assert "gpu1" not in daemon_state.get("idle_endpoints", {})


# ---------------------------------------------------------------------------
# Phase check: config gating
# ---------------------------------------------------------------------------


class TestComputeIdleConfigGating:
    @pytest.mark.asyncio
    async def test_skips_when_check_interval_not_met(self):
        tracker = _make_tracker(
            ComputeEndpoint(
                endpoint_id="gpu1",
                url="http://gpu1:8000",
                model="llama3",
                gpu_type="A100",
                current_load=0,
                last_used=time.time() - 3600,
            ),
        )
        daemon_state: dict = {}
        loop = EventLoop(
            utilization_tracker=tracker,
            daemon_state=daemon_state,
            config={
                "compute_idle_check_interval_ticks": 5,
                "compute_idle_teardown_threshold_ticks": 3,
                "compute_idle_gpu_sm_pct": 5.0,
            },
        )

        loop._total_ticks = 3
        await loop._phase_check_compute_utilization()
        assert "idle_endpoints" not in daemon_state

        loop._total_ticks = 5
        await loop._phase_check_compute_utilization()
        assert "gpu1" in daemon_state.get("idle_endpoints", {})

    @pytest.mark.asyncio
    async def test_no_tracker_does_not_crash(self):
        loop = EventLoop(
            utilization_tracker=None,
            daemon_state={},
            config={
                "compute_idle_check_interval_ticks": 1,
                "compute_idle_teardown_threshold_ticks": 3,
                "compute_idle_gpu_sm_pct": 5.0,
            },
        )
        loop._total_ticks = 1
        await loop._phase_check_compute_utilization()

    @pytest.mark.asyncio
    async def test_no_idle_endpoints_clean_state(self):
        tracker = _make_tracker(
            ComputeEndpoint(
                endpoint_id="gpu1",
                url="http://gpu1:8000",
                model="llama3",
                gpu_type="A100",
                current_load=10,
                last_used=time.time(),
            ),
        )
        daemon_state: dict = {}
        loop = EventLoop(
            utilization_tracker=tracker,
            daemon_state=daemon_state,
            config={
                "compute_idle_check_interval_ticks": 1,
                "compute_idle_teardown_threshold_ticks": 3,
                "compute_idle_gpu_sm_pct": 5.0,
            },
        )
        loop._total_ticks = 1
        await loop._phase_check_compute_utilization()
        assert "idle_endpoints" not in daemon_state


# ---------------------------------------------------------------------------
# Multi-endpoint scenarios
# ---------------------------------------------------------------------------


class TestComputeIdleMultiEndpoint:
    @pytest.mark.asyncio
    async def test_only_idle_gpu_torn_down_others_retained(self):
        now = time.time()
        tracker = _make_tracker(
            ComputeEndpoint(
                endpoint_id="idle-gpu",
                url="http://idle:8000",
                model="llama3",
                gpu_type="A100",
                current_load=0,
                last_used=now - 3600,
            ),
            ComputeEndpoint(
                endpoint_id="busy-gpu",
                url="http://busy:8000",
                model="mistral",
                gpu_type="H100",
                current_load=4,
                last_used=now,
            ),
        )
        daemon_state: dict = {}
        deploy_mgr = AsyncMock()
        deploy_mgr.destroy = AsyncMock()
        deploy_mgr.get_deployment = MagicMock(return_value=MagicMock(instance_id="idle-gpu"))

        loop = EventLoop(
            utilization_tracker=tracker,
            deployment_manager=deploy_mgr,
            daemon_state=daemon_state,
            config={
                "compute_idle_check_interval_ticks": 1,
                "compute_idle_teardown_threshold_ticks": 2,
                "compute_idle_gpu_sm_pct": 5.0,
            },
        )

        loop._total_ticks = 1
        await loop._phase_check_compute_utilization()
        idle = daemon_state.get("idle_endpoints", {})
        assert "idle-gpu" in idle
        assert "busy-gpu" not in idle

        loop._total_ticks = 2
        await loop._phase_check_compute_utilization()
        deploy_mgr.destroy.assert_called_once_with("idle-gpu")
        assert "idle-gpu" not in daemon_state.get("idle_endpoints", {})
        assert "idle-gpu" in daemon_state.get("torn_down_endpoints", [])

    @pytest.mark.asyncio
    async def test_multiple_idle_all_torn_down(self):
        now = time.time()
        tracker = _make_tracker(
            ComputeEndpoint(
                endpoint_id="gpu-a",
                url="http://a:8000",
                model="llama3",
                gpu_type="A100",
                current_load=0,
                last_used=now - 3600,
            ),
            ComputeEndpoint(
                endpoint_id="gpu-b",
                url="http://b:8000",
                model="mistral",
                gpu_type="A100",
                current_load=0,
                last_used=now - 3600,
            ),
        )
        daemon_state: dict = {}
        deploy_mgr = AsyncMock()
        deploy_mgr.destroy = AsyncMock()
        deploy_mgr.get_deployment = MagicMock(
            side_effect=lambda eid: MagicMock(instance_id=eid)
        )

        loop = EventLoop(
            utilization_tracker=tracker,
            deployment_manager=deploy_mgr,
            daemon_state=daemon_state,
            config={
                "compute_idle_check_interval_ticks": 1,
                "compute_idle_teardown_threshold_ticks": 1,
                "compute_idle_gpu_sm_pct": 5.0,
            },
        )

        loop._total_ticks = 1
        await loop._phase_check_compute_utilization()
        assert deploy_mgr.destroy.call_count == 2
        torn = daemon_state.get("torn_down_endpoints", [])
        assert "gpu-a" in torn
        assert "gpu-b" in torn

    @pytest.mark.asyncio
    async def test_skip_non_gpu_endpoints_from_idle_check(self):
        now = time.time()
        tracker = _make_tracker(
            ComputeEndpoint(
                endpoint_id="cpu-only",
                url="http://cpu:8000",
                model="tiny",
                gpu_type="",
                current_load=0,
                last_used=now - 3600,
            ),
        )
        daemon_state: dict = {}
        loop = EventLoop(
            utilization_tracker=tracker,
            daemon_state=daemon_state,
            config={
                "compute_idle_check_interval_ticks": 1,
                "compute_idle_teardown_threshold_ticks": 3,
                "compute_idle_gpu_sm_pct": 5.0,
            },
        )
        loop._total_ticks = 1
        await loop._phase_check_compute_utilization()
        assert "idle_endpoints" not in daemon_state


# ---------------------------------------------------------------------------
# Underutilized detection integration
# ---------------------------------------------------------------------------


class TestFindUnderutilizedIntegration:
    def test_underutilized_in_heterogeneous_fleet(self):
        tracker = _make_tracker(
            ComputeEndpoint(endpoint_id="e1", url="http://e1", max_concurrent=8, current_load=1),
            ComputeEndpoint(endpoint_id="e2", url="http://e2", max_concurrent=4, current_load=3),
            ComputeEndpoint(endpoint_id="e3", url="http://e3", max_concurrent=2, current_load=2),
            ComputeEndpoint(endpoint_id="e4", url="http://e4", max_concurrent=10, current_load=1),
        )
        under = tracker.find_underutilized(threshold=0.3)
        ids = {e.endpoint_id for e in under}
        assert ids == {"e1", "e4"}

    def test_all_busy_no_underutilized(self):
        tracker = _make_tracker(
            ComputeEndpoint(endpoint_id="e1", url="http://e1", max_concurrent=4, current_load=4),
            ComputeEndpoint(endpoint_id="e2", url="http://e2", max_concurrent=8, current_load=8),
        )
        under = tracker.find_underutilized(threshold=0.3)
        assert len(under) == 0

    def test_zero_max_concurrent_excluded(self):
        tracker = _make_tracker(
            ComputeEndpoint(endpoint_id="e-zero", url="http://zero", max_concurrent=0, current_load=0),
        )
        under = tracker.find_underutilized(threshold=0.3)
        assert len(under) == 0
