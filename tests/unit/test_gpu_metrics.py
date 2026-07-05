"""Unit tests for GPU metrics collection and UtilizationTracker GPU integration."""

from __future__ import annotations

import sys
import time
from unittest import mock

from general_ludd.infra.gpu_metrics import GPUMetrics, GPUMetricsCollector
from general_ludd.infra.utilization import ComputeEndpoint, UtilizationTracker


class TestGPUMetricsDataclass:
    def test_defaults_are_zero(self):
        m = GPUMetrics()
        assert m.gpu_sm_util_pct == 0.0
        assert m.gpu_mem_util_pct == 0.0
        assert m.gpu_temp_c == 0.0
        assert m.power_draw_w == 0.0
        assert m.memory_used_mb == 0.0
        assert m.memory_total_mb == 0.0

    def test_as_dict(self):
        m = GPUMetrics(
            gpu_sm_util_pct=85.0,
            gpu_mem_util_pct=60.0,
            gpu_temp_c=72.0,
            power_draw_w=150.0,
            memory_used_mb=12000.0,
            memory_total_mb=24576.0,
        )
        d = m.as_dict()
        assert d["gpu_sm_util_pct"] == 85.0
        assert d["gpu_mem_util_pct"] == 60.0
        assert d["gpu_temp_c"] == 72.0
        assert d["power_draw_w"] == 150.0
        assert d["memory_used_mb"] == 12000.0
        assert d["memory_total_mb"] == 24576.0


class TestGPUMetricsCollectorInaccessible:
    """Graceful degradation when NVML is unavailable (macOS, no GPU, import error)."""

    def test_is_available_returns_false_when_nvml_not_importable(self):
        assert not GPUMetricsCollector.is_available()

    def test_collect_gpu_metrics_returns_zeros(self):
        result = GPUMetricsCollector.collect_gpu_metrics()
        assert result.gpu_sm_util_pct == 0.0
        assert result.gpu_mem_util_pct == 0.0
        assert result.gpu_temp_c == 0.0
        assert result.power_draw_w == 0.0
        assert result.memory_used_mb == 0.0
        assert result.memory_total_mb == 0.0

    def test_collect_all_gpu_metrics_returns_empty_list(self):
        result = GPUMetricsCollector.collect_all_gpu_metrics()
        assert result == []

    def test_shutdown_is_noop(self):
        GPUMetricsCollector.shutdown()

    def test_collect_gpu_metrics_handles_import_error(self):
        with mock.patch.dict(sys.modules, {"pynvml": None}):
            import importlib

            import general_ludd.infra.gpu_metrics as gm

            importlib.reload(gm)
            try:
                assert not gm.GPUMetricsCollector.is_available()
                result = gm.GPUMetricsCollector.collect_gpu_metrics()
                assert result.gpu_sm_util_pct == 0.0
            finally:
                importlib.reload(gm)


class TestGPUMetricsCollectorMocked:
    def test_collect_gpu_metrics_with_mocked_nvml(self):
        mock_nvml = mock.MagicMock()

        mock_handle = mock.MagicMock()
        mock_nvml.nvmlDeviceGetHandleByIndex.return_value = mock_handle

        util_info = mock.MagicMock()
        util_info.gpu = 75
        util_info.memory = 50
        mock_nvml.nvmlDeviceGetUtilizationRates.return_value = util_info

        mock_nvml.nvmlDeviceGetTemperature.return_value = 65
        mock_nvml.NVML_TEMPERATURE_GPU = 0

        mock_nvml.nvmlDeviceGetPowerUsage.return_value = 200000

        mem_info = mock.MagicMock()
        mem_info.used = 8000 * 1024 * 1024
        mem_info.total = 16000 * 1024 * 1024
        mock_nvml.nvmlDeviceGetMemoryInfo.return_value = mem_info

        mock_nvml.nvmlDeviceGetCount.return_value = 1

        with mock.patch.dict(sys.modules, {"pynvml": mock_nvml}):
            import importlib

            import general_ludd.infra.gpu_metrics as gm

            importlib.reload(gm)
            try:
                assert gm.GPUMetricsCollector.is_available()

                result = gm.GPUMetricsCollector.collect_gpu_metrics(0)
                assert result.gpu_sm_util_pct == 75.0
                assert result.gpu_mem_util_pct == 50.0
                assert result.gpu_temp_c == 65.0
                assert result.power_draw_w == 200.0
                assert result.memory_used_mb == 8000.0
                assert result.memory_total_mb == 16000.0

                all_metrics = gm.GPUMetricsCollector.collect_all_gpu_metrics()
                assert len(all_metrics) == 1

                gm.GPUMetricsCollector.shutdown()
                mock_nvml.nvmlShutdown.assert_called_once()
            finally:
                importlib.reload(gm)

    def test_collect_gpu_metrics_failure_returns_zeros(self):
        mock_nvml = mock.MagicMock()
        mock_nvml.nvmlInit.side_effect = RuntimeError("NVML init failed")

        with mock.patch.dict(sys.modules, {"pynvml": mock_nvml}):
            import importlib

            import general_ludd.infra.gpu_metrics as gm

            importlib.reload(gm)
            try:
                assert not gm.GPUMetricsCollector.is_available()
                result = gm.GPUMetricsCollector.collect_gpu_metrics()
                assert result.gpu_sm_util_pct == 0.0
            finally:
                importlib.reload(gm)


class TestComputeEndpointGPUFields:
    def test_default_gpu_fields_are_none(self):
        ep = ComputeEndpoint(endpoint_id="e1", url="http://e1")
        assert ep.gpu_sm_util is None
        assert ep.gpu_mem_util is None
        assert ep.gpu_temp_c is None


class TestUtilizationTrackerGPUIntegration:
    def test_update_gpu_metrics_updates_endpoint(self):
        tracker = UtilizationTracker()
        tracker.register_endpoint("e1", "http://e1")
        tracker.update_gpu_metrics("e1", {
            "gpu_sm_util_pct": 82.0,
            "gpu_mem_util_pct": 45.0,
            "gpu_temp_c": 71.0,
        })
        ep = tracker.get_endpoint("e1")
        assert ep is not None
        assert ep.gpu_sm_util == 82.0
        assert ep.gpu_mem_util == 45.0
        assert ep.gpu_temp_c == 71.0

    def test_update_gpu_metrics_unknown_endpoint_noop(self):
        tracker = UtilizationTracker()
        tracker.update_gpu_metrics("nonexistent", {"gpu_sm_util_pct": 50.0})

    def test_update_gpu_metrics_partial_fields(self):
        tracker = UtilizationTracker()
        tracker.register_endpoint("e1", "http://e1")
        tracker.update_gpu_metrics("e1", {"gpu_sm_util_pct": 50.0})
        ep = tracker.get_endpoint("e1")
        assert ep is not None
        assert ep.gpu_sm_util == 50.0
        assert ep.gpu_mem_util is None
        assert ep.gpu_temp_c is None

    def test_get_utilization_report_includes_gpu_metrics(self):
        tracker = UtilizationTracker()
        tracker.register_endpoint("e1", "http://e1", model="llama3", max_concurrent=4)
        tracker.update_gpu_metrics("e1", {
            "gpu_sm_util_pct": 90.0,
            "gpu_mem_util_pct": 60.0,
            "gpu_temp_c": 75.0,
        })
        report = tracker.get_utilization_report()
        ep_report = report["endpoints"][0]
        assert ep_report["gpu_sm_util"] == 90.0
        assert ep_report["gpu_mem_util"] == 60.0
        assert ep_report["gpu_temp_c"] == 75.0

    def test_get_utilization_report_gpu_defaults_none(self):
        tracker = UtilizationTracker()
        tracker.register_endpoint("e1", "http://e1")
        report = tracker.get_utilization_report()
        ep_report = report["endpoints"][0]
        assert ep_report["gpu_sm_util"] is None
        assert ep_report["gpu_mem_util"] is None
        assert ep_report["gpu_temp_c"] is None

    def test_find_idle_gpus_below_threshold(self):
        tracker = UtilizationTracker(max_history=10)
        tracker.register_endpoint("e1", "http://e1")
        tracker.register_endpoint("e2", "http://e2")
        tracker.update_gpu_metrics("e1", {"gpu_sm_util_pct": 3.0})
        tracker.update_gpu_metrics("e2", {"gpu_sm_util_pct": 95.0})
        idle = tracker.find_idle_gpus(threshold_pct=10.0, window_seconds=60.0)
        assert len(idle) == 1
        assert idle[0].endpoint_id == "e1"

    def test_find_idle_gpus_none_below_threshold(self):
        tracker = UtilizationTracker(max_history=10)
        tracker.register_endpoint("e1", "http://e1")
        tracker.register_endpoint("e2", "http://e2")
        tracker.update_gpu_metrics("e1", {"gpu_sm_util_pct": 95.0})
        tracker.update_gpu_metrics("e2", {"gpu_sm_util_pct": 88.0})
        idle = tracker.find_idle_gpus(threshold_pct=10.0, window_seconds=60.0)
        assert len(idle) == 0

    def test_find_idle_gpus_requires_all_window_below_threshold(self):
        tracker = UtilizationTracker(max_history=10)
        tracker.register_endpoint("e1", "http://e1")
        tracker.update_gpu_metrics("e1", {"gpu_sm_util_pct": 5.0})
        tracker.update_gpu_metrics("e1", {"gpu_sm_util_pct": 95.0})
        idle = tracker.find_idle_gpus(threshold_pct=10.0, window_seconds=60.0)
        assert len(idle) == 0

    def test_find_idle_gpus_no_history(self):
        tracker = UtilizationTracker()
        tracker.register_endpoint("e1", "http://e1")
        idle = tracker.find_idle_gpus(threshold_pct=10.0, window_seconds=60.0)
        assert len(idle) == 0

    def test_find_idle_gpus_excludes_inactive(self):
        tracker = UtilizationTracker(max_history=10)
        tracker.register_endpoint("e1", "http://e1")
        tracker.update_gpu_metrics("e1", {"gpu_sm_util_pct": 3.0})
        tracker.unregister_endpoint("e1")
        idle = tracker.find_idle_gpus(threshold_pct=10.0, window_seconds=60.0)
        assert len(idle) == 0

    def test_find_idle_gpus_respects_window(self):
        tracker = UtilizationTracker(max_history=10)
        tracker.register_endpoint("e1", "http://e1")
        tracker.update_gpu_metrics("e1", {"gpu_sm_util_pct": 5.0})
        now = time.time()
        st_mock = mock.MagicMock(return_value=now + 120.0)
        with mock.patch("general_ludd.infra.utilization.time.time", st_mock):
            idle = tracker.find_idle_gpus(threshold_pct=10.0, window_seconds=60.0)
            assert len(idle) == 0

    def test_update_gpu_metrics_truncates_history(self):
        tracker = UtilizationTracker(max_history=3)
        tracker.register_endpoint("e1", "http://e1")
        tracker.update_gpu_metrics("e1", {"gpu_sm_util_pct": 10.0})
        tracker.update_gpu_metrics("e1", {"gpu_sm_util_pct": 20.0})
        tracker.update_gpu_metrics("e1", {"gpu_sm_util_pct": 30.0})
        tracker.update_gpu_metrics("e1", {"gpu_sm_util_pct": 40.0})
        assert len(tracker._gpu_history["e1"]) == 3
        values = [sm for ts, sm in tracker._gpu_history["e1"]]
        assert values == [20.0, 30.0, 40.0]
