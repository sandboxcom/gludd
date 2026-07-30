"""MATE-P2 tests: selection (screening/ranking) and analytical strength checks.

Covers spec MATE-001 sections 4.7 (Strength/build modeling) and 7 (decision
methods MATE-DEC-001..004), plus safety invariants MATE-SAFE-003 (no fabricated
precision) and MATE-SAFE-006 (fail closed).

Test surface:
  - screen_candidates: rejects hard-constraint violations, missing-property
    candidates, unknown materials; keeps survivors with explicit state.
  - rank_candidates: normalizes compatible units (GPa->MPa), computes
    performance indices, exposes trade-offs (not a collapsed score), and
    produces nominal / conservative / sensitivity cases. Ranking can flip
    between nominal and conservative when uncertainty bands differ.
  - Data hierarchy (MATE-DEC-003): lot > supplier > handbook > estimated;
    lower-tier data is labeled and surfaced, never silently substituted.
  - Strength checks (tension, compression, shear, bending, Euler buckling,
    thermal stress, fatigue S-N baseline): each returns margin + units +
    uncertainty + equation id (MATE-DEC-004) and fails closed on bad input.
"""

from __future__ import annotations

import math

import pytest

from general_ludd.materials.core import normalize_requirements
from general_ludd.materials.material_selection import (
    DATA_TIERS,
    rank_candidates,
    resolve_property,
    screen_candidates,
)
from general_ludd.materials.strength import (
    check_bending,
    check_buckling_euler,
    check_compression,
    check_fatigue_sn,
    check_shear,
    check_tension,
    check_thermal_stress,
)

# ---------------------------------------------------------------------------
# Screening (MATE-DEC-002 step 1: reject hard-constraint violations)
# ---------------------------------------------------------------------------


class TestScreening:
    def test_screen_rejects_yield_violation(self):
        reqs = normalize_requirements(
            {
                "load_cases": [{"id": "y1", "type": "yield", "magnitude": 200, "unit": "MPa"}],
                "failure_consequence": "significant",
            }
        )
        result = screen_candidates(reqs, candidates=["pa66_gf30", "aisi_1045"])
        rejected = [c for c in result["candidates"] if c["state"] == "rejected"]
        survived = [c for c in result["candidates"] if c["state"] == "survived"]
        # pa66_gf30 yield=180 < 200 -> rejected; aisi_1045 yield=310 > 200 -> survived
        rejected_ids = {c["material_id"] for c in rejected}
        survived_ids = {c["material_id"] for c in survived}
        assert "pa66_gf30" in rejected_ids
        assert "aisi_1045" in survived_ids
        assert any("hard_constraint" in c["reason"] for c in rejected)

    def test_screen_rejects_candidate_missing_required_property(self):
        # epoxy_cast has ultimate_strength but no yield_strength; a yield
        # requirement is a hard constraint the candidate cannot satisfy.
        reqs = normalize_requirements({"load_cases": [{"id": "y1", "type": "yield", "magnitude": 50, "unit": "MPa"}]})
        result = screen_candidates(reqs, candidates=["epoxy_cast"])
        cand = result["candidates"][0]
        assert cand["state"] == "rejected"
        assert "yield_strength" in cand["reason"]

    def test_screen_keeps_survivors_with_survived_state(self):
        reqs = normalize_requirements({"load_cases": [{"id": "y1", "type": "yield", "magnitude": 200, "unit": "MPa"}]})
        result = screen_candidates(reqs, candidates=["aisi_1045", "aa6061_t6"])
        assert all(c["state"] == "survived" for c in result["candidates"])
        assert result["verdict"] == "candidate"

    def test_screen_rejects_unknown_material_id(self):
        reqs = normalize_requirements({"load_cases": [{"id": "y1", "type": "yield", "magnitude": 100, "unit": "MPa"}]})
        result = screen_candidates(reqs, candidates=["unobtanium_9000"])
        cand = result["candidates"][0]
        assert cand["state"] == "rejected"
        assert "unknown_material" in cand["reason"]


# ---------------------------------------------------------------------------
# Ranking (MATE-DEC-002 steps 2-5: normalize, compute margins, rank under
# multiple cases, expose trade-offs)
# ---------------------------------------------------------------------------


class TestRanking:
    def test_rank_returns_nominal_conservative_and_sensitivity_cases(self):
        reqs = normalize_requirements({"load_cases": [{"id": "y1", "type": "yield", "magnitude": 200, "unit": "MPa"}]})
        result = rank_candidates(reqs, candidates=["aisi_1045"])
        for case in ("nominal", "conservative", "sensitivity"):
            assert case in result, f"missing case: {case}"
            assert isinstance(result[case], list)
            assert len(result[case]) >= 1

    def test_rank_normalizes_GPa_modulus_to_MPa(self):
        # aisi_1045 youngs_modulus is stored in GPa; ranker must expose the
        # tradeoff in a single consistent unit (MPa) alongside the original.
        reqs = normalize_requirements({"load_cases": [{"id": "y1", "type": "yield", "magnitude": 200, "unit": "MPa"}]})
        result = rank_candidates(reqs, candidates=["aisi_1045"])
        nominal = result["nominal"][0]
        assert "youngs_modulus" in nominal["tradeoffs"]
        # Value should be the MPa-equivalent (200 GPa -> 200000 MPa) with the
        # unit label making the conversion explicit.
        mod_tradeoff = nominal["tradeoffs"]["youngs_modulus"]
        assert mod_tradeoff["value"] == pytest.approx(200000.0, rel=1e-3)
        assert mod_tradeoff["unit"] == "MPa"

    def test_rank_conservative_uses_lower_bound_capacity(self):
        # nominal capacity = 310 MPa, uncertainty = 20 -> conservative = 290 MPa
        reqs = normalize_requirements({"load_cases": [{"id": "y1", "type": "yield", "magnitude": 200, "unit": "MPa"}]})
        result = rank_candidates(reqs, candidates=["aisi_1045"])
        nominal_margin = result["nominal"][0]["margins"][0]
        cons_margin = result["conservative"][0]["margins"][0]
        assert nominal_margin["capacity"] == pytest.approx(310.0)
        assert cons_margin["capacity"] == pytest.approx(290.0, rel=1e-3)
        assert cons_margin["margin"] < nominal_margin["margin"]

    def test_rank_sensitivity_can_flip_ranking_vs_nominal(self):
        # Two candidates: handbook value with wide uncertainty vs lot value
        # with tight uncertainty. Nominal favors the higher handbook value;
        # conservative favors the lot-validated candidate.
        reqs = normalize_requirements({"load_cases": [{"id": "y1", "type": "yield", "magnitude": 200, "unit": "MPa"}]})
        overrides = {
            "aisi_1045": {
                "yield_strength": {
                    "value": 300.0,
                    "unit": "MPa",
                    "uncertainty": 50.0,
                    "tier": "handbook",
                }
            },
            "aa6061_t6": {
                "yield_strength": {
                    "value": 280.0,
                    "unit": "MPa",
                    "uncertainty": 10.0,
                    "tier": "lot",
                }
            },
        }
        result = rank_candidates(reqs, candidates=["aisi_1045", "aa6061_t6"], overrides=overrides)
        nominal = {c["material_id"]: c["margins"][0]["margin"] for c in result["nominal"]}
        conservative = {c["material_id"]: c["margins"][0]["margin"] for c in result["conservative"]}
        # Nominal: 1045 (300/200-1=0.5) > 6061 (280/200-1=0.4)
        assert nominal["aisi_1045"] > nominal["aa6061_t6"]
        # Conservative: 1045 (250/200-1=0.25) < 6061 (270/200-1=0.35) -> flip
        assert conservative["aa6061_t6"] > conservative["aisi_1045"]

    def test_rank_exposes_tradeoffs_not_single_score(self):
        # MATE-DEC-002 step 5: expose trade-offs, do not collapse to one score.
        reqs = normalize_requirements({"load_cases": [{"id": "y1", "type": "yield", "magnitude": 200, "unit": "MPa"}]})
        result = rank_candidates(reqs, candidates=["aisi_1045", "aa6061_t6"])
        for cand in result["nominal"]:
            assert "tradeoffs" in cand
            assert isinstance(cand["tradeoffs"], dict)
            assert len(cand["tradeoffs"]) >= 2  # at least 2 properties exposed
            assert "score" not in cand
            assert "aggregate_score" not in cand

    def test_rank_computes_performance_indices_when_density_available(self):
        overrides = {
            "aisi_1045": {
                "density": {
                    "value": 7.85,
                    "unit": "g/cm^3",
                    "uncertainty": 0.1,
                    "tier": "handbook",
                }
            }
        }
        reqs = normalize_requirements({"load_cases": [{"id": "y1", "type": "yield", "magnitude": 200, "unit": "MPa"}]})
        result = rank_candidates(reqs, candidates=["aisi_1045"], overrides=overrides)
        indices = result["nominal"][0]["performance_indices"]
        assert "specific_strength" in indices
        # specific_strength = yield_MPa / density_g_cm3 = 310 / 7.85
        assert indices["specific_strength"] == pytest.approx(310.0 / 7.85, rel=1e-2)

    def test_ranking_invalid_without_any_load_cases(self):
        # MATE-DEC-001: ranking SHALL be invalid until mandatory loads are
        # present or explicitly marked unknown. No load_cases -> nothing to
        # rank against.
        reqs = normalize_requirements({})
        result = rank_candidates(reqs, candidates=["aisi_1045"])
        assert result["verdict"] == "insufficient_data"


# ---------------------------------------------------------------------------
# Data hierarchy (MATE-DEC-003: lot > supplier > handbook > estimated)
# ---------------------------------------------------------------------------


class TestDataHierarchy:
    def test_data_tiers_ordered_lot_beats_handbook(self):
        assert DATA_TIERS[0] == "lot"
        assert DATA_TIERS.index("lot") < DATA_TIERS.index("handbook")
        assert DATA_TIERS.index("handbook") < DATA_TIERS.index("estimated")

    def test_resolve_property_prefers_lot_override_over_handbook(self):
        overrides = {
            "aisi_1045": {
                "yield_strength": {
                    "value": 295.0,
                    "unit": "MPa",
                    "uncertainty": 5.0,
                    "tier": "lot",
                    "method": "ISO 6892",
                }
            }
        }
        prop, tier = resolve_property("aisi_1045", "yield_strength", overrides=overrides)
        assert prop is not None
        assert tier == "lot"
        assert prop["value"] == 295.0  # lot value, not handbook 310
        assert prop["uncertainty"] == 5.0

    def test_resolve_property_falls_back_to_handbook_registry(self):
        prop, tier = resolve_property("aisi_1045", "yield_strength")
        assert prop is not None
        assert tier == "handbook"
        assert prop["value"] == 310.0

    def test_estimated_tier_surfaces_wide_uncertainty_in_conservative(self):
        # MATE-SAFE-003: estimated data SHALL widen uncertainty or return
        # insufficient_data. Here an estimated yield with +/-50% uncertainty
        # causes the conservative case to fail against a requirement the
        # nominal case passes -- the uncertainty must be surfaced, not hidden.
        overrides = {
            "epoxy_cast": {
                "yield_strength": {
                    "value": 60.0,
                    "unit": "MPa",
                    "uncertainty": 30.0,
                    "tier": "estimated",
                    "basis": "analogy_to_similar_resin",
                }
            }
        }
        reqs = normalize_requirements({"load_cases": [{"id": "y1", "type": "yield", "magnitude": 40, "unit": "MPa"}]})
        result = rank_candidates(reqs, candidates=["epoxy_cast"], overrides=overrides)
        nominal_margin = result["nominal"][0]["margins"][0]
        cons_margin = result["conservative"][0]["margins"][0]
        assert nominal_margin["data_tier"] == "estimated"
        assert nominal_margin["state"] == "pass"  # 60 > 40
        # conservative: 60 - 30 = 30 < 40 -> fail (wide uncertainty surfaced)
        assert cons_margin["state"] == "fail"

    def test_insufficient_context_property_not_used_for_margin(self):
        # ABS yield_strength has state=INSUFFICIENT_CONTEXT (no condition
        # metadata). MATE-SAFE-003: do not fabricate precision from it.
        reqs = normalize_requirements({"load_cases": [{"id": "y1", "type": "yield", "magnitude": 30, "unit": "MPa"}]})
        result = rank_candidates(reqs, candidates=["abs"])
        margin = result["nominal"][0]["margins"][0]
        assert margin["state"] == "insufficient_data"


# ---------------------------------------------------------------------------
# Analytical strength checks (spec section 4.7)
# ---------------------------------------------------------------------------


class TestStrengthTension:
    def test_tension_margin_matches_handcalc(self):
        prop = {"value": 310.0, "unit": "MPa", "uncertainty": 20.0, "basis": "nominal"}
        result = check_tension(prop, applied_stress_MPa=200.0)
        # margin = (310 - 200) / 200 = 0.55
        assert result["state"] == "pass"
        assert result["margin"] == pytest.approx(0.55, rel=1e-3)
        assert result["capacity"] == 310.0
        assert result["applied"] == 200.0
        assert result["unit"] == "MPa"
        assert result["uncertainty"] == 20.0

    def test_tension_fail_on_negative_margin(self):
        prop = {"value": 100.0, "unit": "MPa", "uncertainty": 10.0}
        result = check_tension(prop, applied_stress_MPa=150.0)
        assert result["state"] == "fail"
        assert result["margin"] < 0


class TestStrengthCompressionShear:
    def test_compression_margin(self):
        prop = {"value": 250.0, "unit": "MPa", "uncertainty": 20.0}
        result = check_compression(prop, applied_stress_MPa=200.0)
        assert result["state"] == "pass"
        assert result["margin"] == pytest.approx(0.25)
        assert result["failure_mode"] == "compression_failure"

    def test_shear_margin(self):
        prop = {"value": 180.0, "unit": "MPa", "uncertainty": 15.0}
        result = check_shear(prop, applied_stress_MPa=100.0)
        assert result["state"] == "pass"
        assert result["margin"] == pytest.approx(0.8, rel=1e-3)
        assert result["failure_mode"] == "shear_failure"


class TestStrengthBendingBuckling:
    def test_bending_extreme_fiber_margin(self):
        # sigma = M*c/I = 10000*10/1000 = 100 MPa; yield=310 -> margin=2.1
        prop = {"value": 310.0, "unit": "MPa", "uncertainty": 20.0}
        result = check_bending(prop, applied_moment_Nmm=10000.0, c_mm=10.0, I_mm4=1000.0)
        assert result["state"] == "pass"
        assert result["applied"] == pytest.approx(100.0)
        assert result["margin"] == pytest.approx(2.1, rel=1e-3)
        assert result["unit"] == "MPa"

    def test_euler_buckling_matches_reference(self):
        # P_cr = pi^2 * E * I / (K*L)^2
        # E=200000 MPa, I=1000 mm^4, L=1000 mm, K=1.0
        # P_cr = pi^2 * 200000 * 1000 / 1e6 = pi^2 * 200 = 1973.92 N
        result = check_buckling_euler(
            E_MPa=200000.0,
            I_mm4=1000.0,
            L_mm=1000.0,
            K=1.0,
            applied_force_N=1000.0,
        )
        assert result["state"] == "pass"
        assert result["capacity"] == pytest.approx(math.pi**2 * 200000.0 * 1000.0 / (1.0 * 1000.0) ** 2, rel=1e-3)
        assert result["unit"] == "N"
        assert result["margin"] > 0
        assert "euler" in result["equation_id"].lower()


class TestStrengthThermalFatigue:
    def test_thermal_stress_fully_constrained(self):
        # sigma = E * alpha * dT = 200000 * 12e-6 * 100 = 240 MPa
        # yield = 310 -> margin = (310-240)/240 = 0.2917
        prop = {"value": 310.0, "unit": "MPa", "uncertainty": 20.0}
        result = check_thermal_stress(E_MPa=200000.0, alpha_per_K=12e-6, delta_T_K=100.0, capacity_prop=prop)
        assert result["state"] == "pass"
        assert result["applied"] == pytest.approx(240.0, rel=1e-3)
        assert result["margin"] == pytest.approx((310.0 - 240.0) / 240.0, rel=1e-2)
        assert "thermal" in result["equation_id"].lower() or "alpha" in result["equation_id"].lower()

    def test_fatigue_sn_baseline_estimated_endurance(self):
        # S_ut = 565 MPa, cycles = 1e6 -> estimated S_e = 0.5*S_ut = 282.5 MPa
        # applied amplitude = 200 MPa -> margin = (282.5-200)/200 = 0.4125
        result = check_fatigue_sn(S_ut_MPa=565.0, applied_amplitude_MPa=200.0, cycles=1_000_000)
        assert result["state"] == "pass"
        assert result["capacity"] == pytest.approx(282.5, rel=1e-3)
        assert result["margin"] == pytest.approx(0.4125, rel=1e-2)
        # Estimated endurance limit must be labeled and carry wide uncertainty
        assert any("estimated" in a.lower() for a in result["assumptions"])
        assert result["uncertainty"] >= 0.1 * 282.5  # >= 10% of capacity

    def test_fatigue_uses_measured_endurance_when_supplied(self):
        result = check_fatigue_sn(
            S_ut_MPa=565.0,
            applied_amplitude_MPa=200.0,
            cycles=1_000_000,
            S_e_MPa=260.0,
        )
        assert result["capacity"] == pytest.approx(260.0)
        assert result["margin"] == pytest.approx((260.0 - 200.0) / 200.0, rel=1e-2)
        # Measured S_e should not carry the "estimated" flag
        assert not any("estimated" in a.lower() for a in result["assumptions"])

    def test_fatigue_finite_life_uses_basquin_interpolation(self):
        # At N=1e3, allowable ~= 0.9*S_ut per Shigley baseline.
        result = check_fatigue_sn(S_ut_MPa=500.0, applied_amplitude_MPa=400.0, cycles=1_000)
        assert result["capacity"] == pytest.approx(450.0, rel=1e-2)
        assert result["margin"] == pytest.approx((450.0 - 400.0) / 400.0, rel=1e-2)


class TestStrengthTraceabilityAndFailClosed:
    def test_strength_returns_equation_id_and_inputs(self):
        # MATE-DEC-004: every derived value retains equation id + inputs.
        prop = {"value": 310.0, "unit": "MPa", "uncertainty": 20.0}
        result = check_tension(prop, applied_stress_MPa=200.0)
        assert result["equation_id"]
        assert isinstance(result["inputs"], dict)
        assert result["inputs"]  # non-empty
        assert "capacity" in result["inputs"] or "yield_strength" in result["inputs"]

    def test_strength_fail_closed_on_zero_or_negative_applied(self):
        prop = {"value": 310.0, "unit": "MPa", "uncertainty": 20.0}
        result = check_tension(prop, applied_stress_MPa=0.0)
        assert result["state"] in ("insufficient_data", "fail_closed")
        assert result["margin"] is None or result["margin"] != result["margin"]  # None or NaN

    def test_buckling_rejects_invalid_geometry(self):
        # Non-positive length or I -> fail closed.
        result = check_buckling_euler(E_MPa=200000.0, I_mm4=0.0, L_mm=1000.0, K=1.0, applied_force_N=100.0)
        assert result["state"] in ("insufficient_data", "fail_closed")
