"""Immutable contracts shared by model-runner launch adapters."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

from general_ludd.hardware.accelerator_topology import DistributionMode

_MAX_TEXT = 1_024
_MAX_ITEMS = 256
_MAX_COUNT = 1_000_000
_ENVIRONMENT_NAME = re.compile(r"^[A-Z_][A-Z0-9_]*$")
_OPTION_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_.-]*$")


def _require_text(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value or len(value) > _MAX_TEXT:
        raise ValueError(f"{field_name} must be bounded non-empty text")
    if any(delimiter in value for delimiter in "\x00\r\n"):
        raise ValueError(f"{field_name} must not contain control delimiters")
    return value


def _bounded_int(value: object, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{field_name} must be an integer")
    if not 1 <= value <= _MAX_COUNT:
        raise ValueError(f"{field_name} is outside its bounded range")
    return value


def _text_tuple(value: object, field_name: str, *, require_item: bool) -> tuple[str, ...]:
    if not isinstance(value, tuple) or (require_item and not value):
        raise ValueError(f"{field_name} must be a tuple with the required items")
    if len(value) > _MAX_ITEMS:
        raise ValueError(f"{field_name} exceeds its bounded item count")
    for item in value:
        _require_text(item, field_name)
    return value


class LaunchValue(StrEnum):
    """Selection values that an attested runner profile may bind."""

    MODEL_ID = "model_id"
    ARCHITECTURE = "architecture"
    QUANTIZATION = "quantization"
    CONTEXT_TOKENS = "context_tokens"
    OUTPUT_TOKENS = "output_tokens"
    BATCH_SIZE = "batch_size"
    MODEL_PARALLEL_DEVICES = "model_parallel_devices"
    TENSOR_PARALLEL_SIZE = "tensor_parallel_size"
    PIPELINE_PARALLEL_SIZE = "pipeline_parallel_size"
    DEVICE_KIND = "device_kind"


class LaunchTarget(StrEnum):
    """Safe output channel for one runner launch value."""

    POSITIONAL_ARGUMENT = "positional_argument"
    ARGUMENT = "argument"
    ENVIRONMENT = "environment"
    REQUEST_OPTION = "request_option"


@dataclass(frozen=True, slots=True)
class RunnerLaunchBinding:
    """Declaratively map one selected value to a runner interface."""

    source: LaunchValue
    target: LaunchTarget
    name: str | None = None

    def __post_init__(self) -> None:
        """Reject unknown values, channels, and ambiguous names."""
        if not isinstance(self.source, LaunchValue):
            raise ValueError("source must be a LaunchValue")
        if not isinstance(self.target, LaunchTarget):
            raise ValueError("target must be a LaunchTarget")
        if self.target is LaunchTarget.POSITIONAL_ARGUMENT:
            if self.name is not None:
                raise ValueError("positional arguments cannot declare a name")
            return
        if self.name is None:
            raise ValueError("named launch targets require a name")
        name = _require_text(self.name, "name")
        if self.target is LaunchTarget.ARGUMENT and not name.startswith("-"):
            raise ValueError("argument names must start with a dash")
        if self.target is LaunchTarget.ENVIRONMENT and not _ENVIRONMENT_NAME.fullmatch(
            name
        ):
            raise ValueError("environment names must be portable uppercase names")
        if self.target is LaunchTarget.REQUEST_OPTION and not _OPTION_NAME.fullmatch(
            name
        ):
            raise ValueError("request option names must be portable identifiers")


@dataclass(frozen=True, slots=True)
class RunnerLaunchProfile:
    """Versioned runner interface attested by a concrete adapter."""

    runner_id: str
    adapter_id: str
    source_revision: str
    executable: tuple[str, ...]
    fixed_arguments: tuple[str, ...]
    bindings: tuple[RunnerLaunchBinding, ...]
    distribution_mode: DistributionMode
    facts_attested: bool

    def __post_init__(self) -> None:
        """Require immutable, unique, bounded launch metadata."""
        for field_name in ("runner_id", "adapter_id", "source_revision"):
            _require_text(getattr(self, field_name), field_name)
        _text_tuple(self.executable, "executable", require_item=True)
        _text_tuple(self.fixed_arguments, "fixed_arguments", require_item=False)
        if not isinstance(self.bindings, tuple) or any(
            not isinstance(binding, RunnerLaunchBinding) for binding in self.bindings
        ):
            raise ValueError("bindings must be a RunnerLaunchBinding tuple")
        if len(self.bindings) > _MAX_ITEMS:
            raise ValueError("bindings exceed their bounded item count")
        if not isinstance(self.distribution_mode, DistributionMode):
            raise ValueError("distribution_mode must be a DistributionMode")
        if not isinstance(self.facts_attested, bool):
            raise ValueError("facts_attested must be a bool")
        seen: set[tuple[LaunchTarget, str | None]] = set()
        for binding in self.bindings:
            key = (binding.target, binding.name)
            if key in seen:
                raise ValueError("launch binding output names must be unique")
            seen.add(key)


@dataclass(frozen=True, slots=True)
class RunnerLaunchPlan:
    """Exact runner process and request desired state for one service plan."""

    runner_id: str
    adapter_id: str
    source_revision: str
    variant_id: str
    model_id: str
    quantization: str
    distribution_mode: DistributionMode
    command: tuple[str, ...]
    environment: tuple[tuple[str, str], ...]
    request_options: tuple[tuple[str, str | int], ...]
    replica_count: int
    devices_per_replica: int

    def __post_init__(self) -> None:
        """Protect the execution boundary from mutable or duplicate output."""
        for field_name in (
            "runner_id",
            "adapter_id",
            "source_revision",
            "variant_id",
            "model_id",
            "quantization",
        ):
            _require_text(getattr(self, field_name), field_name)
        if not isinstance(self.distribution_mode, DistributionMode):
            raise ValueError("distribution_mode must be a DistributionMode")
        _text_tuple(self.command, "command", require_item=True)
        self._validate_named_values(self.environment, "environment", strings_only=True)
        self._validate_named_values(
            self.request_options,
            "request_options",
            strings_only=False,
        )
        _bounded_int(self.replica_count, "replica_count")
        _bounded_int(self.devices_per_replica, "devices_per_replica")

    @staticmethod
    def _validate_named_values(
        values: object,
        field_name: str,
        *,
        strings_only: bool,
    ) -> None:
        if not isinstance(values, tuple) or len(values) > _MAX_ITEMS:
            raise ValueError(f"{field_name} must be a bounded tuple")
        names: set[str] = set()
        for item in values:
            if not isinstance(item, tuple) or len(item) != 2:
                raise ValueError(f"{field_name} items must be name/value tuples")
            name, value = item
            _require_text(name, field_name)
            if name in names:
                raise ValueError(f"{field_name} names must be unique")
            names.add(name)
            if strings_only or isinstance(value, str):
                _require_text(value, field_name)
            else:
                _bounded_int(value, field_name)

    def to_dict(self) -> dict[str, object]:
        """Return safe desired state without credentials or shell expansion."""
        return {
            "schema_version": 1,
            "runner_id": self.runner_id,
            "adapter_id": self.adapter_id,
            "source_revision": self.source_revision,
            "variant_id": self.variant_id,
            "model_id": self.model_id,
            "quantization": self.quantization,
            "distribution_mode": self.distribution_mode.value,
            "command": list(self.command),
            "environment": dict(self.environment),
            "request_options": dict(self.request_options),
            "replica_count": self.replica_count,
            "devices_per_replica": self.devices_per_replica,
        }


class RunnerLaunchError(ValueError):
    """Stable refusal to render an unsafe or incompatible launch."""

    def __init__(self, reason_code: str) -> None:
        """Retain one bounded reason without command or model content."""
        self.reason_code = _require_text(reason_code, "reason_code")
        super().__init__(f"runner launch rendering refused: {self.reason_code}")


__all__ = (
    "LaunchTarget",
    "LaunchValue",
    "RunnerLaunchBinding",
    "RunnerLaunchError",
    "RunnerLaunchPlan",
    "RunnerLaunchProfile",
)
