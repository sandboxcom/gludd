"""Provider-neutral accelerator discovery for local and Slurm runtimes.

Hardware names are observed data, never configuration keys.  Local discovery
reuses the existing OS survey, PyTorch's XPU runtime for Intel GPUs, and JAX's
device API for TPUs.  Slurm discovery consumes node records from the existing
authenticated :class:`~general_ludd.infra.slurm.SlurmAdapter`.
"""

from __future__ import annotations

import enum
import importlib
import math
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol, cast, runtime_checkable

from general_ludd.hardware.survey import GpuInfo, HardwareSurvey

_MAX_ACCELERATORS = 100_000
_GIB = 1024**3
_GRES_RE = re.compile(
    r"^(?P<kind>gpu|tpu):(?:(?P<model>[A-Za-z0-9_.+-]+):)?"
    r"(?P<count>[0-9]+)(?:\([^\r\n]*\))?$",
    re.IGNORECASE,
)
_MEMORY_SUFFIX_RE = re.compile(r"(?P<size>[0-9]+(?:\.[0-9]+)?)gb(?:$|[_+.-])", re.IGNORECASE)
_UNAVAILABLE_STATES = frozenset(
    {
        "DOWN",
        "DRAIN",
        "DRAINED",
        "FAIL",
        "FAILING",
        "FUTURE",
        "INVAL",
        "MAINT",
        "NO_RESPOND",
        "POWER_DOWN",
        "POWERING_DOWN",
        "UNKNOWN",
    }
)
_KNOWN_STATES = frozenset({"ALLOCATED", "COMPLETING", "IDLE", "MIXED", "PLANNED"})


class AcceleratorKind(enum.StrEnum):
    """The compute primitive exposed by a runtime or scheduler."""

    GPU = "gpu"
    TPU = "tpu"


class AcceleratorLocation(enum.StrEnum):
    """Where a discovered resource is scheduled."""

    LOCAL = "local"
    SLURM = "slurm"


class DiscoveryEvent(enum.StrEnum):
    """Content-free progress emitted by accelerator discovery."""

    DISCOVERY_STARTED = "discovery_started"
    SOURCE_COMPLETED = "source_completed"
    SOURCE_UNAVAILABLE = "source_unavailable"
    SOURCE_FAILED = "source_failed"
    DISCOVERY_COMPLETED = "discovery_completed"


@dataclass(frozen=True, slots=True)
class DiscoveryTrace:
    """A bounded discovery event that carries counts, never host payloads."""

    event: DiscoveryEvent
    source: str
    discovered_count: int = 0

    def __post_init__(self) -> None:
        """Validate the bounded discovery trace."""
        if not isinstance(self.event, DiscoveryEvent):
            raise ValueError("event must be a DiscoveryEvent")
        _require_text(self.source, "source")
        if not isinstance(self.discovered_count, int) or isinstance(self.discovered_count, bool):
            raise ValueError("discovered_count must be an integer")
        if not 0 <= self.discovered_count <= _MAX_ACCELERATORS:
            raise ValueError("discovered_count is outside its bounded range")


@dataclass(frozen=True, slots=True)
class AcceleratorResource:
    """A normalized local device or scheduler resource pool."""

    kind: AcceleratorKind
    location: AcceleratorLocation
    backend: str
    model: str
    vendor: str
    resource_key: str
    total_count: int
    available_count: int
    memory_gb: float | None
    source: str
    node: str | None = None
    partitions: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        """Validate one normalized accelerator resource."""
        if not isinstance(self.kind, AcceleratorKind):
            raise ValueError("kind must be an AcceleratorKind")
        if not isinstance(self.location, AcceleratorLocation):
            raise ValueError("location must be an AcceleratorLocation")
        for field_name in ("backend", "model", "vendor", "resource_key", "source"):
            _require_text(getattr(self, field_name), field_name)
        if not isinstance(self.total_count, int) or isinstance(self.total_count, bool):
            raise ValueError("total_count must be an integer")
        if not 1 <= self.total_count <= _MAX_ACCELERATORS:
            raise ValueError("total_count is outside its bounded range")
        if not isinstance(self.available_count, int) or isinstance(self.available_count, bool):
            raise ValueError("available_count must be an integer")
        if not 0 <= self.available_count <= self.total_count:
            raise ValueError("available_count must be between zero and total_count")
        if self.memory_gb is not None:
            if isinstance(self.memory_gb, bool) or not isinstance(self.memory_gb, int | float):
                raise ValueError("memory_gb must be a number or None")
            if not math.isfinite(float(self.memory_gb)) or not 0 < float(self.memory_gb) <= 1_000_000:
                raise ValueError("memory_gb is outside its bounded range")
        if self.node is not None:
            _require_text(self.node, "node")
        if not isinstance(self.partitions, tuple):
            raise ValueError("partitions must be a tuple")
        for partition in self.partitions:
            _require_text(partition, "partition")


@dataclass(frozen=True, slots=True)
class AcceleratorInventory:
    """Immutable normalized resources available to routing and sizing."""

    resources: tuple[AcceleratorResource, ...] = ()

    def __post_init__(self) -> None:
        """Validate inventory shape and resource identity uniqueness."""
        if not isinstance(self.resources, tuple):
            raise ValueError("resources must be a tuple")
        if len(self.resources) > _MAX_ACCELERATORS:
            raise ValueError("resource inventory exceeds its bounded size")
        if any(not isinstance(resource, AcceleratorResource) for resource in self.resources):
            raise ValueError("resources must contain AcceleratorResource values")
        keys = [resource.resource_key for resource in self.resources]
        if len(keys) != len(set(keys)):
            raise ValueError("resource_key values must be unique")

    @property
    def total_count(self) -> int:
        """Return the total accelerator count across all resource pools."""
        return sum(resource.total_count for resource in self.resources)

    @property
    def available_count(self) -> int:
        """Return the currently allocatable accelerator count."""
        return sum(resource.available_count for resource in self.resources)

    def to_dict(self) -> dict[str, object]:
        """Return an exact, JSON-compatible inventory schema."""
        return {
            "schema_version": 1,
            "total_count": self.total_count,
            "available_count": self.available_count,
            "resources": [
                {
                    "kind": resource.kind.value,
                    "location": resource.location.value,
                    "backend": resource.backend,
                    "model": resource.model,
                    "vendor": resource.vendor,
                    "resource_key": resource.resource_key,
                    "total_count": resource.total_count,
                    "available_count": resource.available_count,
                    "memory_gb": resource.memory_gb,
                    "source": resource.source,
                    "node": resource.node,
                    "partitions": list(resource.partitions),
                }
                for resource in self.resources
            ],
        }


@runtime_checkable
class SlurmNodeSource(Protocol):
    """Minimum existing Slurm adapter surface needed by discovery."""

    def list_nodes(self) -> list[dict[str, object]]:
        """Return read-only scheduler node records."""
        ...


@runtime_checkable
class _XpuRuntime(Protocol):
    def is_available(self) -> bool: ...

    def device_count(self) -> int: ...

    def get_device_properties(self, index: int) -> object: ...


@runtime_checkable
class _TorchRuntime(Protocol):
    xpu: _XpuRuntime


@runtime_checkable
class _JaxRuntime(Protocol):
    def devices(self) -> Sequence[object]: ...


def _require_text(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 512:
        raise ValueError(f"{field_name} must be bounded non-empty text")
    if "\x00" in value or "\n" in value or "\r" in value:
        raise ValueError(f"{field_name} must not contain control delimiters")
    return value


def _memory_gb(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float) or value <= 0:
        return None
    gib = float(value) / _GIB
    return round(gib, 2) if math.isfinite(gib) and gib > 0 else None


def _safe_runtime_text(value: object, fallback: str) -> str:
    if not isinstance(value, str):
        return fallback
    cleaned = value.strip()
    if not cleaned or len(cleaned) > 512 or any(char in cleaned for char in "\x00\r\n"):
        return fallback
    return cleaned


def _gpu_source(backend: str) -> str:
    return {
        "metal": "system_profiler",
        "nvidia": "nvidia-smi",
        "rocm": "rocm-smi",
        "xpu": "torch.xpu",
    }.get(backend.casefold(), "hardware-survey")


def _gpu_vendor(gpu: GpuInfo) -> str:
    if gpu.vendor.strip():
        return _safe_runtime_text(gpu.vendor, "unspecified")
    return {
        "metal": "apple",
        "nvidia": "nvidia",
        "rocm": "amd",
        "xpu": "intel",
    }.get(gpu.backend.casefold(), "unspecified")


def _local_gpu(gpu: GpuInfo) -> AcceleratorResource:
    backend = _safe_runtime_text(gpu.backend, "unknown").casefold()
    return AcceleratorResource(
        kind=AcceleratorKind.GPU,
        location=AcceleratorLocation.LOCAL,
        backend=backend,
        model=_safe_runtime_text(gpu.name, "unspecified"),
        vendor=_gpu_vendor(gpu),
        resource_key=f"local:{backend}:{gpu.index}",
        total_count=1,
        available_count=1,
        memory_gb=float(gpu.vram_gb) if gpu.vram_gb > 0 else None,
        source=_gpu_source(backend),
    )


def _runtime_attr(value: object, name: str, fallback: object) -> object:
    try:
        return getattr(value, name)
    except Exception:
        return fallback


def probe_intel_xpu_gpus(
    module_loader: Callable[[str], object] = importlib.import_module,
) -> tuple[GpuInfo, ...]:
    """Return Intel devices reported by PyTorch's supported XPU runtime."""
    torch = cast(_TorchRuntime, module_loader("torch"))
    xpu = torch.xpu
    if not bool(xpu.is_available()):
        return ()
    count = xpu.device_count()
    if not isinstance(count, int) or isinstance(count, bool) or not 0 <= count <= _MAX_ACCELERATORS:
        raise ValueError("torch.xpu returned an invalid device count")
    gpus: list[GpuInfo] = []
    for index in range(count):
        properties = xpu.get_device_properties(index)
        memory = _memory_gb(_runtime_attr(properties, "total_memory", None))
        if memory is None:
            continue
        gpus.append(
            GpuInfo(
                name=_safe_runtime_text(_runtime_attr(properties, "name", None), "Intel XPU"),
                vram_gb=memory,
                index=index,
                backend="xpu",
                vendor=_safe_runtime_text(_runtime_attr(properties, "vendor", None), "intel"),
            )
        )
    return tuple(gpus)


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
                device_kind = _safe_runtime_text(_runtime_attr(device, "device_kind", None), "TPU")
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


def _gres_tokens(value: object) -> tuple[str, ...]:
    if isinstance(value, str):
        return tuple(token.strip() for token in value.split(",") if token.strip())
    if isinstance(value, Sequence) and not isinstance(value, bytes | bytearray | str):
        return tuple(token.strip() for token in value if isinstance(token, str) and token.strip())
    return ()


def _parse_gres(value: object) -> dict[tuple[AcceleratorKind, str], int]:
    parsed: dict[tuple[AcceleratorKind, str], int] = {}
    for token in _gres_tokens(value):
        match = _GRES_RE.fullmatch(token)
        if match is None:
            continue
        count = int(match.group("count"))
        if not 1 <= count <= _MAX_ACCELERATORS:
            continue
        kind = AcceleratorKind(match.group("kind").casefold())
        model = (match.group("model") or "unspecified").casefold()
        key = (kind, model)
        parsed[key] = min(_MAX_ACCELERATORS, parsed.get(key, 0) + count)
    return parsed


def _states(value: object) -> frozenset[str]:
    if isinstance(value, str):
        values = [value]
    elif isinstance(value, Sequence) and not isinstance(value, bytes | bytearray | str):
        values = [item for item in value if isinstance(item, str)]
    else:
        return frozenset({"UNKNOWN"})
    tokens = {
        token
        for item in values
        for token in re.findall(r"[A-Z_]+", item.upper())
    }
    return frozenset(tokens or {"UNKNOWN"})


def _partitions(value: object) -> tuple[str, ...]:
    if isinstance(value, str):
        candidates = value.split(",")
    elif isinstance(value, Sequence) and not isinstance(value, bytes | bytearray | str):
        candidates = [item for item in value if isinstance(item, str)]
    else:
        candidates = []
    return tuple(sorted({_safe_runtime_text(item, "unspecified") for item in candidates if item.strip()}))


def _model_memory_gb(model: str) -> float | None:
    match = _MEMORY_SUFFIX_RE.search(model)
    if match is None:
        return None
    value = float(match.group("size"))
    return value if value > 0 and math.isfinite(value) else None


def parse_slurm_nodes(nodes: Sequence[Mapping[str, object]]) -> tuple[AcceleratorResource, ...]:
    """Normalize scheduler-declared GPU/TPU GRES without inferring SKU facts."""
    if not isinstance(nodes, Sequence) or isinstance(nodes, bytes | bytearray | str):
        raise ValueError("nodes must be a sequence of mappings")
    resources: list[AcceleratorResource] = []
    for raw_node in nodes:
        if not isinstance(raw_node, Mapping):
            continue
        node = _safe_runtime_text(
            raw_node.get("name") or raw_node.get("hostname") or raw_node.get("node_name"),
            "unknown-node",
        )
        configured = _parse_gres(raw_node.get("gres"))
        used_value = raw_node.get("gres_used", raw_node.get("gres_used_detail"))
        used = _parse_gres(used_value)
        state = _states(raw_node.get("state"))
        unhealthy = bool(state & _UNAVAILABLE_STATES) or not bool(state & _KNOWN_STATES)
        usage_known = used_value is not None
        partitions = _partitions(raw_node.get("partitions", raw_node.get("partition")))
        for (kind, model), total_count in configured.items():
            used_count = used.get((kind, model), 0)
            if unhealthy or ("IDLE" not in state and not usage_known):
                available_count = 0
            else:
                available_count = max(0, total_count - used_count)
            resources.append(
                AcceleratorResource(
                    kind=kind,
                    location=AcceleratorLocation.SLURM,
                    backend="slurm-gres",
                    model=model,
                    vendor="unspecified",
                    resource_key=f"slurm:{node}:{kind.value}:{model}",
                    total_count=total_count,
                    available_count=available_count,
                    memory_gb=_model_memory_gb(model),
                    source="slurm-gres",
                    node=node,
                    partitions=partitions,
                )
            )
    return tuple(sorted(resources, key=lambda item: item.resource_key))


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
