"""Deep edge-case tests for alloy_selector.py (spec MATE-001 §4.4)."""

from __future__ import annotations

from general_ludd.materials.alloy_selector import (
    ALLOY_DATA,
    _is_nan_or_inf,
    alloy_composition_tolerance,
    compare_alloys,
    filter_by_cost_index,
    filter_by_environment,
    filter_by_temperature,
    rank_by_performance_index,
    select_alloy,
)

# ── _is_nan_or_inf ────────────────────────────────────────────────────


def test_is_nan_or_inf_nan() -> None:
    assert _is_nan_or_inf(float("nan")) is True


def test_is_nan_or_inf_posinf() -> None:
    assert _is_nan_or_inf(float("inf")) is True


def test_is_nan_or_inf_neginf() -> None:
    assert _is_nan_or_inf(float("-inf")) is True


def test_is_nan_or_inf_normal_zero() -> None:
    assert _is_nan_or_inf(0.0) is False


def test_is_nan_or_inf_normal_large() -> None:
    assert _is_nan_or_inf(1e308) is False


# ── filter_by_environment ─────────────────────────────────────────────


def test_env_all_compatible() -> None:
    """All known alloys are marine-compatible — returns true count."""
    result = filter_by_environment(list(ALLOY_DATA.keys()), "marine")
    compat = [r for r in result if r["compatible"]]
    assert len(compat) >= 1  # 316l, inconel, ti, monel, hastelloy


def test_env_unknown_alloy_insufficient_data() -> None:
    result = filter_by_environment(["bogus_alloy_xyz"], "atmospheric")
    assert len(result) == 1
    assert result[0]["state"] == "insufficient_data"
    assert result[0]["compatible"] is False


def test_env_mixed_known_and_unknown() -> None:
    result = filter_by_environment(["aa6061_t6", "bogus_alloy_xyz", "aisi_1045"], "atmospheric")
    states = {r["alloy_id"]: r["state"] for r in result}
    assert states["aa6061_t6"] == "ok"
    assert states["bogus_alloy_xyz"] == "insufficient_data"
    assert states["aisi_1045"] == "ok"


def test_env_empty_list() -> None:
    result = filter_by_environment([], "atmospheric")
    assert result == []


def test_env_whitespace_in_environment() -> None:
    """Leading/trailing spaces in environment string are stripped."""
    result = filter_by_environment(["aa6061_t6"], "  atmospheric  ")
    assert result[0]["compatible"] is True


def test_env_case_insensitive_environment() -> None:
    """Environment matching is case-insensitive."""
    result = filter_by_environment(["316l_stainless"], "CHEMICAL_ACIDIC")
    assert result[0]["compatible"] is True


def test_env_incompatible_strict() -> None:
    """aisi_1045 is NOT marine compatible."""
    result = filter_by_environment(["aisi_1045"], "marine")
    assert result[0]["compatible"] is False
    assert result[0]["state"] == "incompatible_environment"


def test_env_multiple_identical_alloys() -> None:
    """Duplicate alloy IDs produce duplicate results."""
    result = filter_by_environment(["aa6061_t6", "aa6061_t6"], "atmospheric")
    assert len(result) == 2
    assert all(r["compatible"] for r in result)


# ── filter_by_temperature ─────────────────────────────────────────────


def test_temp_cryo_compatible_alloys_survive() -> None:
    """316l, inconel, ti survive 80-300 K range."""
    result = filter_by_temperature(list(ALLOY_DATA.keys()), 80.0, 300.0)
    survivors = [r for r in result if r["compatible"]]
    assert any(r["alloy_id"] == "316l_stainless" for r in survivors)
    assert any(r["alloy_id"] == "ti_6al4v" for r in survivors)


def test_temp_aisi_1045_cryo_fails() -> None:
    """aisi_1045 temp_min is 200 K; 80 K should fail."""
    result = filter_by_temperature(["aisi_1045"], 80.0, 300.0)
    assert result[0]["compatible"] is False
    assert result[0]["state"] == "out_of_range"


def test_temp_nan_input_rejects_all() -> None:
    result = filter_by_temperature(["aa6061_t6", "316l_stainless"], float("nan"), 500.0)
    assert all(r["state"] == "invalid_input" for r in result)
    assert all(not r["compatible"] for r in result)


def test_temp_inf_input_rejects_all() -> None:
    result = filter_by_temperature(["aa6061_t6", "316l_stainless"], float("inf"), 500.0)
    assert all(r["state"] == "invalid_input" for r in result)


def test_temp_min_greater_than_max() -> None:
    result = filter_by_temperature(["aa6061_t6"], 500.0, 100.0)
    assert result[0]["state"] == "invalid_input"
    assert result[0]["compatible"] is False


def test_temp_min_equals_max_at_boundary() -> None:
    """aisi_1045 max is 600 K; checking at exactly 600 should be compatible."""
    result = filter_by_temperature(["aisi_1045"], 600.0, 600.0)
    assert result[0]["compatible"] is True


def test_temp_unknown_alloy_insufficient_data() -> None:
    result = filter_by_temperature(["bogus_alloy"], 100.0, 500.0)
    assert result[0]["state"] == "insufficient_data"


def test_temp_negative_min_kelvin() -> None:
    """Negative temperature: aisi_1045 min is 200 K, -50 is below that."""
    result = filter_by_temperature(["aisi_1045"], -50.0, 500.0)
    assert result[0]["state"] == "out_of_range"


def test_temp_no_limits_alloy() -> None:
    """An alloy with no temp limits (tmin=tmax=None) is assumed compatible."""
    result = filter_by_temperature(["aa6061_t6"], 300.0, 350.0)
    assert result[0]["compatible"] is True


# ── filter_by_cost_index ──────────────────────────────────────────────


def test_cost_all_below_max() -> None:
    """All known alloys cost <= 14; max 100 should include all."""
    result = filter_by_cost_index(list(ALLOY_DATA.keys()), 100.0)
    assert all(r["compatible"] for r in result if r["state"] != "insufficient_data")


def test_cost_budget_1_excludes_expensive() -> None:
    """inconel_718 cost=12, hastelloy cost=14 — neither should pass max=1."""
    result = filter_by_cost_index(["inconel_718", "aa6061_t6"], 1.0)
    by_id = {r["alloy_id"]: r for r in result}
    assert by_id["aa6061_t6"]["compatible"] is True
    assert by_id["inconel_718"]["compatible"] is False


def test_cost_inf_allows_all() -> None:
    result = filter_by_cost_index(["inconel_718", "hastelloy_c276"], float("inf"))
    assert all(r["compatible"] for r in result)


def test_cost_nan_rejects_all() -> None:
    result = filter_by_cost_index(["aa6061_t6", "316l_stainless"], float("nan"))
    assert all(r["state"] == "invalid_input" for r in result)


def test_cost_negative_rejects_all() -> None:
    result = filter_by_cost_index(["aa6061_t6"], -5.0)
    assert result[0]["state"] == "invalid_input"


def test_cost_zero_budget() -> None:
    """Zero budget — cheapest alloy is 0.8, so everything fails."""
    result = filter_by_cost_index(list(ALLOY_DATA.keys()), 0.0)
    non_insufficient = [r for r in result if r["state"] != "insufficient_data"]
    assert all(not r["compatible"] for r in non_insufficient)


def test_cost_unknown_alloy() -> None:
    result = filter_by_cost_index(["bogus_alloy"], 10.0)
    assert result[0]["state"] == "insufficient_data"


def test_cost_empty_list() -> None:
    assert filter_by_cost_index([], 10.0) == []


# ── alloy_composition_tolerance ───────────────────────────────────────


def test_composition_exact_match() -> None:
    ref = ALLOY_DATA["aa6061_t6"]["composition"]
    result = alloy_composition_tolerance("aa6061_t6", ref)
    assert result["within_tolerance"] is True
    assert result["state"] == "ok"


def test_composition_minor_deviation_within_tol() -> None:
    """4% deviation on Si (tol=5%) should pass; shift from Al to keep sum same."""
    ref = ALLOY_DATA["aa6061_t6"]["composition"]
    measured = dict(ref)
    measured["Al"] = ref["Al"] - 0.04
    measured["Si"] = ref["Si"] + 0.04
    result = alloy_composition_tolerance("aa6061_t6", measured)
    assert result["within_tolerance"] is True


def test_composition_major_deviation_outside_tol() -> None:
    """10% deviation on Al (tol=5%) should fail."""
    ref = ALLOY_DATA["aa6061_t6"]["composition"]
    measured = dict(ref)
    measured["Al"] = ref["Al"] - 0.08
    measured["Si"] = ref["Si"] + 0.08
    result = alloy_composition_tolerance("aa6061_t6", measured)
    assert result["within_tolerance"] is False
    assert result["state"] == "out_of_tolerance"


def test_composition_unknown_alloy() -> None:
    result = alloy_composition_tolerance("bogus_alloy", {"Fe": 0.99})
    assert result["state"] == "insufficient_data"
    assert result["within_tolerance"] is False


def test_composition_empty_measured() -> None:
    result = alloy_composition_tolerance("aa6061_t6", {})
    assert result["state"] == "invalid_input"


def test_composition_nan_in_measured() -> None:
    result = alloy_composition_tolerance("aa6061_t6", {"Al": float("nan")})
    assert result["state"] == "invalid_input"


def test_composition_inf_in_measured() -> None:
    result = alloy_composition_tolerance("aa6061_t6", {"Al": float("inf")})
    assert result["state"] == "invalid_input"


def test_composition_sum_exceeds_one() -> None:
    """Composition summing to 1.5 should be rejected."""
    result = alloy_composition_tolerance("aa6061_t6", {"Al": 0.8, "Mg": 0.8})
    assert result["state"] == "invalid_input"


def test_composition_negative_fraction() -> None:
    result = alloy_composition_tolerance("aa6061_t6", {"Al": -0.1})
    assert result["state"] == "invalid_input"


def test_composition_extra_element_silently_ignored() -> None:
    """Extra elements not in the reference are NOT compared."""
    ref = ALLOY_DATA["aa6061_t6"]["composition"]
    measured = dict(ref)
    measured["Au"] = 0.005
    result = alloy_composition_tolerance("aa6061_t6", measured)
    assert result["within_tolerance"] is True


def test_composition_missing_element_from_measured() -> None:
    """When measured is missing a ref element, it doesn't trigger a violation."""
    result = alloy_composition_tolerance("aa6061_t6", {"Al": 0.974, "Mg": 0.01})
    assert result["within_tolerance"] is True


# ── compare_alloys ────────────────────────────────────────────────────


def test_compare_specific_strength_ranking() -> None:
    """ti_6al4v has high specific strength (880/4.43 ≈ 199); should rank high."""
    result = compare_alloys(["aa6061_t6", "ti_6al4v", "az31b_mg"], ["specific_strength"])
    rankings = result["rankings"]
    assert len(rankings) == 3
    # First entry has highest composite score.
    first_aid = rankings[0]["alloy_id"]
    assert first_aid == "ti_6al4v"  # 199 > 102 (6061) > 113 (AZ31B)


def test_compare_cost_index_ranking() -> None:
    """Lower cost => higher inverse score => ranked first."""
    result = compare_alloys(["inconel_718", "aa6061_t6", "aisi_1045"], ["cost_index"])
    rankings = result["rankings"]
    # aisi_1045 cost 0.8 => 1.25, aa6061 cost 1.0 => 1.0, inconel cost 12 => 0.083
    assert rankings[0]["alloy_id"] == "aisi_1045"
    assert rankings[-1]["alloy_id"] == "inconel_718"


def test_compare_multiple_criteria() -> None:
    result = compare_alloys(["aa6061_t6", "ti_6al4v"], ["specific_strength", "cost_index", "temp_range"])
    rankings = result["rankings"]
    assert result["verdict"] == "candidate"
    assert len(rankings) == 2


def test_compare_unknown_alloy() -> None:
    result = compare_alloys(["bogus_alloy"], ["specific_strength"])
    rankings = result["rankings"]
    assert rankings[0]["state"] == "insufficient_data"
    assert rankings[0]["composite_score"] is None


def test_compare_empty_alloy_list() -> None:
    result = compare_alloys([], ["specific_strength"])
    assert result["rankings"] == []
    assert result["verdict"] == "insufficient_data"


def test_compare_unknown_criterion_zero_score() -> None:
    result = compare_alloys(["aa6061_t6"], ["bogus_criterion"])
    assert result["rankings"][0]["scores"]["bogus_criterion"] == 0.0


# ── rank_by_performance_index ─────────────────────────────────────────


def test_rank_specific_strength() -> None:
    result = rank_by_performance_index(["aa6061_t6", "ti_6al4v", "az31b_mg"], "specific_strength")
    assert result["verdict"] == "ranked"
    entries = result["entries"]
    assert entries[0]["alloy_id"] == "ti_6al4v"  # 880/4.43 ≈ 199
    assert entries[-1]["alloy_id"] == "aa6061_t6"  # 200/1.77 ≈ 113 — wait, aa6061 is 102


def test_rank_specific_stiffness() -> None:
    result = rank_by_performance_index(["aa6061_t6", "aisi_1045"], "specific_stiffness")
    assert result["verdict"] == "ranked"
    entries = result["entries"]
    # aa6061: 68.9/2.7 ≈ 25.5, aisi_1045: 200/7.85 ≈ 25.5
    assert len(entries) == 2
    assert all(e["state"] == "ok" for e in entries)


def test_rank_unknown_index_type() -> None:
    result = rank_by_performance_index(["aa6061_t6"], "bogus_index")
    assert result["verdict"] == "invalid_index"
    assert result["entries"] == []


def test_rank_unknown_alloy() -> None:
    result = rank_by_performance_index(["bogus_alloy", "aa6061_t6"], "specific_strength")
    entries = result["entries"]
    unknown = next(e for e in entries if e["alloy_id"] == "bogus_alloy")
    assert unknown["state"] == "insufficient_data"
    assert unknown["index_value"] is None


def test_rank_empty_list() -> None:
    result = rank_by_performance_index([], "specific_strength")
    assert result["entries"] == []
    assert result["verdict"] == "ranked"


def test_rank_missing_property() -> None:
    """An alloy without yield_strength should return missing_property state."""
    result = rank_by_performance_index(["aa6061_t6"], "specific_strength")
    assert result["verdict"] == "ranked"


# ── select_alloy (full pipeline) ──────────────────────────────────────


def test_select_marine_environment_candidates() -> None:
    reqs = {"environment": "marine", "min_yield_mpa": 250.0}
    result = select_alloy(reqs)
    assert result["verdict"] == "candidate"
    survivors = [c for c in result["candidates"] if c["state"] == "survived"]
    assert len(survivors) >= 1


def test_select_impossible_combination() -> None:
    """Cryogenic + low cost + high yield — probably infeasible."""
    reqs = {"cryogenic_required": True, "min_yield_mpa": 5000.0, "environment": "marine"}
    result = select_alloy(reqs)
    assert result["verdict"] == "infeasible"


def test_select_invalid_min_yield() -> None:
    result = select_alloy({"min_yield_mpa": float("nan")})
    assert result["state"] == "invalid_input"


def test_select_invalid_max_cost() -> None:
    result = select_alloy({"max_cost_index": float("nan")})
    assert result["state"] == "invalid_input"


def test_select_invalid_both_nan() -> None:
    result = select_alloy({"min_yield_mpa": float("nan"), "max_cost_index": float("nan")})
    assert result["state"] == "invalid_input"


def test_select_negative_max_cost() -> None:
    result = select_alloy({"max_cost_index": -1.0})
    assert result["state"] == "invalid_input"


def test_select_negative_min_yield() -> None:
    result = select_alloy({"min_yield_mpa": -100.0})
    assert result["state"] == "invalid_input"


def test_select_cryo_with_high_min_temp() -> None:
    """Cryogenic required but min_temp > 200 K is infeasible."""
    result = select_alloy({"cryogenic_required": True, "min_temp_kelvin": 250.0, "max_temp_kelvin": 500.0})
    assert result["state"] == "infeasible"


def test_select_custom_candidates_subset() -> None:
    """Only consider a subset of candidate alloys."""
    result = select_alloy(
        {"min_yield_mpa": 800.0, "max_cost_index": 10.0},
        candidates=["aa6061_t6", "ti_6al4v"],
    )
    survivors = [c for c in result["candidates"] if c["state"] == "survived"]
    assert len(survivors) == 1
    assert survivors[0]["alloy_id"] == "ti_6al4v"


def test_select_bogus_candidates() -> None:
    result = select_alloy({"min_yield_mpa": 100.0}, candidates=["bogus_1", "bogus_2"])
    rejected = [c for c in result["candidates"] if c["state"] == "rejected"]
    assert len(rejected) == 2
    assert all("unknown alloy" in r["reason"] for r in rejected)


def test_select_aisi_1045_rejected_for_marine() -> None:
    result = select_alloy({"environment": "marine"}, candidates=["aisi_1045"])
    assert result["candidates"][0]["state"] == "rejected"
    assert "environment" in result["candidates"][0]["reason"]


def test_select_inconel_survives_high_temp() -> None:
    result = select_alloy(
        {"min_temp_kelvin": 500.0, "max_temp_kelvin": 900.0, "min_yield_mpa": 500.0},
        candidates=["inconel_718", "aisi_1045"],
    )
    survivors = [c for c in result["candidates"] if c["state"] == "survived"]
    # aisi_1045 max_temp is 600 K and yield is 310; inconel max is 920 K and yield is 1034
    assert len(survivors) == 1
    assert survivors[0]["alloy_id"] == "inconel_718"


def test_select_empty_requirements_all_survive() -> None:
    """No filters => all known alloys survive."""
    result = select_alloy({})
    survivors = [c for c in result["candidates"] if c["state"] == "survived"]
    assert len(survivors) == len(ALLOY_DATA)


def test_select_composite_margin_ordering() -> None:
    """Survivors are sorted by descending composite_margin."""
    result = select_alloy({"min_yield_mpa": 0.0, "max_cost_index": float("inf")})
    survivors = [c for c in result["candidates"] if c["state"] == "survived"]
    margins = [s["composite_margin"] for s in survivors]
    assert margins == sorted(margins, reverse=True)


def test_select_cryogenic_non_compatible_rejected() -> None:
    """aisi_1045 is not cryogenic-compatible."""
    result = select_alloy(
        {"cryogenic_required": True, "min_temp_kelvin": 4.0, "max_temp_kelvin": 300.0},
        candidates=["aisi_1045"],
    )
    assert result["candidates"][0]["state"] == "rejected"
    assert "cryogenic" in result["candidates"][0]["reason"]


def test_select_min_yield_exceeds_all_capacity() -> None:
    """Nothing has yield > 5000 MPa."""
    result = select_alloy({"min_yield_mpa": 5000.0})
    assert result["verdict"] == "infeasible"


def test_select_cost_index_zero_survivor() -> None:
    """hastelloy cost 14, ti cost 8 — with max_cost 1, aisi_1045 cost 0.8 survives."""
    result = select_alloy(
        {"max_cost_index": 1.0, "min_yield_mpa": 100.0},
        candidates=["inconel_718", "aa6061_t6", "aisi_1045"],
    )
    survivors = [c for c in result["candidates"] if c["state"] == "survived"]
    assert len(survivors) == 2
    survivor_ids = {s["alloy_id"] for s in survivors}
    assert "aa6061_t6" in survivor_ids
    assert "aisi_1045" in survivor_ids


# ── cross-function integration ────────────────────────────────────────


def test_filter_then_select_consistency() -> None:
    """filter_by_environment -> filter_by_temperature -> select_alloy should be consistent."""
    env_result = filter_by_environment(list(ALLOY_DATA.keys()), "marine")
    marine_ids = [r["alloy_id"] for r in env_result if r["compatible"]]

    temp_result = filter_by_temperature(marine_ids, 80.0, 400.0)
    temp_ok_ids = [r["alloy_id"] for r in temp_result if r["compatible"]]

    result = select_alloy(
        {"environment": "marine", "min_temp_kelvin": 80.0, "max_temp_kelvin": 400.0, "min_yield_mpa": 200.0},
        candidates=temp_ok_ids,
    )
    assert result["verdict"] in ("candidate", "infeasible")
