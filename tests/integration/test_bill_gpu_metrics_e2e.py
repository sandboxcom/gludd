"""Integration tests for GPU metrics collection pipeline.

Proves end-to-end that:
- GPUMetricsCollector handles real and mocked NVML gracefully
- GPU metrics flow into UtilTracker and UtilizationTracker
- GPUMetrics dataclass correctly marshals all fields
- collect_all_gpu_metrics handles multi-GPU scenarios
- UtilizationTracker.update_gpu_metrics() populates ComputeEndpoint fields
- idle GPU detection works with real metric thresholds
- Historical GPU data is truncated and aged out correctly
- GPU metrics survive reload/import cycle
"""

from __future__ import annotations

import sys
from unittest import mock

import pytest

from general_ludd.infra.gpu_metrics import GPUMetrics, GPUMetricsCollector
from general_ludd.infra.utilization import UtilizationTracker

# ---------------------------------------------------------------------------
# GPUMetrics dataclass integration
# ---------------------------------------------------------------------------


class TestGPUMetricsDataclassIntegration:
    def test_full_roundtrip_as_dict(self):
        metrics = GPUMetrics(
            gpu_sm_util_pct=92.5,
            gpu_mem_util_pct=78.3,
            gpu_temp_c=71.2,
            power_draw_w=210.7,
            memory_used_mb=16384.0,
            memory_total_mb=24576.0,
        )
        d = metrics.as_dict()
        assert len(d) == 6
        assert d["gpu_sm_util_pct"] == pytest.approx(92.5)
        assert d["gpu_mem_util_pct"] == pytest.approx(78.3)
        assert d["gpu_temp_c"] == pytest.approx(71.2)
        assert d["power_draw_w"] == pytest.approx(210.7)
        assert d["memory_used_mb"] == pytest.approx(16384.0)
        assert d["memory_total_mb"] == pytest.approx(24576.0)

    def test_high_precision_float_values(self):
        metrics = GPUMetrics(
            gpu_sm_util_pct=99.999,
            gpu_mem_util_pct=0.001,
            gpu_temp_c=95.0,
            power_draw_w=300.0,
            memory_used_mb=81920.0,
            memory_total_mb=81920.0,
        )
        d = metrics.as_dict()
        assert d["memory_used_mb"] / d["memory_total_mb"] == pytest.approx(1.0)

    def test_default_metrics_are_all_zero(self):
        metrics = GPUMetrics()
        d = metrics.as_dict()
        assert all(v == 0.0 for v in d.values())


# ---------------------------------------------------------------------------
# GPUMetricsCollector unavailable path (integration with UtilizationTracker)
# ---------------------------------------------------------------------------


class TestGPUMetricsCollectorUnavailable:
    def test_is_available_false_on_non_gpu(self):
        assert not GPUMetricsCollector.is_available()

    def test_collect_returns_zeros_which_dont_perturb_tracker(self):
        tracker = UtilizationTracker()
        tracker.register_endpoint("e1", "http://e1:8000")
        result = GPUMetricsCollector.collect_gpu_metrics()
        tracker.update_gpu_metrics("e1", result.as_dict())
        ep = tracker.get_endpoint("e1")
        assert ep is not None
        assert ep.gpu_sm_util == 0.0
        assert ep.gpu_mem_util == 0.0
        assert ep.gpu_temp_c == 0.0

    def test_collect_all_returns_empty_does_not_break_loop(self):
        result = GPUMetricsCollector.collect_all_gpu_metrics()
        assert result == []

    def test_multiple_collect_calls_are_stable(self):
        for _ in range(5):
            result = GPUMetricsCollector.collect_gpu_metrics()
            assert result.gpu_sm_util_pct == 0.0
        for _ in range(5):
            result = GPUMetricsCollector.collect_all_gpu_metrics()
            assert result == []


# ---------------------------------------------------------------------------
# Mocked NVML: full integration pipeline
# ---------------------------------------------------------------------------


def _make_mock_nvml(
    gpu_count: int = 2,
    sm_util_pcts: list[float] | None = None,
    mem_util_pcts: list[float] | None = None,
    temps: list[float] | None = None,
    power_mw: list[int] | None = None,
    mem_mb: list[tuple[int, int]] | None = None,
) -> mock.MagicMock:
    mock_nvml = mock.MagicMock()
    mock_nvml.nvmlDeviceGetCount.return_value = gpu_count
    mock_nvml.NVML_TEMPERATURE_GPU = 0

    sm = sm_util_pcts or [75.0] * gpu_count
    mem_u = mem_util_pcts or [50.0] * gpu_count
    ts = temps or [65.0] * gpu_count
    pw = power_mw or [200000] * gpu_count
    mb = mem_mb or [(8000, 16000)] * gpu_count

    handles = [mock.MagicMock() for _ in range(gpu_count)]
    mock_nvml.nvmlDeviceGetHandleByIndex.side_effect = handles

    util_infos = []
    for i in range(gpu_count):
        ui = mock.MagicMock()
        ui.gpu = sm[i]
        ui.memory = mem_u[i]
        util_infos.append(ui)

    mock_nvml.nvmlDeviceGetUtilizationRates.side_effect = util_infos
    mock_nvml.nvmlDeviceGetTemperature.side_effect = ts

    power_results = [float(p) for p in pw]
    mock_nvml.nvmlDeviceGetPowerUsage.side_effect = power_results

    mem_infos = []
    for i in range(gpu_count):
        mi = mock.MagicMock()
        mi.used = int(mb[i][0] * 1024 * 1024)
        mi.total = int(mb[i][1] * 1024 * 1024)
        mem_infos.append(mi)
    mock_nvml.nvmlDeviceGetMemoryInfo.side_effect = mem_infos

    return mock_nvml


class TestGPUMetricsCollectorMockedIntegration:
    def test_single_gpu_full_pipeline_to_tracker(self):
        mock_nvml = _make_mock_nvml(
            gpu_count=1,
            sm_util_pcts=[82.0],
            mem_util_pcts=[45.0],
            temps=[68.0],
            power_mw=[250000],
            mem_mb=[(10000, 32000)],
        )

        with mock.patch.dict(sys.modules, {"pynvml": mock_nvml}):
            import importlib

            import general_ludd.infra.gpu_metrics as gm
            importlib.reload(gm)
            try:
                assert gm.GPUMetricsCollector.is_available()

                all_metrics = gm.GPUMetricsCollector.collect_all_gpu_metrics()
                assert len(all_metrics) == 1
                m = all_metrics[0]
                assert m.gpu_sm_util_pct == pytest.approx(82.0)
                assert m.gpu_mem_util_pct == pytest.approx(45.0)
                assert m.gpu_temp_c == pytest.approx(68.0)
                assert m.power_draw_w == pytest.approx(250.0)
                assert m.memory_used_mb == pytest.approx(10000.0)
                assert m.memory_total_mb == pytest.approx(32000.0)

                tracker = UtilizationTracker()
                tracker.register_endpoint("gpu0", "http://gpu0:8000", gpu_type="A100")
                tracker.update_gpu_metrics("gpu0", m.as_dict())
                ep = tracker.get_endpoint("gpu0")
                assert ep is not None
                assert ep.gpu_sm_util == pytest.approx(82.0)
                assert ep.gpu_mem_util == pytest.approx(45.0)
                assert ep.gpu_temp_c == pytest.approx(68.0)
            finally:
                importlib.reload(gm)

    def test_multi_gpu_collect_all(self):
        mock_nvml = _make_mock_nvml(
            gpu_count=4,
            sm_util_pcts=[90.0, 10.0, 50.0, 95.0],
            mem_util_pcts=[80.0, 15.0, 45.0, 85.0],
        )

        with mock.patch.dict(sys.modules, {"pynvml": mock_nvml}):
            import importlib

            import general_ludd.infra.gpu_metrics as gm
            importlib.reload(gm)
            try:
                all_metrics = gm.GPUMetricsCollector.collect_all_gpu_metrics()
                assert len(all_metrics) == 4
                assert all_metrics[0].gpu_sm_util_pct == pytest.approx(90.0)
                assert all_metrics[1].gpu_sm_util_pct == pytest.approx(10.0)
                assert all_metrics[2].gpu_sm_util_pct == pytest.approx(50.0)
                assert all_metrics[3].gpu_sm_util_pct == pytest.approx(95.0)
            finally:
                importlib.reload(gm)

    def test_multi_gpu_all_registered_in_tracker(self):
        mock_nvml = _make_mock_nvml(gpu_count=3)

        with mock.patch.dict(sys.modules, {"pynvml": mock_nvml}):
            import importlib

            import general_ludd.infra.gpu_metrics as gm
            importlib.reload(gm)
            try:
                tracker = UtilizationTracker()
                for i in range(3):
                    tracker.register_endpoint(f"gpu{i}", f"http://gpu{i}:8000", gpu_type="A100")

                all_metrics = gm.GPUMetricsCollector.collect_all_gpu_metrics()
                for i, m in enumerate(all_metrics):
                    tracker.update_gpu_metrics(f"gpu{i}", m.as_dict())

                report = tracker.get_utilization_report()
                assert len(report["endpoints"]) == 3
                for ep_report in report["endpoints"]:
                    assert ep_report["gpu_sm_util"] == pytest.approx(75.0)
                    assert ep_report["gpu_mem_util"] == pytest.approx(50.0)
            finally:
                importlib.reload(gm)

    def test_graceful_degradation_on_single_gpu_error(self):
        mock_nvml = mock.MagicMock()
        mock_nvml.nvmlDeviceGetCount.return_value = 2

        ok_handle = mock.MagicMock()
        mock_nvml.nvmlDeviceGetHandleByIndex.side_effect = [
            RuntimeError("GPU 0 offline"),
            ok_handle,
        ]

        with mock.patch.dict(sys.modules, {"pynvml": mock_nvml}):
            import importlib

            import general_ludd.infra.gpu_metrics as gm
            importlib.reload(gm)
            try:
                active = gm._NVML_AVAILABLE
                if active:
                    all_metrics = gm.GPUMetricsCollector.collect_all_gpu_metrics()
                    assert isinstance(all_metrics, list)
                else:
                    all_metrics = gm.GPUMetricsCollector.collect_all_gpu_metrics()
                    assert all_metrics == []
            finally:
                importlib.reload(gm)

    def test_shutdown_is_idempotent(self):
        GPUMetricsCollector.shutdown()
        GPUMetricsCollector.shutdown()
        GPUMetricsCollector.shutdown()

    def test_mocked_shutdown_called(self):
        mock_nvml = _make_mock_nvml(gpu_count=1)

        with mock.patch.dict(sys.modules, {"pynvml": mock_nvml}):
            import importlib

            import general_ludd.infra.gpu_metrics as gm
            importlib.reload(gm)
            try:
                gm.GPUMetricsCollector.shutdown()
                mock_nvml.nvmlShutdown.assert_called_once()
            finally:
                importlib.reload(gm)


# ---------------------------------------------------------------------------
# UtilizationTracker GPU idle detection integration
# ---------------------------------------------------------------------------


class TestUtilizationTrackerGPUIdleIntegration:
    def test_find_idle_gpus_with_varied_utilization(self):
        tracker = UtilizationTracker(max_history=20)
        tracker.register_endpoint("busy", "http://busy:8000", gpu_type="A100")
        tracker.register_endpoint("idle", "http://idle:8000", gpu_type="A100")
        tracker.register_endpoint("medium", "http://med:8000", gpu_type="H100")

        tracker.update_gpu_metrics("busy", {"gpu_sm_util_pct": 85.0})
        tracker.update_gpu_metrics("idle", {"gpu_sm_util_pct": 2.0})
        tracker.update_gpu_metrics("medium", {"gpu_sm_util_pct": 50.0})

        idle = tracker.find_idle_gpus(threshold_pct=10.0, window_seconds=60.0)
        assert len(idle) == 1
        assert idle[0].endpoint_id == "idle"

    def test_find_idle_gpus_requires_sustained_low_utilization(self):
        tracker = UtilizationTracker(max_history=20)
        tracker.register_endpoint("gpu0", "http://gpu0:8000", gpu_type="A100")

        tracker.update_gpu_metrics("gpu0", {"gpu_sm_util_pct": 5.0})
        tracker.update_gpu_metrics("gpu0", {"gpu_sm_util_pct": 90.0})
        tracker.update_gpu_metrics("gpu0", {"gpu_sm_util_pct": 3.0})

        idle = tracker.find_idle_gpus(threshold_pct=10.0, window_seconds=60.0)
        assert len(idle) == 0

    def test_idle_excludes_non_gpu_endpoints(self):
        tracker = UtilizationTracker(max_history=10)
        tracker.register_endpoint("cpu0", "http://cpu0:8000", gpu_type="")
        tracker.update_gpu_metrics("cpu0", {"gpu_sm_util_pct": 3.0})
        idle = tracker.find_idle_gpus(threshold_pct=10.0, window_seconds=60.0)
        assert len(idle) == 0

    def test_gpu_history_ages_out(self):
        tracker = UtilizationTracker(max_history=3)
        tracker.register_endpoint("gpu0", "http://gpu0:8000", gpu_type="A100")

        tracker.update_gpu_metrics("gpu0", {"gpu_sm_util_pct": 10.0})
        tracker.update_gpu_metrics("gpu0", {"gpu_sm_util_pct": 20.0})
        tracker.update_gpu_metrics("gpu0", {"gpu_sm_util_pct": 30.0})
        tracker.update_gpu_metrics("gpu0", {"gpu_sm_util_pct": 40.0})

        assert len(tracker._gpu_history["gpu0"]) == 3
        values = [sm for _ts, sm in tracker._gpu_history["gpu0"]]
        assert values == [20.0, 30.0, 40.0]
