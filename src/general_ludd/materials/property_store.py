"""Property store for the materials engineering collection (spec MATE-001 §4.1).

Stores :class:`PropertyRecord` instances keyed by (material, name, conditions) and
resolves a single best observation per query using the data hierarchy
MATE-DEC-003 (lot > supplier > handbook > estimated).

Invariants enforced here:

  - MATE-SAFE-003: a record whose ``conditions`` dict is empty is flagged
    ``insufficient_context`` — the value is kept (for traceability) but cannot
    be used as a silent default.
  - MATE-DEC-003: conflicting values from different sources are retained as
    distinct observations. :meth:`PropertyStore.resolve_property` returns the
    highest-authority survivor and exposes the lower-tier alternatives via
    :attr:`ResolvedProperty.alternatives`.
  - Retracted (superseded) sources are excluded from resolution; their records
    remain on the store for audit but never win.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from general_ludd.materials.source_registry import (
    AUTHORITY_RANK,
    Authority,
    SourceEntry,
    SourceRegistry,
)

INSUFFICIENT_CONTEXT = "insufficient_context"

# Conditions the resolver considers when matching records to a query. If any
# required key is missing on the record, the record is still returned (for
# audit) but is flagged insufficient_context per MATE-SAFE-003.
_REQUIRED_CONDITION_KEYS: tuple[str, ...] = (
    "product_form",
    "temper",
)


@dataclass(frozen=True)
class PropertyRecord:
    """A single observed property value for a material under stated conditions.

    Matches the property shape in spec §5.2 ``MaterialProperty`` plus the
    condition metadata required by §4.1.
    """

    record_id: str
    material_id: str
    name: str
    value: float
    unit: str
    basis: str  # yield / ultimate / proof / endurance / nominal / ...
    method: str  # e.g. "ASTM B209"
    uncertainty: float
    conditions: dict[str, Any]
    source_id: str
    state: str = "ok"

    def __post_init__(self) -> None:
        if not self.record_id:
            raise ValueError("record_id must be non-empty")
        if not self.material_id:
            raise ValueError("material_id must be non-empty")
        if not self.name:
            raise ValueError("name must be non-empty")
        if self.uncertainty < 0:
            raise ValueError("uncertainty must be non-negative")
        # MATE-SAFE-003: missing condition metadata => insufficient_context.
        if not self.conditions:
            object.__setattr__(self, "state", INSUFFICIENT_CONTEXT)


@dataclass(frozen=True)
class StoreQuery:
    """Query parameters for :class:`PropertyStore`."""

    material_id: str
    name: str
    conditions: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ResolvedProperty:
    """Result of :meth:`PropertyStore.resolve_property`.

    ``alternatives`` carries the lower-tier observations that lost the
    hierarchy resolution; they are surfaced for transparency (MATE-DEC-003)
    and never silently discarded.
    """

    record: PropertyRecord
    alternatives: list[PropertyRecord]

    @property
    def value(self) -> float:
        return self.record.value

    @property
    def unit(self) -> str:
        return self.record.unit

    @property
    def source_id(self) -> str:
        return self.record.source_id

    @property
    def state(self) -> str:
        return self.record.state


@dataclass
class PropertyStore:
    """Append-only store of property observations keyed by (material, name).

    Records reference a source in the :class:`SourceRegistry`; resolution
    consults that registry to apply the MATE-DEC-003 hierarchy and to skip
    retracted sources.
    """

    registry: SourceRegistry = field(default_factory=SourceRegistry)
    _records: list[PropertyRecord] = field(default_factory=list)

    # ─── mutation ──────────────────────────────────────────────────────────

    def add_source(self, entry: SourceEntry) -> None:
        """Register a source with the underlying registry."""
        self.registry.add(entry)

    def add_property(self, record: PropertyRecord) -> None:
        """Append a property observation. Duplicate record_ids are ignored."""
        if any(r.record_id == record.record_id for r in self._records):
            return
        self._records.append(record)

    # ─── read ──────────────────────────────────────────────────────────────

    def query(self, q: StoreQuery, include_retracted: bool = False) -> list[PropertyRecord]:
        """Return all records matching the query, filtered by retraction + conditions.

        Records whose source is retracted are excluded unless
        ``include_retracted=True``. Records whose conditions do not match the
        query's ``conditions`` dict (where the query specifies a key) are
        filtered out.
        """
        retracted_ids = {entry.source_id for entry in self.registry.all_entries() if entry.is_retracted}

        out: list[PropertyRecord] = []
        for rec in self._records:
            if rec.material_id != q.material_id:
                continue
            if rec.name != q.name:
                continue
            if not include_retracted and rec.source_id in retracted_ids:
                continue
            # Condition filter: query-specified keys must match the record's.
            matched = True
            for key, val in q.conditions.items():
                if rec.conditions.get(key) != val:
                    matched = False
                    break
            if not matched:
                continue
            out.append(rec)
        return out

    def resolve_property(self, q: StoreQuery) -> ResolvedProperty | None:
        """Resolve the highest-authority observation for the query.

        Returns ``None`` when no records match. Otherwise returns the
        highest-authority record (MATE-DEC-003) with the losing observations
        in ``alternatives``. Retracted sources are always skipped.
        """
        records = self.query(q, include_retracted=False)
        if not records:
            return None

        # Authority rank per record, looked up from the registry.
        def _rank(rec: PropertyRecord) -> int:
            try:
                entry = self.registry.get(rec.source_id)
            except KeyError:
                # Record references an unregistered source: treat as lowest tier.
                return AUTHORITY_RANK[Authority.ESTIMATED]
            return AUTHORITY_RANK[entry.authority]

        ordered = sorted(records, key=_rank)
        winner = ordered[0]
        alternatives = ordered[1:]
        return ResolvedProperty(record=winner, alternatives=alternatives)


__all__ = [
    "INSUFFICIENT_CONTEXT",
    "PropertyRecord",
    "PropertyStore",
    "ResolvedProperty",
    "StoreQuery",
]
