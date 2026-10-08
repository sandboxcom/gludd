"""Tests for material_selection: screening, ranking, and property resolution
(spec MATE-001 §7, MATE-DEC-002, MATE-DEC-003).

Covers resolve_property, screen_candidates, rank_candidates with deep edge
cases: overrides, insufficient_context, missing properties, malformed inputs,
unit normalization, tradeoff profiles, performance indices, conservative and
sensitivity margins, and the fail-closed guarantee for unrankable queries.
"""

from __future__ import annotations

from typing import Any

import pytest

from general_ludd.materials.material_selection import (
    DATA_TIERS,
    rank_candidates,
    resolve_property,
    screen_candidates,
)


class TestResolveProperty:
    def test_resolve_known_material_handbook_tier(self):
        rec, tier = resolve_property("aisi_1045", "yield_strength")
        assert rec is not None
        assert tier == "handbook"
        assert rec["value"] == 310.0

    def test_resolve_value_or_range_converted_to_value(self):
        rec, _tier = resolve_property("aa6061_t6", "yield_strength")
        assert rec is not None
        assert rec["value"] == 276.0
        assert "tier" in rec

    def test_resolve_unknown_material_returns_none(self):
        rec, tier = resolve_property("unobtanium", "yield_strength")
        assert rec is None
        assert tier is None

    def test_resolve_missing_property_returns_none(self):
        rec, tier = resolve_property("aisi_1045", "fracture_toughness")
        assert rec is None
        assert tier is None

    def test_resolve_with_override_supplier_tier(self):
        overrides: dict[str, dict[str, dict[str, Any]]] = {
            "pa66_gf30": {"yield_strength": {"value": 195.0, "unit": "MPa"}},
        }
        rec, tier = resolve_property("pa66_gf30", "yield_strength", overrides)
        assert rec is not None
        assert rec["value"] == 195.0
        assert tier == "supplier"

    def test_resolve_strips_override_without_value_defaults_to_handbook(self):
        overrides: dict[str, dict[str, dict[str, Any]]] = {
            "aisi_1045": {"youngs_modulus": {"value": 205.0}},
        }
        rec, tier = resolve_property("aisi_1045", "youngs_modulus", overrides)
        assert rec is not None
        assert rec["value"] == 205.0
        assert tier == "supplier"

        result2 = resolve_property("aisi_1045", "ultimate_strength", overrides)
        assert result2 is not None and result2[0] is not None
        assert result2[1] == "handbook"
        assert result2[0]["value"] == 565.0

    def test_resolve_empty_material_id_returns_none(self):
        rec, tier = resolve_property("", "yield_strength")
        assert rec is None
        assert tier is None

    def test_resolve_none_overrides_still_resolves_handbook(self):
        rec, tier = resolve_property("abs", "yield_strength", None)
        assert rec is not None
        assert tier == "handbook"
        assert rec["value"] == 45.0


class TestScreenCandidates:
    def test_screen_all_candidates_default_list(self):
        result = screen_candidates({"load_cases": [{"type": "tensile", "magnitude": 50.0, "unit": "MPa"}]})
        assert result["verdict"] == "candidate"
        survived = [c for c in result["candidates"] if c["state"] == "survived"]
        assert len(survived) >= 2

    def test_screen_unknown_material_rejected(self):
        result = screen_candidates(
            {"load_cases": [{"type": "tensile", "magnitude": 50.0, "unit": "MPa"}]},
            candidates=["unobtanium"],
        )
        assert result["verdict"] == "infeasible"
        assert result["candidates"][0]["state"] == "rejected"
        assert "unknown_material" in result["candidates"][0]["violations"]

    def test_screen_missing_property_rejected(self):
        result = screen_candidates(
            {"load_cases": [{"type": "tensile", "magnitude": 50.0, "unit": "MPa"}]},
            candidates=["epoxy_cast"],
        )
        c = result["candidates"][0]
        assert c["state"] == "rejected"
        assert any("no yield_strength" in v for v in c["violations"])

    def test_screen_insufficient_context_rejected(self):
        result = screen_candidates(
            {"load_cases": [{"type": "yield", "magnitude": 30.0, "unit": "MPa"}]},
            candidates=["abs"],
        )
        c = result["candidates"][0]
        assert c["state"] == "rejected"
        assert any("insufficient_context" in v for v in c["violations"])

    def test_screen_capacity_below_applied_rejected(self):
        result = screen_candidates(
            {"load_cases": [{"type": "yield", "magnitude": 999.0, "unit": "MPa"}]},
            candidates=["aisi_1045"],
        )
        c = result["candidates"][0]
        assert c["state"] == "rejected"
        assert any("hard_constraint" in v for v in c["violations"])

    def test_screen_applied_zero_sets_insufficient_data_margin(self):
        result = screen_candidates(
            {"load_cases": [{"type": "tensile", "magnitude": 0.0, "unit": "MPa"}]},
            candidates=["aisi_1045"],
        )
        margins = result["candidates"][0]["requirement_margins"]
        assert any(m["state"] == "insufficient_data" for m in margins)

    def test_screen_non_list_load_cases_falls_back_to_empty(self):
        result = screen_candidates({"load_cases": "not_a_list"}, candidates=["aisi_1045"])
        assert result["verdict"] == "candidate"
        assert result["candidates"][0]["state"] == "survived"

    def test_screen_empty_candidates_list(self):
        result = screen_candidates(
            {"load_cases": [{"type": "tensile", "magnitude": 50.0, "unit": "MPa"}]},
            candidates=[],
        )
        assert result["verdict"] == "infeasible"
        assert result["candidates"] == []

    def test_screen_unknown_load_type_ignored(self):
        result = screen_candidates(
            {"load_cases": [{"type": "torsion", "magnitude": 50.0, "unit": "MPa"}]},
            candidates=["aisi_1045"],
        )
        c = result["candidates"][0]
        assert c["state"] == "survived"

    def test_screen_mixed_survivors_and_rejects(self):
        result = screen_candidates(
            {"load_cases": [{"type": "tensile", "magnitude": 300.0, "unit": "MPa"}]},
            candidates=["aa6061_t6", "aisi_1045"],
        )
        statuses = {c["material_id"]: c["state"] for c in result["candidates"]}
        assert statuses["aa6061_t6"] == "rejected"
        assert statuses["aisi_1045"] == "survived"

    def test_screen_margins_include_capacity_and_applied(self):
        result = screen_candidates(
            {"load_cases": [{"type": "tensile", "magnitude": 100.0, "unit": "MPa"}]},
            candidates=["aisi_1045"],
        )
        m = result["candidates"][0]["requirement_margins"][0]
        assert m["state"] == "pass"
        assert m["capacity"] == 310.0
        assert m["applied"] == 100.0
        assert m["margin"] == pytest.approx((310.0 - 100.0) / 100.0)

    def test_screen_no_load_cases_all_survive(self):
        result = screen_candidates({"load_cases": []}, candidates=["aisi_1045"])
        assert result["verdict"] == "candidate"
        c = result["candidates"][0]
        assert c["state"] == "survived"


class TestRankCandidates:
    def test_rank_returns_three_case_lists(self):
        result = rank_candidates(
            {
                "load_cases": [{"type": "yield", "magnitude": 100.0, "unit": "MPa"}],
            }
        )
        assert result["verdict"] == "candidate"
        assert isinstance(result["nominal"], list)
        assert isinstance(result["conservative"], list)
        assert isinstance(result["sensitivity"], list)
        assert len(result["nominal"]) >= 2

    def test_rank_no_load_cases_returns_insufficient_data(self):
        result = rank_candidates({"load_cases": []})
        assert result["verdict"] == "insufficient_data"
        assert result["nominal"] == []

    def test_rank_load_cases_str_insufficient_data(self):
        result = rank_candidates({"load_cases": "unknown"})
        assert result["verdict"] == "insufficient_data"

    def test_rank_load_cases_missing_key_insufficient_data(self):
        result = rank_candidates({})
        assert result["verdict"] == "insufficient_data"

    def test_rank_nominal_margin_positive_for_strong_material(self):
        result = rank_candidates(
            {
                "load_cases": [{"type": "yield", "magnitude": 100.0, "unit": "MPa"}],
                "candidates": ["aisi_1045"],
            }
        )
        entry = result["nominal"][0]
        margin = entry["margins"][0]
        assert margin["state"] == "pass"
        assert margin["margin"] > 0

    def test_rank_conservative_lower_margin_than_nominal(self):
        result = rank_candidates(
            {
                "load_cases": [{"type": "yield", "magnitude": 100.0, "unit": "MPa"}],
                "candidates": ["aisi_1045"],
            }
        )
        nom = result["nominal"][0]["margins"][0]["margin"]
        con = result["conservative"][0]["margins"][0]["margin"]
        assert con is not None and nom is not None
        assert con <= nom

    def test_rank_sensitivity_lower_margin_than_nominal(self):
        result = rank_candidates(
            {
                "load_cases": [{"type": "yield", "magnitude": 100.0, "unit": "MPa"}],
                "candidates": ["aisi_1045"],
            }
        )
        nom = result["nominal"][0]["margins"][0]["margin"]
        sen = result["sensitivity"][0]["margins"][0]["margin"]
        assert sen is not None and nom is not None
        assert sen <= nom

    def test_rank_insufficient_context_property_not_passing(self):
        result = rank_candidates(
            {
                "load_cases": [{"type": "yield", "magnitude": 30.0, "unit": "MPa"}],
                "candidates": ["abs"],
            }
        )
        margins = result["nominal"][0]["margins"]
        assert all(m["state"] != "pass" for m in margins)

    def test_rank_override_changes_margin(self):
        overrides = {
            "pa66_gf30": {"yield_strength": {"value": 500.0, "unit": "MPa"}},
        }
        result = rank_candidates(
            {"load_cases": [{"type": "yield", "magnitude": 100.0, "unit": "MPa"}]},
            candidates=["pa66_gf30"],
            overrides=overrides,
        )
        m = result["nominal"][0]["margins"][0]
        assert m["state"] == "pass"
        assert m["data_tier"] == "supplier"

    def test_rank_specific_candidates_only(self):
        result = rank_candidates(
            {"load_cases": [{"type": "tensile", "magnitude": 80.0, "unit": "MPa"}]},
            candidates=["aisi_1045"],
        )
        assert len(result["nominal"]) == 1
        assert result["nominal"][0]["material_id"] == "aisi_1045"

    def test_rank_tradeoff_profile_has_known_properties(self):
        result = rank_candidates(
            {"load_cases": [{"type": "yield", "magnitude": 100.0, "unit": "MPa"}]},
            candidates=["aisi_1045"],
        )
        tradeoffs = result["nominal"][0]["tradeoffs"]
        assert "yield_strength" in tradeoffs
        assert "youngs_modulus" in tradeoffs
        for name in tradeoffs:
            assert "value" in tradeoffs[name]
            assert "unit" in tradeoffs[name]
            assert "tier" in tradeoffs[name]

    def test_rank_performance_indices_present_when_density_available(self):
        overrides = {
            "aa6061_t6": {"density": {"value": 2.7, "unit": "g/cm^3"}},
        }
        result = rank_candidates(
            {"load_cases": [{"type": "yield", "magnitude": 100.0, "unit": "MPa"}]},
            candidates=["aa6061_t6"],
            overrides=overrides,
        )
        indices = result["nominal"][0]["performance_indices"]
        assert "specific_strength" in indices
        assert "specific_stiffness" in indices

    def test_rank_performance_indices_missing_without_density(self):
        result = rank_candidates(
            {
                "load_cases": [{"type": "yield", "magnitude": 100.0, "unit": "MPa"}],
                "candidates": ["pa66_gf30"],
            }
        )
        indices = result["nominal"][0]["performance_indices"]
        assert indices == {}

    def test_rank_unknown_material_in_candidates_handled(self):
        result = rank_candidates(
            {"load_cases": [{"type": "yield", "magnitude": 50.0, "unit": "MPa"}]},
            candidates=["nonexistent_999"],
        )
        assert len(result["nominal"]) == 1
        assert result["nominal"][0]["material_id"] == "nonexistent_999"
        margins = result["nominal"][0]["margins"]
        assert any(m["state"] == "insufficient_data" for m in margins)

    def test_rank_multiple_load_cases(self):
        result = rank_candidates(
            {
                "load_cases": [
                    {"type": "yield", "magnitude": 100.0, "unit": "MPa"},
                    {"type": "ultimate", "magnitude": 200.0, "unit": "MPa"},
                    {"type": "shear", "magnitude": 50.0, "unit": "MPa"},
                ],
                "candidates": ["aisi_1045"],
            }
        )
        margins = result["nominal"][0]["margins"]
        assert len(margins) == 3

    def test_rank_margin_state_pass_or_fail(self):
        result = rank_candidates(
            {
                "load_cases": [
                    {"type": "yield", "magnitude": 100.0, "unit": "MPa"},
                    {"type": "yield", "magnitude": 999.0, "unit": "MPa", "id": "extreme"},
                ],
                "candidates": ["aisi_1045"],
            }
        )
        margins = {m["requirement_id"]: m["state"] for m in result["nominal"][0]["margins"]}
        assert margins.get("yield") == "pass"
        assert margins.get("extreme") == "fail"

    def test_rank_non_numeric_capacity_insufficient_data(self):
        result = rank_candidates(
            {
                "load_cases": [{"type": "compression", "magnitude": 50.0, "unit": "MPa"}],
                "candidates": ["pa66_gf30"],
            }
        )
        margins = result["nominal"][0]["margins"]
        assert any(m["state"] == "insufficient_data" for m in margins)

    def test_rank_non_numeric_uncertainty_defaults_to_zero(self):
        overrides = {
            "aisi_1045": {"yield_strength": {"value": 310.0, "unit": "MPa", "uncertainty": "n/a"}},
        }
        result = rank_candidates(
            {"load_cases": [{"type": "yield", "magnitude": 100.0, "unit": "MPa"}]},
            candidates=["aisi_1045"],
            overrides=overrides,
        )
        m = result["nominal"][0]["margins"][0]
        assert m["state"] == "pass"


class TestEdgeCases:
    def test_schema_version_set_on_all_results(self):
        for result in [
            screen_candidates({"load_cases": [{"type": "tensile", "magnitude": 50.0, "unit": "MPa"}]}),
            rank_candidates({"load_cases": [{"type": "yield", "magnitude": 50.0, "unit": "MPa"}]}),
        ]:
            assert "schema_version" in result
            assert result["schema_version"].startswith("mate-")

    def test_data_tiers_hierarchy_order(self):
        assert DATA_TIERS == ("lot", "supplier", "handbook", "estimated")

    def test_resolve_property_material_with_multiple_properties(self):
        rec, _tier = resolve_property("aisi_1045", "ultimate_strength")
        assert rec is not None
        assert rec["value"] == 565.0

    def test_screen_candidates_includes_designation_and_family(self):
        result = screen_candidates(
            {"load_cases": [{"type": "tensile", "magnitude": 50.0, "unit": "MPa"}]},
            candidates=["aisi_1045"],
        )
        c = result["candidates"][0]
        assert c["designation"] == "AISI 1045 medium carbon steel"
        assert c["family"] == "metal"

    def test_screen_verdict_with_load_case_id(self):
        result = screen_candidates(
            {
                "load_cases": [{"type": "tensile", "magnitude": 100.0, "unit": "MPa", "id": "LC_primary"}],
                "candidates": ["aisi_1045"],
            }
        )
        margin = result["candidates"][0]["requirement_margins"][0]
        assert margin["requirement_id"] == "LC_primary"
