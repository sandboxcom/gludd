"""Integration tests for bill-6 GPU metrics wiring.

Proves the GPUMetricsCollector → UtilizationTracker → ComputeEndpoint pipeline
is fully wired end-to-end, including:
- GPUMetricsCollector instantiation with mocked NVML
- GPU metric collection (SM%/mem/temp/power)
- macOS/no-GPU graceful degradation
- GPUMetricsCollector → UtilizationTracker wiring via update_gpu_metrics
- ComputeEndpoint GPU fields populated after wiring
"""

from __future__ import annotations

import sys
from unittest import mock

import pytest

from general_ludd.infra.gpu_metrics import GPUMetrics, GPUMetricsCollector
from general_ludd.infra.utilization import ComputeEndpoint, UtilizationTracker


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
    mock_nvml.nvmlDeviceGetPowerUsage.side_effect = [float(p) for p in pw]

    mem_infos = []
    for i in range(gpu_count):
        mi = mock.MagicMock()
        mi.used = int(mb[i][0] * 1024 * 1024)
        mi.total = int(mb[i][1] * 1024 * 1024)
        mem_infos.append(mi)
    mock_nvml.nvmlDeviceGetMemoryInfo.side_effect = mem_infos

    return mock_nvml


class TestGPUMetricsCollectorInstantiation:
    def test_collector_defaults_to_unavailable_on_macos(self):
        assert not GPUMetricsCollector.is_available()
        assert GPUMetricsCollector.collect_gpu_metrics() == GPUMetrics()

    def test_collector_becomes_available_with_mocked_nvml(self):
        mock_nvml = _make_mock_nvml(gpu_count=1, sm_util_pcts=[50.0])

        import importlib

        import general_ludd.infra.gpu_metrics as gm
        with mock.patch.dict(sys.modules, {"pynvml": mock_nvml}):
            importlib.reload(gm)
            try:
                assert gm.GPUMetricsCollector.is_available()
            finally:
                pass
        importlib.reload(gm)
        assert not gm.GPUMetricsCollector.is_available()

    def test_collector_shutdown_idempotent_when_unavailable(self):
        GPUMetricsCollector.shutdown()
        GPUMetricsCollector.shutdown()

    def test_collect_all_returns_empty_when_unavailable(self):
        if not GPUMetricsCollector.is_available():
            assert GPUMetricsCollector.collect_all_gpu_metrics() == []


class TestGPUFullMetricCollection:
    def test_collects_sm_mem_temp_power_all_fields(self):
        mock_nvml = _make_mock_nvml(
            gpu_count=1,
            sm_util_pcts=[88.0],
            mem_util_pcts=[62.0],
            temps=[73.0],
            power_mw=[310000],
            mem_mb=[(20480, 40960)],
        )

        with mock.patch.dict(sys.modules, {"pynvml": mock_nvml}):
            import importlib

            import general_ludd.infra.gpu_metrics as gm
            importlib.reload(gm)
            try:
                m = gm.GPUMetricsCollector.collect_gpu_metrics(0)
                assert m.gpu_sm_util_pct == pytest.approx(88.0)
                assert m.gpu_mem_util_pct == pytest.approx(62.0)
                assert m.gpu_temp_c == pytest.approx(73.0)
                assert m.power_draw_w == pytest.approx(310.0)
                assert m.memory_used_mb == pytest.approx(20480.0)
                assert m.memory_total_mb == pytest.approx(40960.0)
            finally:
                importlib.reload(gm)

    def test_collect_handles_missing_temperature_gracefully(self):
        mock_nvml = _make_mock_nvml(gpu_count=1, sm_util_pcts=[44.0], mem_util_pcts=[30.0])
        mock_nvml.nvmlDeviceGetTemperature.side_effect = RuntimeError("no temp sensor")

        with mock.patch.dict(sys.modules, {"pynvml": mock_nvml}):
            import importlib

            import general_ludd.infra.gpu_metrics as gm
            importlib.reload(gm)
            try:
                m = gm.GPUMetricsCollector.collect_gpu_metrics(0)
                assert m.gpu_temp_c == 0.0
                assert m.gpu_sm_util_pct == pytest.approx(44.0)
            finally:
                importlib.reload(gm)

    def test_collect_handles_failing_device_index_returning_zeros(self):
        mock_nvml = mock.MagicMock()
        mock_nvml.nvmlDeviceGetCount.return_value = 1
        mock_nvml.nvmlDeviceGetHandleByIndex.side_effect = RuntimeError("device offline")

        with mock.patch.dict(sys.modules, {"pynvml": mock_nvml}):
            import importlib

            import general_ludd.infra.gpu_metrics as gm
            importlib.reload(gm)
            try:
                m = gm.GPUMetricsCollector.collect_gpu_metrics(0)
                assert m.gpu_sm_util_pct == 0.0
                assert m.gpu_mem_util_pct == 0.0
            finally:
                importlib.reload(gm)

    def test_collect_power_draw_w_is_converted_from_milliwatts(self):
        mock_nvml = _make_mock_nvml(gpu_count=1, sm_util_pcts=[60.0], power_mw=[275000])

        with mock.patch.dict(sys.modules, {"pynvml": mock_nvml}):
            import importlib

            import general_ludd.infra.gpu_metrics as gm
            importlib.reload(gm)
            try:
                m = gm.GPUMetricsCollector.collect_gpu_metrics(0)
                assert m.power_draw_w == pytest.approx(275.0)
            finally:
                importlib.reload(gm)


class TestMacOSNoGPUGracefulDegradation:
    def test_collect_returns_zeroed_metrics_on_no_gpu(self):
        result = GPUMetricsCollector.collect_gpu_metrics()
        d = result.as_dict()
        for k, v in d.items():
            assert v == 0.0, f"field {k} should be 0.0, got {v}"

    def test_collect_all_returns_empty_list_on_no_gpu(self):
        if not GPUMetricsCollector.is_available():
            result = GPUMetricsCollector.collect_all_gpu_metrics()
            assert result == []

    def test_zeroed_metrics_do_not_corrupt_tracker_state(self):
        tracker = UtilizationTracker()
        tracker.register_endpoint("e1", "http://e1:8000", gpu_type="A100")
        result = GPUMetricsCollector.collect_gpu_metrics()
        tracker.update_gpu_metrics("e1", result.as_dict())
        ep = tracker.get_endpoint("e1")
        assert ep is not None
        assert ep.gpu_sm_util == 0.0
        assert ep.gpu_mem_util == 0.0
        assert ep.gpu_temp_c == 0.0

    def test_repeated_collection_returns_consistent_zeros(self):
        for _ in range(3):
            m = GPUMetricsCollector.collect_gpu_metrics()
            assert m.as_dict() == {
                "gpu_sm_util_pct": 0.0,
                "gpu_mem_util_pct": 0.0,
                "gpu_temp_c": 0.0,
                "power_draw_w": 0.0,
                "memory_used_mb": 0.0,
                "memory_total_mb": 0.0,
            }


class TestGPUMetricsCollectorUtilizationTrackerWiring:
    def test_metrics_flow_into_tracker_endpoint_gpu_fields(self):
        mock_nvml = _make_mock_nvml(
            gpu_count=1,
            sm_util_pcts=[92.0],
            mem_util_pcts=[44.0],
            temps=[67.0],
            power_mw=[210000],
            mem_mb=[(12000, 24000)],
        )

        with mock.patch.dict(sys.modules, {"pynvml": mock_nvml}):
            import importlib

            import general_ludd.infra.gpu_metrics as gm
            importlib.reload(gm)
            try:
                tracker = UtilizationTracker()
                tracker.register_endpoint("gpu0", "http://gpu0:8000", gpu_type="A100_80")
                m = gm.GPUMetricsCollector.collect_gpu_metrics(0)
                tracker.update_gpu_metrics("gpu0", m.as_dict())

                ep = tracker.get_endpoint("gpu0")
                assert ep is not None
                assert ep.gpu_sm_util == pytest.approx(92.0)
                assert ep.gpu_mem_util == pytest.approx(44.0)
                assert ep.gpu_temp_c == pytest.approx(67.0)
            finally:
                importlib.reload(gm)

    def test_multi_gpu_metrics_flow_into_multiple_endpoints(self):
        mock_nvml = _make_mock_nvml(
            gpu_count=3,
            sm_util_pcts=[85.0, 12.0, 95.0],
            mem_util_pcts=[70.0, 20.0, 90.0],
            temps=[60.0, 55.0, 72.0],
        )

        with mock.patch.dict(sys.modules, {"pynvml": mock_nvml}):
            import importlib

            import general_ludd.infra.gpu_metrics as gm
            importlib.reload(gm)
            try:
                tracker = UtilizationTracker()
                for i in range(3):
                    tracker.register_endpoint(f"gpu{i}", f"http://gpu{i}:8000", gpu_type="A100")
                metrics_list = gm.GPUMetricsCollector.collect_all_gpu_metrics()
                for i, m in enumerate(metrics_list):
                    tracker.update_gpu_metrics(f"gpu{i}", m.as_dict())

                assert tracker.get_endpoint("gpu0").gpu_sm_util == pytest.approx(85.0)
                assert tracker.get_endpoint("gpu1").gpu_sm_util == pytest.approx(12.0)
                assert tracker.get_endpoint("gpu2").gpu_sm_util == pytest.approx(95.0)
            finally:
                importlib.reload(gm)

    def test_tracker_gpu_history_recorded_on_wiring(self):
        mock_nvml = _make_mock_nvml(gpu_count=1, sm_util_pcts=[77.0], mem_util_pcts=[55.0])

        with mock.patch.dict(sys.modules, {"pynvml": mock_nvml}):
            import importlib

            import general_ludd.infra.gpu_metrics as gm
            importlib.reload(gm)
            try:
                tracker = UtilizationTracker(max_history=5)
                tracker.register_endpoint("gpu0", "http://gpu0:8000", gpu_type="A100")
                m = gm.GPUMetricsCollector.collect_gpu_metrics(0)
                tracker.update_gpu_metrics("gpu0", m.as_dict())

                hist = tracker._gpu_history.get("gpu0")
                assert hist is not None
                assert len(hist) == 1
                _ts, sm = hist[0]
                assert sm == pytest.approx(77.0)
            finally:
                importlib.reload(gm)

    def test_wiring_is_idempotent_multiple_updates(self):
        tracker = UtilizationTracker()
        tracker.register_endpoint("gpu0", "http://gpu0:8000", gpu_type="A100")
        tracker.update_gpu_metrics("gpu0", {"gpu_sm_util_pct": 50.0})
        tracker.update_gpu_metrics("gpu0", {"gpu_sm_util_pct": 75.0})

        ep = tracker.get_endpoint("gpu0")
        assert ep is not None
        assert ep.gpu_sm_util == pytest.approx(75.0)


class TestComputeEndpointGPUFieldsPopulated:
    def test_endpoint_gpu_fields_present_and_mutable(self):
        ep = ComputeEndpoint(
            endpoint_id="gpu0",
            url="http://gpu0:8000",
            gpu_type="A100_80",
            gpu_count=1,
            gpu_sm_util=45.0,
            gpu_mem_util=33.0,
            gpu_temp_c=62.0,
        )
        assert ep.gpu_sm_util == 45.0
        assert ep.gpu_mem_util == 33.0
        assert ep.gpu_temp_c == 62.0

    def test_endpoint_defaults_to_zero_gpu_fields(self):
        ep = ComputeEndpoint(endpoint_id="cpu", url="http://cpu:8000")
        assert ep.gpu_sm_util == 0.0
        assert ep.gpu_mem_util == 0.0
        assert ep.gpu_temp_c == 0.0
        assert ep.gpu_type == ""

    def test_gpu_fields_survive_utilization_report(self):
        tracker = UtilizationTracker()
        tracker.register_endpoint(
            "gpu0", "http://gpu0:8000", gpu_type="A100",
            gpu_sm_util=82.0, gpu_mem_util=44.0, gpu_temp_c=68.0,
        )
        report = tracker.get_utilization_report()
        ep_report = report["endpoints"][0]
        assert ep_report["endpoint_id"] == "gpu0"
        assert ep_report["model"] == ""
        assert ep_report["active"] is True

        ep = tracker.get_endpoint("gpu0")
        assert ep is not None
        assert ep.gpu_sm_util == 82.0
        assert ep.gpu_mem_util == 44.0
        assert ep.gpu_temp_c == 68.0

    def test_unregistered_endpoint_update_gpu_metrics_noop(self):
        tracker = UtilizationTracker()
        tracker.update_gpu_metrics("nonexistent", {"gpu_sm_util_pct": 99.0})
        assert tracker.get_endpoint("nonexistent") is None
