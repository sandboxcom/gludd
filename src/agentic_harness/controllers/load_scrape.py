"""System load scraping and pressure classification."""

from __future__ import annotations

import enum
from dataclasses import dataclass

import psutil

from agentic_harness.schemas.todo import ResourceProfile


class PressureLevel(enum.StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    SEVERE = "severe"


@dataclass(frozen=True)
class LoadSnapshot:
    loadavg_1m: float
    loadavg_5m: float
    loadavg_10m: float
    logical_cpu_count: int
    cpu_percent: float
    memory_available_percent: float
    disk_free_percent: float
    active_jobs: int


def _count_active_jobs() -> int:
    count = 0
    for proc in psutil.process_iter(["name"]):
        try:
            name = (proc.info.get("name") or "").lower()
            if "ansible" in name or "gunicorn" in name or "uvicorn" in name:
                count += 1
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    return count


def scrape_system_load() -> LoadSnapshot:
    loadavg_1m, loadavg_5m, loadavg_10m = psutil.getloadavg()
    cpu_count = psutil.cpu_count(logical=True) or 1
    cpu_pct = psutil.cpu_percent(interval=0.1)
    mem = psutil.virtual_memory()
    disk = psutil.disk_usage("/")
    return LoadSnapshot(
        loadavg_1m=loadavg_1m,
        loadavg_5m=loadavg_5m,
        loadavg_10m=loadavg_10m,
        logical_cpu_count=cpu_count,
        cpu_percent=cpu_pct,
        memory_available_percent=100.0 - mem.percent,
        disk_free_percent=100.0 - disk.percent,
        active_jobs=_count_active_jobs(),
    )


def classify_pressure(snapshot: LoadSnapshot) -> dict[ResourceProfile, PressureLevel]:
    ratio = snapshot.loadavg_10m / max(snapshot.logical_cpu_count, 1)

    local = _pressure_from_ratio(ratio, (0.5, 0.8, 1.0))
    hybrid = _pressure_from_ratio(ratio, (0.5, 0.8, 1.2))
    low_res = _pressure_from_ratio(ratio, (1.0, 1.5, 2.0))

    if snapshot.cpu_percent > 95 and snapshot.memory_available_percent < 5:
        ai = PressureLevel.SEVERE
    elif snapshot.cpu_percent > 90 and snapshot.memory_available_percent < 10:
        ai = PressureLevel.HIGH
    elif snapshot.cpu_percent > 80:
        ai = PressureLevel.MEDIUM
    else:
        ai = PressureLevel.LOW

    if snapshot.disk_free_percent < 5:
        net = PressureLevel.SEVERE
    elif snapshot.disk_free_percent < 10:
        net = PressureLevel.HIGH
    elif snapshot.disk_free_percent < 20:
        net = PressureLevel.MEDIUM
    else:
        net = PressureLevel.LOW

    return {
        ResourceProfile.LOCAL_HEAVY: local,
        ResourceProfile.AI_HEAVY: ai,
        ResourceProfile.HYBRID: hybrid,
        ResourceProfile.NETWORK_HEAVY: net,
        ResourceProfile.LOW_RESOURCE: low_res,
    }


def _pressure_from_ratio(ratio: float, thresholds: tuple[float, float, float]) -> PressureLevel:
    low, med, high = thresholds
    if ratio < low:
        return PressureLevel.LOW
    if ratio < med:
        return PressureLevel.MEDIUM
    if ratio < high:
        return PressureLevel.HIGH
    return PressureLevel.SEVERE
