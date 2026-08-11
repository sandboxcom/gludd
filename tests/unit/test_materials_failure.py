"""Dedicated tests for ``general_ludd.materials.failure`` (MATE-001 §3, §4.7).

The ``FailureAnalyzer`` enumerates *competing* failure hypotheses (yield,
fracture, fatigue, creep, buckling) from a load case + material record and
prescribes a combined NDT + destructive test plan. Every hypothesis carries
an explicit ``confidence_state`` in ``{candidate, ruled_out, insufficient_data}`` —
``confirmed`` is intentionally forbidden (MATE-SAFE-003: no fabricated
precision; root cause requires physical evidence).

These tests complement ``test_materials_tolerance`` by exercising the helper
functions, the per-mode screening branches, and the test-plan assembly in
isolation. Verdict-dict shape mirrors ``test_materials_strength``.
"""

from __future__ import annotations

import pytest

from general_ludd.materials.failure import (
    CANDIDATE,
    CONFIDENCE_STATES,
    INSUFFICIENT,
    RULED_OUT,
    FailureAnalyzer,
    _as_prop,
    _extract_stress,
)

# ─── Module-level constants ────────────────────────────────────────────────────


class TestConfidenceStates:
    def test_confirmed_is_excluded_from_states(self):
        # MATE-SAFE-003 invariant: the analyzer may never claim confirmation.
        assert "confirmed" not in CONFIDENCE_STATES

    def test_exported_state_constants_match_set(self):
        assert frozenset({CANDIDATE, RULED_OUT, INSUFFICIENT}) == CONFIDENCE_STATES
        assert CANDIDATE == "candidate"
        assert RULED_OUT == "ruled_out"
        assert INSUFFICIENT == "insufficient_data"


# ─── Helpers ───────────────────────────────────────────────────────────────────


class TestHelpers:
    def test_as_prop_wraps_mpa_value(self):
        out = _as_prop(250.0)
        assert out == {"value": 250.0, "unit": "MPa", "uncertainty": 0.0}

    def test_as_prop_none_passes_through(self):
        assert _as_prop(None) is None

    def test_extract_stress_falls_back_across_keys(self):
        # The analyzer must accept any of the documented stress keys.
        for key in ("max_stress_MPa", "stress_MPa", "applied_stress_MPa"):
            assert _extract_stress({key: 180.0}) == pytest.approx(180.0)

    def test_extract_stress_rejects_non_positive(self):
        # Zero or negative stress is physically meaningless for screening.
        assert _extract_stress({"max_stress_MPa": 0.0}) is None
        assert _extract_stress({"max_stress_MPa": -10.0}) is None
        assert _extract_stress({"type": "static"}) is None


# ─── develop_hypotheses — per-mode screening ───────────────────────────────────


class TestDevelopHypotheses:
    def test_returns_one_entry_per_failure_mode(self):
        analyzer = FailureAnalyzer()
        hyps = analyzer.develop_hypotheses(
            load_case={"type": "static", "stress_MPa": 100.0, "temperature_K": 300.0},
            material={"yield_MPa": 250.0, "ultimate_MPa": 400.0},
        )
        modes = [h["failure_mode"] for h in hyps]
        assert modes == ["yield", "fracture", "fatigue", "creep", "buckling"]

    def test_fracture_candidate_when_stress_exceeds_ultimate(self):
        analyzer = FailureAnalyzer()
        hyps = analyzer.develop_hypotheses(
            load_case={"type": "static", "stress_MPa": 450.0},
            material={"yield_MPa": 250.0, "ultimate_MPa": 400.0},
        )
        fracture = next(h for h in hyps if h["failure_mode"] == "fracture")
        assert fracture["confidence_state"] == CANDIDATE
        # A candidate must carry the inputs used to reach the verdict.
        assert fracture["inputs"]["ultimate_MPa"] == pytest.approx(400.0)

    def test_fatigue_ruled_out_when_load_is_not_cyclic(self):
        analyzer = FailureAnalyzer()
        hyps = analyzer.develop_hypotheses(
            load_case={"type": "static", "stress_MPa": 100.0},
            material={"yield_MPa": 250.0, "ultimate_MPa": 400.0, "endurance_MPa": 120.0},
        )
        fatigue = next(h for h in hyps if h["failure_mode"] == "fatigue")
        assert fatigue["confidence_state"] == RULED_OUT

    def test_fatigue_insufficient_when_cyclic_but_missing_endurance(self):
        analyzer = FailureAnalyzer()
        hyps = analyzer.develop_hypotheses(
            load_case={"type": "cyclic", "max_stress_MPa": 100.0, "cycles": 1_000_000},
            material={"yield_MPa": 250.0, "ultimate_MPa": 400.0},  # no endurance
        )
        fatigue = next(h for h in hyps if h["failure_mode"] == "fatigue")
        assert fatigue["confidence_state"] == INSUFFICIENT
        assert "missing endurance" in fatigue["rationale"]

    def test_creep_candidate_above_homologous_threshold(self):
        # T/Tm >= 0.4 must surface creep as a candidate. Use explicit melt_K
        # so the verdict is independent of the steel fallback constant.
        analyzer = FailureAnalyzer()
        hyps = analyzer.develop_hypotheses(
            load_case={"type": "static", "temperature_K": 800.0},
            material={"melt_K": 1000.0},
        )
        creep = next(h for h in hyps if h["failure_mode"] == "creep")
        assert creep["confidence_state"] == CANDIDATE
        assert creep["inputs"]["melt_K"] == pytest.approx(1000.0)

    def test_creep_ruled_out_below_homologous_threshold(self):
        analyzer = FailureAnalyzer()
        hyps = analyzer.develop_hypotheses(
            load_case={"type": "static", "temperature_K": 300.0},
            material={"melt_K": 1810.0},
        )
        creep = next(h for h in hyps if h["failure_mode"] == "creep")
        assert creep["confidence_state"] == RULED_OUT

    def test_creep_insufficient_when_temperature_missing(self):
        analyzer = FailureAnalyzer()
        hyps = analyzer.develop_hypotheses(
            load_case={"type": "static"},
            material={"melt_K": 1810.0},
        )
        creep = next(h for h in hyps if h["failure_mode"] == "creep")
        assert creep["confidence_state"] == INSUFFICIENT

    def test_buckling_insufficient_when_compressive_without_geometry(self):
        # Compressive load present but no E/I/L → cannot screen, must fail-open.
        analyzer = FailureAnalyzer()
        hyps = analyzer.develop_hypotheses(
            load_case={"type": "compressive", "compressive_force_N": 5000.0},
            material={"yield_MPa": 250.0},
        )
        buckling = next(h for h in hyps if h["failure_mode"] == "buckling")
        assert buckling["confidence_state"] == INSUFFICIENT
        assert "geometry" in buckling["rationale"]


# ─── prescribe_tests — plan assembly ───────────────────────────────────────────


class TestPrescribeTests:
    def test_no_candidates_adds_baseline_ndt_and_limitation(self):
        analyzer = FailureAnalyzer()
        # All hypotheses ruled_out/insufficient → no targeted destructive tests.
        hyps = [
            {"failure_mode": "yield", "confidence_state": RULED_OUT, "rationale": "ok"},
            {"failure_mode": "creep", "confidence_state": INSUFFICIENT, "rationale": "no T"},
        ]
        plan = analyzer.prescribe_tests(hyps)
        # Baseline visual/dimensional NDT must still be prescribed.
        cats = {t["category"] for t in plan["tests"]}
        assert "nondestructive" in cats
        # Caller must be told no candidate was identified.
        assert any("no candidate" in lim for lim in plan["limitations"])

    def test_insufficient_hypotheses_surfaced_in_limitations(self):
        analyzer = FailureAnalyzer()
        hyps = [
            {"failure_mode": "fracture", "confidence_state": CANDIDATE, "rationale": "ok"},
            {"failure_mode": "buckling", "confidence_state": INSUFFICIENT, "rationale": "missing I"},
        ]
        plan = analyzer.prescribe_tests(hyps)
        joined = " | ".join(plan["limitations"])
        assert "buckling" in joined and "missing I" in joined

    def test_causality_note_forbids_analytical_confirmation(self):
        analyzer = FailureAnalyzer()
        plan = analyzer.prescribe_tests([{"failure_mode": "yield", "confidence_state": CANDIDATE, "rationale": "x"}])
        note = plan["causality_note"]
        # The note must explicitly disown confirmation without physical evidence.
        assert "candidate" in note or "physical evidence" in note
        assert "MATE-SAFE-003" in note

    def test_creep_candidate_prescribes_creep_rupture(self):
        analyzer = FailureAnalyzer()
        plan = analyzer.prescribe_tests(
            [{"failure_mode": "creep", "confidence_state": CANDIDATE, "rationale": "T/Tm high"}]
        )
        methods = {t["method"] for t in plan["tests"]}
        assert "creep_rupture_test" in methods
        assert "metallography_sectioning" in methods

    def test_fatigue_candidate_prescribes_penetrant_and_sn_fatigue(self):
        analyzer = FailureAnalyzer()
        plan = analyzer.prescribe_tests(
            [{"failure_mode": "fatigue", "confidence_state": CANDIDATE, "rationale": "cyclic"}]
        )
        methods = {t["method"] for t in plan["tests"]}
        assert "fluorescent_penetrant" in methods
        assert "S-N_fatigue_test" in methods
        assert any(t["category"] == "nondestructive" for t in plan["tests"])
        assert any(t["category"] == "destructive" for t in plan["tests"])

    def test_fracture_candidate_prescribes_sem_and_charpy(self):
        analyzer = FailureAnalyzer()
        plan = analyzer.prescribe_tests(
            [{"failure_mode": "fracture", "confidence_state": CANDIDATE, "rationale": "overload"}]
        )
        methods = {t["method"] for t in plan["tests"]}
        assert "SEM_fractography" in methods
        assert "charpy_impact" in methods

    def test_yield_candidate_prescribes_tensile_test(self):
        analyzer = FailureAnalyzer()
        plan = analyzer.prescribe_tests(
            [{"failure_mode": "yield", "confidence_state": CANDIDATE, "rationale": "stress > yield"}]
        )
        methods = {t["method"] for t in plan["tests"]}
        assert "tensile_test" in methods
        assert any("ASTM E8" in t.get("specimen", "") for t in plan["tests"])

    def test_buckling_candidate_prescribes_dimensional_inspection(self):
        analyzer = FailureAnalyzer()
        plan = analyzer.prescribe_tests(
            [{"failure_mode": "buckling", "confidence_state": CANDIDATE, "rationale": "slender column"}]
        )
        methods = {t["method"] for t in plan["tests"]}
        assert "dimensional_inspection" in methods
        assert any(t["category"] == "nondestructive" for t in plan["tests"])

    def test_multiple_candidates_prescribes_all_modes(self):
        analyzer = FailureAnalyzer()
        hyps = [
            {"failure_mode": "yield", "confidence_state": CANDIDATE, "rationale": "high stress"},
            {"failure_mode": "fatigue", "confidence_state": CANDIDATE, "rationale": "cyclic"},
            {"failure_mode": "creep", "confidence_state": CANDIDATE, "rationale": "hot"},
        ]
        plan = analyzer.prescribe_tests(hyps)
        methods = {t["method"] for t in plan["tests"]}
        assert "tensile_test" in methods
        assert "S-N_fatigue_test" in methods
        assert "creep_rupture_test" in methods


# ─── develop_hypotheses — deep edge cases ──────────────────────────────────────


class TestDevelopHypothesesDeep:
    def test_fatigue_candidate_with_full_cyclic_inputs(self):
        analyzer = FailureAnalyzer()
        hyps = analyzer.develop_hypotheses(
            load_case={"type": "cyclic", "stress_MPa": 220.0, "cycles": 1_000_000, "temperature_K": 300.0},
            material={"yield_MPa": 250.0, "ultimate_MPa": 550.0, "endurance_MPa": 120.0},
        )
        fatigue = next(h for h in hyps if h["failure_mode"] == "fatigue")
        assert fatigue["confidence_state"] == CANDIDATE
        assert fatigue["inputs"]["cycles"] == 1_000_000
        assert fatigue["inputs"]["endurance_MPa"] == pytest.approx(120.0)

    def test_buckling_candidate_with_full_geometry(self):
        analyzer = FailureAnalyzer()
        hyps = analyzer.develop_hypotheses(
            load_case={
                "type": "compressive",
                "compressive_force_N": 50000.0,
                "E_MPa": 200000.0,
                "I_mm4": 10.0,
                "L_mm": 2000.0,
                "temperature_K": 300.0,
            },
            material={"yield_MPa": 250.0, "ultimate_MPa": 400.0},
        )
        buckling = next(h for h in hyps if h["failure_mode"] == "buckling")
        assert buckling["confidence_state"] == CANDIDATE
        assert buckling["inputs"]["E_MPa"] == pytest.approx(200000.0)
        assert buckling["inputs"]["L_mm"] == pytest.approx(2000.0)

    def test_buckling_ruled_out_without_compressive_load(self):
        analyzer = FailureAnalyzer()
        hyps = analyzer.develop_hypotheses(
            load_case={"type": "static", "temperature_K": 300.0},
            material={"yield_MPa": 250.0, "ultimate_MPa": 400.0},
        )
        buckling = next(h for h in hyps if h["failure_mode"] == "buckling")
        assert buckling["confidence_state"] == RULED_OUT
        assert "no compressive" in buckling["rationale"]

    def test_creep_uses_steel_melt_k_fallback(self):
        analyzer = FailureAnalyzer()
        hyps = analyzer.develop_hypotheses(
            load_case={"type": "static", "temperature_K": 900.0},
            material={"yield_MPa": 250.0},
        )
        creep = next(h for h in hyps if h["failure_mode"] == "creep")
        assert creep["inputs"]["melt_K"] == pytest.approx(1810.0)

    def test_yield_insufficient_when_both_missing(self):
        analyzer = FailureAnalyzer()
        hyps = analyzer.develop_hypotheses(
            load_case={"type": "static"},
            material={},
        )
        yield_hyp = next(h for h in hyps if h["failure_mode"] == "yield")
        assert yield_hyp["confidence_state"] == INSUFFICIENT
        assert "missing" in yield_hyp["rationale"].lower()

    def test_all_hypotheses_carry_inputs_dict(self):
        analyzer = FailureAnalyzer()
        hyps = analyzer.develop_hypotheses(
            load_case={"type": "static", "stress_MPa": 100.0, "temperature_K": 300.0},
            material={"yield_MPa": 250.0, "ultimate_MPa": 400.0},
        )
        for h in hyps:
            assert "inputs" in h, f"missing inputs for {h['failure_mode']}"
            assert isinstance(h["inputs"], dict)

    def test_cyclic_detection_via_alternating_keyword(self):
        analyzer = FailureAnalyzer()
        hyps = analyzer.develop_hypotheses(
            load_case={"type": "alternating_load", "max_stress_MPa": 100.0},
            material={"yield_MPa": 250.0, "ultimate_MPa": 400.0},
        )
        fatigue = next(h for h in hyps if h["failure_mode"] == "fatigue")
        assert fatigue["confidence_state"] != RULED_OUT

    def test_fatigue_insufficient_when_cyclic_but_zero_cycles(self):
        analyzer = FailureAnalyzer()
        hyps = analyzer.develop_hypotheses(
            load_case={"type": "cyclic", "max_stress_MPa": 100.0, "cycles": 0},
            material={"yield_MPa": 250.0, "ultimate_MPa": 400.0, "endurance_MPa": 120.0},
        )
        fatigue = next(h for h in hyps if h["failure_mode"] == "fatigue")
        assert fatigue["confidence_state"] == INSUFFICIENT

    def test_buckling_ruled_out_with_non_compressive_no_force(self):
        analyzer = FailureAnalyzer()
        hyps = analyzer.develop_hypotheses(
            load_case={"type": "tensile", "temperature_K": 300.0},
            material={"yield_MPa": 250.0},
        )
        buckling = next(h for h in hyps if h["failure_mode"] == "buckling")
        assert buckling["confidence_state"] == RULED_OUT
        assert "no compressive" in buckling["rationale"]


# ─── integration: develop + prescribe ──────────────────────────────────────────


class TestIntegrationDevelopPrescribe:
    def test_end_to_end_static_overload_yields_candidate_and_tensile(self):
        analyzer = FailureAnalyzer()
        hyps = analyzer.develop_hypotheses(
            load_case={"type": "static", "stress_MPa": 350.0, "temperature_K": 300.0},
            material={"yield_MPa": 250.0, "ultimate_MPa": 400.0},
        )
        yield_hyp = next(h for h in hyps if h["failure_mode"] == "yield")
        assert yield_hyp["confidence_state"] == CANDIDATE
        plan = analyzer.prescribe_tests(hyps)
        methods = {t["method"] for t in plan["tests"]}
        assert "tensile_test" in methods

    def test_end_to_end_cyclic_load_yields_fatigue_candidate_and_ndt(self):
        analyzer = FailureAnalyzer()
        hyps = analyzer.develop_hypotheses(
            load_case={"type": "cyclic", "stress_MPa": 200.0, "cycles": 500_000, "temperature_K": 300.0},
            material={"yield_MPa": 250.0, "ultimate_MPa": 400.0, "endurance_MPa": 100.0},
        )
        fatigue = next(h for h in hyps if h["failure_mode"] == "fatigue")
        assert fatigue["confidence_state"] == CANDIDATE
        plan = analyzer.prescribe_tests(hyps)
        categories = {t["category"] for t in plan["tests"]}
        assert "nondestructive" in categories
        assert "destructive" in categories

    def test_end_to_end_compressive_yields_buckling_candidate(self):
        analyzer = FailureAnalyzer()
        hyps = analyzer.develop_hypotheses(
            load_case={
                "type": "compressive",
                "compressive_force_N": 500000.0,
                "E_MPa": 200000.0,
                "I_mm4": 10.0,
                "L_mm": 2000.0,
                "temperature_K": 300.0,
            },
            material={"yield_MPa": 250.0, "ultimate_MPa": 400.0},
        )
        buckling = next(h for h in hyps if h["failure_mode"] == "buckling")
        assert buckling["confidence_state"] == CANDIDATE
        plan = analyzer.prescribe_tests(hyps)
        assert "causality_note" in plan
        assert "MATE-SAFE-003" in plan["causality_note"]
