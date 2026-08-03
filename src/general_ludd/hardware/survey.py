"""Runtime hardware survey — GPU count/VRAM, system RAM, disk, CPU.

Discovers local GPU resources (NVIDIA via nvidia-smi, Apple Metal via
system_profiler, AMD ROCm via rocm-smi) and aggregates system RAM, disk
free, and CPU cores into a :class:`HardwareInventory`.

Used by :mod:`general_ludd.infra.deployment_optimizer` for model-to-hardware
matching via :func:`HardwareSurvey.survey`.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
from dataclasses import asdict, dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

_MIN_GPU_VRAM_GB = 0.25


@dataclass(frozen=True)
class GpuInfo:
    """A single GPU detected on the machine."""

    name: str
    vram_gb: float
    index: int = 0
    backend: str = ""  # "nvidia", "metal", "rocm", "unknown"


@dataclass(frozen=True)
class HardwareInventory:
    """Aggregate hardware resources usable for model-to-hardware matching.

    ``gpus`` carries per-GPU VRAM and backend. ``total_ram_gb`` and
    ``disk_free_gb`` gate total model fit, while ``cpu_cores`` informs
    thread-pool sizing and CPU-offload capacity.
    """

    gpus: list[GpuInfo] = field(default_factory=list)
    total_ram_gb: float = 0.0
    disk_free_gb: float = 0.0
    cpu_cores: int = 0

    @property
    def gpu_count(self) -> int:
        return len(self.gpus)

    @property
    def total_vram_gb(self) -> float:
        return round(sum(g.vram_gb for g in self.gpus), 2)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------------------
# HardwareSurvey
# ---------------------------------------------------------------------------


class HardwareSurvey:
    """Probe local hardware for GPU RAM, system RAM, disk, and CPU.

    Usage::

        survey = HardwareSurvey()
        inventory: HardwareInventory = survey.survey()
    """

    def probe_gpu_nvidia(self) -> list[GpuInfo]:
        """Query NVIDIA GPUs via ``nvidia-smi``."""
        try:
            result = subprocess.run(
                [
                    "nvidia-smi",
                    "--query-gpu=name,memory.total",
                    "--format=csv,noheader,nounits",
                ],
                capture_output=True,
                text=True,
                timeout=15,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return []
        if result.returncode != 0:
            return []

        gpus: list[GpuInfo] = []
        for idx, line in enumerate(result.stdout.strip().splitlines()):
            if not line.strip():
                continue
            parts = [p.strip() for p in line.split(",")]
            if len(parts) >= 2:
                try:
                    vram_mb = float(parts[1].strip())
                except ValueError:
                    continue
                vram_gb = vram_mb / 1024.0
                if vram_gb >= _MIN_GPU_VRAM_GB:
                    gpus.append(
                        GpuInfo(
                            name=parts[0].strip(),
                            vram_gb=round(vram_gb, 2),
                            index=idx,
                            backend="nvidia",
                        )
                    )
        return gpus

    def probe_gpu_metal(self) -> list[GpuInfo]:
        """Query Apple Metal GPUs via ``system_profiler``."""
        try:
            result = subprocess.run(
                ["system_profiler", "SPDisplaysDataType"],
                capture_output=True,
                text=True,
                timeout=15,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return []
        if result.returncode != 0:
            return []

        gpus: list[GpuInfo] = []
        current_name: str | None = None
        current_vram: float | None = None

        for line in result.stdout.splitlines():
            stripped = line.strip()
            if stripped.startswith("Chipset Model:"):
                if current_name and current_vram is not None and current_vram >= _MIN_GPU_VRAM_GB:
                    gpus.append(
                        GpuInfo(
                            name=current_name,
                            vram_gb=round(current_vram, 2),
                            index=len(gpus),
                            backend="metal",
                        )
                    )
                current_name = stripped.split(":", 1)[1].strip()
                current_vram = None
            elif stripped.startswith("Vendor:"):
                if not current_name:
                    current_name = stripped.split(":", 1)[1].strip()
            elif stripped.startswith("VRAM (Total):") or stripped.startswith("VRAM (Dynamic, Max):"):
                vram_str = stripped.split(":", 1)[1].strip()
                vram_str = vram_str.replace("GB", "").replace("MB", "").strip()
                try:
                    raw = float(vram_str)
                except ValueError:
                    continue
                current_vram = raw / 1024.0 if "MB" in stripped.split(":", 1)[1] else raw
            elif stripped.startswith("Metal Support:") and current_name and current_vram is None:
                if "Unified" in current_name or "M" in current_name:
                    ram = self._probe_ram_bytes()
                    current_vram = ram / (1024**3) * 0.67

        if current_name and current_vram is not None and current_vram >= _MIN_GPU_VRAM_GB:
            gpus.append(
                GpuInfo(
                    name=current_name,
                    vram_gb=round(current_vram, 2),
                    index=len(gpus),
                    backend="metal",
                )
            )

        return gpus

    def probe_gpu_rocm(self) -> list[GpuInfo]:
        """Query AMD GPUs via ``rocm-smi``."""
        try:
            result = subprocess.run(
                ["rocm-smi", "--showmeminfo", "vram"],
                capture_output=True,
                text=True,
                timeout=15,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return []
        if result.returncode != 0:
            return []

        gpus: list[GpuInfo] = []
        for idx, line in enumerate(result.stdout.strip().splitlines()):
            name = f"AMD GPU {idx}"
            if "VRAM" in line:
                parts = line.split()
                for i, p in enumerate(parts):
                    if "VRAM" in p and i + 1 < len(parts):
                        try:
                            vram_mb = float(parts[i + 1])
                        except ValueError:
                            continue
                        vram_gb = vram_mb / 1024.0
                        if vram_gb >= _MIN_GPU_VRAM_GB:
                            gpus.append(
                                GpuInfo(
                                    name=name,
                                    vram_gb=round(vram_gb, 2),
                                    index=idx,
                                    backend="rocm",
                                )
                            )
                        break
        return gpus

    def probe_gpus(self) -> list[GpuInfo]:
        """Discover all local GPUs by trying each backend."""
        gpus: list[GpuInfo] = []

        nvidia_gpus = self.probe_gpu_nvidia()
        if nvidia_gpus:
            return nvidia_gpus
        gpus.extend(nvidia_gpus)

        metal_gpus = self.probe_gpu_metal()
        if metal_gpus:
            return metal_gpus
        gpus.extend(metal_gpus)

        rocm_gpus = self.probe_gpu_rocm()
        gpus.extend(rocm_gpus)

        return gpus

    def probe_ram(self) -> float:
        """Total system RAM in GB."""
        return round(self._probe_ram_bytes() / (1024**3), 2)

    def _probe_ram_bytes(self) -> int:
        try:
            import psutil

            return psutil.virtual_memory().total
        except Exception:
            pass
        try:
            result = subprocess.run(
                ["sysctl", "-n", "hw.memsize"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0:
                return int(result.stdout.strip())
        except Exception:
            pass
        return 0

    def probe_disk(self) -> float:
        """Free disk space in GB on the filesystem containing the CWD."""
        try:
            usage = shutil.disk_usage(os.getcwd())
            return round(usage.free / (1024**3), 2)
        except Exception:
            return 0.0

    def probe_cpu(self) -> int:
        """Logical CPU core count."""
        return os.cpu_count() or 1

    def survey(self) -> HardwareInventory:
        """Run all probes and return a :class:`HardwareInventory`."""
        gpus = self.probe_gpus()
        return HardwareInventory(
            gpus=gpus,
            total_ram_gb=self.probe_ram(),
            disk_free_gb=self.probe_disk(),
            cpu_cores=self.probe_cpu(),
        )
