"""Conservative model-to-GPU sizing for Azure Container Apps.

The planner selects the smallest supported serverless GPU whose usable VRAM
fits an immutable model requirement. It refuses silent promotion when the
right-sized profile is unavailable, so regional scarcity cannot turn a small
T4 workload into an accidental A100 bill.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Set
from dataclasses import dataclass

_MIB = 1024 * 1024
_MODEL_ID_MAX_CHARS = 200
_MAX_PARAMETER_COUNT = 1_000_000_000_000
_MAX_MEMORY_MIB = 1_000_000


class AzureContainerAppGPUUnavailable(ValueError):
    """Raised when no exact, sufficiently sized serverless GPU is available."""


@dataclass(frozen=True, slots=True)
class AzureContainerAppGPUProfile:
    """One documented Azure Container Apps serverless GPU shape."""

    name: str
    workload_profile_name: str
    workload_profile_type: str
    gpu_vram_mib: int
    usable_vram_mib: int
    cpu_cores: int
    memory_gib: int

    def __post_init__(self) -> None:
        """Validate provider inventory facts without enumerating accelerator names."""
        identifier_fields: tuple[tuple[str, object], ...] = (
            ("name", self.name),
            ("workload_profile_name", self.workload_profile_name),
            ("workload_profile_type", self.workload_profile_type),
        )
        for field_name, value in identifier_fields:
            if (
                not isinstance(value, str)
                or not value
                or len(value) > 200
                or any(character.isspace() or ord(character) < 32 for character in value)
            ):
                raise ValueError(f"{field_name} must be one bounded identifier")
        capacity_fields: tuple[tuple[str, object], ...] = (
            ("gpu_vram_mib", self.gpu_vram_mib),
            ("usable_vram_mib", self.usable_vram_mib),
            ("cpu_cores", self.cpu_cores),
            ("memory_gib", self.memory_gib),
        )
        for field_name, value in capacity_fields:
            if (
                isinstance(value, bool)
                or not isinstance(value, int)
                or not 0 < value <= _MAX_MEMORY_MIB
            ):
                raise ValueError(f"{field_name} must be a positive bounded integer")
        if self.usable_vram_mib > self.gpu_vram_mib:
            raise ValueError("usable_vram_mib cannot exceed gpu_vram_mib")


T4_PROFILE = AzureContainerAppGPUProfile(
    name="T4",
    workload_profile_name="gpu-t4",
    workload_profile_type="Consumption-GPU-NC8as-T4",
    gpu_vram_mib=16 * 1024,
    usable_vram_mib=14_745,
    cpu_cores=8,
    memory_gib=56,
)
A100_PROFILE = AzureContainerAppGPUProfile(
    name="A100",
    workload_profile_name="gpu-a100",
    workload_profile_type="Consumption-GPU-NC24-A100",
    gpu_vram_mib=80 * 1024,
    usable_vram_mib=73_728,
    cpu_cores=24,
    memory_gib=220,
)


@dataclass(frozen=True, slots=True)
class ModelServingRequirement:
    """Immutable model facts used for conservative VRAM estimation."""

    model_id: str
    revision: str
    parameter_count: int
    weight_bits: int
    kv_cache_mib: int
    runtime_overhead_mib: int

    def __post_init__(self) -> None:
        """Validate immutable model provenance and bounded memory inputs."""
        if (
            not isinstance(self.model_id, str)
            or not self.model_id
            or len(self.model_id) > _MODEL_ID_MAX_CHARS
            or any(character.isspace() or ord(character) < 32 for character in self.model_id)
        ):
            raise ValueError("model_id must be a bounded non-whitespace identifier")
        if not isinstance(self.revision, str) or re.fullmatch(
            r"[0-9a-f]{40}", self.revision
        ) is None:
            raise ValueError("revision must be a full lowercase 40-character commit")
        if (
            isinstance(self.parameter_count, bool)
            or not isinstance(self.parameter_count, int)
            or not 0 < self.parameter_count <= _MAX_PARAMETER_COUNT
        ):
            raise ValueError("parameter_count must be a positive bounded integer")
        if self.weight_bits not in {4, 8, 16}:
            raise ValueError("weight_bits must be one of 4, 8, or 16")
        self._validate_memory("kv_cache_mib", self.kv_cache_mib)
        self._validate_memory("runtime_overhead_mib", self.runtime_overhead_mib)

    @staticmethod
    def _validate_memory(name: str, value: int) -> None:
        if (
            isinstance(value, bool)
            or not isinstance(value, int)
            or not 0 <= value <= _MAX_MEMORY_MIB
        ):
            raise ValueError(f"{name} must be a non-negative bounded integer")

    @property
    def required_vram_mib(self) -> int:
        """Return weights plus a 25% load margin, KV cache, and runtime memory."""
        weight_bytes = (self.parameter_count * self.weight_bits + 7) // 8
        weight_mib = (weight_bytes + _MIB - 1) // _MIB
        weight_with_margin_mib = (weight_mib * 5 + 3) // 4
        return weight_with_margin_mib + self.kv_cache_mib + self.runtime_overhead_mib


@dataclass(frozen=True, slots=True)
class GPUProfileSelection:
    """Right-sized profile and deterministic memory evidence."""

    profile: AzureContainerAppGPUProfile
    required_vram_mib: int
    headroom_mib: int


@dataclass(frozen=True, slots=True)
class SizingTrace:
    """Content-free sizing transition safe for events and logs."""

    phase: str
    required_vram_mib: int
    profile_name: str | None = None
    reason: str | None = None


def _emit(sink: Callable[[SizingTrace], None], trace: SizingTrace) -> None:
    try:
        sink(trace)
    except Exception:
        raise RuntimeError("sizing trace publication failed") from None


def _discard_trace(_trace: SizingTrace) -> None:
    return None


def select_smallest_sufficient_profile(
    requirement: ModelServingRequirement,
    *,
    available_profile_types: Set[str] | None = None,
    hardware_profiles: tuple[AzureContainerAppGPUProfile, ...] | None = None,
    trace_sink: Callable[[SizingTrace], None] | None = None,
) -> GPUProfileSelection:
    """Select the exact smallest sufficient profile or refuse before spend."""
    sink = _discard_trace if trace_sink is None else trace_sink
    if not callable(sink):
        raise ValueError("trace_sink must be callable")
    required = requirement.required_vram_mib
    _emit(sink, SizingTrace("sizing_started", required))

    if hardware_profiles is not None:
        if available_profile_types is not None:
            raise ValueError(
                "hardware_profiles and available_profile_types are mutually exclusive"
            )
        if (
            type(hardware_profiles) is not tuple
            or not hardware_profiles
            or not all(
                isinstance(profile, AzureContainerAppGPUProfile)
                for profile in hardware_profiles
            )
            or len({profile.workload_profile_type for profile in hardware_profiles})
            != len(hardware_profiles)
        ):
            raise ValueError("hardware_profiles must be one unique non-empty tuple")
        fitting = tuple(
            profile
            for profile in hardware_profiles
            if required <= profile.usable_vram_mib
        )
        if not fitting:
            _emit(
                sink,
                SizingTrace("sizing_refused", required, reason="model_too_large"),
            )
            raise AzureContainerAppGPUUnavailable(
                "estimated model memory exceeds available accelerator memory"
            )
        selected = min(
            fitting,
            key=lambda profile: (
                profile.usable_vram_mib,
                profile.gpu_vram_mib,
                profile.workload_profile_type,
            ),
        )
    elif required <= T4_PROFILE.usable_vram_mib:
        selected = T4_PROFILE
    elif required <= A100_PROFILE.usable_vram_mib:
        selected = A100_PROFILE
    else:
        _emit(sink, SizingTrace("sizing_refused", required, reason="model_too_large"))
        raise AzureContainerAppGPUUnavailable(
            "estimated model memory exceeds A100 usable VRAM"
        )

    if (
        available_profile_types is not None
        and selected.workload_profile_type not in available_profile_types
    ):
        _emit(
            sink,
            SizingTrace(
                "sizing_refused",
                required,
                profile_name=selected.name,
                reason="profile_unavailable",
            ),
        )
        if selected == T4_PROFILE:
            raise AzureContainerAppGPUUnavailable(
                "right-sized T4 profile is unavailable; refusing A100 oversizing"
            )
        raise AzureContainerAppGPUUnavailable("required A100 profile is unavailable")

    selection = GPUProfileSelection(
        profile=selected,
        required_vram_mib=required,
        headroom_mib=selected.usable_vram_mib - required,
    )
    _emit(
        sink,
        SizingTrace(
            "sizing_selected",
            required,
            profile_name=selected.name,
        ),
    )
    return selection


__all__ = [
    "A100_PROFILE",
    "T4_PROFILE",
    "AzureContainerAppGPUProfile",
    "AzureContainerAppGPUUnavailable",
    "GPUProfileSelection",
    "ModelServingRequirement",
    "SizingTrace",
    "select_smallest_sufficient_profile",
]
