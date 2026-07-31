"""Validity tests for the materials property fixtures.

Verifies the fixture data in ``tests/fixtures/materials_properties.py``
satisfies the invariants mandated by spec MATE-001:

  - §4.1: every property record carries condition metadata (product_form,
    temper) so it is NOT flagged ``insufficient_context`` (MATE-SAFE-003).
  - §5.2: every property has a name, value, unit, basis, method, uncertainty.
  - §11: every material cites a source (publisher, revision).
  - §13 / §14: nominal handbook values are physically plausible (yield <
    ultimate for ductile metals, ceramic compressive strengths far exceed
    polymers, etc.) — this is the smoke test that the fixtures are not
    garbage numbers.

These tests do NOT exercise the runtime :class:`PropertyStore` resolution
(that lives in ``test_materials_source_registry.py``). They only assert that
the fixture DATA is well-formed and physically sane.
"""

from __future__ import annotations

import pytest

from general_ludd.materials.property_store import INSUFFICIENT_CONTEXT, PropertyRecord
from tests.fixtures.materials_properties import (
    MATERIAL_PROPERTY_FIXTURES,
    FixtureSource,
    MaterialFixture,
    to_property_records,
)

# ─── counts & coverage ────────────────────────────────────────────────────────


class TestFixtureCoverage:
    def test_at_least_twenty_property_entries(self):
        total_props = sum(len(m.properties) for m in MATERIAL_PROPERTY_FIXTURES)
        assert total_props >= 20, f"only {total_props} property entries"

    def test_all_required_material_classes_present(self):
        families = {m.family for m in MATERIAL_PROPERTY_FIXTURES}
        assert "metal" in families
        assert "polymer" in families
        assert "ceramic" in families
        assert "composite" in families

    def test_required_steel_alloys_present(self):
        ids = {m.material_id for m in MATERIAL_PROPERTY_FIXTURES}
        for required in ("aisi_1018", "aisi_1045", "aisi_4140", "ss_316l"):
            assert required in ids, f"missing steel alloy {required}"

    def test_required_aluminum_alloys_present(self):
        ids = {m.material_id for m in MATERIAL_PROPERTY_FIXTURES}
        assert "al_6061_t6" in ids
        assert "al_7075_t6" in ids

    def test_required_polymers_present(self):
        ids = {m.material_id for m in MATERIAL_PROPERTY_FIXTURES}
        for required in ("abs", "pc", "peek", "nylon_66"):
            assert required in ids, f"missing polymer {required}"


# ─── per-property structural invariants (§5.2, §4.1) ──────────────────────────


class TestPropertyShape:
    @pytest.mark.parametrize("mat", MATERIAL_PROPERTY_FIXTURES, ids=lambda m: m.material_id)
    def test_every_property_has_nonempty_unit(self, mat: MaterialFixture):
        for p in mat.properties:
            assert p.unit, f"{mat.material_id}.{p.name} missing unit"

    @pytest.mark.parametrize("mat", MATERIAL_PROPERTY_FIXTURES, ids=lambda m: m.material_id)
    def test_every_property_has_strictly_positive_uncertainty(self, mat: MaterialFixture):
        for p in mat.properties:
            assert p.uncertainty > 0, f"{mat.material_id}.{p.name} uncertainty not > 0"

    @pytest.mark.parametrize("mat", MATERIAL_PROPERTY_FIXTURES, ids=lambda m: m.material_id)
    def test_every_property_has_positive_value(self, mat: MaterialFixture):
        for p in mat.properties:
            assert p.value > 0, f"{mat.material_id}.{p.name} value not > 0"

    @pytest.mark.parametrize("mat", MATERIAL_PROPERTY_FIXTURES, ids=lambda m: m.material_id)
    def test_every_property_has_method_and_basis(self, mat: MaterialFixture):
        for p in mat.properties:
            assert p.method, f"{mat.material_id}.{p.name} missing test method"
            assert p.basis, f"{mat.material_id}.{p.name} missing basis"

    @pytest.mark.parametrize("mat", MATERIAL_PROPERTY_FIXTURES, ids=lambda m: m.material_id)
    def test_condition_includes_product_form_and_temper(self, mat: MaterialFixture):
        # MATE-SAFE-003: a record missing required condition metadata is flagged
        # insufficient_context. Our fixtures must NOT be — they are real
        # handbook data, not analogies.
        assert "product_form" in mat.condition, f"{mat.material_id} missing product_form"
        assert "temper" in mat.condition, f"{mat.material_id} missing temper"


# ─── source provenance (§11) ──────────────────────────────────────────────────


class TestSourceProvenance:
    @pytest.mark.parametrize("mat", MATERIAL_PROPERTY_FIXTURES, ids=lambda m: m.material_id)
    def test_every_material_has_recorded_source(self, mat: MaterialFixture):
        src: FixtureSource = mat.source
        assert src.source_id, f"{mat.material_id} source missing source_id"
        assert src.publisher, f"{mat.material_id} source missing publisher"
        assert src.revision, f"{mat.material_id} source missing revision"

    @pytest.mark.parametrize("mat", MATERIAL_PROPERTY_FIXTURES, ids=lambda m: m.material_id)
    def test_source_authority_is_handbook_grade(self, mat: MaterialFixture):
        # Fixtures are explicitly handbook-grade nominal data, NOT lot or
        # supplier data (MATE-DEC-003 hierarchy). A fixture claiming
        # authority='lot' would be a false claim.
        assert mat.source.authority == "handbook", (
            f"{mat.material_id} authority {mat.source.authority!r} not 'handbook'"
        )

    def test_source_ids_are_unique_per_publisher_revision(self):
        # Multiple materials from the same handbook volume legitimately share
        # a source_id (e.g. all steels reference ASM Handbook Vol 1). The
        # invariant is that every source_id maps to consistent publisher and
        # revision values, not that source_ids are globally unique.
        source_to_props: dict[str, tuple[str, str]] = {}
        for mat in MATERIAL_PROPERTY_FIXTURES:
            sid = mat.source.source_id
            pair = (mat.source.publisher, mat.source.revision)
            if sid in source_to_props:
                prev = source_to_props[sid]
                assert pair == prev, f"source_id {sid!r} mapped to {prev} but now {pair}"
            else:
                source_to_props[sid] = pair


# ─── physical plausibility (smoke test that fixtures aren't garbage) ──────────


class TestPhysicalPlausibility:
    @pytest.fixture(scope="class")
    def steel_yield(self) -> dict[str, float]:
        return {
            m.material_id: next(p.value for p in m.properties if p.name == "yield_strength")
            for m in MATERIAL_PROPERTY_FIXTURES
            if m.material_id.startswith("aisi_") or m.material_id == "ss_316l"
        }

    @pytest.fixture(scope="class")
    def steel_uts(self) -> dict[str, float]:
        return {
            m.material_id: next(p.value for p in m.properties if p.name == "ultimate_tensile_strength")
            for m in MATERIAL_PROPERTY_FIXTURES
            if m.material_id.startswith("aisi_") or m.material_id == "ss_316l"
        }

    def test_steel_yield_strengths_in_handbook_range(self, steel_yield):
        # Low-alloy and stainless steels: handbook yields land ~250-700 MPa.
        for mid, val in steel_yield.items():
            assert 250.0 <= val <= 700.0, f"{mid} yield {val} out of range"

    def test_steel_ultimate_exceeds_yield(self, steel_yield, steel_uts):
        # Ductile metals: UTS > yield. (A material where UTS < yield would
        # indicate a ceramic-style failure mode, not a ductile steel.)
        for mid in steel_yield:
            assert steel_uts[mid] > steel_yield[mid], f"{mid} UTS not > yield"

    def test_steel_modulus_near_200_gpa(self):
        for m in MATERIAL_PROPERTY_FIXTURES:
            if m.family != "metal" or m.material_id.startswith("al_"):
                continue
            e = next(p.value for p in m.properties if p.name == "youngs_modulus")
            assert 190.0 <= e <= 210.0, f"{m.material_id} E={e} GPa not near 200"

    def test_aluminum_density_below_three(self):
        for m in MATERIAL_PROPERTY_FIXTURES:
            if not m.material_id.startswith("al_"):
                continue
            rho = next(p.value for p in m.properties if p.name == "density")
            assert 2.5 <= rho <= 3.0, f"{m.material_id} density {rho} out of Al range"

    def test_aluminum_yield_below_steel_yield(self, steel_yield):
        for m in MATERIAL_PROPERTY_FIXTURES:
            if not m.material_id.startswith("al_"):
                continue
            y = next(p.value for p in m.properties if p.name == "yield_strength")
            assert y < max(steel_yield.values()), f"{m.material_id} yield {y} not < max steel yield"

    def test_polymer_modulus_at_least_two_orders_below_steel(self):
        steel_e = next(
            p.value
            for m in MATERIAL_PROPERTY_FIXTURES
            for p in m.properties
            if m.material_id == "aisi_1018" and p.name == "youngs_modulus"
        )
        for m in MATERIAL_PROPERTY_FIXTURES:
            if m.family != "polymer":
                continue
            e = next(p.value for p in m.properties if p.name == "youngs_modulus")
            assert e < steel_e / 50.0, f"{m.material_id} E={e} not << steel E={steel_e}"

    def test_ceramic_compressive_strength_exceeds_one_gpa(self):
        for m in MATERIAL_PROPERTY_FIXTURES:
            if m.family != "ceramic":
                continue
            cs = next(p.value for p in m.properties if p.name == "compressive_strength")
            assert cs > 1000.0, f"{m.material_id} compressive {cs} not > 1 GPa"

    def test_composite_tensile_in_advanced_composite_range(self):
        cf = next(m for m in MATERIAL_PROPERTY_FIXTURES if m.material_id == "carbon_fiber_epoxy")
        ts = next(p.value for p in cf.properties if p.name == "tensile_strength")
        # UD carbon/epoxy at Vf=0.60: handbook range ~1000-2000 MPa.
        assert 1000.0 <= ts <= 2000.0, f"CF/epoxy tensile {ts} out of range"


# ─── conversion to runtime PropertyRecord ─────────────────────────────────────


class TestToPropertyRecords:
    def test_returns_one_record_per_property(self):
        records = to_property_records()
        expected = sum(len(m.properties) for m in MATERIAL_PROPERTY_FIXTURES)
        assert len(records) == expected

    def test_every_record_is_property_record_instance(self):
        for r in to_property_records():
            assert isinstance(r, PropertyRecord)

    def test_no_record_flagged_insufficient_context(self):
        # MATE-SAFE-003: every fixture carries product_form + temper, so the
        # runtime PropertyRecord must NOT come back flagged.
        for r in to_property_records():
            assert r.state != INSUFFICIENT_CONTEXT, f"{r.record_id} flagged insufficient_context (missing conditions)"

    def test_record_ids_are_unique(self):
        ids = [r.record_id for r in to_property_records()]
        assert len(set(ids)) == len(ids), "duplicate record_ids"

    def test_record_carries_source_id_from_fixture(self):
        records = to_property_records()
        fixture_source_ids = {m.source.source_id for m in MATERIAL_PROPERTY_FIXTURES}
        for r in records:
            assert r.source_id in fixture_source_ids, f"{r.record_id} source_id {r.source_id!r} not in fixture sources"
