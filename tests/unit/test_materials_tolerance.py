"""Tests for MATE-P2/P3 tolerance modeling and failure analysis.

Covers spec MATE-001 section 3 (``tolerance_model`` and ``failure_analyze``
roles) and section 4.7 (strength/build modeling). Mirrors the verdict-dict style
of ``test_materials_strength`` so every result carries equation id, inputs with
units, assumptions, and an explicit ``state``.

Verifies:
  - Worst-case (linear) dimensional-chain stack-up.
  - RSS (statistical) tolerance stack-up is tighter than worst-case.
  - Thermal expansion delta and dimensional compensation for elevated service.
  - Process capability Cp/Cpk from spec limits + sigma.
  - Assembly clearance / interference classification.
  - Competing failure hypotheses (yield/fracture/fatigue/creep/buckling).
  - Hypotheses never overstate causality (no "confirmed" state).
  - Prescribed tests cite the hypothesis each would confirm/refute.
"""

from __future__ import annotations

import math

import pytest

from general_ludd.materials.failure import FailureAnalyzer
from general_ludd.materials.tolerance import (
    ToleranceChain,
    assess_assembly,
    process_capability,
)

# ─── ToleranceChain: worst-case stack-up ───────────────────────────────────────


class TestWorstCaseStackUp:
    def test_three_equal_dims_sum_linearly(self):
        chain = ToleranceChain(dims=[(10.0, 0.1), (10.0, 0.1), (10.0, 0.1)], unit="mm")
        wc = chain.worst_case_stackup()
        assert wc["nominal"] == pytest.approx(30.0)
        assert wc["upper"] == pytest.approx(30.3)
        assert wc["lower"] == pytest.approx(29.7)
        assert wc["unit"] == "mm"
        assert wc["equation_id"] == "worst-case: band = sum(|t_i|)"
        assert wc["state"] == "ok"

    def test_empty_chain_fails_closed(self):
        chain = ToleranceChain(dims=[], unit="mm")
        wc = chain.worst_case_stackup()
        assert wc["state"] == "fail_closed"
        assert wc["nominal"] == 0.0
        assert wc["band"] == 0.0


# ─── ToleranceChain: RSS statistical stack-up ─────────────────────────────────


class TestRSSStackUp:
    def test_rss_of_three_equal_tolerances(self):
        chain = ToleranceChain(dims=[(10.0, 0.1), (10.0, 0.1), (10.0, 0.1)], unit="mm")
        rss = chain.rss_stackup()
        assert rss["nominal"] == pytest.approx(30.0)
        assert rss["sigma_band"] == pytest.approx(math.sqrt(3) * 0.1, rel=1e-4)
        assert rss["upper"] == pytest.approx(30.0 + math.sqrt(3) * 0.1, rel=1e-4)
        assert rss["lower"] == pytest.approx(30.0 - math.sqrt(3) * 0.1, rel=1e-4)

    def test_rss_is_tighter_than_worst_case(self):
        chain = ToleranceChain(dims=[(20.0, 0.2), (15.0, 0.15), (5.0, 0.1)], unit="mm")
        wc = chain.worst_case_stackup()
        rss = chain.rss_stackup()
        assert (rss["upper"] - rss["lower"]) < (wc["upper"] - wc["lower"])
        assert rss["nominal"] == wc["nominal"]


# ─── Thermal expansion delta + compensation ────────────────────────────────────


class TestThermalExpansion:
    def test_steel_rod_heated_100K(self):
        # 1000 mm rod, alpha=12e-6 /K, ΔT=+100 K → ΔL ≈ 1.2 mm
        chain = ToleranceChain(dims=[(1000.0, 0.0)], unit="mm")
        out = chain.thermal_expansion_delta(alpha_per_K=12e-6, delta_T_K=100.0)
        assert out["delta"] == pytest.approx(1.2, rel=1e-4)
        assert out["unit"] == "mm"
        assert out["equation_id"] == "thermal: dL = alpha * L0 * dT"

    def test_zero_delta_T_is_zero_delta(self):
        chain = ToleranceChain(dims=[(500.0, 0.0)], unit="mm")
        out = chain.thermal_expansion_delta(alpha_per_K=23e-6, delta_T_K=0.0)
        assert out["delta"] == 0.0

    def test_compensation_target_returns_negative_of_delta(self):
        # A part dimensioned at room temp and assembled at +ΔT must be cut
        # SHORTER by exactly the predicted growth to fit at temperature.
        chain = ToleranceChain(dims=[(1000.0, 0.0)], unit="mm")
        comp = chain.thermal_compensation(alpha_per_K=12e-6, delta_T_K=100.0)
        assert comp["compensation"] == pytest.approx(-1.2, rel=1e-4)
        assert comp["state"] == "ok"


# ─── Process capability Cp / Cpk ───────────────────────────────────────────────


class TestProcessCapability:
    def test_centered_process_cpk_equals_cp(self):
        # Spec 10.0 ± 0.3, sigma=0.05, centered → Cp = Cpk = 2.0
        out = process_capability(spec_lower=9.7, spec_upper=10.3, sigma=0.05, mean=10.0)
        assert out["Cp"] == pytest.approx(2.0)
        assert out["Cpk"] == pytest.approx(2.0)
        assert out["state"] == "ok"

    def test_off_center_lowers_cpk_only(self):
        # Same spec/sigma, mean shifted toward USL → Cpk < Cp
        out = process_capability(spec_lower=9.7, spec_upper=10.3, sigma=0.05, mean=10.1)
        assert out["Cp"] == pytest.approx(2.0)
        assert out["Cpk"] < out["Cp"]
        # Cpk = min((10.3-10.1)/(3*0.05), (10.1-9.7)/(3*0.05)) = min(1.333, 2.667)
        assert out["Cpk"] == pytest.approx(4.0 / 3.0, rel=1e-4)

    def test_zero_sigma_fails_closed(self):
        out = process_capability(spec_lower=9.7, spec_upper=10.3, sigma=0.0)
        assert out["state"] == "fail_closed"
        assert "sigma must be a positive finite number" in out["reason"]


# ─── Assembly clearance / interference ─────────────────────────────────────────


class TestAssessAssembly:
    def test_clearance_fit(self):
        out = assess_assembly(
            hole_nominal=10.0,
            hole_tol=0.05,
            shaft_nominal=9.8,
            shaft_tol=0.1,
            unit="mm",
        )
        assert out["fit_class"] == "clearance"
        assert out["min_clearance"] > 0.0
        assert out["max_clearance"] > 0.0

    def test_interference_fit(self):
        out = assess_assembly(
            hole_nominal=10.0,
            hole_tol=0.05,
            shaft_nominal=10.2,
            shaft_tol=0.05,
            unit="mm",
        )
        assert out["fit_class"] == "interference"
        assert out["max_clearance"] < 0.0


# ─── FailureAnalyzer: competing hypotheses ─────────────────────────────────────


class TestFailureHypotheses:
    def test_returns_multiple_competing_modes(self):
        analyzer = FailureAnalyzer()
        material = {"yield_MPa": 250.0, "ultimate_MPa": 400.0, "endurance_MPa": 120.0}
        load = {
            "type": "cyclic_axial",
            "max_stress_MPa": 200.0,
            "cycles": 1_000_000,
            "temperature_K": 650.0,
        }
        hyps = analyzer.develop_hypotheses(load_case=load, material=material)
        assert len(hyps) >= 3
        modes = {h["failure_mode"] for h in hyps}
        assert {"yield", "fatigue"} <= modes
        # Elevated temperature must surface creep as a candidate.
        assert "creep" in modes

    def test_hypotheses_never_claim_confirmation(self):
        analyzer = FailureAnalyzer()
        hyps = analyzer.develop_hypotheses(
            load_case={"type": "static", "stress_MPa": 100.0},
            material={"yield_MPa": 250.0, "ultimate_MPa": 400.0},
        )
        for h in hyps:
            assert h["confidence_state"] in {"candidate", "ruled_out", "insufficient_data"}
            assert h["confidence_state"] != "confirmed"

    def test_yield_is_ruled_out_when_stress_below_capacity(self):
        analyzer = FailureAnalyzer()
        hyps = analyzer.develop_hypotheses(
            load_case={"type": "static", "stress_MPa": 50.0},
            material={"yield_MPa": 250.0, "ultimate_MPa": 400.0},
        )
        yield_h = next(h for h in hyps if h["failure_mode"] == "yield")
        assert yield_h["confidence_state"] == "ruled_out"


class TestFailureTestPlan:
    def test_plan_has_ndt_and_destructive(self):
        analyzer = FailureAnalyzer()
        hyps = analyzer.develop_hypotheses(
            load_case={"type": "cyclic", "max_stress_MPa": 200.0, "cycles": 1_000_000},
            material={"yield_MPa": 250.0, "ultimate_MPa": 400.0, "endurance_MPa": 120.0},
        )
        plan = analyzer.prescribe_tests(hypotheses=hyps)
        cats = {t["category"] for t in plan["tests"]}
        assert "nondestructive" in cats
        assert "destructive" in cats
        for t in plan["tests"]:
            assert t["targets_hypothesis"]

    def test_fracture_candidate_prescribes_fractography(self):
        analyzer = FailureAnalyzer()
        hyps = [{"failure_mode": "fracture", "confidence_state": "candidate", "rationale": "x"}]
        plan = analyzer.prescribe_tests(hypotheses=hyps)
        methods = {t["method"] for t in plan["tests"]}
        assert "SEM_fractography" in methods
