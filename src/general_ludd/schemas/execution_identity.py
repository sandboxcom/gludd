"""Shared source, runtime, and model identity schema contracts."""

from __future__ import annotations

import math
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, field_validator

BoundedIdentifier = Annotated[
    str,
    Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$"),
]
BoundedText = Annotated[str, Field(min_length=1, max_length=256)]
Sha256Digest = Annotated[str, Field(pattern=r"^sha256:[0-9a-f]{64}$")]
GitObjectId = Annotated[str, Field(pattern=r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")]


class _StrictIdentityModel(BaseModel):
    model_config = ConfigDict(
        allow_inf_nan=False,
        extra="forbid",
        frozen=True,
        serialize_by_alias=True,
        strict=True,
    )


def validate_json_value(value: object, *, path: str = "$") -> None:
    """Reject non-JSON or non-finite values in persisted identity metadata."""
    if value is None or isinstance(value, (str, bool, int)):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"replay JSON number at {path} must be finite")
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            validate_json_value(item, path=f"{path}[{index}]")
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError(f"replay JSON object key at {path} must be text")
            validate_json_value(item, path=f"{path}.{key}")
        return
    raise TypeError(
        f"replay JSON value at {path} has unsupported type {type(value).__name__}"
    )


class SourceIdentityV1(_StrictIdentityModel):
    """Credential-free source identity captured at run start."""

    repository_url_sha256: Sha256Digest
    commit_sha: GitObjectId
    tree_sha: GitObjectId
    branch: BoundedText | None
    dirty: bool


class RuntimeIdentityV1(_StrictIdentityModel):
    """Execution runtime fields needed to compare replay environments."""

    gludd_version: BoundedText
    python_version: BoundedText
    os: BoundedText
    architecture: BoundedText
    config_sha256: Sha256Digest
    feature_flags: dict[BoundedIdentifier, bool]


class ModelIdentityV1(_StrictIdentityModel):
    """Safe model identity and provider-visible request parameters."""

    provider: BoundedIdentifier
    profile: BoundedIdentifier
    model: BoundedIdentifier
    request_parameters: dict[str, object]
    provider_revision: BoundedText | None

    @field_validator("request_parameters")
    @classmethod
    def _request_parameters_are_json(
        cls,
        value: dict[str, object],
    ) -> dict[str, object]:
        validate_json_value(value, path="$.model.request_parameters")
        return value


__all__ = [
    "BoundedIdentifier",
    "BoundedText",
    "GitObjectId",
    "ModelIdentityV1",
    "RuntimeIdentityV1",
    "Sha256Digest",
    "SourceIdentityV1",
    "validate_json_value",
]
