"""Observed sandbox-state evaluation with durable, integrity-checked events."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Annotated, Literal, TypeAlias
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from general_ludd.db.models import MAX_JSON_BLOB_LEN, AuditEventModel
from general_ludd.security.policy.profiles import BackendName, ResolvedSandboxProfile

Guarantee: TypeAlias = Literal[
    "application-kernel",
    "approved-backend",
    "backend-applied",
    "filesystem-isolation",
    "network-policy",
    "no-new-privileges",
    "process-identity",
    "resource-limits",
    "syscall-filter",
    "syscall-profile-match",
    "virtual-machine",
]
Decision: TypeAlias = Literal["allow", "deny"]

_EVENT_TYPE = "sandbox_runtime_attestation"
_ENTITY_TYPE = "sandbox_runtime_attestation"
_MAX_EVENT_BYTES = min(32_768, MAX_JSON_BLOB_LEN)
_BACKEND_STRENGTH = {
    "firecracker": 2,
    "gvisor": 1,
    "nsjail": 0,
    "bubblewrap": 0,
}
_REQUIRED_STRENGTH = {
    "process-isolation": 0,
    "application-kernel": 1,
    "virtual-machine": 2,
}

BoundedId = Annotated[
    str,
    Field(min_length=1, max_length=128, pattern=r"[A-Za-z0-9][A-Za-z0-9._:/-]*"),
]
Digest = Annotated[str, Field(pattern=r"sha256:[0-9a-f]{64}")]


class _StrictFrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class NamespaceEvidence(_StrictFrozenModel):
    user: Annotated[str, Field(min_length=1, max_length=128)] | None = None
    mount: Annotated[str, Field(min_length=1, max_length=128)] | None = None
    pid: Annotated[str, Field(min_length=1, max_length=128)] | None = None
    ipc: Annotated[str, Field(min_length=1, max_length=128)] | None = None
    network: Annotated[str, Field(min_length=1, max_length=128)] | None = None
    uts: Annotated[str, Field(min_length=1, max_length=128)] | None = None


class ResourceSnapshot(_StrictFrozenModel):
    cpu_millis: Annotated[int, Field(strict=True, ge=0)] = 0
    memory_bytes: Annotated[int, Field(strict=True, ge=0)] = 0
    pids: Annotated[int, Field(strict=True, ge=0)] = 0
    disk_bytes: Annotated[int, Field(strict=True, ge=0)] = 0
    network_bytes: Annotated[int, Field(strict=True, ge=0)] = 0


class RuntimeSandboxObservation(_StrictFrozenModel):
    """Typed evidence collected from the host, not a backend success flag alone."""

    applied: StrictBool
    backend: BackendName
    backend_version: Annotated[str, Field(min_length=1, max_length=128)]
    image_digest: Digest | None = None
    guarantees: frozenset[Guarantee]
    namespaces: NamespaceEvidence
    vm_identity: Annotated[str, Field(min_length=1, max_length=128)] | None = None
    cgroup: Annotated[str, Field(min_length=1, max_length=512)] | None = None
    filesystem_mounts: tuple[Annotated[str, Field(min_length=1, max_length=256)], ...]
    network_policy: Annotated[str, Field(min_length=1, max_length=128)] | None = None
    uid: Annotated[int, Field(strict=True, ge=0)]
    gid: Annotated[int, Field(strict=True, ge=0)]
    syscall_profile: Annotated[str, Field(min_length=1, max_length=128)] | None = None

    @field_validator("guarantees", mode="before")
    @classmethod
    def _normalize_guarantees(cls, value: object) -> object:
        if isinstance(value, (set, list, tuple)):
            return frozenset(value)
        return value

    @field_validator("filesystem_mounts", mode="before")
    @classmethod
    def _normalize_mounts(cls, value: object) -> object:
        if isinstance(value, list):
            return tuple(value)
        return value

    @field_validator("filesystem_mounts")
    @classmethod
    def _validate_mounts(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(value) != len(set(value)):
            raise ValueError("filesystem_mounts contains duplicate evidence")
        return tuple(sorted(value))


class SandboxAttestationEvent(_StrictFrozenModel):
    schema_version: Literal[1] = 1
    event_id: BoundedId = Field(default_factory=lambda: f"att-{uuid4().hex}")
    sequence: Annotated[int, Field(strict=True, ge=0)] = 0
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    project_id: BoundedId
    work_item_id: BoundedId
    agent_id: BoundedId
    tenant_id: BoundedId
    policy_version: Annotated[str, Field(min_length=1, max_length=96)]
    policy_hash: Annotated[str, Field(pattern=r"[0-9a-f]{64}")]
    requested_profile: Annotated[str, Field(min_length=1, max_length=64)]
    effective_profile: Annotated[str, Field(min_length=1, max_length=64)]
    requested_backend: BackendName
    effective_backend: BackendName
    backend_version: Annotated[str, Field(min_length=1, max_length=128)]
    image_digest: Digest | None
    decision: Decision
    reason_code: Annotated[str, Field(min_length=1, max_length=128)]
    missing_guarantees: tuple[Guarantee, ...]
    resource_snapshot: ResourceSnapshot
    parent_event_id: BoundedId | None = None
    correlation_id: BoundedId
    observation: RuntimeSandboxObservation
    integrity_sha256: Annotated[str, Field(pattern=r"(?:|[0-9a-f]{64})")] = ""

    @field_validator("timestamp")
    @classmethod
    def _timestamp_is_utc(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("attestation timestamp must be timezone-aware")
        return value.astimezone(UTC)

    @field_validator("missing_guarantees", mode="before")
    @classmethod
    def _normalize_missing(cls, value: object) -> object:
        if isinstance(value, list):
            return tuple(value)
        return value

    def seal(self, sequence: int) -> SandboxAttestationEvent:
        if sequence <= 0:
            raise ValueError("durable attestation sequence must be positive")
        unsealed = self.model_copy(update={"sequence": sequence, "integrity_sha256": ""})
        return unsealed.model_copy(update={"integrity_sha256": _event_digest(unsealed)})

    def verify_integrity(self) -> bool:
        return bool(self.integrity_sha256) and self.integrity_sha256 == _event_digest(self)


class AttestationIntegrityError(RuntimeError):
    """Raised when persisted sandbox evidence fails its integrity contract."""


class AttestationEventTooLargeError(ValueError):
    """Raised before committing an event that exceeds the durable payload cap."""


def _event_digest(event: SandboxAttestationEvent) -> str:
    payload = event.model_dump(mode="json", exclude={"integrity_sha256"})
    # A frozenset's iteration order is process/hash-seed dependent.  Persist a
    # canonical set ordering so a different Gunicorn worker can verify the
    # digest it did not create.
    payload["observation"]["guarantees"] = sorted(event.observation.guarantees)
    canonical = json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _required_guarantees(resolved: ResolvedSandboxProfile) -> set[Guarantee]:
    policy = resolved.policy
    required: set[Guarantee] = {
        "filesystem-isolation",
        "network-policy",
        "no-new-privileges",
        "process-identity",
        "resource-limits",
        "syscall-filter",
    }
    if policy.backend.minimum_strength == "application-kernel":
        required.add("application-kernel")
    elif policy.backend.minimum_strength == "virtual-machine":
        required.add("virtual-machine")
    return required


def evaluate_runtime_attestation(
    *,
    resolved: ResolvedSandboxProfile,
    observation: RuntimeSandboxObservation,
    project_id: str,
    work_item_id: str,
    agent_id: str,
    tenant_id: str,
    correlation_id: str,
    parent_event_id: str | None = None,
    resource_snapshot: ResourceSnapshot | None = None,
) -> SandboxAttestationEvent:
    """Compare observed state to policy and return an allow/deny event draft."""

    policy = resolved.policy
    missing = _required_guarantees(resolved).difference(observation.guarantees)
    reason_code = "attestation-verified"

    if observation.backend not in policy.backend.preference:
        missing.add("approved-backend")
        reason_code = "backend-not-approved"
    elif not observation.applied:
        missing.add("backend-applied")
        reason_code = "backend-not-applied"
    elif _BACKEND_STRENGTH[observation.backend] < _REQUIRED_STRENGTH[
        policy.backend.minimum_strength
    ]:
        strength_guarantee: Guarantee = (
            "virtual-machine"
            if policy.backend.minimum_strength == "virtual-machine"
            else "application-kernel"
        )
        missing.add(strength_guarantee)
        reason_code = "backend-strength-insufficient"
    elif observation.syscall_profile != policy.process.syscall_profile:
        missing.add("syscall-profile-match")
        reason_code = "syscall-profile-mismatch"
    elif missing:
        reason_code = "missing-guarantees"

    decision: Decision = "deny" if missing else "allow"
    return SandboxAttestationEvent(
        project_id=project_id,
        work_item_id=work_item_id,
        agent_id=agent_id,
        tenant_id=tenant_id,
        policy_version=resolved.policy_version,
        policy_hash=resolved.policy_hash,
        requested_profile=resolved.requested_profile,
        effective_profile=policy.profile,
        requested_backend=policy.backend.preference[0],
        effective_backend=observation.backend,
        backend_version=observation.backend_version,
        image_digest=observation.image_digest,
        decision=decision,
        reason_code=reason_code,
        missing_guarantees=tuple(sorted(missing)),
        resource_snapshot=resource_snapshot or ResourceSnapshot(),
        parent_event_id=parent_event_id,
        correlation_id=correlation_id,
        observation=observation,
    )


def _partition_key(tenant_id: str, work_item_id: str) -> str:
    value = f"{len(tenant_id)}:{tenant_id}{len(work_item_id)}:{work_item_id}"
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class DurableSandboxAttestationStore:
    """Append/query attestation rows through the shared cross-worker database."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        max_event_bytes: int = _MAX_EVENT_BYTES,
    ) -> None:
        if not 1 <= max_event_bytes <= MAX_JSON_BLOB_LEN:
            raise ValueError("max_event_bytes must fit the database audit-event bound")
        self._session_factory = session_factory
        self._max_event_bytes = max_event_bytes

    async def append(self, event: SandboxAttestationEvent) -> SandboxAttestationEvent:
        """Commit a sealed event before returning it to the dispatch caller."""

        if event.sequence != 0 or event.integrity_sha256:
            raise ValueError("attestation event has already been persisted")
        async with self._session_factory() as session:
            try:
                row = AuditEventModel(
                    event_type=_EVENT_TYPE,
                    project_id=event.project_id,
                    actor=event.agent_id,
                    entity_type=_ENTITY_TYPE,
                    entity_id=_partition_key(event.tenant_id, event.work_item_id),
                    correlation_id=event.correlation_id,
                    details="{}",
                )
                session.add(row)
                await session.flush()
                sealed = event.seal(row.id)
                details = json.dumps(
                    sealed.model_dump(mode="json"),
                    ensure_ascii=True,
                    separators=(",", ":"),
                    sort_keys=True,
                )
                if len(details.encode("utf-8")) > self._max_event_bytes:
                    raise AttestationEventTooLargeError(
                        f"sandbox attestation exceeds {self._max_event_bytes} bytes"
                    )
                row.details = details
                await session.commit()
            except Exception:
                await session.rollback()
                raise
        return sealed

    async def list_events(
        self,
        *,
        project_id: str,
        tenant_id: str,
        work_item_id: str,
        after_sequence: int = 0,
        limit: int = 100,
    ) -> list[SandboxAttestationEvent]:
        """Read committed events incrementally for live cross-worker consumers."""

        if after_sequence < 0:
            raise ValueError("after_sequence must not be negative")
        if not 1 <= limit <= 1000:
            raise ValueError("limit must be between 1 and 1000")
        entity_id = _partition_key(tenant_id, work_item_id)
        async with self._session_factory() as session:
            rows = list(
                (
                    await session.execute(
                        select(AuditEventModel)
                        .where(
                            AuditEventModel.event_type == _EVENT_TYPE,
                            AuditEventModel.entity_type == _ENTITY_TYPE,
                            AuditEventModel.project_id == project_id,
                            AuditEventModel.entity_id == entity_id,
                            AuditEventModel.id > after_sequence,
                        )
                        .order_by(AuditEventModel.id)
                        .limit(limit)
                    )
                )
                .scalars()
                .all()
            )

        events: list[SandboxAttestationEvent] = []
        for row in rows:
            try:
                event = SandboxAttestationEvent.model_validate_json(row.details)
            except Exception as error:
                raise AttestationIntegrityError(
                    f"sandbox attestation row {row.id} is not valid"
                ) from error
            if (
                event.sequence != row.id
                or event.project_id != project_id
                or event.tenant_id != tenant_id
                or event.work_item_id != work_item_id
                or not event.verify_integrity()
            ):
                raise AttestationIntegrityError(
                    f"sandbox attestation row {row.id} failed integrity verification"
                )
            events.append(event)
        return events


__all__ = [
    "AttestationEventTooLargeError",
    "AttestationIntegrityError",
    "DurableSandboxAttestationStore",
    "ResourceSnapshot",
    "RuntimeSandboxObservation",
    "SandboxAttestationEvent",
    "evaluate_runtime_attestation",
]
