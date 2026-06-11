"""Hardware resource detection for adaptive configuration.

Centralises CPU, memory, and capability detection so that gunicorn worker
count, thread-pool sizes, network concurrency, and local-model gatekeeping
all derive from a single, testable source of truth.
"""

from __future__ import annotations

import os
from dataclasses import asdict, dataclass

_MIN_MEMORY_GB_FOR_LOCAL_MODEL = 4.0


def _total_memory_gb() -> float:
    try:
        import psutil
        return psutil.virtual_memory().total / (1024 ** 3)
    except Exception:
        return 0.0


@dataclass(frozen=True)
class HardwareProfile:
    cpu_count: int
    total_memory_gb: float
    recommended_workers: int
    gunicorn_workers: int
    thread_pool_size: int
    network_concurrency: int
    local_model_allowed: bool

    def to_dict(self) -> dict[str, int | float | bool]:
        return asdict(self)


def probe_hardware() -> HardwareProfile:
    cpu = os.cpu_count() or 1
    mem = _total_memory_gb()
    workers = max(1, cpu // 4)
    thread_pool = max(1, min(cpu * 2, workers * 4))
    net_concurrency = max(1, min(cpu * 4, workers * 8))
    local_ok = mem >= _MIN_MEMORY_GB_FOR_LOCAL_MODEL
    return HardwareProfile(
        cpu_count=cpu,
        total_memory_gb=round(mem, 2),
        recommended_workers=workers,
        gunicorn_workers=workers,
        thread_pool_size=thread_pool,
        network_concurrency=net_concurrency,
        local_model_allowed=local_ok,
    )
