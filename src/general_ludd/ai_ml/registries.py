"""AIML Phase A — registry records and atomic aliases (spec §4.3).

Every Source, Dataset, Model, Adapter, Simulator, EvaluationSuite, and
Deployment record carries the common contract fields: stable ID, semantic
version, SHA-256 digest, creator, creation time, license, origin URI,
dependency lock digest, input digests, policy decision, validation state,
supersedes relation, and tombstone state.

The :class:`Registry` provides atomic alias swaps so a mutable name (e.g.
``production-retrieval-index``) can be repointed to a new immutable record
without ever exposing a mixed state. Records themselves are frozen
dataclasses; corrections are made by superseding, never by editing in place.
"""

from __future__ import annotations

import dataclasses
import enum
import re
import time
from dataclasses import dataclass, field

from general_ludd.ai_ml.schemas import _require_nonempty_str, _require_sha256

_SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$")


class ValidationState(enum.StrEnum):
    """Validation lifecycle for a registry record (spec §4.3)."""

    PENDING = "pending"
    VALIDATED = "validated"
    FAILED = "failed"
    QUARANTINED = "quarantined"


_VALIDATION_TRANSITIONS: dict[ValidationState, frozenset[ValidationState]] = {
    ValidationState.PENDING: frozenset(
        {ValidationState.PENDING, ValidationState.VALIDATED, ValidationState.FAILED, ValidationState.QUARANTINED}
    ),
    ValidationState.VALIDATED: frozenset({ValidationState.VALIDATED}),
    ValidationState.FAILED: frozenset({ValidationState.FAILED, ValidationState.QUARANTINED}),
    ValidationState.QUARANTINED: frozenset({ValidationState.QUARANTINED}),
}


def _validate_semver(version: str) -> None:
    if not isinstance(version, str) or not _SEMVER_RE.match(version):
        raise ValueError(f"version must be a semantic-version string (MAJOR.MINOR.PATCH), got {version!r}")


@dataclass(frozen=True)
class RegistryRecord:
    """Base contract for every registry record (spec §4.3).

    Subclasses (Source, Dataset, Model, ...) fix ``kind`` to a stable
    discriminator string so the registry can be filtered and audited per
    kind. Records are frozen — mutations happen via supersede, not edits.
    """

    record_id: str
    kind: str
    version: str
    sha256: str
    creator: str
    license: str
    origin_uri: str
    dependency_lock_sha256: str
    input_digests: tuple[str, ...] = ()
    created_at: int = field(default_factory=lambda: int(time.time()))
    policy_decision_id: str | None = None
    validation_state: ValidationState = ValidationState.PENDING
    supersedes: str | None = None
    tombstone: bool = False
    tombstone_reason: str = ""

    def __post_init__(self) -> None:
        _require_nonempty_str(self.record_id, "record_id")
        _require_nonempty_str(self.kind, "kind")
        _validate_semver(self.version)
        _require_sha256(self.sha256, "sha256")
        _require_nonempty_str(self.creator, "creator")
        _require_nonempty_str(self.license, "license")
        _require_nonempty_str(self.origin_uri, "origin_uri")
        _require_sha256(self.dependency_lock_sha256, "dependency_lock_sha256")
        for d in self.input_digests:
            _require_sha256(d, "input_digests[i]")
        if self.tombstone and not self.tombstone_reason.strip():
            raise ValueError("a tombstoned record must carry a non-empty tombstone_reason")
        if self.supersedes is not None and not self.supersedes.strip():
            raise ValueError("supersedes, when set, must be a non-empty record_id")


@dataclass(frozen=True)
class Source(RegistryRecord):
    """A research / documentation / dataset source provenance record."""


@dataclass(frozen=True)
class Dataset(RegistryRecord):
    """A versioned, manifest-backed dataset (spec §6.1)."""


@dataclass(frozen=True)
class Model(RegistryRecord):
    """A trained or pretrained model record with weight digest."""


@dataclass(frozen=True)
class Adapter(RegistryRecord):
    """A LoRA / QLoRA / adapter record (spec §6.4)."""


@dataclass(frozen=True)
class Simulator(RegistryRecord):
    """A federated simulator adapter (spec §8.2)."""


@dataclass(frozen=True)
class EvaluationSuite(RegistryRecord):
    """A pinned task / safety / regression evaluation suite (spec §6.3)."""


@dataclass(frozen=True)
class Deployment(RegistryRecord):
    """A canary / shadow / production deployment alias target (spec §12)."""


class Registry:
    """In-memory registry of immutable records + atomic aliases.

    Mutations: ``publish`` adds a record; ``set_alias`` repoints a mutable
    name; ``tombstone`` marks a record as inactive; ``supersede`` does
    publish+link+tombstone atomically. None of these methods edit a record
    in place — supersede builds a new frozen record carrying
    ``supersedes=<old_id>`` and tombstones the old one.
    """

    def __init__(self) -> None:
        self._records: dict[str, RegistryRecord] = {}
        self._aliases: dict[str, str] = {}

    def publish(self, record: RegistryRecord) -> RegistryRecord:
        if record.record_id in self._records:
            raise ValueError(
                f"record with id {record.record_id!r} already exists; use supersede() to publish a corrected version"
            )
        self._records[record.record_id] = record
        return record

    def get(self, record_id: str) -> RegistryRecord | None:
        return self._records.get(record_id)

    def set_alias(self, alias: str, record_id: str) -> None:
        if not isinstance(alias, str) or not alias.strip():
            raise ValueError("alias must be a non-empty string")
        if record_id not in self._records:
            raise KeyError(f"cannot alias to unknown record_id: {record_id!r}")
        # Atomic swap: a single dict assignment is the linearization point.
        self._aliases[alias] = record_id

    def resolve(self, alias: str) -> RegistryRecord | None:
        record_id = self._aliases.get(alias)
        if record_id is None:
            return None
        return self._records.get(record_id)

    def tombstone(self, record_id: str, *, reason: str) -> RegistryRecord:
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError("reason must be a non-empty string")
        existing = self._records.get(record_id)
        if existing is None:
            raise KeyError(f"unknown record_id: {record_id!r}")
        if existing.tombstone:
            return existing
        tombstoned = _rebuild_record(existing, tombstone=True, tombstone_reason=reason)
        self._records[record_id] = tombstoned
        return tombstoned

    def supersede(
        self,
        old_record_id: str,
        new_record: RegistryRecord,
    ) -> RegistryRecord:
        old = self._records.get(old_record_id)
        if old is None:
            raise KeyError(f"unknown old record_id: {old_record_id!r}")
        if new_record.supersedes is not None and new_record.supersedes != old_record_id:
            raise ValueError(f"new record supersedes {new_record.supersedes!r}, expected {old_record_id!r}")
        # Attach the supersedes link if the caller did not set it.
        if new_record.supersedes is None:
            new_record = _rebuild_record(new_record, supersedes=old_record_id)
        self.publish(new_record)
        self.tombstone(old_record_id, reason=f"superseded by {new_record.record_id}")
        return new_record

    def list_kind(self, kind: str) -> list[RegistryRecord]:
        return [r for r in self._records.values() if r.kind == kind]

    def list_all(self) -> list[RegistryRecord]:
        return list(self._records.values())


def _rebuild_record(record: RegistryRecord, **overrides: object) -> RegistryRecord:
    """Return a new frozen record of the same concrete type with ``overrides``."""
    return dataclasses.replace(record, **overrides)


__all__ = [
    "Adapter",
    "Dataset",
    "Deployment",
    "EvaluationSuite",
    "Model",
    "Registry",
    "RegistryRecord",
    "Simulator",
    "Source",
    "ValidationState",
]
