"""Integration tests for bill-7 compute idle wiring through daemon endpoints.

Proves the compute idle detection pipeline is wired end-to-end:
- GET /admin/compute/idle returns idle_endpoints and torn_down_endpoints
- UtilizationTracker.find_idle_gpus() detects idle GPUs with mock data
- Idle tick counting and threshold triggering in daemon state
- Auto-teardown when idle threshold exceeded via deployment manager
- Config gates: check_interval_ticks, teardown_threshold_ticks, gpu_sm_pct
"""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from general_ludd.daemon import create_daemon_app
from general_ludd.event_loop.loop import EventLoop
from general_ludd.infra.utilization import ComputeEndpoint, UtilizationTracker


def _make_tracker(*endpoints: ComputeEndpoint) -> UtilizationTracker:
    tracker = UtilizationTracker()
    for ep in endpoints:
        tracker._endpoints[ep.endpoint_id] = ep
    return tracker


class TestAdminComputeIdleEndpoint:
    def test_get_idle_returns_empty_when_no_state(self):
        with patch(
            "general_ludd.ansible.runner.AnsibleRunnerAdapter",
            return_value=MagicMock(),
        ):
            app = create_daemon_app(tick_interval=10.0)
            with TestClient(app) as client:
                resp = client.get("/admin/compute/idle")
                assert resp.status_code == 200
                body = resp.json()
                assert body["idle_endpoints"] == []
                assert body["torn_down_endpoints"] == []

    def test_get_idle_returns_populated_state(self):
        with patch(
            "general_ludd.ansible.runner.AnsibleRunnerAdapter",
            return_value=MagicMock(),
        ):
            app = create_daemon_app(tick_interval=10.0)
            app.state.daemon_state["idle_endpoints"] = {
                "gpu1": {
                    "endpoint_id": "gpu1",
                    "idle_ticks": 3,
                    "gpu_type": "A100",
                },
                "gpu2": {
                    "endpoint_id": "gpu2",
                    "idle_ticks": 1,
                    "gpu_type": "H100",
                },
            }
            app.state.daemon_state["torn_down_endpoints"] = ["gpu3"]
            with TestClient(app) as client:
                resp = client.get("/admin/compute/idle")
                assert resp.status_code == 200
                body = resp.json()
                assert len(body["idle_endpoints"]) == 2
                assert body["torn_down_endpoints"] == ["gpu3"]
                eids = {e["endpoint_id"] for e in body["idle_endpoints"]}
                assert eids == {"gpu1", "gpu2"}

    def test_get_idle_returns_idle_ticks_in_state(self):
        with patch(
            "general_ludd.ansible.runner.AnsibleRunnerAdapter",
            return_value=MagicMock(),
        ):
            app = create_daemon_app(tick_interval=10.0)
            app.state.daemon_state["idle_endpoints"] = {
                "gpu-a": {"endpoint_id": "gpu-a", "idle_ticks": 5},
            }
            with TestClient(app) as client:
                resp = client.get("/admin/compute/idle")
                body = resp.json()
                assert body["idle_endpoints"][0]["idle_ticks"] == 5

    def test_get_idle_produces_valid_json_schema(self):
        with patch(
            "general_ludd.ansible.runner.AnsibleRunnerAdapter",
            return_value=MagicMock(),
        ):
            app = create_daemon_app(tick_interval=10.0)
            with TestClient(app) as client:
                resp = client.get("/admin/compute/idle")
                body = resp.json()
                assert isinstance(body, dict)
                assert "idle_endpoints" in body
                assert "torn_down_endpoints" in body
                assert isinstance(body["idle_endpoints"], list)
                assert isinstance(body["torn_down_endpoints"], list)


class TestUtilizationTrackerFindIdleGpus:
    def test_find_idle_detects_low_sm_utilization(self):
        tracker = UtilizationTracker(max_history=10)
        tracker.register_endpoint("gpu-low", "http://low:8000", gpu_type="A100")
        tracker.register_endpoint("gpu-high", "http://high:8000", gpu_type="A100")

        tracker.update_gpu_metrics("gpu-low", {"gpu_sm_util_pct": 2.0})
        tracker.update_gpu_metrics("gpu-high", {"gpu_sm_util_pct": 88.0})

        idle = tracker.find_idle_gpus(threshold=5.0, window=900.0)
        assert len(idle) == 1
        assert idle[0].endpoint_id == "gpu-low"

    def test_find_idle_requires_sustained_low_within_window(self):
        tracker = UtilizationTracker(max_history=10)
        tracker.register_endpoint("gpu0", "http://gpu0:8000", gpu_type="A100")

        tracker.update_gpu_metrics("gpu0", {"gpu_sm_util_pct": 3.0})
        tracker.update_gpu_metrics("gpu0", {"gpu_sm_util_pct": 95.0})
        tracker.update_gpu_metrics("gpu0", {"gpu_sm_util_pct": 2.0})

        idle = tracker.find_idle_gpus(threshold=5.0, window=60.0)
        assert len(idle) == 0

    def test_find_idle_zero_load_stale_also_detected(self):
        now = time.time()
        tracker = _make_tracker(
            ComputeEndpoint(
                endpoint_id="stale-gpu",
                url="http://stale:8000",
                gpu_type="A100",
                current_load=0,
                last_used=now - 1800,
            ),
        )
        idle = tracker.find_idle_gpus(threshold=5.0, window=900.0)
        assert len(idle) == 1
        assert idle[0].endpoint_id == "stale-gpu"

    def test_find_idle_excludes_non_gpu_endpoints(self):
        tracker = UtilizationTracker(max_history=10)
        tracker.register_endpoint("cpu", "http://cpu:8000", gpu_type="")
        tracker.update_gpu_metrics("cpu", {"gpu_sm_util_pct": 1.0})
        idle = tracker.find_idle_gpus(threshold=5.0, window=60.0)
        assert len(idle) == 0

    def test_find_idle_excludes_inactive_endpoints(self):
        now = time.time()
        tracker = _make_tracker(
            ComputeEndpoint(
                endpoint_id="dead-gpu",
                url="http://dead:8000",
                gpu_type="A100",
                active=False,
                current_load=0,
                last_used=now - 3600,
            ),
        )
        idle = tracker.find_idle_gpus(threshold=5.0, window=900.0)
        assert len(idle) == 0


class TestIdleTickCountingAndThreshold:
    @pytest.mark.asyncio
    async def test_idle_counter_increments_across_ticks(self):
        now = time.time()
        tracker = _make_tracker(
            ComputeEndpoint(
                endpoint_id="gpu1",
                url="http://gpu1:8000",
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
                "compute_idle_teardown_threshold_ticks": 4,
                "compute_idle_gpu_sm_pct": 5.0,
            },
        )

        for tick in range(1, 4):
            loop._total_ticks = tick
            await loop._phase_check_compute_utilization()
            idle = daemon_state.get("idle_endpoints", {})
            assert "gpu1" in idle, f"tick {tick}: gpu1 should be tracked"
            assert idle["gpu1"]["idle_ticks"] == tick

    @pytest.mark.asyncio
    async def test_threshold_exceeded_triggers_teardown(self):
        now = time.time()
        tracker = _make_tracker(
            ComputeEndpoint(
                endpoint_id="gpu-to-teardown",
                url="http://teardown:8000",
                gpu_type="A100",
                current_load=0,
                last_used=now - 3600,
            ),
        )
        daemon_state: dict = {}
        deploy_mgr = AsyncMock()
        deploy_mgr.destroy = AsyncMock()
        deploy_mgr.get_deployment = MagicMock(return_value=MagicMock(instance_id="gpu-to-teardown"))

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
        deploy_mgr.destroy.assert_called_once_with("gpu-to-teardown")
        assert "gpu-to-teardown" not in daemon_state.get("idle_endpoints", {})
        assert "gpu-to-teardown" in daemon_state.get("torn_down_endpoints", [])

    @pytest.mark.asyncio
    async def test_non_idle_endpoint_resets_counter(self):
        now = time.time()
        tracker = _make_tracker(
            ComputeEndpoint(
                endpoint_id="gpu1",
                url="http://gpu1:8000",
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

        tracker._endpoints["gpu1"].current_load = 5
        tracker._endpoints["gpu1"].last_used = time.time()

        loop._total_ticks = 3
        await loop._phase_check_compute_utilization()
        assert "gpu1" not in daemon_state.get("idle_endpoints", {})


class TestAutoTeardownWhenThresholdExceeded:
    @pytest.mark.asyncio
    async def test_multiple_idle_teardown_at_same_threshold(self):
        now = time.time()
        tracker = _make_tracker(
            ComputeEndpoint(
                endpoint_id="idle-a", url="http://a:8000", gpu_type="A100",
                current_load=0, last_used=now - 3600,
            ),
            ComputeEndpoint(
                endpoint_id="idle-b", url="http://b:8000", gpu_type="A100",
                current_load=0, last_used=now - 3600,
            ),
        )
        daemon_state: dict = {}
        deploy_mgr = AsyncMock()
        deploy_mgr.destroy = AsyncMock()
        deploy_mgr.get_deployment = MagicMock(side_effect=lambda eid: MagicMock(instance_id=eid))

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

    @pytest.mark.asyncio
    async def test_torn_down_endpoint_not_retried(self):
        now = time.time()
        tracker = _make_tracker(
            ComputeEndpoint(
                endpoint_id="gpu1", url="http://gpu1:8000", gpu_type="A100",
                current_load=0, last_used=now - 3600,
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
                "compute_idle_teardown_threshold_ticks": 1,
                "compute_idle_gpu_sm_pct": 5.0,
            },
        )
        loop._total_ticks = 1
        await loop._phase_check_compute_utilization()
        assert deploy_mgr.destroy.call_count == 1

        deploy_mgr.destroy.reset_mock()
        loop._total_ticks = 2
        await loop._phase_check_compute_utilization()
        deploy_mgr.destroy.assert_not_called()


class TestConfigGates:
    @pytest.mark.asyncio
    async def test_check_interval_skips_when_not_met(self):
        now = time.time()
        tracker = _make_tracker(
            ComputeEndpoint(
                endpoint_id="gpu1", url="http://gpu1:8000", gpu_type="A100",
                current_load=0, last_used=now - 3600,
            ),
        )
        daemon_state: dict = {}
        loop = EventLoop(
            utilization_tracker=tracker,
            daemon_state=daemon_state,
            config={
                "compute_idle_check_interval_ticks": 5,
                "compute_idle_teardown_threshold_ticks": 2,
                "compute_idle_gpu_sm_pct": 5.0,
            },
        )
        loop._total_ticks = 2
        await loop._phase_check_compute_utilization()
        assert "idle_endpoints" not in daemon_state

    @pytest.mark.asyncio
    async def test_teardown_threshold_ticks_gate_timing(self):
        now = time.time()
        tracker = _make_tracker(
            ComputeEndpoint(
                endpoint_id="gpu1", url="http://gpu1:8000", gpu_type="A100",
                current_load=0, last_used=now - 3600,
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

        for tick in range(1, 3):
            loop._total_ticks = tick
            await loop._phase_check_compute_utilization()
            deploy_mgr.destroy.assert_not_called()

        loop._total_ticks = 3
        await loop._phase_check_compute_utilization()
        deploy_mgr.destroy.assert_called_once()

    @pytest.mark.asyncio
    async def test_gpu_sm_pct_threshold_determines_idle(self):
        time.time()
        tracker = _make_tracker(
            ComputeEndpoint(
                endpoint_id="gpu-busy", url="http://busy:8000", gpu_type="A100",
                current_load=2, last_used=time.time(),
            ),
        )
        tracker.update_gpu_metrics("gpu-busy", {"gpu_sm_util_pct": 90.0})
        daemon_state: dict = {}
        loop = EventLoop(
            utilization_tracker=tracker,
            daemon_state=daemon_state,
            config={
                "compute_idle_check_interval_ticks": 1,
                "compute_idle_teardown_threshold_ticks": 3,
                "compute_idle_gpu_sm_pct": 10.0,
            },
        )
        loop._total_ticks = 1
        await loop._phase_check_compute_utilization()
        assert "gpu-busy" not in daemon_state.get("idle_endpoints", {})
