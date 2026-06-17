"""LocalProvider — zero new dep, fully offline. The v1 must-work path.

Reuses ``controllers.load_scrape.scrape_system_load`` (mockable) for load/CPU/mem
and ``psutil`` for physical core count + total RAM. GPU detection is OPTIONAL:
``pynvml`` is lazy-imported and its absence/failure degrades to ``gpu_count=0``
(NOT provider_unavailable) — local ALWAYS returns OK with at least a CPU
resource. ``cost_per_hour`` is 0.0 (owned hardware). ``endpoint_url`` is taken
from ``GLUDD_LOCAL_INFERENCE_URL`` only when it passes the SSRF guard.
"""

from __future__ import annotations

import contextlib
import logging
import os

from general_ludd.infra.compute import GPUType
from general_ludd.infra.discovery.base import (
    DiscoveredResource,
    DiscoveryProvider,
    DiscoveryResult,
    DiscoverySource,
    DiscoveryStatus,
)

logger = logging.getLogger(__name__)

# Coarse GPU name -> GPUType mapping for pynvml-reported device names.
_GPU_NAME_HINTS: tuple[tuple[str, GPUType], ...] = (
    ("h200", GPUType.H200),
    ("h100", GPUType.H100),
    ("a100", GPUType.A100_80),
    ("l40s", GPUType.L40S),
    ("a40", GPUType.A40),
    ("a10g", GPUType.A10G),
    ("a10", GPUType.A10),
    ("l4", GPUType.L4),
    ("t4", GPUType.T4),
    ("4090", GPUType.RTX_4090),
    ("6000 ada", GPUType.RTX_6000_ADA),
)


def _map_gpu_name(name: str) -> GPUType | None:
    low = name.lower()
    for needle, gpu in _GPU_NAME_HINTS:
        if needle in low:
            return gpu
    return None


def _detect_gpus() -> tuple[int, GPUType | None]:
    """Best-effort local GPU detection via pynvml. Never raises."""
    try:
        import pynvml
    except Exception:
        return 0, None
    try:
        pynvml.nvmlInit()
        try:
            count = int(pynvml.nvmlDeviceGetCount())
            gpu_type: GPUType | None = None
            if count > 0:
                handle = pynvml.nvmlDeviceGetHandleByIndex(0)
                raw = pynvml.nvmlDeviceGetName(handle)
                name = raw.decode() if isinstance(raw, bytes) else str(raw)
                gpu_type = _map_gpu_name(name)
            return count, gpu_type
        finally:
            with contextlib.suppress(Exception):
                pynvml.nvmlShutdown()
    except Exception:
        return 0, None


class LocalProvider(DiscoveryProvider):
    source = DiscoverySource.LOCAL
    required_aliases = ()

    async def discover(self) -> DiscoveryResult:
        import psutil  # core dep

        from general_ludd.controllers.load_scrape import (
            PressureLevel,
            classify_pressure,
            scrape_system_load,
        )
        from general_ludd.schemas.todo import ResourceProfile

        try:
            snapshot = scrape_system_load()
        except Exception as exc:
            return self._error(exc)

        physical = psutil.cpu_count(logical=False) or snapshot.logical_cpu_count or 1
        try:
            mem_gb = psutil.virtual_memory().total / 1e9
        except Exception:
            mem_gb = 0.0

        gpu_count, gpu_type = _detect_gpus()

        pressure = classify_pressure(snapshot)
        local_pressure = pressure.get(ResourceProfile.LOCAL_HEAVY, PressureLevel.LOW)
        load_ratio = snapshot.loadavg_1m / max(snapshot.logical_cpu_count, 1)
        available = local_pressure != PressureLevel.SEVERE and load_ratio < 1.0

        endpoint_url = os.environ.get("GLUDD_LOCAL_INFERENCE_URL") or None
        if endpoint_url and not self.guard_endpoint(endpoint_url):
            logger.warning("GLUDD_LOCAL_INFERENCE_URL failed SSRF guard; dropping")
            endpoint_url = None

        resource = DiscoveredResource(
            source=self.source,
            id="localhost",
            region=None,
            gpu_type=gpu_type,
            gpu_count=gpu_count,
            vcpu=int(physical),
            mem_gb=float(mem_gb),
            cost_per_hour=0.0,  # owned hardware
            available=available,
            endpoint_url=endpoint_url,
            labels={
                "pressure": str(local_pressure),
                "load_ratio": f"{load_ratio:.2f}",
                "cpu_percent": f"{snapshot.cpu_percent:.1f}",
            },
        )
        return DiscoveryResult(
            source=self.source,
            status=DiscoveryStatus.OK,
            resources=(resource,),
            meta={"physical_cores": int(physical), "gpu_count": gpu_count},
        )
