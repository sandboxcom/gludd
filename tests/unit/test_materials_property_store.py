"""Deep tests for PropertyStore, PropertyRecord, StoreQuery, ResolvedProperty.

Covers the full surface of property_store.py: record validation,
store mutation/query, hierarchy resolution, retraction filtering,
condition matching, unregistered sources, and edge cases.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from general_ludd.materials.property_store import (
    INSUFFICIENT_CONTEXT,
    PropertyRecord,
    PropertyStore,
    ResolvedProperty,
    StoreQuery,
)
from general_ludd.materials.source_registry import (
    Authority,
    SourceEntry,
)


def _now() -> datetime:
    return datetime(2026, 1, 1, tzinfo=UTC)


def _source(source_id: str, authority: Authority = Authority.SUPPLIER) -> SourceEntry:
    return SourceEntry(
        source_id=source_id,
        authority=authority,
        uri=f"urn:test:{source_id}",
        revision="2025-01",
        retrieval_time=_now(),
        content_digest=f"sha256:{source_id}",
        license="test",
        applicability={"material_id": "aa6061_t6"},
        uncertainty=10.0,
        review_expiry=_now() + timedelta(days=365),
    )


def _rec(
    record_id: str = "rec-A",
    material_id: str = "aa6061_t6",
    name: str = "yield_strength",
    value: float = 270.0,
    unit: str = "MPa",
    basis: str = "yield",
    method: str = "ASTM B209",
    uncertainty: float = 10.0,
    conditions: dict | None = None,
    source_id: str = "sup-A",
    state: str = "ok",
) -> PropertyRecord:
    if conditions is None:
        conditions = {"product_form": "sheet", "temper": "T6"}
    return PropertyRecord(
        record_id=record_id,
        material_id=material_id,
        name=name,
        value=value,
        unit=unit,
        basis=basis,
        method=method,
        uncertainty=uncertainty,
        conditions=conditions,
        source_id=source_id,
        state=state,
    )


# ─── PropertyRecord ────────────────────────────────────────────────────────


class TestPropertyRecord:
    def test_empty_record_id_raises(self):
        with pytest.raises(ValueError, match="record_id must be non-empty"):
            PropertyRecord(
                record_id="",
                material_id="aa6061_t6",
                name="yield_strength",
                value=270.0,
                unit="MPa",
                basis="yield",
                method="ASTM B209",
                uncertainty=10.0,
                conditions={"product_form": "sheet"},
                source_id="sup-A",
            )

    def test_empty_material_id_raises(self):
        with pytest.raises(ValueError, match="material_id must be non-empty"):
            PropertyRecord(
                record_id="rec-A",
                material_id="",
                name="yield_strength",
                value=270.0,
                unit="MPa",
                basis="yield",
                method="ASTM B209",
                uncertainty=10.0,
                conditions={"product_form": "sheet"},
                source_id="sup-A",
            )

    def test_empty_name_raises(self):
        with pytest.raises(ValueError, match="name must be non-empty"):
            PropertyRecord(
                record_id="rec-A",
                material_id="aa6061_t6",
                name="",
                value=270.0,
                unit="MPa",
                basis="yield",
                method="ASTM B209",
                uncertainty=10.0,
                conditions={"product_form": "sheet"},
                source_id="sup-A",
            )

    def test_negative_uncertainty_raises(self):
        with pytest.raises(ValueError, match="uncertainty must be non-negative"):
            PropertyRecord(
                record_id="rec-A",
                material_id="aa6061_t6",
                name="yield_strength",
                value=270.0,
                unit="MPa",
                basis="yield",
                method="ASTM B209",
                uncertainty=-1.0,
                conditions={"product_form": "sheet"},
                source_id="sup-A",
            )

    def test_zero_uncertainty_allowed(self):
        rec = _rec(uncertainty=0.0)
        assert rec.uncertainty == 0.0
        assert rec.state == "ok"

    def test_empty_conditions_flags_insufficient_context(self):
        rec = _rec(conditions={})
        assert rec.state == INSUFFICIENT_CONTEXT

    def test_nonempty_conditions_preserves_state(self):
        rec = _rec(conditions={"product_form": "sheet"})
        assert rec.state == "ok"

    def test_post_init_always_flags_empty_conditions_regardless_of_state(self):
        rec = PropertyRecord(
            record_id="rec-A",
            material_id="aa6061_t6",
            name="yield_strength",
            value=270.0,
            unit="MPa",
            basis="yield",
            method="ASTM B209",
            uncertainty=10.0,
            conditions={},
            source_id="sup-A",
            state="superseded",
        )
        assert rec.state == INSUFFICIENT_CONTEXT

    def test_frozen_dataclass_prevents_mutation(self):
        rec = _rec()
        try:
            rec.value = 999.0  # type: ignore[misc]
            raise AssertionError("expected FrozenInstanceError")
        except BaseException:
            assert rec.value == 270.0

    def test_field_values_round_trip(self):
        rec = _rec(value=310.0, unit="ksi", basis="ultimate")
        assert rec.value == 310.0
        assert rec.unit == "ksi"
        assert rec.basis == "ultimate"
        assert rec.method == "ASTM B209"
        assert rec.conditions == {"product_form": "sheet", "temper": "T6"}


# ─── StoreQuery ─────────────────────────────────────────────────────────────


class TestStoreQuery:
    def test_default_empty_conditions(self):
        q = StoreQuery(material_id="aa6061_t6", name="yield_strength")
        assert q.conditions == {}

    def test_explicit_conditions(self):
        q = StoreQuery(
            material_id="aa6061_t6",
            name="yield_strength",
            conditions={"product_form": "sheet"},
        )
        assert q.conditions == {"product_form": "sheet"}


# ─── PropertyStore mutation ─────────────────────────────────────────────────


class TestPropertyStoreMutation:
    def test_add_property_appends(self):
        store = PropertyStore()
        store.add_source(_source("sup-A"))
        store.add_property(_rec(record_id="r1"))
        store.add_property(_rec(record_id="r2"))
        results = store.query(StoreQuery(material_id="aa6061_t6", name="yield_strength"))
        assert len(results) == 2

    def test_add_property_ignores_duplicate_record_ids(self):
        store = PropertyStore()
        store.add_source(_source("sup-A"))
        store.add_property(_rec(record_id="r1", value=270.0))
        store.add_property(_rec(record_id="r1", value=999.0))
        results = store.query(StoreQuery(material_id="aa6061_t6", name="yield_strength"))
        assert len(results) == 1
        assert results[0].value == 270.0

    def test_add_source_delegates_to_registry(self):
        store = PropertyStore()
        entry = _source("sup-A")
        store.add_source(entry)
        retrieved = store.registry.get("sup-A")
        assert retrieved.source_id == "sup-A"


# ─── PropertyStore.query ────────────────────────────────────────────────────


class TestPropertyStoreQuery:
    def test_filters_by_material_id(self):
        store = PropertyStore()
        store.add_source(_source("sup-A"))
        store.add_property(_rec(record_id="r1", material_id="aa6061_t6"))
        store.add_property(_rec(record_id="r2", material_id="ti6al4v"))
        results = store.query(StoreQuery(material_id="aa6061_t6", name="yield_strength"))
        assert len(results) == 1
        assert results[0].material_id == "aa6061_t6"

    def test_filters_by_name(self):
        store = PropertyStore()
        store.add_source(_source("sup-A"))
        store.add_property(_rec(record_id="r1", name="yield_strength", value=270.0))
        store.add_property(_rec(record_id="r2", name="tensile_strength", value=310.0))
        results = store.query(StoreQuery(material_id="aa6061_t6", name="yield_strength"))
        assert len(results) == 1
        assert results[0].name == "yield_strength"

    def test_excludes_retracted_sources_by_default(self):
        store = PropertyStore()
        store.add_source(_source("sup-retracted"))
        store.add_source(_source("sup-valid"))
        store.registry.supersede(older_id="sup-retracted", newer_id="sup-valid", reason="retracted")
        store.add_property(_rec(record_id="r1", source_id="sup-retracted", value=999.0))
        store.add_property(_rec(record_id="r2", source_id="sup-valid", value=270.0))
        results = store.query(StoreQuery(material_id="aa6061_t6", name="yield_strength"))
        assert len(results) == 1
        assert results[0].source_id == "sup-valid"

    def test_include_retracted_true_returns_retracted_records(self):
        store = PropertyStore()
        store.add_source(_source("sup-retracted"))
        store.add_source(_source("sup-valid"))
        store.registry.supersede(older_id="sup-retracted", newer_id="sup-valid", reason="retracted")
        store.add_property(_rec(record_id="r1", source_id="sup-retracted", value=999.0))
        store.add_property(_rec(record_id="r2", source_id="sup-valid", value=270.0))
        results = store.query(
            StoreQuery(material_id="aa6061_t6", name="yield_strength"),
            include_retracted=True,
        )
        assert len(results) == 2

    def test_condition_filter_matches_exact(self):
        store = PropertyStore()
        store.add_source(_source("sup-A"))
        store.add_property(
            _rec(
                record_id="r1",
                value=270.0,
                conditions={"product_form": "sheet", "temper": "T6"},
            )
        )
        store.add_property(
            _rec(
                record_id="r2",
                value=200.0,
                conditions={"product_form": "bar", "temper": "T6"},
            )
        )
        results = store.query(
            StoreQuery(
                material_id="aa6061_t6",
                name="yield_strength",
                conditions={"product_form": "sheet"},
            )
        )
        assert len(results) == 1
        assert results[0].value == 270.0

    def test_condition_filter_multi_key_all_must_match(self):
        store = PropertyStore()
        store.add_source(_source("sup-A"))
        store.add_property(
            _rec(
                record_id="r1",
                conditions={"product_form": "sheet", "temper": "T6", "thickness": "1mm"},
            )
        )
        store.add_property(
            _rec(
                record_id="r2",
                conditions={"product_form": "sheet", "temper": "T651"},
            )
        )
        results = store.query(
            StoreQuery(
                material_id="aa6061_t6",
                name="yield_strength",
                conditions={"product_form": "sheet", "temper": "T6"},
            )
        )
        assert len(results) == 1
        assert results[0].record_id == "r1"

    def test_query_empty_store_returns_empty_list(self):
        store = PropertyStore()
        results = store.query(StoreQuery(material_id="aa6061_t6", name="yield_strength"))
        assert results == []

    def test_condition_key_missing_on_record_excludes_it(self):
        store = PropertyStore()
        store.add_source(_source("sup-A"))
        store.add_property(_rec(record_id="r1", conditions={"product_form": "sheet"}))
        store.add_property(_rec(record_id="r2", conditions={"product_form": "sheet", "extra_key": "value"}))
        results = store.query(
            StoreQuery(
                material_id="aa6061_t6",
                name="yield_strength",
                conditions={"product_form": "sheet", "extra_key": "value"},
            )
        )
        assert len(results) == 1
        assert results[0].record_id == "r2"


# ─── PropertyStore.resolve_property ─────────────────────────────────────────


class TestPropertyStoreResolve:
    def test_no_matching_records_returns_none(self):
        store = PropertyStore()
        resolved = store.resolve_property(StoreQuery(material_id="aa6061_t6", name="yield_strength"))
        assert resolved is None

    def test_highest_authority_wins(self):
        store = PropertyStore()
        store.add_source(_source("lot-A", Authority.LOT))
        store.add_source(_source("sup-A", Authority.SUPPLIER))
        store.add_source(_source("hb-A", Authority.HANDBOOK))
        store.add_property(_rec(record_id="r-lot", source_id="lot-A", value=290.0))
        store.add_property(_rec(record_id="r-sup", source_id="sup-A", value=260.0))
        store.add_property(_rec(record_id="r-hb", source_id="hb-A", value=250.0))
        resolved = store.resolve_property(StoreQuery(material_id="aa6061_t6", name="yield_strength"))
        assert resolved is not None
        assert resolved.value == 290.0
        assert resolved.source_id == "lot-A"

    def test_alternatives_surface_lower_tiers(self):
        store = PropertyStore()
        store.add_source(_source("lot-A", Authority.LOT))
        store.add_source(_source("sup-A", Authority.SUPPLIER))
        store.add_source(_source("hb-A", Authority.HANDBOOK))
        store.add_property(_rec(record_id="r-lot", source_id="lot-A", value=290.0))
        store.add_property(_rec(record_id="r-sup", source_id="sup-A", value=260.0))
        store.add_property(_rec(record_id="r-hb", source_id="hb-A", value=250.0))
        resolved = store.resolve_property(StoreQuery(material_id="aa6061_t6", name="yield_strength"))
        assert resolved is not None
        assert len(resolved.alternatives) == 2

    def test_retracted_source_excluded_from_resolution(self):
        store = PropertyStore()
        store.add_source(_source("lot-retracted", Authority.LOT))
        store.add_source(_source("sup-good", Authority.SUPPLIER))
        store.registry.supersede(older_id="lot-retracted", newer_id="sup-good", reason="retracted")
        store.add_property(_rec(record_id="r-lot", source_id="lot-retracted", value=400.0))
        store.add_property(_rec(record_id="r-sup", source_id="sup-good", value=276.0))
        resolved = store.resolve_property(StoreQuery(material_id="aa6061_t6", name="yield_strength"))
        assert resolved is not None
        assert resolved.source_id == "sup-good"
        assert resolved.value == 276.0

    def test_unregistered_source_falls_back_to_estimated(self):
        store = PropertyStore()
        store.add_source(_source("sup-A", Authority.SUPPLIER))
        store.add_property(_rec(record_id="r-sup", source_id="sup-A", value=260.0))
        store.add_property(_rec(record_id="r-orphan", source_id="unknown-src", value=150.0))
        resolved = store.resolve_property(StoreQuery(material_id="aa6061_t6", name="yield_strength"))
        assert resolved is not None
        assert resolved.source_id == "sup-A"
        assert len(resolved.alternatives) == 1
        assert resolved.alternatives[0].source_id == "unknown-src"

    def test_only_insufficient_context_record_surfaces_state(self):
        store = PropertyStore()
        store.add_source(_source("sup-A"))
        store.add_property(_rec(record_id="r1", source_id="sup-A", conditions={}))
        resolved = store.resolve_property(StoreQuery(material_id="aa6061_t6", name="yield_strength"))
        assert resolved is not None
        assert resolved.state == INSUFFICIENT_CONTEXT

    def test_single_record_no_alternatives(self):
        store = PropertyStore()
        store.add_source(_source("sup-A"))
        store.add_property(_rec(record_id="r1", source_id="sup-A"))
        resolved = store.resolve_property(StoreQuery(material_id="aa6061_t6", name="yield_strength"))
        assert resolved is not None
        assert len(resolved.alternatives) == 0


# ─── ResolvedProperty accessors ─────────────────────────────────────────────


class TestResolvedProperty:
    def test_value_delegates_to_record(self):
        rec = _rec(value=310.0)
        rp = ResolvedProperty(record=rec, alternatives=[])
        assert rp.value == 310.0

    def test_unit_delegates_to_record(self):
        rec = _rec(unit="ksi")
        rp = ResolvedProperty(record=rec, alternatives=[])
        assert rp.unit == "ksi"

    def test_source_id_delegates_to_record(self):
        rec = _rec(source_id="hb-abc")
        rp = ResolvedProperty(record=rec, alternatives=[])
        assert rp.source_id == "hb-abc"

    def test_state_delegates_to_record(self):
        rec = _rec(state="superseded")
        rp = ResolvedProperty(record=rec, alternatives=[])
        assert rp.state == "superseded"

    def test_alternatives_preserved(self):
        rec1 = _rec(record_id="r1", value=290.0)
        rec2 = _rec(record_id="r2", value=260.0)
        rec3 = _rec(record_id="r3", value=250.0)
        rp = ResolvedProperty(record=rec1, alternatives=[rec2, rec3])
        assert len(rp.alternatives) == 2
        assert rp.alternatives[0].record_id == "r2"


# ─── Integration: full resolution pipeline ──────────────────────────────────


class TestFullResolutionPipeline:
    def test_mixed_authorities_condition_filtered(self):
        store = PropertyStore()
        store.add_source(_source("lot-A", Authority.LOT))
        store.add_source(_source("sup-A", Authority.SUPPLIER))
        store.add_property(
            _rec(
                record_id="r-lot-sheet",
                source_id="lot-A",
                value=290.0,
                conditions={"product_form": "sheet", "temper": "T6"},
            )
        )
        store.add_property(
            _rec(
                record_id="r-lot-bar",
                source_id="lot-A",
                value=280.0,
                conditions={"product_form": "bar", "temper": "T6"},
            )
        )
        store.add_property(
            _rec(
                record_id="r-sup-sheet",
                source_id="sup-A",
                value=260.0,
                conditions={"product_form": "sheet", "temper": "T6"},
            )
        )
        resolved = store.resolve_property(
            StoreQuery(
                material_id="aa6061_t6",
                name="yield_strength",
                conditions={"product_form": "bar"},
            )
        )
        assert resolved is not None
        assert resolved.value == 280.0
        assert len(resolved.alternatives) == 0

    def test_multiple_properties_different_names(self):
        store = PropertyStore()
        store.add_source(_source("sup-A"))
        store.add_property(_rec(record_id="r1", name="yield_strength", value=270.0))
        store.add_property(_rec(record_id="r2", name="tensile_strength", value=310.0))
        ys = store.resolve_property(StoreQuery(material_id="aa6061_t6", name="yield_strength"))
        ts = store.resolve_property(StoreQuery(material_id="aa6061_t6", name="tensile_strength"))
        assert ys is not None and ys.value == 270.0
        assert ts is not None and ts.value == 310.0

    def test_estimated_authority_always_lowest(self):
        store = PropertyStore()
        store.add_source(_source("est-A", Authority.ESTIMATED))
        store.add_source(_source("hb-A", Authority.HANDBOOK))
        store.add_property(_rec(record_id="r-est", source_id="est-A", value=200.0))
        store.add_property(_rec(record_id="r-hb", source_id="hb-A", value=250.0))
        resolved = store.resolve_property(StoreQuery(material_id="aa6061_t6", name="yield_strength"))
        assert resolved is not None
        assert resolved.source_id == "hb-A"
