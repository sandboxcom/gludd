"""Provider-neutral accelerator discovery for local and Slurm runtimes.

Hardware names are observed data, never configuration keys.  Local discovery
reuses the existing OS survey, PyTorch's XPU runtime for Intel GPUs, and JAX's
device API for TPUs.  Slurm discovery consumes node records from the existing
authenticated :class:`~general_ludd.infra.slurm.SlurmAdapter`.
"""

from __future__ import annotations

import importlib
from collections.abc import Callable, Mapping, Sequence
from typing import Protocol, cast, runtime_checkable

from general_ludd.hardware.accelerator_slurm import parse_slurm_nodes
from general_ludd.hardware.accelerator_types import (
    AcceleratorInventory,
    AcceleratorKind,
    AcceleratorLocation,
    AcceleratorResource,
    DiscoveryEvent,
    DiscoveryTrace,
    safe_runtime_text,
)
from general_ludd.hardware.survey import (
    GpuInfo,
    HardwareSurvey,
    _memory_gb,
    _runtime_attr,
    probe_intel_xpu_gpus,
)


@runtime_checkable
class SlurmNodeSource(Protocol):
    """Minimum existing Slurm adapter surface needed by discovery."""

    def list_nodes(self) -> list[dict[str, object]]:
        """Return read-only scheduler node records."""
        ...


@runtime_checkable
class _JaxRuntime(Protocol):
    def devices(self) -> Sequence[object]: ...


def _gpu_source(backend: str) -> str:
    return {
        "metal": "system_profiler",
        "nvidia": "nvidia-smi",
        "rocm": "rocm-smi",
        "xpu": "torch.xpu",
    }.get(backend.casefold(), "hardware-survey")


def _gpu_vendor(gpu: GpuInfo) -> str:
    if gpu.vendor.strip():
        return safe_runtime_text(gpu.vendor, "unspecified")
    return {
        "metal": "apple",
        "nvidia": "nvidia",
        "rocm": "amd",
        "xpu": "intel",
    }.get(gpu.backend.casefold(), "unspecified")


def _local_gpu(gpu: GpuInfo) -> AcceleratorResource:
    backend = safe_runtime_text(gpu.backend, "unknown").casefold()
    return AcceleratorResource(
        kind=AcceleratorKind.GPU,
        location=AcceleratorLocation.LOCAL,
        backend=backend,
        model=safe_runtime_text(gpu.name, "unspecified"),
        vendor=_gpu_vendor(gpu),
        resource_key=f"local:{backend}:{gpu.index}",
        total_count=1,
        available_count=1,
        memory_gb=float(gpu.vram_gb) if gpu.vram_gb > 0 else None,
        source=_gpu_source(backend),
    )


class HardwareDiscovery:
    """Discover accelerator facts without provisioning or model-specific rules."""

    def __init__(
        self,
        *,
        survey: HardwareSurvey | None = None,
        surveyed_gpus: Sequence[GpuInfo] | None = None,
        slurm: SlurmNodeSource | None = None,
        module_loader: Callable[[str], object] = importlib.import_module,
        trace_sink: Callable[[DiscoveryTrace], None] | None = None,
    ) -> None:
        """Configure runtime probes, optional Slurm input, and trace delivery."""
        if not callable(module_loader):
            raise ValueError("module_loader must be callable")
        if trace_sink is not None and not callable(trace_sink):
            raise ValueError("trace_sink must be callable")
        if surveyed_gpus is not None and (
            not isinstance(surveyed_gpus, Sequence)
            or isinstance(surveyed_gpus, bytes | bytearray | str)
            or any(not isinstance(gpu, GpuInfo) for gpu in surveyed_gpus)
        ):
            raise ValueError("surveyed_gpus must be a sequence of GpuInfo values")
        self._survey = survey if survey is not None else HardwareSurvey()
        self._surveyed_gpus = tuple(surveyed_gpus) if surveyed_gpus is not None else None
        self._slurm = slurm
        self._module_loader = module_loader
        self._trace_sink = trace_sink or (lambda _trace: None)

    def _emit(self, event: DiscoveryEvent, source: str, count: int = 0) -> None:
        self._trace_sink(DiscoveryTrace(event, source, count))

    def _intel_xpu(self) -> tuple[AcceleratorResource, ...]:
        source = "torch.xpu"
        try:
            gpus = probe_intel_xpu_gpus(self._module_loader)
        except Exception:
            self._emit(DiscoveryEvent.SOURCE_UNAVAILABLE, source)
            return ()
        if not gpus:
            self._emit(DiscoveryEvent.SOURCE_UNAVAILABLE, source)
            return ()
        resources = tuple(_local_gpu(gpu) for gpu in gpus)
        self._emit(DiscoveryEvent.SOURCE_COMPLETED, source, len(resources))
        return resources

    def _jax_tpu(self) -> tuple[AcceleratorResource, ...]:
        source = "jax.devices"
        try:
            jax = cast(_JaxRuntime, self._module_loader("jax"))
            devices = jax.devices()
            resources: list[AcceleratorResource] = []
            for device in devices:
                platform = str(_runtime_attr(device, "platform", "")).casefold()
                device_kind = safe_runtime_text(_runtime_attr(device, "device_kind", None), "TPU")
                if platform != "tpu" and "tpu" not in device_kind.casefold():
                    continue
                memory_gb: float | None = None
                memory_stats = _runtime_attr(device, "memory_stats", None)
                if callable(memory_stats):
                    stats = memory_stats()
                    if isinstance(stats, Mapping):
                        memory_gb = _memory_gb(stats.get("bytes_limit"))
                process_index = _runtime_attr(device, "process_index", 0)
                device_id = _runtime_attr(device, "id", len(resources))
                resources.append(
                    AcceleratorResource(
                        kind=AcceleratorKind.TPU,
                        location=AcceleratorLocation.LOCAL,
                        backend="jax-tpu",
                        model=device_kind,
                        vendor="google",
                        resource_key=f"local:jax-tpu:{process_index}:{device_id}",
                        total_count=1,
                        available_count=1,
                        memory_gb=memory_gb,
                        source=source,
                    )
                )
        except Exception:
            self._emit(DiscoveryEvent.SOURCE_UNAVAILABLE, source)
            return ()
        self._emit(DiscoveryEvent.SOURCE_COMPLETED, source, len(resources))
        return tuple(resources)

    def discover_local(self) -> AcceleratorInventory:
        """Return Apple/NVIDIA/AMD/Intel GPU and JAX TPU runtime facts."""
        self._emit(DiscoveryEvent.DISCOVERY_STARTED, "local")
        gpu_snapshot = (
            self._surveyed_gpus
            if self._surveyed_gpus is not None
            else tuple(self._survey.probe_gpus())
        )
        surveyed = tuple(_local_gpu(gpu) for gpu in gpu_snapshot)
        self._emit(DiscoveryEvent.SOURCE_COMPLETED, "hardware-survey", len(surveyed))
        intel = () if any(gpu.backend.casefold() == "xpu" for gpu in gpu_snapshot) else self._intel_xpu()
        combined = (*surveyed, *intel, *self._jax_tpu())
        unique = {resource.resource_key: resource for resource in combined}
        resources = tuple(unique[key] for key in sorted(unique))
        self._emit(DiscoveryEvent.DISCOVERY_COMPLETED, "local", len(resources))
        return AcceleratorInventory(resources)

    def discover_slurm(self) -> AcceleratorInventory:
        """Return normalized GRES capacity from the configured Slurm adapter."""
        self._emit(DiscoveryEvent.DISCOVERY_STARTED, "slurm")
        if self._slurm is None:
            self._emit(DiscoveryEvent.SOURCE_UNAVAILABLE, "slurm")
            self._emit(DiscoveryEvent.DISCOVERY_COMPLETED, "slurm")
            return AcceleratorInventory()
        try:
            resources = parse_slurm_nodes(self._slurm.list_nodes())
        except Exception:
            self._emit(DiscoveryEvent.SOURCE_FAILED, "slurm")
            raise
        self._emit(DiscoveryEvent.SOURCE_COMPLETED, "slurm", len(resources))
        self._emit(DiscoveryEvent.DISCOVERY_COMPLETED, "slurm", len(resources))
        return AcceleratorInventory(resources)

    def discover(self) -> AcceleratorInventory:
        """Merge local and configured Slurm resources into one inventory."""
        local = self.discover_local().resources
        slurm = self.discover_slurm().resources if self._slurm is not None else ()
        return AcceleratorInventory((*local, *slurm))


__all__ = [
    "AcceleratorInventory",
    "AcceleratorKind",
    "AcceleratorLocation",
    "AcceleratorResource",
    "DiscoveryEvent",
    "DiscoveryTrace",
    "HardwareDiscovery",
    "SlurmNodeSource",
    "parse_slurm_nodes",
    "probe_intel_xpu_gpus",
]
