"""Validate public model-catalog facts used for immutable deployment plans."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

_REVISION_RE = re.compile(r"^[0-9a-f]{40}$")
_MODEL_ID_RE = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9._-]{0,95}/[A-Za-z0-9][A-Za-z0-9._-]{0,127}$"
)
_CONTEXT_FIELDS = (
    "max_position_embeddings",
    "model_max_length",
    "max_seq_len",
    "seq_length",
)


@dataclass(frozen=True, slots=True)
class ModelDeploymentMetadata:
    """Immutable public Hub facts required before model deployment planning."""

    model_id: str
    revision: str
    parameter_count: int
    context_tokens: int
    storage_bytes: int
    weight_bits: int
    license_id: str
    tags: tuple[str, ...]
    pipeline_tag: str
    library_name: str
    downloads: int

    def __post_init__(self) -> None:
        """Reject mutable, ambiguous, or effectively unbounded Hub metadata."""
        if _MODEL_ID_RE.fullmatch(self.model_id) is None:
            raise ValueError("model_id must be one canonical owner/repository pair")
        if _REVISION_RE.fullmatch(self.revision) is None:
            raise ValueError("model deployment requires an immutable revision")
        for field_name, value, maximum in (
            ("parameter_count", self.parameter_count, 1_000_000_000_000),
            ("context_tokens", self.context_tokens, 10_000_000),
            ("storage_bytes", self.storage_bytes, 10_000_000_000_000),
            ("downloads", self.downloads, 10_000_000_000_000),
        ):
            if (
                isinstance(value, bool)
                or not isinstance(value, int)
                or value < (0 if field_name == "downloads" else 1)
                or value > maximum
            ):
                raise ValueError(f"{field_name} is outside its bounded range")
        if self.weight_bits not in {4, 8, 16}:
            raise ValueError("weight_bits must be 4, 8, or 16")
        if (
            not isinstance(self.license_id, str)
            or not self.license_id
            or self.license_id != self.license_id.casefold()
        ):
            raise ValueError("license_id must be one normalized identifier")
        if (
            type(self.tags) is not tuple
            or tuple(sorted(set(self.tags))) != self.tags
            or any(not isinstance(tag, str) or not tag for tag in self.tags)
        ):
            raise ValueError("tags must be one sorted unique tuple")


def model_license_id(info: Any, tags: tuple[str, ...]) -> str:
    """Resolve one normalized declared license from catalog metadata."""
    licenses = tuple(tag.split(":", 1)[1] for tag in tags if tag.startswith("license:"))
    if len(set(licenses)) == 1:
        return licenses[0].casefold()
    card_data = getattr(info, "card_data", None)
    raw_license = getattr(card_data, "license", None)
    if isinstance(card_data, Mapping):
        raw_license = card_data.get("license")
    if isinstance(raw_license, str) and raw_license.strip():
        return raw_license.strip().casefold()
    raise ValueError("model deployment requires one declared license")


def model_context_tokens(info: Any) -> int:
    """Return the largest supported positive context field in catalog metadata."""
    config = getattr(info, "config", None)
    if not isinstance(config, Mapping):
        raise ValueError("model deployment requires bounded context metadata")
    values = tuple(
        value
        for name in _CONTEXT_FIELDS
        if isinstance((value := config.get(name)), int)
        and not isinstance(value, bool)
        and value > 0
    )
    if not values:
        raise ValueError("model deployment requires bounded context metadata")
    return max(values)


def safetensors_shape(info: Any) -> tuple[int, int]:
    """Return exact parameter count and supported weight width."""
    safetensors = getattr(info, "safetensors", None)
    total = getattr(safetensors, "total", None)
    parameters = getattr(safetensors, "parameters", None)
    if (
        isinstance(total, bool)
        or not isinstance(total, int)
        or total <= 0
        or not isinstance(parameters, Mapping)
    ):
        raise ValueError("model deployment requires exact safetensors metadata")
    dtypes = {str(dtype).upper() for dtype, count in parameters.items() if count}
    if dtypes and dtypes <= {"F16", "BF16"}:
        return total, 16
    if dtypes and dtypes <= {"I8", "U8"}:
        return total, 8
    raise ValueError("model deployment requires a supported safetensors dtype")


__all__ = (
    "ModelDeploymentMetadata",
    "model_context_tokens",
    "model_license_id",
    "safetensors_shape",
)
