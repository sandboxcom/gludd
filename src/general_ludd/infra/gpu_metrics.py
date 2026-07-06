"""NVML-based GPU metrics collection with graceful degradation."""

from __future__ import annotations

import contextlib
import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)

_NVML_AVAILABLE = False
_nvml = None


def _try_import_nvml() -> None:
    global _NVML_AVAILABLE, _nvml
    try:
        import importlib

        nvml = importlib.import_module("pynvml")  # pynvml: optional, NVIDIA-only, guarded by try/except

        nvml.nvmlInit()
        _nvml = nvml
        _NVML_AVAILABLE = True
    except Exception:
        _NVML_AVAILABLE = False
        _nvml = None


_try_import_nvml()


@dataclass
class GPUMetrics:
    gpu_sm_util_pct: float = 0.0
    gpu_mem_util_pct: float = 0.0
    gpu_temp_c: float = 0.0
    power_draw_w: float = 0.0
    memory_used_mb: float = 0.0
    memory_total_mb: float = 0.0

    def as_dict(self) -> dict[str, float]:
        return {
            "gpu_sm_util_pct": self.gpu_sm_util_pct,
            "gpu_mem_util_pct": self.gpu_mem_util_pct,
            "gpu_temp_c": self.gpu_temp_c,
            "power_draw_w": self.power_draw_w,
            "memory_used_mb": self.memory_used_mb,
            "memory_total_mb": self.memory_total_mb,
        }


class GPUMetricsCollector:
    @staticmethod
    def is_available() -> bool:
        return _NVML_AVAILABLE

    @staticmethod
    def collect_gpu_metrics(device_index: int = 0) -> GPUMetrics:
        if not _NVML_AVAILABLE or _nvml is None:
            return GPUMetrics()

        try:
            handle = _nvml.nvmlDeviceGetHandleByIndex(device_index)

            util_info = _nvml.nvmlDeviceGetUtilizationRates(handle)
            sm_util = float(util_info.gpu)
            mem_util = float(util_info.memory)

            temp_c = 0.0
            try:
                temp_info = _nvml.nvmlDeviceGetTemperature(handle, _nvml.NVML_TEMPERATURE_GPU)
                temp_c = float(temp_info)
            except Exception:
                pass

            power_w = 0.0
            try:
                power_info = _nvml.nvmlDeviceGetPowerUsage(handle)
                power_w = float(power_info) / 1000.0
            except Exception:
                pass

            mem_info = _nvml.nvmlDeviceGetMemoryInfo(handle)
            mem_used = float(mem_info.used) / (1024 * 1024)
            mem_total = float(mem_info.total) / (1024 * 1024)

            return GPUMetrics(
                gpu_sm_util_pct=sm_util,
                gpu_mem_util_pct=mem_util,
                gpu_temp_c=temp_c,
                power_draw_w=power_w,
                memory_used_mb=mem_used,
                memory_total_mb=mem_total,
            )
        except Exception:
            logger.warning("Failed to collect GPU metrics for device %d", device_index, exc_info=True)
            return GPUMetrics()

    @staticmethod
    def collect_all_gpu_metrics() -> list[GPUMetrics]:
        if not _NVML_AVAILABLE or _nvml is None:
            return []

        try:
            count = _nvml.nvmlDeviceGetCount()
            return [GPUMetricsCollector.collect_gpu_metrics(i) for i in range(count)]
        except Exception:
            logger.warning("Failed to enumerate GPU devices", exc_info=True)
            return []

    @staticmethod
    def shutdown() -> None:
        if _NVML_AVAILABLE and _nvml is not None:
            with contextlib.suppress(Exception):
                _nvml.nvmlShutdown()
