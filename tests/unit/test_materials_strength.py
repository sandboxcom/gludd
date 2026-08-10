"""Deep edge-case tests for materials/strength.py (MATE-001 §4.7).

Covers all 7 check functions with boundary, negative, zero, NaN, extreme
floating-point, missing-key, and non-numeric-input edge cases. Also validates
the MATE-SAFE-003 fail-closed invariant (no fabricated precision) and the
MATE-DEC-004 traceability contract (every verdict carries equation_id + inputs).
"""

from __future__ import annotations

import math
from typing import Any

import pytest

from general_ludd.materials.strength import (
    STATE_FAIL,
    STATE_FAIL_CLOSED,
    STATE_INSUFFICIENT,
    STATE_PASS,
    _extract_capacity,
    _stress_check,
    check_bending,
    check_buckling_euler,
    check_compression,
    check_fatigue_sn,
    check_shear,
    check_tension,
    check_thermal_stress,
)

# ── helpers ────────────────────────────────────────────────────────────────

CAP_GOOD: dict[str, object] = {
    "value": 250.0,
    "unit": "MPa",
    "uncertainty": 5.0,
}

CAP_VALUE_OR_RANGE: dict[str, object] = {
    "value_or_range": 180.0,
    "unit": "MPa",
}

CAP_MISSING: dict[str, object] = {
    "unit": "MPa",
    "uncertainty": 3.0,
}

CAP_ZERO: dict[str, object] = {
    "value": 0.0,
    "unit": "MPa",
}

CAP_NEGATIVE: dict[str, object] = {
    "value": -50.0,
    "unit": "MPa",
}

CAP_BOGUS: dict[str, object] = {
    "value": "twelve",
    "unit": "MPa",
}

CAP_NONE_VAL: dict[str, object] = {
    "value": None,
    "unit": "MPa",
}


def _verdict_keys_ok(v: dict) -> bool:
    required = {
        "failure_mode",
        "equation_id",
        "inputs",
        "assumptions",
        "state",
        "reason",
        "margin",
        "capacity",
        "applied",
        "unit",
        "uncertainty",
    }
    return required.issubset(v.keys())


# ── _extract_capacity ─────────────────────────────────────────────────────


class TestExtractCapacity:
    def test_value_key(self):
        assert _extract_capacity({"value": 42}) == 42.0

    def test_value_or_range_key(self):
        assert _extract_capacity({"value_or_range": 99}) == 99.0

    def test_value_wins_over_value_or_range(self):
        assert _extract_capacity({"value": 1, "value_or_range": 2}) == 1.0

    def test_int_is_float(self):
        assert isinstance(_extract_capacity({"value": 10}), float)

    def test_none_value(self):
        assert _extract_capacity({"value": None}) is None

    def test_missing_both(self):
        assert _extract_capacity({"unit": "MPa"}) is None

    def test_empty_dict(self):
        assert _extract_capacity({}) is None

    def test_string_value(self):
        assert _extract_capacity({"value": "high"}) is None

    def test_dict_value(self):
        assert _extract_capacity({"value": {"min": 10}}) is None

    def test_list_value(self):
        assert _extract_capacity({"value": [250]}) is None

    def test_true_bool_value(self):
        assert _extract_capacity({"value": True}) is None

    def test_false_bool_value(self):
        assert _extract_capacity({"value": False}) is None


# ── _stress_check core ────────────────────────────────────────────────────


class TestStressCheckCore:
    def test_normal_pass(self):
        v = _stress_check(CAP_GOOD, 100.0, "tensile_yield", "eq1")
        assert v["state"] == STATE_PASS
        assert v["margin"] == pytest.approx(1.5)
        assert _verdict_keys_ok(v)

    def test_normal_fail(self):
        v = _stress_check(CAP_GOOD, 300.0, "tensile_yield", "eq2")
        assert v["state"] == STATE_FAIL
        assert v["margin"] == pytest.approx((250 - 300) / 300)

    def test_margin_exactly_zero(self):
        v = _stress_check({"value": 100.0}, 100.0, "x", "eq")
        assert v["margin"] == 0.0
        assert v["state"] == STATE_FAIL

    def test_tiny_positive_margin(self):
        v = _stress_check({"value": 100.0001}, 100.0, "x", "eq")
        assert v["margin"] > 0
        assert v["state"] == STATE_PASS

    def test_insufficient_missing_capacity(self):
        v = _stress_check(CAP_MISSING, 100.0, "x", "eq")
        assert v["state"] == STATE_INSUFFICIENT
        assert v["margin"] is None

    def test_insufficient_zero_capacity(self):
        v = _stress_check(CAP_ZERO, 100.0, "x", "eq")
        assert v["state"] == STATE_INSUFFICIENT
        assert v["margin"] is None

    def test_insufficient_negative_capacity(self):
        v = _stress_check(CAP_NEGATIVE, 100.0, "x", "eq")
        assert v["state"] == STATE_INSUFFICIENT

    def test_fail_closed_zero_applied(self):
        v = _stress_check(CAP_GOOD, 0.0, "x", "eq")
        assert v["state"] == STATE_FAIL_CLOSED

    def test_fail_closed_negative_applied(self):
        v = _stress_check(CAP_GOOD, -10.0, "x", "eq")
        assert v["state"] == STATE_FAIL_CLOSED

    def test_fail_closed_nan_applied(self):
        v = _stress_check(CAP_GOOD, float("nan"), "x", "eq")
        assert v["state"] == STATE_FAIL_CLOSED

    def test_fail_closed_infinity_applied(self):
        v = _stress_check(CAP_GOOD, float("inf"), "x", "eq")
        assert v["state"] == STATE_FAIL_CLOSED

    def test_fail_closed_string_applied(self):
        bad: Any = "fifty"
        v = _stress_check(CAP_GOOD, bad, "x", "eq")
        assert v["state"] == STATE_FAIL_CLOSED

    def test_fail_closed_none_applied(self):
        bad: Any = None
        v = _stress_check(CAP_GOOD, bad, "x", "eq")
        assert v["state"] == STATE_FAIL_CLOSED

    def test_fail_closed_bool_applied(self):
        bad: Any = True
        v = _stress_check(CAP_GOOD, bad, "x", "eq")
        assert v["state"] == STATE_FAIL_CLOSED

    def test_value_or_range_capacity_used(self):
        v = _stress_check(CAP_VALUE_OR_RANGE, 60.0, "x", "eq")
        assert v["state"] == STATE_PASS
        assert v["capacity"] == 180.0

    def test_bogus_capacity_insufficient_data(self):
        v = _stress_check(CAP_BOGUS, 100.0, "x", "eq")
        assert v["state"] == STATE_INSUFFICIENT

    def test_none_val_capacity_insufficient(self):
        v = _stress_check(CAP_NONE_VAL, 100.0, "x", "eq")
        assert v["state"] == STATE_INSUFFICIENT

    def test_extra_inputs_merged(self):
        v = _stress_check(CAP_GOOD, 100.0, "x", "eq", extra_inputs={"temperature": {"value": 300, "unit": "K"}})
        assert "temperature" in v["inputs"]

    def test_assumptions_passed_through(self):
        v = _stress_check(CAP_GOOD, 100.0, "x", "eq", assumptions=["linear elastic", "isotropic"])
        assert "linear elastic" in v["assumptions"]

    def test_uncertainty_default_zero(self):
        v = _stress_check({"value": 250.0}, 100.0, "x", "eq")
        assert v["uncertainty"] == 0.0

    def test_very_large_capacity(self):
        v = _stress_check({"value": 1e12}, 1.0, "x", "eq")
        assert v["state"] == STATE_PASS
        assert v["margin"] > 1e10

    def test_very_small_applied(self):
        v = _stress_check({"value": 250.0}, 1e-12, "x", "eq")
        assert v["state"] == STATE_PASS
        assert v["margin"] > 1e12

    def test_capacity_just_below_applied(self):
        v = _stress_check({"value": 99.9999}, 100.0, "x", "eq")
        assert v["state"] == STATE_FAIL
        assert v["margin"] == pytest.approx(-0.000001, abs=1e-8)

    def test_float_edge_near_denorm(self):
        v = _stress_check({"value": 2e-308}, 1e-308, "x", "eq")
        assert v["margin"] == pytest.approx(1.0)


# ── check_tension ─────────────────────────────────────────────────────────


class TestCheckTension:
    def test_pass(self):
        v = check_tension(CAP_GOOD, 150.0)
        assert v["state"] == STATE_PASS
        assert v["failure_mode"] == "tensile_yield"
        assert "sigma=P/A" in v["equation_id"]

    def test_fail(self):
        v = check_tension(CAP_GOOD, 300.0)
        assert v["state"] == STATE_FAIL

    def test_insufficient_capacity(self):
        v = check_tension(CAP_MISSING, 100.0)
        assert v["state"] == STATE_INSUFFICIENT

    def test_zero_applied_closed(self):
        v = check_tension(CAP_GOOD, 0.0)
        assert v["state"] == STATE_FAIL_CLOSED

    def test_negative_applied_closed(self):
        v = check_tension(CAP_GOOD, -10.0)
        assert v["state"] == STATE_FAIL_CLOSED

    def test_reason_empty_on_pass(self):
        v = check_tension(CAP_GOOD, 100.0)
        assert v["reason"] == ""


# ── check_compression ─────────────────────────────────────────────────────


class TestCheckCompression:
    def test_pass(self):
        v = check_compression({"value": 300.0}, 200.0)
        assert v["state"] == STATE_PASS
        assert v["failure_mode"] == "compression_failure"

    def test_fail(self):
        v = check_compression({"value": 100.0}, 200.0)
        assert v["state"] == STATE_FAIL

    def test_negative_applied_closed(self):
        v = check_compression({"value": 300.0}, -1.0)
        assert v["state"] == STATE_FAIL_CLOSED

    def test_none_applied_closed(self):
        bad: Any = None
        v = check_compression({"value": 300.0}, bad)
        assert v["state"] == STATE_FAIL_CLOSED


# ── check_shear ───────────────────────────────────────────────────────────


class TestCheckShear:
    def test_pass(self):
        v = check_shear({"value": 100.0}, 50.0)
        assert v["state"] == STATE_PASS
        assert "shear" in v["failure_mode"].lower()

    def test_fail(self):
        v = check_shear({"value": 40.0}, 50.0)
        assert v["state"] == STATE_FAIL


# ── check_bending ─────────────────────────────────────────────────────────


class TestCheckBending:
    def test_normal_pass(self):
        v = check_bending(CAP_GOOD, 100_000.0, 50.0, 500_000.0)
        assert v["state"] == STATE_PASS
        assert "bending" in v["failure_mode"].lower()
        assert v["inputs"]["computed_stress"]["value"] == pytest.approx(10.0)

    def test_normal_fail(self):
        v = check_bending({"value": 10.0}, 100_000.0, 50.0, 100_000.0)
        assert v["state"] == STATE_FAIL
        assert v["inputs"]["computed_stress"]["value"] == pytest.approx(50.0)

    def test_zero_I_fail_closed(self):
        v = check_bending(CAP_GOOD, 100_000.0, 50.0, 0.0)
        assert v["state"] == STATE_FAIL_CLOSED
        assert "invalid" in str(v.get("assumptions", ""))

    def test_zero_c_fail_closed(self):
        v = check_bending(CAP_GOOD, 100_000.0, 0.0, 100_000.0)
        assert v["state"] == STATE_FAIL_CLOSED

    def test_negative_I_fail_closed(self):
        v = check_bending(CAP_GOOD, 100_000.0, 50.0, -1000.0)
        assert v["state"] == STATE_FAIL_CLOSED

    def test_negative_c_fail_closed(self):
        v = check_bending(CAP_GOOD, 100_000.0, -10.0, 100_000.0)
        assert v["state"] == STATE_FAIL_CLOSED

    def test_both_negative_fail_closed(self):
        v = check_bending(CAP_GOOD, 100_000.0, -1.0, -1.0)
        assert v["state"] == STATE_FAIL_CLOSED

    def test_insufficient_capacity_still_fail_closed_on_bad_geometry(self):
        v = check_bending(CAP_MISSING, 100_000.0, 0.0, 1000.0)
        assert v["state"] == STATE_FAIL_CLOSED

    def test_extra_inputs_present(self):
        v = check_bending(CAP_GOOD, 50_000.0, 25.0, 400_000.0)
        assert v["inputs"]["moment"]["value"] == 50_000.0
        assert v["inputs"]["distance_c"]["value"] == 25.0
        assert v["inputs"]["moment_of_inertia"]["value"] == 400_000.0

    def test_cantilever_beam(self):
        v = check_bending(CAP_GOOD, 5000.0, 10.0, 833.0)
        sigma = 5000.0 * 10.0 / 833.0
        assert v["inputs"]["computed_stress"]["value"] == pytest.approx(sigma, rel=1e-6)


# ── check_buckling_euler ──────────────────────────────────────────────────


class TestCheckBucklingEuler:
    def test_pinned_pinned_pass(self):
        v = check_buckling_euler(200_000.0, 1000.0, 1000.0, 1.0, 500.0)
        assert v["state"] == STATE_PASS

    def test_pinned_pinned_fail(self):
        v = check_buckling_euler(200_000.0, 1000.0, 1000.0, 1.0, 10_000.0)
        assert v["state"] == STATE_FAIL

    def test_fixed_fixed_pass(self):
        v = check_buckling_euler(200_000.0, 1000.0, 1000.0, 0.5, 2000.0)
        assert v["state"] == STATE_PASS
        assert v["assumptions"][-1].startswith("effective length factor K=0.5")

    def test_cantilever(self):
        v = check_buckling_euler(200_000.0, 1000.0, 1000.0, 2.0, 100.0)
        assert v["state"] == STATE_PASS

    def test_fixed_pinned(self):
        v = check_buckling_euler(200_000.0, 1000.0, 1000.0, 0.7, 800.0)
        assert v["state"] == STATE_PASS

    def test_zero_E_fail_closed(self):
        v = check_buckling_euler(0.0, 1000.0, 1000.0, 1.0, 500.0)
        assert v["state"] == STATE_FAIL_CLOSED

    def test_negative_E_fail_closed(self):
        v = check_buckling_euler(-200_000.0, 1000.0, 1000.0, 1.0, 500.0)
        assert v["state"] == STATE_FAIL_CLOSED

    def test_zero_I_fail_closed(self):
        v = check_buckling_euler(200_000.0, 0.0, 1000.0, 1.0, 500.0)
        assert v["state"] == STATE_FAIL_CLOSED

    def test_zero_L_fail_closed(self):
        v = check_buckling_euler(200_000.0, 1000.0, 0.0, 1.0, 500.0)
        assert v["state"] == STATE_FAIL_CLOSED

    def test_zero_K_fail_closed(self):
        v = check_buckling_euler(200_000.0, 1000.0, 1000.0, 0.0, 500.0)
        assert v["state"] == STATE_FAIL_CLOSED

    def test_negative_K_fail_closed(self):
        v = check_buckling_euler(200_000.0, 1000.0, 1000.0, -1.0, 500.0)
        assert v["state"] == STATE_FAIL_CLOSED

    def test_all_zero_fail_closed(self):
        v = check_buckling_euler(0.0, 0.0, 0.0, 0.0, 500.0)
        assert v["state"] == STATE_FAIL_CLOSED

    def test_negative_applied_force_fail_closed(self):
        v = check_buckling_euler(200_000.0, 1000.0, 1000.0, 1.0, -500.0)
        assert v["state"] == STATE_FAIL_CLOSED

    def test_zero_applied_force_fail_closed(self):
        v = check_buckling_euler(200_000.0, 1000.0, 1000.0, 1.0, 0.0)
        assert v["state"] == STATE_FAIL_CLOSED

    def test_none_applied_force_fail_closed(self):
        bad: Any = None
        v = check_buckling_euler(200_000.0, 1000.0, 1000.0, 1.0, bad)
        assert v["state"] == STATE_FAIL_CLOSED

    def test_margin_exactly_zero(self):
        e_mod, inertia, length, k_factor = 200_000.0, 1000.0, 1000.0, 1.0
        p_cr = math.pi**2 * e_mod * inertia / (k_factor * length) ** 2
        v = check_buckling_euler(e_mod, inertia, length, k_factor, p_cr)
        assert v["margin"] == 0.0
        assert v["state"] == STATE_FAIL

    def test_buckling_formula_correct(self):
        e_mod, inertia, length, k_factor = 200_000.0, 1000.0, 3000.0, 2.0
        expected_p_cr = math.pi**2 * e_mod * inertia / (k_factor * length) ** 2
        v = check_buckling_euler(e_mod, inertia, length, k_factor, 1.0)
        assert v["capacity"] == pytest.approx(expected_p_cr, rel=1e-9)

    def test_traceability_keys(self):
        v = check_buckling_euler(200_000.0, 1000.0, 1000.0, 1.0, 500.0)
        assert "E" in v["inputs"]
        assert "I" in v["inputs"]
        assert "L" in v["inputs"]
        assert v["equation_id"].startswith("euler")

    def test_very_short_column(self):
        column_length = 1.0
        v = check_buckling_euler(200_000.0, 1000.0, column_length, 1.0, 1.0)
        assert v["state"] == STATE_PASS

    def test_string_applied_force_fail_closed(self):
        bad: Any = "heavy"
        v = check_buckling_euler(200_000.0, 1000.0, 1000.0, 1.0, bad)
        assert v["state"] == STATE_FAIL_CLOSED


# ── check_thermal_stress ──────────────────────────────────────────────────


class TestCheckThermalStress:
    def test_heating_pass(self):
        v = check_thermal_stress(200_000.0, 12e-6, 50.0, {"value": 200.0})
        assert v["state"] == STATE_PASS
        assert "fully constrained" in v["equation_id"]

    def test_cooling(self):
        v = check_thermal_stress(200_000.0, 12e-6, -30.0, {"value": 200.0})
        assert v["state"] == STATE_PASS

    def test_zero_delta_T(self):
        v = check_thermal_stress(200_000.0, 12e-6, 0.0, {"value": 200.0})
        assert v["state"] == STATE_PASS
        assert v["inputs"]["computed_thermal_stress"]["value"] == 0.0

    def test_high_delta_T_fail(self):
        v = check_thermal_stress(200_000.0, 12e-6, 500.0, {"value": 100.0})
        assert v["state"] == STATE_FAIL

    def test_zero_alpha(self):
        v = check_thermal_stress(200_000.0, 0.0, 100.0, {"value": 200.0})
        assert v["inputs"]["computed_thermal_stress"]["value"] == 0.0

    def test_zero_E(self):
        v = check_thermal_stress(0.0, 12e-6, 100.0, {"value": 200.0})
        assert v["inputs"]["computed_thermal_stress"]["value"] == 0.0

    def test_negative_E(self):
        v = check_thermal_stress(-200_000.0, 12e-6, 50.0, {"value": 200.0})
        assert v["inputs"]["computed_thermal_stress"]["value"] < 0
        assert v["inputs"]["computed_thermal_stress"]["value"] == pytest.approx(-200_000.0 * 12e-6 * 50.0)

    def test_assumptions_present(self):
        v = check_thermal_stress(200_000.0, 12e-6, 50.0, {"value": 200.0})
        assert any("fully constrained" in a for a in v["assumptions"])
        assert any("uniform temperature" in a for a in v["assumptions"])

    def test_insufficient_capacity(self):
        v = check_thermal_stress(200_000.0, 12e-6, 50.0, CAP_MISSING)
        assert v["state"] == STATE_INSUFFICIENT

    def test_very_high_delta_T(self):
        v = check_thermal_stress(200_000.0, 12e-6, 1_000_000.0, {"value": 1e9})
        assert v["state"] == STATE_FAIL

    def test_titanium_thermal(self):
        v = check_thermal_stress(110_000.0, 8.6e-6, 100.0, {"value": 500.0})
        sigma = 110_000.0 * 8.6e-6 * 100.0
        assert v["inputs"]["computed_thermal_stress"]["value"] == pytest.approx(sigma, rel=1e-6)


# ── check_fatigue_sn ──────────────────────────────────────────────────────


class TestCheckFatigueSN:
    def test_lcf_pass(self):
        v = check_fatigue_sn(500.0, 200.0, 500)
        assert v["state"] == STATE_PASS
        assert v["capacity"] == pytest.approx(0.9 * 500.0)
        assert "LCF" in v["assumptions"][-1]

    def test_lcf_fail(self):
        v = check_fatigue_sn(500.0, 500.0, 1000)
        assert v["state"] == STATE_FAIL

    def test_endurance_pass(self):
        v = check_fatigue_sn(500.0, 100.0, 1_000_000)
        assert v["state"] == STATE_PASS
        assert v["capacity"] == pytest.approx(0.5 * 500.0)

    def test_endurance_fail(self):
        v = check_fatigue_sn(500.0, 260.0, 2_000_000)
        assert v["state"] == STATE_FAIL

    def test_finite_life_interpolation(self):
        v = check_fatigue_sn(500.0, 200.0, 10_000)
        assert 0.5 * 500.0 < v["capacity"] < 0.9 * 500.0

    def test_exact_thousand_cycles(self):
        v = check_fatigue_sn(500.0, 200.0, 1000)
        assert v["capacity"] == pytest.approx(0.9 * 500.0)

    def test_exact_million_cycles(self):
        v = check_fatigue_sn(500.0, 100.0, 1_000_000)
        assert v["capacity"] == pytest.approx(0.5 * 500.0)

    def test_cycle_1001_finite_life(self):
        v = check_fatigue_sn(500.0, 200.0, 1001)
        assert v["capacity"] < 0.9 * 500.0
        assert "finite-life" in v["assumptions"][-2] or "Basquin" in str(v["assumptions"])

    def test_cycle_999999_finite_life(self):
        v = check_fatigue_sn(500.0, 100.0, 999_999)
        assert v["capacity"] > 0.5 * 500.0
        assert "finite-life" in v["assumptions"][-2] or "Basquin" in str(v["assumptions"])

    def test_custom_endurance_limit(self):
        v = check_fatigue_sn(500.0, 150.0, 1_000_000, S_e_MPa=200.0)
        assert v["capacity"] == pytest.approx(200.0)
        assert v["state"] == STATE_PASS

    def test_estimated_endurance_flagged(self):
        v = check_fatigue_sn(500.0, 100.0, 1_000_000)
        assert any("estimated" in a for a in v["assumptions"])
        assert v["uncertainty"] == pytest.approx(125.0 * 0.15)

    def test_measured_endurance_flagged(self):
        v = check_fatigue_sn(500.0, 100.0, 1_000_000, S_e_MPa=200.0)
        assert not any("estimated" in a for a in v["assumptions"])
        assert v["upsert"] != pytest.approx(125.0 * 0.15) if False else v["uncertainty"] == pytest.approx(200.0 * 0.05)

    def test_zero_cycles(self):
        v = check_fatigue_sn(500.0, 200.0, 0)
        assert v["capacity"] == pytest.approx(0.9 * 500.0)

    def test_negative_cycles(self):
        v = check_fatigue_sn(500.0, 200.0, -100)
        assert v["capacity"] == pytest.approx(0.9 * 500.0)

    def test_zero_amplitude_fail_closed(self):
        v = check_fatigue_sn(500.0, 0.0, 10_000)
        assert v["state"] == STATE_FAIL_CLOSED

    def test_negative_amplitude_fail_closed(self):
        v = check_fatigue_sn(500.0, -50.0, 10_000)
        assert v["state"] == STATE_FAIL_CLOSED

    def test_none_amplitude_fail_closed(self):
        bad: Any = None
        v = check_fatigue_sn(500.0, bad, 10_000)
        assert v["state"] == STATE_FAIL_CLOSED

    def test_string_amplitude_fail_closed(self):
        bad: Any = "low"
        v = check_fatigue_sn(500.0, bad, 10_000)
        assert v["state"] == STATE_FAIL_CLOSED

    def test_infinity_amplitude_fail_closed(self):
        v = check_fatigue_sn(500.0, float("inf"), 10_000)
        assert v["state"] == STATE_FAIL_CLOSED

    def test_margin_exactly_at_endurance(self):
        v = check_fatigue_sn(500.0, 250.0, 1_000_000)
        assert v["margin"] == 0.0
        assert v["state"] == STATE_FAIL

    def test_traceability_keys_present(self):
        v = check_fatigue_sn(500.0, 100.0, 50_000)
        assert v["equation_id"].startswith("fatigue")
        assert "S_ut" in v["inputs"]
        assert "cycles" in v["inputs"]
        assert "applied_amplitude" in v["inputs"]
        assert "allowable_strength" in v["inputs"]

    def test_interpolation_specific_value(self):
        S_ut = 500.0
        cycles = 100_000
        log_n = math.log10(cycles)
        log_s_hi = math.log10(0.9 * S_ut)
        log_s_lo = math.log10(0.5 * S_ut)
        log_s = log_s_hi + (log_n - 3.0) * (log_s_lo - log_s_hi) / 3.0
        expected = 10.0**log_s
        v = check_fatigue_sn(S_ut, 100.0, cycles)
        assert v["capacity"] == pytest.approx(expected, rel=1e-9)

    def test_aluminum_fatigue(self):
        v = check_fatigue_sn(310.0, 50.0, 500_000, S_e_MPa=95.0)
        assert v["state"] == STATE_PASS
        assert v["capacity"] == pytest.approx(95.0)


# ── integration: all check functions use same state constants ─────────────


class TestStateConstants:
    def test_names_exported(self):
        assert STATE_PASS == "pass"
        assert STATE_FAIL == "fail"
        assert STATE_INSUFFICIENT == "insufficient_data"
        assert STATE_FAIL_CLOSED == "fail_closed"


# ── traceability (MATE-DEC-004) ──────────────────────────────────────────


class TestTraceability:
    def test_tension_has_equation_id(self):
        v = check_tension(CAP_GOOD, 100.0)
        assert v["equation_id"] and isinstance(v["equation_id"], str)

    def test_compression_has_equation_id(self):
        v = check_compression(CAP_GOOD, 100.0)
        assert v["equation_id"] and isinstance(v["equation_id"], str)

    def test_shear_has_equation_id(self):
        v = check_shear(CAP_GOOD, 100.0)
        assert v["equation_id"] and isinstance(v["equation_id"], str)

    def test_bending_has_equation_id(self):
        v = check_bending(CAP_GOOD, 5000.0, 10.0, 833.0)
        assert v["equation_id"] and isinstance(v["equation_id"], str)

    def test_buckling_has_equation_id(self):
        v = check_buckling_euler(200_000.0, 1000.0, 1000.0, 1.0, 500.0)
        assert v["equation_id"] and isinstance(v["equation_id"], str)

    def test_thermal_has_equation_id(self):
        v = check_thermal_stress(200_000.0, 12e-6, 50.0, {"value": 200.0})
        assert v["equation_id"] and isinstance(v["equation_id"], str)

    def test_fatigue_has_equation_id(self):
        v = check_fatigue_sn(500.0, 50.0, 100_000)
        assert v["equation_id"] and isinstance(v["equation_id"], str)

    def test_all_have_assumptions_list(self):
        funcs = [
            lambda: check_tension(CAP_GOOD, 100.0),
            lambda: check_compression(CAP_GOOD, 100.0),
            lambda: check_shear(CAP_GOOD, 100.0),
            lambda: check_bending(CAP_GOOD, 5000.0, 10.0, 833.0),
            lambda: check_buckling_euler(200_000.0, 1000.0, 1000.0, 1.0, 500.0),
            lambda: check_thermal_stress(200_000.0, 12e-6, 50.0, {"value": 200.0}),
            lambda: check_fatigue_sn(500.0, 50.0, 100_000),
        ]
        for fn in funcs:
            v = fn()
            assert isinstance(v["assumptions"], list), f"{v['failure_mode']} assumptions is not list"

    def test_all_have_inputs_dict(self):
        funcs = [
            lambda: check_tension(CAP_GOOD, 100.0),
            lambda: check_compression(CAP_GOOD, 100.0),
            lambda: check_shear(CAP_GOOD, 100.0),
            lambda: check_bending(CAP_GOOD, 5000.0, 10.0, 833.0),
            lambda: check_buckling_euler(200_000.0, 1000.0, 1000.0, 1.0, 500.0),
            lambda: check_thermal_stress(200_000.0, 12e-6, 50.0, {"value": 200.0}),
            lambda: check_fatigue_sn(500.0, 50.0, 100_000),
        ]
        for fn in funcs:
            v = fn()
            assert isinstance(v["inputs"], dict), f"{v['failure_mode']} inputs is not dict"
