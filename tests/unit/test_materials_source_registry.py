"""Tests for MATE-P1 source registry and property store (spec MATE-001 §11, §4.1).

Verifies:
  - SourceEntry carries authority/revision/digest/license/review_expiry (§11).
  - Stale sources (past review_expiry) are flagged by check_freshness.
  - Conflicting values are retained as distinct observations, not merged.
  - Data hierarchy MATE-DEC-003 is enforced (lot > supplier > handbook > estimated).
  - Retracted sources are excluded from resolution.
  - PropertyRecord carries the §4.1 condition metadata.
  - PropertyStore.resolve_property applies hierarchy and flags insufficient_context.
  - Missing condition metadata marks a record insufficient_context (MATE-SAFE-003).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import cast

import pytest

from general_ludd.materials.property_store import (
    PropertyRecord,
    PropertyStore,
    StoreQuery,
)
from general_ludd.materials.source_registry import (
    AUTHORITY_RANK,
    Authority,
    SourceEntry,
    SourceRegistry,
)

# ─── helpers ─────────────────────────────────────────────────────────────────


def _now() -> datetime:
    return datetime(2026, 1, 1, tzinfo=UTC)


def _fresh_expiry(days: int = 365) -> datetime:
    return _now() + timedelta(days=days)


def _stale_expiry() -> datetime:
    return _now() - timedelta(days=30)


def _lot_source(source_id: str = "lot-A") -> SourceEntry:
    return SourceEntry(
        source_id=source_id,
        authority=Authority.LOT,
        uri="urn:lot:mill-cert-A",
        revision="2025-01",
        retrieval_time=_now(),
        content_digest="sha256:lot-abc",
        license="proprietary-lot",
        applicability={"material_id": "aa6061_t6", "lot": "L1234"},
        uncertainty=5.0,
        review_expiry=_fresh_expiry(),
    )


def _supplier_source(source_id: str = "sup-A") -> SourceEntry:
    return SourceEntry(
        source_id=source_id,
        authority=Authority.SUPPLIER,
        uri="urn:supplier:data-sheet",
        revision="2024-Q4",
        retrieval_time=_now(),
        content_digest="sha256:sup-def",
        license="supplier-nda",
        applicability={"material_id": "aa6061_t6"},
        uncertainty=15.0,
        review_expiry=_fresh_expiry(),
    )


def _handbook_source(source_id: str = "hb-A") -> SourceEntry:
    return SourceEntry(
        source_id=source_id,
        authority=Authority.HANDBOOK,
        uri="urn:handbook:asm-v2",
        revision="2023",
        retrieval_time=_now(),
        content_digest="sha256:hb-123",
        license="reference",
        applicability={"material_id": "aa6061_t6"},
        uncertainty=25.0,
        review_expiry=_fresh_expiry(),
    )


def _estimated_source(source_id: str = "est-A") -> SourceEntry:
    return SourceEntry(
        source_id=source_id,
        authority=Authority.ESTIMATED,
        uri="urn:model:analogy-v1",
        revision="model-v0",
        retrieval_time=_now(),
        content_digest="sha256:est-999",
        license="internal",
        applicability={"material_id": "aa6061_t6"},
        uncertainty=50.0,
        review_expiry=_fresh_expiry(),
    )


def _yield_record(
    value: float,
    source_id: str,
    conditions: dict[str, str] | None = None,
) -> PropertyRecord:
    return PropertyRecord(
        record_id=f"rec-{source_id}",
        material_id="aa6061_t6",
        name="yield_strength",
        value=value,
        unit="MPa",
        basis="yield",
        method="ASTM B209",
        uncertainty=10.0,
        conditions={"product_form": "sheet", "temper": "T6"} if conditions is None else conditions,
        source_id=source_id,
    )


# ─── SourceEntry ──────────────────────────────────────────────────────────────


class TestSourceEntry:
    def test_entry_carries_required_spec_fields(self) -> None:
        """§11: each entry has authority, revision, digest, license, review_expiry."""
        s = _lot_source()
        assert s.authority == Authority.LOT
        assert s.revision == "2025-01"
        assert s.content_digest == "sha256:lot-abc"
        assert s.license == "proprietary-lot"
        assert s.review_expiry == _fresh_expiry()

    def test_invalid_authority_rejected(self) -> None:
        with pytest.raises(ValueError):
            SourceEntry(
                source_id="bad",
                authority=cast(Authority, "bogus"),
                uri="urn:x",
                revision="1",
                retrieval_time=_now(),
                content_digest="sha256:x",
                license="none",
                applicability={},
                uncertainty=0.0,
                review_expiry=_fresh_expiry(),
            )

    def test_empty_source_id_rejected(self) -> None:
        with pytest.raises(ValueError, match="source_id must be non-empty"):
            SourceEntry(
                source_id="",
                authority=Authority.LOT,
                uri="urn:x",
                revision="1",
                retrieval_time=_now(),
                content_digest="sha256:x",
                license="none",
                applicability={},
                uncertainty=0.0,
                review_expiry=_fresh_expiry(),
            )

    def test_valid_string_authority_is_normalized(self) -> None:
        entry = SourceEntry(
            source_id="string-authority",
            authority=cast(Authority, Authority.SUPPLIER.value),
            uri="urn:x",
            revision="1",
            retrieval_time=_now(),
            content_digest="sha256:x",
            license="none",
            applicability={},
            uncertainty=0.0,
            review_expiry=_fresh_expiry(),
        )

        assert entry.authority is Authority.SUPPLIER

    def test_negative_uncertainty_rejected(self) -> None:
        with pytest.raises(ValueError, match="uncertainty must be non-negative"):
            SourceEntry(
                source_id="negative-uncertainty",
                authority=Authority.ESTIMATED,
                uri="urn:x",
                revision="1",
                retrieval_time=_now(),
                content_digest="sha256:x",
                license="none",
                applicability={},
                uncertainty=-0.1,
                review_expiry=_fresh_expiry(),
            )


# ─── SourceRegistry ──────────────────────────────────────────────────────────


class TestSourceRegistry:
    def test_duplicate_add_keeps_original_entry(self) -> None:
        reg = SourceRegistry(now=_now)
        original = _lot_source("stable-id")
        reg.add(original)
        reg.add(_supplier_source("stable-id"))

        assert reg.get("stable-id") is original
        assert reg.all_entries() == [original]

    @pytest.mark.parametrize(
        ("older_id", "newer_id", "missing_id"),
        [
            ("missing-old", "present", "missing-old"),
            ("present", "missing-new", "missing-new"),
        ],
    )
    def test_supersede_rejects_unknown_source(
        self,
        older_id: str,
        newer_id: str,
        missing_id: str,
    ) -> None:
        reg = SourceRegistry(now=_now)
        reg.add(_lot_source("present"))

        with pytest.raises(KeyError, match=missing_id):
            reg.supersede(older_id=older_id, newer_id=newer_id, reason="missing")

    def test_check_freshness_flags_stale_source(self) -> None:
        """§11: stale source (past review_expiry) is flagged."""
        reg = SourceRegistry(now=_now)
        stale = SourceEntry(
            source_id="stale-1",
            authority=Authority.HANDBOOK,
            uri="urn:hb:old",
            revision="2010",
            retrieval_time=_now() - timedelta(days=3650),
            content_digest="sha256:old",
            license="reference",
            applicability={},
            uncertainty=30.0,
            review_expiry=_stale_expiry(),
        )
        reg.add(stale)
        fresh = reg.check_freshness("stale-1")
        assert fresh.is_stale is True
        assert fresh.days_past_expiry is not None and fresh.days_past_expiry > 0

    def test_check_freshness_passes_recent_source(self) -> None:
        reg = SourceRegistry(now=_now)
        reg.add(_handbook_source("hb-fresh"))
        fresh = reg.check_freshness("hb-fresh")
        assert fresh.is_stale is False

    def test_check_freshness_aligns_now_with_naive_expiry(self) -> None:
        reg = SourceRegistry(now=_now)
        entry = SourceEntry(
            source_id="naive-expiry",
            authority=Authority.HANDBOOK,
            uri="urn:hb:naive",
            revision="2025",
            retrieval_time=_now(),
            content_digest="sha256:naive",
            license="reference",
            applicability={},
            uncertainty=1.0,
            review_expiry=datetime(2025, 12, 31),
        )
        reg.add(entry)

        report = reg.check_freshness(entry.source_id)

        assert report.is_stale is True
        assert report.days_past_expiry == 1

    def test_supersede_marks_prior_source_retracted(self) -> None:
        """Superseded sources are flagged retracted and excluded from queries."""
        reg = SourceRegistry(now=_now)
        reg.add(_handbook_source("hb-old"))
        reg.add(_handbook_source("hb-new"))
        reg.supersede(older_id="hb-old", newer_id="hb-new", reason="erratum")
        # Query should not return retracted sources by default.
        results = reg.query(material_id="aa6061_t6")
        ids = {s.source_id for s in results}
        assert "hb-old" not in ids
        assert "hb-new" in ids
        # The retraction reason is recorded.
        assert reg.get("hb-old").retraction_reason == "erratum"

    def test_query_excludes_retracted_source_directly(self) -> None:
        reg = SourceRegistry(now=_now)
        reg.add(_lot_source("lot-retracted"))
        reg.add(_supplier_source("sup-A"))
        reg.supersede(older_id="lot-retracted", newer_id="sup-A", reason="withdrawn")
        # Even with include_retracted, the retraction flag is set.
        all_results = reg.query(material_id="aa6061_t6", include_retracted=True)
        retracted = {s.source_id: s for s in all_results}
        assert retracted["lot-retracted"].is_retracted is True

    def test_query_filters_other_materials(self) -> None:
        reg = SourceRegistry(now=_now)
        reg.add(_lot_source("matching"))
        other = SourceEntry(
            source_id="other",
            authority=Authority.SUPPLIER,
            uri="urn:other",
            revision="1",
            retrieval_time=_now(),
            content_digest="sha256:other",
            license="none",
            applicability={"material_id": "other-material"},
            uncertainty=1.0,
            review_expiry=_fresh_expiry(),
        )
        reg.add(other)

        assert [entry.source_id for entry in reg.query(material_id="aa6061_t6")] == ["matching"]


# ─── Data hierarchy (MATE-DEC-003) ────────────────────────────────────────────


class TestDataHierarchy:
    def test_authority_rank_lot_beats_all(self) -> None:
        """MATE-DEC-003: lot > supplier > handbook > estimated."""
        assert AUTHORITY_RANK[Authority.LOT] < AUTHORITY_RANK[Authority.SUPPLIER]
        assert AUTHORITY_RANK[Authority.SUPPLIER] < AUTHORITY_RANK[Authority.HANDBOOK]
        assert AUTHORITY_RANK[Authority.HANDBOOK] < AUTHORITY_RANK[Authority.ESTIMATED]

    def test_conflicting_values_retained_as_distinct_observations(self) -> None:
        """Conflicting values are NOT merged — they remain distinct records."""
        store = PropertyStore()
        store.add_source(_lot_source("lot-A"))
        store.add_source(_supplier_source("sup-A"))
        store.add_property(_yield_record(value=290.0, source_id="lot-A"))
        store.add_property(_yield_record(value=260.0, source_id="sup-A"))
        records = store.query(StoreQuery(material_id="aa6061_t6", name="yield_strength"))
        # Both records are retained, not collapsed to a single averaged value.
        values = sorted(r.value for r in records)
        assert values == [260.0, 290.0]

    def test_resolve_property_picks_higher_authority(self) -> None:
        """resolve_property returns the highest-authority observation."""
        store = PropertyStore()
        store.add_source(_lot_source("lot-A"))
        store.add_source(_supplier_source("sup-A"))
        store.add_source(_handbook_source("hb-A"))
        store.add_property(_yield_record(value=290.0, source_id="lot-A"))
        store.add_property(_yield_record(value=260.0, source_id="sup-A"))
        store.add_property(_yield_record(value=250.0, source_id="hb-A"))
        resolved = store.resolve_property(StoreQuery(material_id="aa6061_t6", name="yield_strength"))
        assert resolved is not None
        # Lot wins over supplier/handbook even though value differs.
        assert resolved.value == 290.0
        assert resolved.source_id == "lot-A"
        # Lower-tier alternatives are surfaced, not discarded.
        assert len(resolved.alternatives) == 2

    def test_resolve_property_skips_retracted_source(self) -> None:
        """A retracted source is not chosen even if it would otherwise win."""
        reg = SourceRegistry(now=_now)
        reg.add(_lot_source("lot-bad"))
        reg.add(_supplier_source("sup-good"))
        reg.supersede(older_id="lot-bad", newer_id="sup-good", reason="retracted by publisher")
        store = PropertyStore(registry=reg)
        store.add_property(_yield_record(value=400.0, source_id="lot-bad"))
        store.add_property(_yield_record(value=276.0, source_id="sup-good"))
        resolved = store.resolve_property(StoreQuery(material_id="aa6061_t6", name="yield_strength"))
        assert resolved is not None
        assert resolved.source_id == "sup-good"
        assert resolved.value == 276.0


# ─── PropertyRecord / PropertyStore ───────────────────────────────────────────


class TestPropertyStore:
    def test_record_with_empty_conditions_flagged_insufficient_context(self) -> None:
        """MATE-SAFE-003: missing condition metadata is insufficient_context."""
        rec = PropertyRecord(
            record_id="rec-bare",
            material_id="aa6061_t6",
            name="yield_strength",
            value=270.0,
            unit="MPa",
            basis="yield",
            method="ASTM B209",
            uncertainty=10.0,
            conditions={},
            source_id="sup-A",
        )
        assert rec.state == "insufficient_context"

    def test_resolve_property_flagged_when_only_insufficient_context_available(self) -> None:
        """resolve_property surfaces the insufficient_context state on the result."""
        store = PropertyStore()
        store.add_source(_supplier_source("sup-A"))
        store.add_property(_yield_record(value=276.0, source_id="sup-A", conditions={}))
        resolved = store.resolve_property(StoreQuery(material_id="aa6061_t6", name="yield_strength"))
        assert resolved is not None
        assert resolved.state == "insufficient_context"

    def test_query_by_condition_filters_records(self) -> None:
        """Query filters records whose conditions do not match the requested set."""
        store = PropertyStore()
        store.add_source(_supplier_source("sup-A"))
        store.add_property(
            _yield_record(value=276.0, source_id="sup-A", conditions={"product_form": "sheet", "temper": "T6"})
        )
        store.add_property(
            _yield_record(value=200.0, source_id="sup-A", conditions={"product_form": "bar", "temper": "T6"})
        )
        results = store.query(
            StoreQuery(
                material_id="aa6061_t6",
                name="yield_strength",
                conditions={"product_form": "sheet"},
            )
        )
        assert len(results) == 1
        assert results[0].conditions["product_form"] == "sheet"

    def test_resolve_unknown_material_returns_none(self) -> None:
        store = PropertyStore()
        store.add_source(_supplier_source("sup-A"))
        store.add_property(_yield_record(value=276.0, source_id="sup-A"))
        resolved = store.resolve_property(StoreQuery(material_id="does-not-exist", name="yield_strength"))
        assert resolved is None
