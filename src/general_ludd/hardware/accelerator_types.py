"""Validated provider-neutral accelerator inventory and discovery event types."""

from __future__ import annotations

import enum
import math
from dataclasses import dataclass

_MAX_ACCELERATORS = 100_000


def _require_text(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 512:
        raise ValueError(f"{field_name} must be bounded non-empty text")
    if "\x00" in value or "\n" in value or "\r" in value:
        raise ValueError(f"{field_name} must not contain control delimiters")
    return value


def safe_runtime_text(value: object, fallback: str) -> str:
    """Return bounded runtime text or a caller-owned non-sensitive fallback."""
    if not isinstance(value, str):
        return fallback
    cleaned = value.strip()
    if not cleaned or len(cleaned) > 512 or any(char in cleaned for char in "\x00\r\n"):
        return fallback
    return cleaned


class AcceleratorKind(enum.StrEnum):
    """The compute primitive exposed by a runtime or scheduler."""

    GPU = "gpu"
    TPU = "tpu"


class AcceleratorLocation(enum.StrEnum):
    """Where a discovered resource is scheduled."""

    LOCAL = "local"
    SLURM = "slurm"
    CLOUD = "cloud"


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


__all__ = (
    "AcceleratorInventory",
    "AcceleratorKind",
    "AcceleratorLocation",
    "AcceleratorResource",
    "DiscoveryEvent",
    "DiscoveryTrace",
    "safe_runtime_text",
)
