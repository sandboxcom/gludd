"""Tests for compute idle detection and auto-teardown phase."""

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


class TestUtilizationTrackerFindIdleGpus:
    def test_find_idle_gpus_returns_endpoints_below_threshold(self):
        now = time.time()
        tracker = _make_tracker(
            ComputeEndpoint(endpoint_id="ep1", url="http://gpu1:8000", model="llama3",
                            gpu_type="A100", current_load=0, last_used=now - 1000),
            ComputeEndpoint(endpoint_id="ep2", url="http://gpu2:8000", model="codellama",
                            gpu_type="H100", current_load=2, last_used=now),
        )
        idle = tracker.find_idle_gpus(threshold=5.0, window=900)
        assert len(idle) == 1
        assert idle[0].endpoint_id == "ep1"

    def test_find_idle_gpus_skips_non_gpu_endpoints(self):
        now = time.time()
        tracker = _make_tracker(
            ComputeEndpoint(endpoint_id="ep1", url="http://cpu1:8000", model="tiny",
                            gpu_type="", current_load=0, last_used=now - 1000),
        )
        idle = tracker.find_idle_gpus(threshold=5.0, window=900)
        assert len(idle) == 0

    def test_find_idle_gpus_recently_used_is_not_idle(self):
        now = time.time()
        tracker = _make_tracker(
            ComputeEndpoint(endpoint_id="ep1", url="http://gpu1:8000", model="llama3",
                            gpu_type="A100", current_load=0, last_used=now - 100),
        )
        idle = tracker.find_idle_gpus(threshold=5.0, window=900)
        assert len(idle) == 0

    def test_find_idle_gpus_active_load_is_not_idle(self):
        now = time.time()
        tracker = _make_tracker(
            ComputeEndpoint(endpoint_id="ep1", url="http://gpu1:8000", model="llama3",
                            gpu_type="A100", current_load=3, last_used=now - 1000),
        )
        idle = tracker.find_idle_gpus(threshold=5.0, window=900)
        assert len(idle) == 0


class TestPhaseCheckComputeUtilization:
    @pytest.mark.asyncio
    async def test_underutilized_endpoint_tracked_in_daemon_state(self):
        now = time.time()
        tracker = _make_tracker(
            ComputeEndpoint(endpoint_id="ep1", url="http://gpu1:8000", model="llama3",
                            gpu_type="A100", current_load=0, last_used=now - 1000),
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
        idle = daemon_state.get("idle_endpoints", {})
        assert "ep1" in idle
        assert idle["ep1"]["idle_ticks"] == 1

    @pytest.mark.asyncio
    async def test_idle_counter_increments_each_tick(self):
        now = time.time()
        tracker = _make_tracker(
            ComputeEndpoint(endpoint_id="ep1", url="http://gpu1:8000", model="llama3",
                            gpu_type="A100", current_load=0, last_used=now - 1000),
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
        assert daemon_state["idle_endpoints"]["ep1"]["idle_ticks"] == 1
        loop._total_ticks = 2
        await loop._phase_check_compute_utilization()
        assert daemon_state["idle_endpoints"]["ep1"]["idle_ticks"] == 2
        loop._total_ticks = 3
        await loop._phase_check_compute_utilization()
        assert daemon_state["idle_endpoints"]["ep1"]["idle_ticks"] == 3

    @pytest.mark.asyncio
    async def test_teardown_triggered_after_threshold_ticks(self):
        now = time.time()
        tracker = _make_tracker(
            ComputeEndpoint(endpoint_id="ep1", url="http://gpu1:8000", model="llama3",
                            gpu_type="A100", current_load=0, last_used=now - 1000),
        )
        daemon_state: dict = {}
        deploy_mgr = AsyncMock()
        deploy_mgr.destroy = AsyncMock()
        deploy_mgr.get_deployment = MagicMock(return_value=MagicMock(instance_id="ep1"))
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
        deploy_mgr.destroy.assert_not_called()
        loop._total_ticks = 2
        await loop._phase_check_compute_utilization()
        deploy_mgr.destroy.assert_called_once_with("ep1")
        assert "ep1" not in daemon_state.get("idle_endpoints", {})
        assert "ep1" in daemon_state.get("torn_down_endpoints", [])

    @pytest.mark.asyncio
    async def test_non_idle_endpoint_resets_counter(self):
        now = time.time()
        tracker = _make_tracker(
            ComputeEndpoint(endpoint_id="ep1", url="http://gpu1:8000", model="llama3",
                            gpu_type="A100", current_load=0, last_used=now - 1000),
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
        assert daemon_state["idle_endpoints"]["ep1"]["idle_ticks"] == 1
        tracker._endpoints["ep1"].current_load = 5
        tracker._endpoints["ep1"].last_used = time.time()
        loop._total_ticks = 2
        await loop._phase_check_compute_utilization()
        assert "ep1" not in daemon_state.get("idle_endpoints", {})

    @pytest.mark.asyncio
    async def test_gpu_idle_respects_sm_threshold_via_load(self):
        now = time.time()
        tracker = _make_tracker(
            ComputeEndpoint(endpoint_id="ep1", url="http://gpu1:8000", model="llama3",
                            gpu_type="A100", current_load=0, last_used=now - 1000),
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
        assert "ep1" in daemon_state.get("idle_endpoints", {})

    @pytest.mark.asyncio
    async def test_phase_skips_when_not_check_tick(self):
        tracker = _make_tracker(
            ComputeEndpoint(endpoint_id="ep1", url="http://gpu1:8000", model="llama3",
                            gpu_type="A100", current_load=0, last_used=time.time() - 1000),
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
        loop._total_ticks = 2
        await loop._phase_check_compute_utilization()
        assert "idle_endpoints" not in daemon_state

    @pytest.mark.asyncio
    async def test_phase_runs_when_no_tracker(self):
        loop = EventLoop(
            utilization_tracker=None,
            daemon_state={},
            config={
                "compute_idle_check_interval_ticks": 1,
                "compute_idle_teardown_threshold_ticks": 3,
                "compute_idle_gpu_sm_pct": 5.0,
            },
        )
        await loop._phase_check_compute_utilization()

    @pytest.mark.asyncio
    async def test_teardown_sets_torn_down_flag_in_daemon_state(self):
        now = time.time()
        tracker = _make_tracker(
            ComputeEndpoint(endpoint_id="ep1", url="http://gpu1:8000", model="llama3",
                            gpu_type="A100", current_load=0, last_used=now - 1000),
        )
        daemon_state: dict = {}
        deploy_mgr = AsyncMock()
        deploy_mgr.destroy = AsyncMock()
        deploy_mgr.get_deployment = MagicMock(return_value=MagicMock(instance_id="ep1"))
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
        assert "ep1" in daemon_state.get("torn_down_endpoints", [])


class TestFindUnderutilized:
    def test_find_underutilized_below_threshold(self):
        tracker = _make_tracker(
            ComputeEndpoint(endpoint_id="ep1", url="http://a:8000", max_concurrent=10, current_load=2),
            ComputeEndpoint(endpoint_id="ep2", url="http://b:8000", max_concurrent=4, current_load=4),
        )
        under = tracker.find_underutilized(threshold=0.3)
        assert len(under) == 1
        assert under[0].endpoint_id == "ep1"

    def test_find_underutilized_none_when_all_busy(self):
        tracker = _make_tracker(
            ComputeEndpoint(endpoint_id="ep1", url="http://a:8000", max_concurrent=4, current_load=4),
        )
        under = tracker.find_underutilized(threshold=0.3)
        assert len(under) == 0
