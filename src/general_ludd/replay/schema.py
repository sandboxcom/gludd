"""Strict, versioned schemas for replay manifests and event envelopes."""

from __future__ import annotations

import json
import math
import re
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Annotated, Literal, NoReturn, TypeAlias, cast

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, field_validator, model_validator
from pydantic.functional_validators import AfterValidator

from general_ludd.integrity.store import canonical_json as _canonical_json

BUNDLE_SCHEMA_V1 = "gludd.run-bundle/v1"
EVENT_SCHEMA_V1 = "gludd.run-event/v1"

_RUN_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
_WINDOWS_RESERVED_STEMS = {
    "AUX",
    "CON",
    "NUL",
    "PRN",
    *(f"COM{number}" for number in range(1, 10)),
    *(f"LPT{number}" for number in range(1, 10)),
}


class ReplaySchemaError(ValueError):
    """Base error for replay wire-format parsing failures."""


class UnsupportedReplaySchemaError(ReplaySchemaError):
    """Raised when a reader encounters a schema major it cannot interpret."""


def _validate_run_id(value: str) -> str:
    if not value.isascii() or _RUN_ID_RE.fullmatch(value) is None:
        raise ValueError(
            "run_id must be 1-128 ASCII characters matching "
            "[A-Za-z0-9][A-Za-z0-9._-]*"
        )
    if ".." in value or value.endswith("."):
        raise ValueError("run_id must not contain traversal tokens or a trailing dot")
    if value.split(".", maxsplit=1)[0].upper() in _WINDOWS_RESERVED_STEMS:
        raise ValueError("run_id must not use a reserved device name")
    return value


SafeRunId = Annotated[str, AfterValidator(_validate_run_id)]
_RUN_ID_ADAPTER = TypeAdapter(SafeRunId, config=ConfigDict(strict=True))

BoundedIdentifier = Annotated[
    str,
    Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$"),
]
BoundedText = Annotated[str, Field(min_length=1, max_length=256)]
Sha256Digest = Annotated[str, Field(pattern=r"^sha256:[0-9a-f]{64}$")]
GitObjectId = Annotated[str, Field(pattern=r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")]

ReplayOperation: TypeAlias = Literal["record", "simulate", "reexecute"]
ReplayStatus: TypeAlias = Literal[
    "running",
    "completed",
    "failed",
    "cancelled",
    "incomplete",
]
ReplayEventType: TypeAlias = Literal[
    "run.started",
    "run.completed",
    "run.failed",
    "run.cancelled",
    "prompt.rendered",
    "model.requested",
    "model.responded",
    "model.failed",
    "tool.requested",
    "tool.responded",
    "tool.failed",
    "workspace.snapshot",
    "workspace.diff",
    "gate.started",
    "gate.completed",
    "review.decided",
    "policy.decided",
    "budget.decided",
    "reconcile.decided",
    "decision.outcome",
    "recording.degraded",
]


class _StrictReplayModel(BaseModel):
    model_config = ConfigDict(
        allow_inf_nan=False,
        extra="forbid",
        frozen=True,
        serialize_by_alias=True,
        strict=True,
    )


def validate_run_id(value: str) -> str:
    """Return a path-safe run identifier or raise ``ValidationError``."""
    return _RUN_ID_ADAPTER.validate_python(value, strict=True)


def validate_replay_json_value(value: object, *, path: str = "$") -> None:
    """Reject non-JSON or non-finite values before hashing or persistence."""
    if value is None or isinstance(value, (str, bool, int)):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"replay JSON number at {path} must be finite")
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            validate_replay_json_value(item, path=f"{path}[{index}]")
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError(f"replay JSON object key at {path} must be text")
            validate_replay_json_value(item, path=f"{path}.{key}")
        return
    raise TypeError(f"replay JSON value at {path} has unsupported type {type(value).__name__}")


def canonical_replay_json(value: object) -> str:
    """Serialize validated replay data with the repository canonical JSON helper."""
    payload = (
        value.model_dump(mode="json", by_alias=True)
        if isinstance(value, BaseModel)
        else value
    )
    validate_replay_json_value(payload)
    return _canonical_json(payload)


def _reject_non_finite_constant(token: str) -> NoReturn:
    raise ValueError(f"replay JSON number {token} must be finite")


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ReplaySchemaError(f"replay JSON contains duplicate key {key!r}")
        result[key] = value
    return result


def decode_replay_json_object(
    data: str | bytes | bytearray | Mapping[str, object],
) -> dict[str, object]:
    """Decode one strict JSON object, rejecting duplicates and non-finite values."""
    if isinstance(data, Mapping):
        decoded: object = dict(data)
    else:
        try:
            decoded = cast(
                object,
                json.loads(
                    data,
                    object_pairs_hook=_reject_duplicate_keys,
                    parse_constant=_reject_non_finite_constant,
                ),
            )
        except json.JSONDecodeError as exc:
            raise ReplaySchemaError(f"replay JSON is invalid: {exc.msg}") from exc
    validate_replay_json_value(decoded)
    if not isinstance(decoded, dict):
        raise ReplaySchemaError("replay JSON root must be an object")
    return cast(dict[str, object], decoded)


def _utc_timestamp(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("replay timestamps must be timezone-aware")
    return value.astimezone(UTC)


class SourceIdentityV1(_StrictReplayModel):
    """Credential-free source identity captured at run start."""

    repository_url_sha256: Sha256Digest
    commit_sha: GitObjectId
    tree_sha: GitObjectId
    branch: BoundedText | None
    dirty: bool


class RuntimeIdentityV1(_StrictReplayModel):
    """Execution runtime fields needed to compare replay environments."""

    gludd_version: BoundedText
    python_version: BoundedText
    os: BoundedText
    architecture: BoundedText
    config_sha256: Sha256Digest
    feature_flags: dict[BoundedIdentifier, bool]


class ModelIdentityV1(_StrictReplayModel):
    """Safe model identity and provider-visible request parameters."""

    provider: BoundedIdentifier
    profile: BoundedIdentifier
    model: BoundedIdentifier
    request_parameters: dict[str, object]
    provider_revision: BoundedText | None

    @field_validator("request_parameters")
    @classmethod
    def _request_parameters_are_json(cls, value: dict[str, object]) -> dict[str, object]:
        validate_replay_json_value(value, path="$.model.request_parameters")
        return value


class AttachmentV1(_StrictReplayModel):
    """Content-addressed attachment metadata without embedded content."""

    digest: Sha256Digest
    original_bytes: Annotated[int, Field(ge=0)]
    stored_bytes: Annotated[int, Field(ge=0)]
    media_type: BoundedText
    encoding: BoundedText | None
    redaction_count: Annotated[int, Field(ge=0)]
    truncated: bool

    @model_validator(mode="after")
    def _stored_size_is_bounded(self) -> AttachmentV1:
        if self.stored_bytes > self.original_bytes:
            raise ValueError("stored_bytes must not exceed original_bytes")
        return self


class MissingSequenceRangeV1(_StrictReplayModel):
    """Inclusive missing event sequence range."""

    start: Annotated[int, Field(ge=0)]
    end: Annotated[int, Field(ge=0)]

    @model_validator(mode="after")
    def _range_is_ordered(self) -> MissingSequenceRangeV1:
        if self.end < self.start:
            raise ValueError("missing sequence range end must not precede start")
        return self


class CompletenessV1(_StrictReplayModel):
    """Expected and observed lifecycle evidence for one run."""

    expected_stages: tuple[ReplayEventType, ...]
    observed_stages: tuple[ReplayEventType, ...]
    recorder_errors: tuple[BoundedText, ...]
    missing_ranges: tuple[MissingSequenceRangeV1, ...]


class RetentionV1(_StrictReplayModel):
    """Content-free retention and hold metadata."""

    expires_at: datetime | None
    pinned: bool
    hold_reason: BoundedText | None

    @field_validator("expires_at")
    @classmethod
    def _expiry_is_utc(cls, value: datetime | None) -> datetime | None:
        return None if value is None else _utc_timestamp(value)


class BundleManifestV1(_StrictReplayModel):
    """Strict manifest for one ``gludd.run-bundle/v1`` forensic bundle."""

    schema_version: Literal["gludd.run-bundle/v1"] = Field(alias="schema")
    run_id: SafeRunId
    parent_run_id: SafeRunId | None
    operation: ReplayOperation
    created_at: datetime
    finalized_at: datetime | None
    status: ReplayStatus
    project_id: BoundedIdentifier | None
    source: SourceIdentityV1
    runtime: RuntimeIdentityV1
    model: ModelIdentityV1
    event_count: Annotated[int, Field(ge=0)]
    events_sha256: Sha256Digest
    attachments: tuple[AttachmentV1, ...]
    completeness: CompletenessV1
    retention: RetentionV1
    integrity: Literal["signed", "unsigned"] = "unsigned"
    signing_key_id: BoundedIdentifier | None = None

    @field_validator("created_at", "finalized_at")
    @classmethod
    def _timestamps_are_utc(cls, value: datetime | None) -> datetime | None:
        return None if value is None else _utc_timestamp(value)

    @model_validator(mode="after")
    def _lifecycle_is_coherent(self) -> BundleManifestV1:
        if self.status in {"completed", "failed", "cancelled"} and self.finalized_at is None:
            raise ValueError("finalized_at is required for a terminal replay manifest")
        if self.finalized_at is not None and self.finalized_at < self.created_at:
            raise ValueError("finalized_at must not precede created_at")
        if self.operation == "record" and self.parent_run_id is not None:
            raise ValueError("record operations must not declare parent_run_id")
        if self.operation != "record" and self.parent_run_id is None:
            raise ValueError("simulate and reexecute operations require parent_run_id")
        digests = [attachment.digest for attachment in self.attachments]
        if len(digests) != len(set(digests)):
            raise ValueError("attachments must have unique content digests")
        if self.integrity == "signed" and self.signing_key_id is None:
            raise ValueError("signed manifests require signing_key_id")
        if self.integrity == "unsigned" and self.signing_key_id is not None:
            raise ValueError("unsigned manifests must not declare signing_key_id")
        return self


class CorrelationV1(_StrictReplayModel):
    """Optional stable IDs that join an event to task and trace evidence."""

    todo_id: BoundedIdentifier | None = None
    task_id: BoundedIdentifier | None = None
    trace_id: BoundedIdentifier | None = None


class RedactionV1(_StrictReplayModel):
    """Observable redaction count and bounded category set."""

    count: Annotated[int, Field(ge=0)]
    kinds: tuple[BoundedIdentifier, ...]

    @field_validator("kinds")
    @classmethod
    def _kinds_are_unique(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(value) != len(set(value)):
            raise ValueError("redaction kinds must be unique")
        return tuple(sorted(value))

    @model_validator(mode="after")
    def _count_matches_categories(self) -> RedactionV1:
        if (self.count == 0) != (not self.kinds):
            raise ValueError("redaction kinds must be present exactly when count is non-zero")
        return self


class EventEnvelopeV1(_StrictReplayModel):
    """Strict envelope for one ordered ``gludd.run-event/v1`` event."""

    schema_version: Literal["gludd.run-event/v1"] = Field(alias="schema")
    sequence: Annotated[int, Field(ge=0)]
    event_id: BoundedIdentifier
    occurred_at: datetime
    recorded_at: datetime
    type: ReplayEventType
    project_id: BoundedIdentifier | None
    correlation: CorrelationV1
    payload: dict[str, object]
    redaction: RedactionV1
    digest: Sha256Digest

    @field_validator("occurred_at", "recorded_at")
    @classmethod
    def _timestamps_are_utc(cls, value: datetime) -> datetime:
        return _utc_timestamp(value)

    @field_validator("payload")
    @classmethod
    def _payload_is_json(cls, value: dict[str, object]) -> dict[str, object]:
        validate_replay_json_value(value, path="$.payload")
        return value


def parse_bundle_manifest(
    data: str | bytes | bytearray | Mapping[str, object],
) -> BundleManifestV1:
    """Parse exactly v1 and reject unknown bundle schema majors."""
    payload = decode_replay_json_object(data)
    schema = payload.get("schema")
    if isinstance(schema, str) and schema != BUNDLE_SCHEMA_V1:
        raise UnsupportedReplaySchemaError(
            f"unsupported replay bundle schema {schema!r}; expected {BUNDLE_SCHEMA_V1}"
        )
    return BundleManifestV1.model_validate_json(canonical_replay_json(payload))


def parse_event_envelope(
    data: str | bytes | bytearray | Mapping[str, object],
) -> EventEnvelopeV1:
    """Parse exactly v1 and reject unknown event schema majors."""
    payload = decode_replay_json_object(data)
    schema = payload.get("schema")
    if isinstance(schema, str) and schema != EVENT_SCHEMA_V1:
        raise UnsupportedReplaySchemaError(
            f"unsupported replay event schema {schema!r}; expected {EVENT_SCHEMA_V1}"
        )
    return EventEnvelopeV1.model_validate_json(canonical_replay_json(payload))


__all__ = [
    "BUNDLE_SCHEMA_V1",
    "EVENT_SCHEMA_V1",
    "AttachmentV1",
    "BundleManifestV1",
    "CompletenessV1",
    "CorrelationV1",
    "EventEnvelopeV1",
    "MissingSequenceRangeV1",
    "ModelIdentityV1",
    "RedactionV1",
    "ReplaySchemaError",
    "RetentionV1",
    "RuntimeIdentityV1",
    "SafeRunId",
    "SourceIdentityV1",
    "UnsupportedReplaySchemaError",
    "canonical_replay_json",
    "decode_replay_json_object",
    "parse_bundle_manifest",
    "parse_event_envelope",
    "validate_replay_json_value",
    "validate_run_id",
]
