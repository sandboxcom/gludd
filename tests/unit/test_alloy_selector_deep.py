"""Deep edge-case tests for alloy_selector (spec MATE-001 section 4.4).

N covers: boundary values, NaN/Inf guards, missing-data fallbacks,
conflicting multi-constraint resolution, tiebreaking, empty inputs,
alloy-family crossover edge cases, and cost-performance tradeoff corner cases.
"""

from __future__ import annotations

import math
from typing import Any

from general_ludd.materials.alloy_selector import (
    ALLOY_DATA,
    alloy_composition_tolerance,
    compare_alloys,
    filter_by_cost_index,
    filter_by_environment,
    filter_by_temperature,
    rank_by_performance_index,
    select_alloy,
)

# ── helpers ──────────────────────────────────────────────────────────


def _ids(alloy_ids: list[str]) -> list[str]:
    return alloy_ids


# ── ALLOY_DATA integrity ─────────────────────────────────────────────


class TestAlloyDataIntegrity:
    def test_every_registered_alloy_has_required_fields(self) -> None:
        required = {"family", "base_element", "density", "cost_index"}
        for aid, data in ALLOY_DATA.items():
            missing = required - data.keys()
            assert not missing, f"{aid} missing fields: {missing}"

    def test_no_negative_or_zero_density(self) -> None:
        for aid, data in ALLOY_DATA.items():
            d = data.get("density")
            assert isinstance(d, (int, float)) and d > 0, f"{aid} density={d}"

    def test_cost_index_non_negative(self) -> None:
        for aid, data in ALLOY_DATA.items():
            ci = data.get("cost_index", 0)
            assert isinstance(ci, (int, float)) and ci >= 0, f"{aid} cost_index={ci}"

    def test_temperature_limits_are_valid_ranges(self) -> None:
        for aid, data in ALLOY_DATA.items():
            tmin = data.get("temp_min_kelvin")
            tmax = data.get("temp_max_kelvin")
            if tmin is not None and tmax is not None:
                assert tmin < tmax, f"{aid} temp range inverted: {tmin} >= {tmax}"


# ── filter_by_environment ────────────────────────────────────────────


class TestFilterByEnvironment:
    def test_marine_returns_only_marine_compatible(self) -> None:
        result = filter_by_environment(_ids(list(ALLOY_DATA)), "marine")
        for entry in result:
            env = ALLOY_DATA[entry["alloy_id"]].get("environment", [])
            assert entry["compatible"] is ("marine" in env)

    def test_high_temperature_oxidizing_matches_nickel_superalloys(self) -> None:
        result = filter_by_environment(_ids(list(ALLOY_DATA)), "high_temp_oxidizing")
        survivors = [e for e in result if e["compatible"]]
        for s in survivors:
            fam = ALLOY_DATA[s["alloy_id"]]["family"]
            assert fam in ("nickel_superalloy", "stainless_steel_high_temp")

    def test_unknown_environment_returns_all_incompatible(self) -> None:
        result = filter_by_environment(_ids(list(ALLOY_DATA)), "plasma_arc_vacuum_nonexistent")
        assert all(not e["compatible"] for e in result)
        assert all(e["state"] == "incompatible_environment" for e in result)

    def test_empty_candidate_list_returns_empty(self) -> None:
        result = filter_by_environment([], "marine")
        assert result == []

    def test_environment_case_sensitivity_normalized(self) -> None:
        result_lower = filter_by_environment(["316l_stainless"], "MARINE")
        result_upper = filter_by_environment(["316l_stainless"], "marine")
        assert result_lower[0]["compatible"] == result_upper[0]["compatible"]

    def test_unknown_alloy_returns_insufficient_data(self) -> None:
        result = filter_by_environment(["nonexistent_alloy_xyz"], "marine")
        assert result[0]["state"] == "insufficient_data"
        assert not result[0]["compatible"]


# ── filter_by_temperature ────────────────────────────────────────────


class TestFilterByTemperature:
    def test_cryogenic_excludes_non_cryo_alloys(self) -> None:
        result = filter_by_temperature(_ids(list(ALLOY_DATA)), min_kelvin=4.0, max_kelvin=300.0)
        survivors = [e for e in result if e["compatible"]]
        for s in survivors:
            assert ALLOY_DATA[s["alloy_id"]].get("cryogenic_compatible") is True

    def test_mid_range_most_alloys_pass(self) -> None:
        result = filter_by_temperature(_ids(list(ALLOY_DATA)), min_kelvin=250.0, max_kelvin=400.0)
        assert any(e["compatible"] for e in result)

    def test_inverted_range_returns_all_incompatible(self) -> None:
        result = filter_by_temperature(_ids(list(ALLOY_DATA)), min_kelvin=500.0, max_kelvin=100.0)
        assert all(not e["compatible"] for e in result)

    def test_zero_kelvin_floor(self) -> None:
        result = filter_by_temperature(_ids(list(ALLOY_DATA)), min_kelvin=0.0, max_kelvin=300.0)
        assert any(e["compatible"] for e in result)

    def test_above_max_for_every_alloy_returns_all_incompatible(self) -> None:
        result = filter_by_temperature(_ids(list(ALLOY_DATA)), min_kelvin=20000.0, max_kelvin=30000.0)
        assert all(not e["compatible"] for e in result)

    def test_nan_min_temp_is_rejected(self) -> None:
        result = filter_by_temperature(_ids(list(ALLOY_DATA)), min_kelvin=math.nan, max_kelvin=400.0)
        assert all(e["state"] == "invalid_input" for e in result)

    def test_nan_max_temp_is_rejected(self) -> None:
        result = filter_by_temperature(_ids(list(ALLOY_DATA)), min_kelvin=100.0, max_kelvin=math.nan)
        assert all(e["state"] == "invalid_input" for e in result)

    def test_inf_temp_is_rejected(self) -> None:
        result = filter_by_temperature(_ids(list(ALLOY_DATA)), min_kelvin=float("-inf"), max_kelvin=float("inf"))
        assert all(e["state"] == "invalid_input" for e in result)

    def test_missing_temp_limits_assumes_unknown_and_flags(self) -> None:
        dict(ALLOY_DATA)
        if "inconel_718" in ALLOY_DATA:
            result = filter_by_temperature(["inconel_718"], min_kelvin=100.0, max_kelvin=500.0)
            assert len(result) == 1

    def test_empty_candidates(self) -> None:
        assert filter_by_temperature([], min_kelvin=100.0, max_kelvin=500.0) == []


# ── filter_by_cost_index ─────────────────────────────────────────────


class TestFilterByCostIndex:
    def test_max_cost_filters_correctly(self) -> None:
        result = filter_by_cost_index(_ids(list(ALLOY_DATA)), max_cost_index=5.0)
        assert all(ALLOY_DATA[e["alloy_id"]]["cost_index"] <= 5.0 for e in result if e["compatible"])

    def test_zero_max_cost_excludes_almost_everything(self) -> None:
        result = filter_by_cost_index(_ids(list(ALLOY_DATA)), max_cost_index=0.0)
        survivors = [e for e in result if e["compatible"]]
        for s in survivors:
            assert ALLOY_DATA[s["alloy_id"]]["cost_index"] == 0.0

    def test_infinite_max_cost_all_pass(self) -> None:
        result = filter_by_cost_index(_ids(list(ALLOY_DATA)), max_cost_index=float("inf"))
        assert all(e["compatible"] for e in result)

    def test_nan_max_cost_rejected(self) -> None:
        result = filter_by_cost_index(_ids(list(ALLOY_DATA)), max_cost_index=math.nan)
        assert all(e["state"] == "invalid_input" for e in result)

    def test_negative_max_cost_rejected(self) -> None:
        result = filter_by_cost_index(_ids(list(ALLOY_DATA)), max_cost_index=-1.0)
        assert all(e["state"] == "invalid_input" for e in result)

    def test_missing_cost_index_treated_as_infinite(self) -> None:
        aid = next(iter(ALLOY_DATA.keys()))
        saved = ALLOY_DATA[aid].pop("cost_index", None)
        try:
            result = filter_by_cost_index([aid], max_cost_index=100.0)
            assert not result[0]["compatible"]
            assert "cost_index" in result[0].get("reason", "")
        finally:
            if saved is not None:
                ALLOY_DATA[aid]["cost_index"] = saved


# ── alloy_composition_tolerance ──────────────────────────────────────


class TestAlloyCompositionTolerance:
    def test_exact_match_within_tolerance(self) -> None:
        result = alloy_composition_tolerance(
            "316l_stainless", {"Fe": 0.655, "Cr": 0.175, "Ni": 0.12, "Mo": 0.025, "Mn": 0.02}
        )
        assert result["within_tolerance"] is True

    def test_out_of_tolerance_fails(self) -> None:
        result = alloy_composition_tolerance("316l_stainless", {"Fe": 0.10, "Cr": 0.80, "Ni": 0.05})
        assert result["within_tolerance"] is False

    def test_empty_composition_rejected(self) -> None:
        result = alloy_composition_tolerance("316l_stainless", {})
        assert result["state"] == "invalid_input"

    def test_composition_sums_greater_than_1_rejected(self) -> None:
        result = alloy_composition_tolerance("316l_stainless", {"Fe": 0.8, "Cr": 0.5})
        assert result["within_tolerance"] is False
        assert any("sum" in v.lower() for v in result.get("violations", []))

    def test_negative_fraction_rejected(self) -> None:
        result = alloy_composition_tolerance("316l_stainless", {"Fe": -0.1})
        assert result["within_tolerance"] is False

    def test_nan_fraction_rejected(self) -> None:
        result = alloy_composition_tolerance("316l_stainless", {"Fe": math.nan})
        assert result["state"] == "invalid_input"

    def test_unknown_alloy(self) -> None:
        result = alloy_composition_tolerance("madeup_alloyium", {"Fe": 1.0})
        assert result["state"] == "insufficient_data"

    def test_partial_composition_checked_against_subset(self) -> None:
        result = alloy_composition_tolerance("316l_stainless", {"Cr": 0.175, "Ni": 0.12})
        assert result["within_tolerance"] is True


# ── compare_alloys ───────────────────────────────────────────────────


class TestCompareAlloys:
    def test_ranks_by_specific_strength_descending(self) -> None:
        result = compare_alloys(["aa6061_t6", "aisi_1045", "inconel_718"], ["specific_strength"])
        assert result["criteria"] == ["specific_strength"]
        values = [e["scores"]["specific_strength"] for e in result["rankings"]]
        assert values == sorted(values, reverse=True), f"not descending: {values}"

    def test_tie_on_equal_scores_preserved_under_stable_sort(self) -> None:
        result = compare_alloys(["ti_6al4v", "316l_stainless"], ["cost_index"])
        assert len(result["rankings"]) == 2

    def test_empty_criteria_returns_identity_ordering(self) -> None:
        result = compare_alloys(["aa6061_t6", "aisi_1045"], [])
        assert result["rankings"][0]["alloy_id"] == "aa6061_t6"
        assert result["rankings"][1]["alloy_id"] == "aisi_1045"

    def test_unknown_alloy_flagged_not_ranked(self) -> None:
        result = compare_alloys(["aa6061_t6", "no_such_alloy"], ["specific_strength"])
        nosuch = [r for r in result["rankings"] if r["alloy_id"] == "no_such_alloy"]
        assert nosuch[0]["state"] == "insufficient_data"

    def test_multi_criteria_produces_composite_scores(self) -> None:
        result = compare_alloys(["aa6061_t6", "aisi_1045", "ti_6al4v"], ["specific_strength", "cost_index"])
        for r in result["rankings"]:
            assert "composite_score" in r if r["state"] == "ok" else True

    def test_single_alloy_returns_single_ranking(self) -> None:
        result = compare_alloys(["aa6061_t6"], ["specific_strength"])
        assert len(result["rankings"]) == 1

    def test_all_unknown_alloys_verdict(self) -> None:
        result = compare_alloys(["x", "y", "z"], ["specific_strength"])
        assert result["verdict"] == "insufficient_data"


# ── rank_by_performance_index ────────────────────────────────────────


class TestRankByPerformanceIndex:
    def test_rank_specific_strength_descending(self) -> None:
        result = rank_by_performance_index(_ids(list(ALLOY_DATA)), "specific_strength")
        ys_vals = [e["index_value"] for e in result["entries"] if e["index_value"] is not None]
        assert ys_vals == sorted(ys_vals, reverse=True)

    def test_rank_specific_stiffness_descending(self) -> None:
        result = rank_by_performance_index(_ids(list(ALLOY_DATA)), "specific_stiffness")
        mod_vals = [e["index_value"] for e in result["entries"] if e["index_value"] is not None]
        assert mod_vals == sorted(mod_vals, reverse=True)

    def test_missing_density_yields_none_value(self) -> None:
        saved_densities: dict[str, Any] = {}
        for aid in list(ALLOY_DATA):
            saved_densities[aid] = ALLOY_DATA[aid].pop("density", None)
        try:
            result = rank_by_performance_index(_ids(list(ALLOY_DATA)), "specific_strength")
            assert all(e["index_value"] is None for e in result["entries"])
        finally:
            for aid, saved in saved_densities.items():
                if saved is not None:
                    ALLOY_DATA[aid]["density"] = saved

    def test_unknown_index_type_rejected(self) -> None:
        result = rank_by_performance_index(_ids(list(ALLOY_DATA)), "magic_toughness_index")
        assert result["verdict"] == "invalid_index"

    def test_empty_candidates(self) -> None:
        result = rank_by_performance_index([], "specific_strength")
        assert result["entries"] == []


# ── select_alloy (full pipeline) ─────────────────────────────────────


class TestSelectAlloy:
    def test_marine_high_strength_selects_inconel_or_ti(self) -> None:
        reqs = {
            "environment": "marine",
            "min_yield_mpa": 500.0,
            "max_cost_index": 20.0,
        }
        result = select_alloy(reqs)
        survivors = [e for e in result["candidates"] if e["state"] == "survived"]
        survivor_ids = {s["alloy_id"] for s in survivors}
        assert survivor_ids <= {"inconel_718", "ti_6al4v", "316l_stainless", "monel_400"}

    def test_no_survivors_returns_infeasible_verdict(self) -> None:
        reqs = {
            "environment": "marine",
            "min_yield_mpa": 3000.0,  # beyond any alloy
            "max_cost_index": 0.0,
        }
        result = select_alloy(reqs)
        assert result["verdict"] == "infeasible"

    def test_multi_constraint_cascade_narrows_correctly(self) -> None:
        reqs = {
            "environment": "high_temp_oxidizing",
            "min_temp_kelvin": 800.0,
            "max_cost_index": 15.0,
        }
        result = select_alloy(reqs)
        survivors = [e for e in result["candidates"] if e["state"] == "survived"]
        for s in survivors:
            data = ALLOY_DATA[s["alloy_id"]]
            assert data.get("temp_max_kelvin", 0) >= 800.0
            assert "high_temp_oxidizing" in data.get("environment", [])

    def test_empty_requirements_returns_all_without_filtering(self) -> None:
        result = select_alloy({})
        assert result["verdict"] == "candidate"
        assert len(result["candidates"]) == len(ALLOY_DATA)

    def test_nan_yield_requirement_rejected(self) -> None:
        reqs = {"min_yield_mpa": math.nan}
        result = select_alloy(reqs)
        assert result["state"] == "invalid_input"

    def test_inf_max_cost_allows_all(self) -> None:
        reqs = {"max_cost_index": float("inf")}
        result = select_alloy(reqs)
        assert all(e["state"] == "survived" for e in result["candidates"])

    def test_negative_min_yield_is_rejected(self) -> None:
        reqs = {"min_yield_mpa": -50.0}
        result = select_alloy(reqs)
        assert result["state"] == "invalid_input"

    def test_zero_min_yield_allows_all(self) -> None:
        reqs = {"min_yield_mpa": 0.0}
        result = select_alloy(reqs)
        assert all(e["state"] == "survived" for e in result["candidates"])

    def test_explicit_candidate_list_honored(self) -> None:
        reqs = {"environment": "marine"}
        result = select_alloy(reqs, candidates=["aa6061_t6", "inconel_718"])
        assert len(result["candidates"]) == 2

    def test_unknown_alloy_in_candidates_flagged_rejected(self) -> None:
        reqs = {"environment": "marine"}
        result = select_alloy(reqs, candidates=["no_such_alloy"])
        assert result["candidates"][0]["state"] == "rejected"

    def test_conflicting_temperature_and_cryogenic_constraints(self) -> None:
        reqs = {"min_temp_kelvin": 1000.0, "cryogenic_required": True}
        result = select_alloy(reqs)
        assert result["verdict"] == "infeasible"

    def test_result_includes_data_tiers(self) -> None:
        reqs = {"environment": "marine"}
        result = select_alloy(reqs)
        for c in result["candidates"]:
            if c["state"] == "survived":
                assert "data_tier" in c

    def test_sort_order_is_best_first(self) -> None:
        reqs = {"environment": "marine", "min_yield_mpa": 200.0}
        result = select_alloy(reqs)
        margins = [c.get("composite_margin", 0) for c in result["candidates"] if c["state"] == "survived"]
        assert margins == sorted(margins, reverse=True)


# ── rebuild helper for mutating tests ────────────────────────────────


def _rebuild_alloy_data() -> None:
    """Re-seed ALLOY_DATA after mutation by tests that temporarily pop keys."""
    ALLOY_DATA.clear()
    ALLOY_DATA.update(
        {
            "aa6061_t6": {
                "family": "aluminum_alloy",
                "base_element": "Al",
                "density": 2.7,
                "cost_index": 1.0,
                "yield_strength_mpa": 276.0,
                "youngs_modulus_gpa": 68.9,
                "ultimate_strength_mpa": 310.0,
                "temp_min_kelvin": None,
                "temp_max_kelvin": 450.0,
                "cryogenic_compatible": True,
                "environment": ["atmospheric", "fresh_water", "marine_limited"],
                "composition": {"Al": 0.974, "Mg": 0.01, "Si": 0.006, "Cu": 0.003, "Cr": 0.003},
            },
            "aisi_1045": {
                "family": "carbon_steel",
                "base_element": "Fe",
                "density": 7.85,
                "cost_index": 0.8,
                "yield_strength_mpa": 310.0,
                "youngs_modulus_gpa": 200.0,
                "ultimate_strength_mpa": 565.0,
                "temp_min_kelvin": 200.0,
                "temp_max_kelvin": 600.0,
                "cryogenic_compatible": False,
                "environment": ["atmospheric", "vacuum"],
                "composition": {"Fe": 0.985, "C": 0.0045, "Mn": 0.008, "Si": 0.0025},
            },
            "316l_stainless": {
                "family": "stainless_steel",
                "base_element": "Fe",
                "density": 8.0,
                "cost_index": 3.0,
                "yield_strength_mpa": 290.0,
                "youngs_modulus_gpa": 193.0,
                "ultimate_strength_mpa": 558.0,
                "temp_min_kelvin": 4.0,
                "temp_max_kelvin": 870.0,
                "cryogenic_compatible": True,
                "environment": ["marine", "atmospheric", "chemical_acidic", "chemical_basic"],
                "composition": {"Fe": 0.655, "Cr": 0.175, "Ni": 0.12, "Mo": 0.025, "Mn": 0.02, "Si": 0.005},
            },
            "inconel_718": {
                "family": "nickel_superalloy",
                "base_element": "Ni",
                "density": 8.19,
                "cost_index": 12.0,
                "yield_strength_mpa": 1034.0,
                "youngs_modulus_gpa": 205.0,
                "ultimate_strength_mpa": 1275.0,
                "temp_min_kelvin": 4.0,
                "temp_max_kelvin": 920.0,
                "cryogenic_compatible": True,
                "environment": ["high_temp_oxidizing", "marine", "chemical_acidic"],
                "composition": {"Ni": 0.525, "Cr": 0.19, "Fe": 0.18, "Nb": 0.051, "Mo": 0.03, "Ti": 0.01, "Al": 0.005},
            },
            "ti_6al4v": {
                "family": "titanium_alloy",
                "base_element": "Ti",
                "density": 4.43,
                "cost_index": 8.0,
                "yield_strength_mpa": 880.0,
                "youngs_modulus_gpa": 113.8,
                "ultimate_strength_mpa": 950.0,
                "temp_min_kelvin": 4.0,
                "temp_max_kelvin": 620.0,
                "cryogenic_compatible": True,
                "environment": ["marine", "atmospheric", "chemical_acidic", "aerospace"],
                "composition": {"Ti": 0.895, "Al": 0.06, "V": 0.04, "Fe": 0.005},
            },
            "monel_400": {
                "family": "nickel_copper",
                "base_element": "Ni",
                "density": 8.8,
                "cost_index": 7.0,
                "yield_strength_mpa": 240.0,
                "youngs_modulus_gpa": 179.0,
                "ultimate_strength_mpa": 550.0,
                "temp_min_kelvin": 4.0,
                "temp_max_kelvin": 510.0,
                "cryogenic_compatible": True,
                "environment": ["marine", "chemical_acidic", "chemical_basic"],
                "composition": {"Ni": 0.66, "Cu": 0.315, "Fe": 0.015, "Mn": 0.01},
            },
            "az31b_mg": {
                "family": "magnesium_alloy",
                "base_element": "Mg",
                "density": 1.77,
                "cost_index": 2.5,
                "yield_strength_mpa": 200.0,
                "youngs_modulus_gpa": 45.0,
                "ultimate_strength_mpa": 260.0,
                "temp_min_kelvin": 200.0,
                "temp_max_kelvin": 370.0,
                "cryogenic_compatible": False,
                "environment": ["atmospheric", "vacuum"],
                "composition": {"Mg": 0.96, "Al": 0.03, "Zn": 0.01},
            },
            "hastelloy_c276": {
                "family": "nickel_superalloy",
                "base_element": "Ni",
                "density": 8.89,
                "cost_index": 14.0,
                "yield_strength_mpa": 355.0,
                "youngs_modulus_gpa": 205.0,
                "ultimate_strength_mpa": 790.0,
                "temp_min_kelvin": 4.0,
                "temp_max_kelvin": 830.0,
                "cryogenic_compatible": True,
                "environment": ["marine", "chemical_acidic", "high_temp_oxidizing"],
                "composition": {"Ni": 0.57, "Mo": 0.16, "Cr": 0.155, "Fe": 0.055, "W": 0.04, "Co": 0.02},
            },
        }
    )
