"""Source registry for the materials engineering collection (spec MATE-001 §11).

Tracks the provenance and freshness of every external data source used by the
materials expert: standards, handbooks, material databases, supplier data,
peer-reviewed studies, validated solver benchmarks, equipment documentation,
safety data, and public practitioner issue reports.

Each :class:`SourceEntry` carries the §11-mandated metadata:

  - ``authority``        tier in the data hierarchy (lot / supplier / handbook / estimated)
  - ``revision``         publisher's version tag
  - ``retrieval_time``   when the source was ingested
  - ``content_digest``   integrity hash of the source content
  - ``license``          under what terms the data may be redistributed
  - ``applicability``    which material/condition/lot the source applies to
  - ``uncertainty``      broad uncertainty contribution (authority-level proxy)
  - ``review_expiry``    when the entry MUST be re-validated

Data hierarchy (MATE-DEC-003):

    lot > supplier > handbook > estimated

Lower-tier data is retained and surfaced alongside higher-tier evidence; it is
never silently overwritten. Conflicting values become distinct observations.
Retracted (superseded) sources are excluded from resolution but kept on the
record for auditability.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any


class Authority(StrEnum):
    """Authority tier in the data hierarchy (MATE-DEC-003)."""

    LOT = "lot"
    SUPPLIER = "supplier"
    HANDBOOK = "handbook"
    ESTIMATED = "estimated"


# Rank: lower number = higher authority. Used to order conflicting observations.
AUTHORITY_RANK: dict[Authority, int] = {
    Authority.LOT: 0,
    Authority.SUPPLIER: 1,
    Authority.HANDBOOK: 2,
    Authority.ESTIMATED: 3,
}


_VALID_AUTHORITIES: frozenset[str] = frozenset(a.value for a in Authority)


@dataclass(frozen=True)
class SourceEntry:
    """A single external data source with full §11 provenance metadata."""

    source_id: str
    authority: Authority
    uri: str
    revision: str
    retrieval_time: datetime
    content_digest: str
    license: str
    applicability: dict[str, Any]
    uncertainty: float
    review_expiry: datetime
    retraction_reason: str | None = None
    superseded_by: str | None = None

    def __post_init__(self) -> None:
        if not self.source_id:
            raise ValueError("source_id must be non-empty")
        # Accept Authority instances or the string value; reject anything else.
        if isinstance(self.authority, Authority):
            auth = self.authority
        elif isinstance(self.authority, str) and self.authority in _VALID_AUTHORITIES:
            auth = Authority(self.authority)
            object.__setattr__(self, "authority", auth)
        else:
            raise ValueError(f"authority must be one of {sorted(_VALID_AUTHORITIES)}, got {self.authority!r}")
        if self.uncertainty < 0:
            raise ValueError("uncertainty must be non-negative")

    @property
    def is_retracted(self) -> bool:
        return self.superseded_by is not None or self.retraction_reason is not None


@dataclass(frozen=True)
class FreshnessReport:
    """Result of :meth:`SourceRegistry.check_freshness`."""

    source_id: str
    is_stale: bool
    days_past_expiry: int | None
    review_expiry: datetime


@dataclass
class SourceRegistry:
    """Append-only registry of every data source the materials expert consults.

    Sources are never deleted — they are superseded. The full retraction chain
    is preserved for audit, but superseded sources are excluded from resolution
    by :meth:`query` unless ``include_retracted=True`` is passed.
    """

    now: Callable[[], datetime] = field(default=datetime.now)
    _entries: dict[str, SourceEntry] = field(default_factory=dict)

    # ─── mutation ──────────────────────────────────────────────────────────

    def add(self, entry: SourceEntry) -> None:
        """Register a new source. Idempotent on source_id."""
        if entry.source_id in self._entries:
            # Re-adding the same id is a no-op (keeps the original record).
            return
        self._entries[entry.source_id] = entry

    def supersede(self, older_id: str, newer_id: str, reason: str) -> None:
        """Mark ``older_id`` as superseded by ``newer_id`` with a recorded reason.

        Raises KeyError if either id is unknown.
        """
        if older_id not in self._entries:
            raise KeyError(f"unknown source: {older_id!r}")
        if newer_id not in self._entries:
            raise KeyError(f"unknown source: {newer_id!r}")
        old = self._entries[older_id]
        self._entries[older_id] = SourceEntry(
            source_id=old.source_id,
            authority=old.authority,
            uri=old.uri,
            revision=old.revision,
            retrieval_time=old.retrieval_time,
            content_digest=old.content_digest,
            license=old.license,
            applicability=old.applicability,
            uncertainty=old.uncertainty,
            review_expiry=old.review_expiry,
            retraction_reason=reason,
            superseded_by=newer_id,
        )

    # ─── read ──────────────────────────────────────────────────────────────

    def get(self, source_id: str) -> SourceEntry:
        return self._entries[source_id]

    def all_entries(self) -> list[SourceEntry]:
        return list(self._entries.values())

    def check_freshness(self, source_id: str) -> FreshnessReport:
        """Report whether ``source_id`` is past its ``review_expiry``."""
        entry = self._entries[source_id]
        now = self.now()
        if entry.review_expiry.tzinfo is None:
            now = now.replace(tzinfo=None)
        delta = now - entry.review_expiry
        is_stale = delta.total_seconds() > 0
        return FreshnessReport(
            source_id=source_id,
            is_stale=is_stale,
            days_past_expiry=int(delta.days) if is_stale else None,
            review_expiry=entry.review_expiry,
        )

    def query(
        self,
        material_id: str | None = None,
        include_retracted: bool = False,
    ) -> list[SourceEntry]:
        """Return sources filtered by applicability.

        Retracted sources are excluded by default; pass ``include_retracted=True``
        to surface them (e.g. for audit reports).
        """
        out: list[SourceEntry] = []
        for entry in self._entries.values():
            if not include_retracted and entry.is_retracted:
                continue
            if material_id is not None and entry.applicability.get("material_id") != material_id:
                continue
            out.append(entry)
        out.sort(key=lambda e: AUTHORITY_RANK[e.authority])
        return out


__all__ = [
    "AUTHORITY_RANK",
    "Authority",
    "FreshnessReport",
    "SourceEntry",
    "SourceRegistry",
]
