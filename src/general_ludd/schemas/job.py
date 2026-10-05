"""Job specification schema with a fail-closed ingress boundary.

D-09: Versioned JobSpec with tenant/project ownership, per-work-type ceilings,
and bounded denial audit records.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import ClassVar, Final, cast

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


@dataclass(frozen=True, slots=True)
class JobIngressLimits:
    """Pinned resource and shape limits for one worker generation.

    Operator values may narrow or moderately widen the defaults, but immutable
    hard ceilings prevent a configuration typo from disabling the boundary.
    ``from_environment`` is called once at module import so in-flight requests
    never observe a partially changed policy during a rolling replacement.
    """

    max_depth: int = 16
    max_collection_items: int = 10_000
    max_serialized_bytes: int = 1_048_576
    max_identifier_chars: int = 128
    max_playbook_chars: int = 255
    max_queue_chars: int = 128

    _ENV_FIELDS: ClassVar[tuple[tuple[str, str], ...]] = (
        ("max_depth", "GLUDD_JOB_INGRESS_MAX_DEPTH"),
        ("max_collection_items", "GLUDD_JOB_INGRESS_MAX_COLLECTION_ITEMS"),
        ("max_serialized_bytes", "GLUDD_JOB_INGRESS_MAX_SERIALIZED_BYTES"),
        ("max_identifier_chars", "GLUDD_JOB_INGRESS_MAX_IDENTIFIER_CHARS"),
        ("max_playbook_chars", "GLUDD_JOB_INGRESS_MAX_PLAYBOOK_CHARS"),
        ("max_queue_chars", "GLUDD_JOB_INGRESS_MAX_QUEUE_CHARS"),
    )
    _SAFE_BOUNDS: ClassVar[dict[str, tuple[int, int]]] = {
        "max_depth": (2, 64),
        "max_collection_items": (16, 100_000),
        "max_serialized_bytes": (256, 8_388_608),
        "max_identifier_chars": (16, 256),
        "max_playbook_chars": (16, 1_024),
        "max_queue_chars": (8, 256),
    }

    def __post_init__(self) -> None:
        """Reject any limit outside its pinned safe bounds at construction."""
        env_by_field = dict(self._ENV_FIELDS)
        for field_name, (minimum, maximum) in self._SAFE_BOUNDS.items():
            value = getattr(self, field_name)
            env_name = env_by_field[field_name]
            if type(value) is not int or not minimum <= value <= maximum:
                raise ValueError(f"{env_name} must be an integer from {minimum} through {maximum}")

    @classmethod
    def from_environment(
        cls,
        environ: Mapping[str, str] | None = None,
    ) -> JobIngressLimits:
        """Load validated limits from an environment snapshot.

        Invalid values abort startup instead of silently reverting to a more
        permissive default. Passing a mapping makes configuration validation
        deterministic without mutating process-global environment state.
        """
        source = os.environ if environ is None else environ
        values: dict[str, int] = {}
        for field_name, env_name in cls._ENV_FIELDS:
            raw = source.get(env_name)
            if raw is None:
                continue
            try:
                values[field_name] = int(raw)
            except (TypeError, ValueError):
                raise ValueError(f"{env_name} must be an integer") from None
        return cls(**values)


# This immutable snapshot is shared by every request handled by the worker.
# Applying new values therefore uses a rolling worker replacement, which keeps
# old and new policies internally consistent and permits immediate rollback.
JOB_INGRESS_LIMITS = JobIngressLimits.from_environment()

_JOB_ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]*\Z")
_QUEUE_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*\Z")
_PLAYBOOK_SEGMENT_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*\Z")
_SHA256_PATTERN = re.compile(r"[0-9a-f]{64}\Z")


def _validate_payload_bounds(payload: dict[str, object], limits: JobIngressLimits) -> None:
    """Reject excessive or non-JSON payloads before Pydantic field coercion."""
    collection_items = 0
    serialized_bytes = 0
    active_containers: set[int] = set()

    def add_serialized_bytes(size: int) -> None:
        nonlocal serialized_bytes
        serialized_bytes += size
        if serialized_bytes > limits.max_serialized_bytes:
            raise ValueError("job payload serialized bytes exceed configured limit")

    def add_scalar(value: object) -> None:
        # Character count is a zero-allocation lower bound for UTF-8 bytes. It
        # rejects a giant string before JSON escaping can allocate a second
        # giant copy; the remaining scalar serialization is therefore bounded.
        if isinstance(value, str) and len(value) > limits.max_serialized_bytes:
            raise ValueError("job payload serialized bytes exceed configured limit")
        try:
            encoded = json.dumps(
                value,
                allow_nan=False,
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
        except (TypeError, ValueError, OverflowError, RecursionError) as exc:
            raise ValueError("job payload must have a finite JSON serialization") from exc
        add_serialized_bytes(len(encoded))

    def visit(value: object, depth: int) -> None:
        nonlocal collection_items
        if depth > limits.max_depth:
            raise ValueError("job payload nesting depth exceeds configured limit")

        # Pydantic BaseModel instances are trusted internal types that have
        # already been validated by their own schema validators. They pass
        # through the ingress boundary without further recursive inspection.
        if isinstance(value, BaseModel):
            return

        value_type = type(value)
        # Pydantic models commonly pass ``StrEnum``/``IntEnum`` members between
        # trusted internal boundaries. They retain the exact JSON scalar
        # semantics of their base class and are normalized by field validation.
        if value is None or isinstance(value, (str, int, float, bool)):
            add_scalar(value)
            return

        if value_type is dict:
            mapping = cast(dict[object, object], value)
            identity = id(mapping)
            if identity in active_containers:
                raise ValueError("job payload cycle is forbidden")
            active_containers.add(identity)
            try:
                add_serialized_bytes(2)  # opening and closing braces
                collection_items += len(mapping)
                if collection_items > limits.max_collection_items:
                    raise ValueError("job payload collection items exceed configured limit")
                for index, (key, child) in enumerate(mapping.items()):
                    if index:
                        add_serialized_bytes(1)  # comma
                    if type(key) is not str:
                        raise ValueError("job payload mapping keys must be strings")
                    add_scalar(key)
                    add_serialized_bytes(1)  # colon
                    visit(child, depth + 1)
            finally:
                active_containers.remove(identity)
            return

        if value_type in (list, tuple):
            sequence = cast(list[object] | tuple[object, ...], value)
            identity = id(sequence)
            if identity in active_containers:
                raise ValueError("job payload cycle is forbidden")
            active_containers.add(identity)
            try:
                add_serialized_bytes(2)  # opening and closing brackets
                collection_items += len(sequence)
                if collection_items > limits.max_collection_items:
                    raise ValueError("job payload collection items exceed configured limit")
                for index, child in enumerate(sequence):
                    if index:
                        add_serialized_bytes(1)  # comma
                    visit(child, depth + 1)
            finally:
                active_containers.remove(identity)
            return

        raise ValueError("job payload values must be JSON-compatible built-in types")

    visit(payload, 0)


def _required_string(value: object, field_name: str) -> str:
    if type(value) is not str:
        raise ValueError(f"{field_name} must be a string")
    cleaned = value.strip()
    if not cleaned:
        raise ValueError(f"{field_name} must not be empty")
    return cleaned


class JobSpec(BaseModel):
    """A validated job specification with fail-closed ingress boundary checks."""

    model_config = ConfigDict(extra="forbid")

    job_id: str
    todo_id: str | None = None
    return_id: str | None = None
    project_id: str | None = None
    playbook: str
    queue: str
    work_type: str = "unknown"
    resource_profile: str = "low_resource"
    model_profile: str | None = None
    prompt_profile: str | None = None
    vars_namespace_refs: list[str] = Field(default_factory=list)
    artifact_dir: str | None = None
    budget_context: dict[str, object] = Field(default_factory=dict)
    candidate_todos: list[str] = Field(default_factory=list)
    artifact_summaries: list[str] = Field(default_factory=list)
    plan_artifact: str | None = None
    repository_binding_digest: str | None = None
    prompt_text: str | None = None
    skill_body: str | None = None
    ansible_roles_path: str | None = None
    templates_dir: str | None = None
    timeout: float | None = None
    human_input: str | None = None

    @model_validator(mode="before")
    @classmethod
    def _validate_ingress(cls, value: object) -> object:
        if type(value) is not dict:
            raise ValueError("job payload must be a plain mapping")
        payload = cast(dict[str, object], value)
        _validate_payload_bounds(payload, JOB_INGRESS_LIMITS)
        return value

    @field_validator("timeout", mode="before")
    @classmethod
    def _validate_timeout(cls, v: object) -> object:
        if v is None:
            return v
        if not isinstance(v, (int, float, str)):
            raise ValueError("timeout must be a number or None")
        try:
            fv = float(v)
        except (TypeError, ValueError):
            raise ValueError("timeout must be a number or None") from None
        if fv <= 0:
            raise ValueError(f"timeout must be positive (got {fv})")
        return fv

    @field_validator("job_id", mode="before")
    @classmethod
    def _validate_job_id(cls, value: object) -> str:
        cleaned = _required_string(value, "job_id")
        if len(cleaned) > JOB_INGRESS_LIMITS.max_identifier_chars:
            raise ValueError("job_id exceeds configured character limit")
        if _JOB_ID_PATTERN.fullmatch(cleaned) is None:
            raise ValueError("job_id must contain only letters, digits, hyphen, and underscore")
        return cleaned

    @field_validator("playbook", mode="before")
    @classmethod
    def _validate_playbook(cls, value: object) -> str:
        cleaned = _required_string(value, "playbook")
        if len(cleaned) > JOB_INGRESS_LIMITS.max_playbook_chars:
            raise ValueError("playbook exceeds configured character limit")
        if cleaned.startswith("/") or "\\" in cleaned or "\x00" in cleaned:
            raise ValueError("playbook must be a safe relative POSIX path")
        segments = cleaned.split("/")
        if any(
            not segment or segment in {".", ".."} or _PLAYBOOK_SEGMENT_PATTERN.fullmatch(segment) is None
            for segment in segments
        ):
            raise ValueError("playbook must contain only safe relative path segments")
        return cleaned

    @field_validator("queue", mode="before")
    @classmethod
    def _validate_queue(cls, value: object) -> str:
        cleaned = _required_string(value, "queue")
        if len(cleaned) > JOB_INGRESS_LIMITS.max_queue_chars:
            raise ValueError("queue exceeds configured character limit")
        if _QUEUE_PATTERN.fullmatch(cleaned) is None:
            raise ValueError("queue must be an identifier-like slug")
        return cleaned

    @field_validator("repository_binding_digest", mode="before")
    @classmethod
    def _validate_repository_binding_digest(cls, value: object) -> str | None:
        if value is None:
            return None
        if type(value) is not str or _SHA256_PATTERN.fullmatch(value) is None:
            raise ValueError(
                "repository_binding_digest must be a lowercase SHA-256 digest"
            )
        return value

    # ── D-09: ownership ──

    ownership: OwnershipSpec | None = None

    def policy_version(self) -> str:
        """Return the versioned policy identifier for this jobspec schema."""
        return f"jobspec-v1:{_JOBSPEC_POLICY_DIGEST_PREFIX}"

    def policy_hash(self) -> str:
        """Return a SHA-256 digest over the policy version and ownership."""
        h = hashlib.sha256()
        h.update(b"jobspec-v1")
        if self.ownership is not None:
            h.update(self.ownership.tenant_id.encode())
            h.update(self.ownership.project_id.encode())
            h.update(self.ownership.agent_id.encode())
        return h.hexdigest()


# ── D-09: cross-tenant ownership validation ──


def validate_cross_tenant(
    ownership: OwnershipSpec | None,
    request_tenant_id: str | None,
) -> tuple[bool, str]:
    """Reject a job whose ownership tenant does not match the authenticated tenant.

    Returns ``(True, "ok")`` when the tenant matches. Returns ``(False, reason)``
    when the request tenant is missing, empty, or mismatched with the ownership
    spec. This function is side-effect free — it does not touch the filesystem,
    network, or database.
    """
    if request_tenant_id is None or not request_tenant_id.strip():
        return False, "CROSS_TENANT_MISMATCH: request tenant is missing or empty"

    if ownership is None:
        return False, "CROSS_TENANT_MISMATCH: job has no ownership spec, cannot verify tenant"

    if ownership.tenant_id != request_tenant_id:
        return False, ("CROSS_TENANT_MISMATCH: ownership tenant does not match request tenant")

    return True, "ok"


# ── D-09: OwnershipSpec ──

_OWNER_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*\Z")


class OwnershipSpec(BaseModel):
    """Authenticated tenant, project, and agent ownership for a job."""

    model_config = ConfigDict(extra="forbid")

    tenant_id: str
    project_id: str
    agent_id: str

    @field_validator("tenant_id", "project_id", "agent_id", mode="before")
    @classmethod
    def _validate_id(cls, value: object) -> str:
        cleaned = _required_string(value, "ownership identifier")
        if len(cleaned) > JOB_INGRESS_LIMITS.max_identifier_chars:
            raise ValueError("ownership identifier exceeds configured character limit")
        if "\x00" in cleaned or "\n" in cleaned or ".." in cleaned or "/" in cleaned or "\\" in cleaned:
            raise ValueError("ownership identifier must not contain unsafe characters")
        if _OWNER_PATTERN.fullmatch(cleaned) is None:
            raise ValueError("ownership identifier must contain only safe characters")
        return cleaned


# ── D-09: WorkCeilingSpec ──


class WorkCeilingSpec(BaseModel):
    """Per-work-type time, resource, and cost ceilings.

    These are *ceilings* — narrower values from downstream layers (project,
    agent, work item) are valid, but no layer may exceed the hard ceiling.
    """

    model_config = ConfigDict(extra="forbid")

    max_wall_seconds: int = Field(default=3600, ge=1)
    max_cpu_seconds: int = Field(default=900, ge=1)
    max_memory_bytes: int = Field(default=536_870_912, ge=1)
    max_output_bytes: int = Field(default=1_048_576, ge=1)
    max_spend_micro_dollars: int = Field(default=10_000_000, ge=0)

    max_allowlisted_backends: tuple[str, ...] = Field(
        default=("firecracker", "gvisor"),
    )

    @classmethod
    def for_work_type(cls, work_type: str) -> WorkCeilingSpec:
        """Return the ceiling defaults for one work type, falling back to base defaults."""
        defaults: dict[str, dict[str, int]] = {
            "code": {
                "max_wall_seconds": 1800,
                "max_cpu_seconds": 300,
                "max_memory_bytes": 268_435_456,
                "max_output_bytes": 524_288,
                "max_spend_micro_dollars": 5_000_000,
            },
            "audit": {
                "max_wall_seconds": 7200,
                "max_cpu_seconds": 1800,
                "max_memory_bytes": 1_073_741_824,
                "max_output_bytes": 2_097_152,
                "max_spend_micro_dollars": 20_000_000,
            },
            "research": {
                "max_wall_seconds": 3600,
                "max_cpu_seconds": 600,
                "max_memory_bytes": 536_870_912,
                "max_output_bytes": 1_048_576,
                "max_spend_micro_dollars": 10_000_000,
            },
        }
        overrides: dict[str, int] = defaults.get(work_type, {})
        return cls.model_validate(overrides)


# ── D-09: Bounded denial audit ──

_DENIAL_AUDIT_MAX_BYTES: Final[int] = 131_072
_REDACTED_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "api_key",
        "psk",
        "token",
        "secret",
        "password",
        "credential",
        "authorization",
        "GLUDD_AUTH_PSK",
    }
)
_JOBSPEC_POLICY_DIGEST_PREFIX: Final[str] = "sha256"


def _redact_payload(raw: dict[str, object]) -> dict[str, object]:
    safe: dict[str, object] = {}
    for k, v in raw.items():
        lower = k.lower()
        if any(needle in lower for needle in _REDACTED_FIELDS):
            safe[k] = "[REDACTED]"
        elif isinstance(v, dict):
            safe[k] = _redact_payload(cast(dict[str, object], v))
        elif isinstance(v, (list, tuple)):
            safe[k] = [
                _redact_payload(cast(dict[str, object], item)) if isinstance(item, dict) else item
                for item in cast(list[object], v)
            ]
        else:
            safe[k] = v
    return safe


@dataclass(frozen=True, slots=True)
class _DenialAuditRecord:
    schema_version: str = "1.0"
    event_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    timestamp: str = field(default_factory=lambda: time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
    decision: str = "deny"
    reason_code: str = ""
    detail: str = ""

    def as_dict(self) -> dict[str, object]:
        d: dict[str, object] = {
            "schema_version": self.schema_version,
            "event_id": self.event_id,
            "timestamp": self.timestamp,
            "decision": self.decision,
            "reason_code": self.reason_code,
        }
        if self.detail:
            d["detail"] = self.detail[:1024]
        return d


def build_denial_audit_record(
    reason_code: str,
    detail: str,
    raw_payload: dict[str, object] | None = None,
) -> dict[str, object]:
    """Produce a bounded, redacted denial audit record.

    The raw payload is redacted (no secrets) and never stored in the record
    itself — only the reason code and truncated detail are included.
    """
    record = _DenialAuditRecord(
        reason_code=reason_code,
        detail=detail[:1024],
    )
    return record.as_dict()


def audit_invalid_job(
    reason_code: str,
    detail: str,
    raw_payload: dict[str, object] | None = None,
) -> dict[str, object]:
    """Record a denial audit event for an invalid job ingress.

    This is always side-effect free (no filesystem, no network, no DB).
    The returned dict is bounded to _DENIAL_AUDIT_MAX_BYTES.
    """
    record = build_denial_audit_record(
        reason_code=reason_code,
        detail=detail,
        raw_payload=raw_payload,
    )
    serialized = json.dumps(record, ensure_ascii=False, separators=(",", ":"))
    if len(serialized.encode("utf-8")) > _DENIAL_AUDIT_MAX_BYTES:
        existing: object = record.get("detail", "")
        record["detail"] = cast(str, existing)[:128]
    return record
