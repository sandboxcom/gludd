"""Deep edge-case tests for tolerance.py (MATE-001 § 4.7).

Covers ToleranceChain, process_capability, assess_assembly with
boundary conditions, numerical edge cases, and fail-closed invariants.
"""

from __future__ import annotations

import math

import pytest

from general_ludd.materials.tolerance import (
    STATE_FAIL_CLOSED,
    STATE_OK,
    ToleranceChain,
    assess_assembly,
    process_capability,
)

# ── ToleranceChain ──────────────────────────────────────────────────────────


class TestToleranceChainDeep:
    def test_empty_chain_worst_case_fail_closed(self):
        tc = ToleranceChain([], "mm")
        result = tc.worst_case_stackup()
        assert result["state"] == STATE_FAIL_CLOSED
        assert "empty" in result["reason"]

    def test_empty_chain_rss_fail_closed(self):
        tc = ToleranceChain([], "mm")
        result = tc.rss_stackup()
        assert result["state"] == STATE_FAIL_CLOSED

    def test_empty_unit_raises(self):
        with pytest.raises(ValueError, match="unit must be a non-empty string"):
            ToleranceChain([(10.0, 0.1)], "")

    def test_none_unit_raises(self):
        with pytest.raises(ValueError, match="unit must be a non-empty string"):
            ToleranceChain([(10.0, 0.1)], None)

    def test_whitespace_only_unit_raises(self):
        with pytest.raises(ValueError, match="unit must be a non-empty string"):
            ToleranceChain([(10.0, 0.1)], "   ")

    def test_single_dimension_worst_case(self):
        tc = ToleranceChain([(25.0, 0.05)], "mm")
        result = tc.worst_case_stackup()
        assert result["state"] == STATE_OK
        assert result["nominal"] == 25.0
        assert result["band"] == 0.05
        assert result["upper"] == 25.05
        assert result["lower"] == 24.95

    def test_single_dimension_rss(self):
        tc = ToleranceChain([(25.0, 0.05)], "mm")
        result = tc.rss_stackup()
        assert result["nominal"] == 25.0
        assert result["sigma_band"] == pytest.approx(0.05)
        assert result["upper"] == 25.05
        assert result["lower"] == 24.95

    def test_rss_tighter_than_worst_case(self):
        dims = [(100.0, 0.5), (50.0, 0.3), (25.0, 0.4)]
        tc = ToleranceChain(dims, "mm")
        wc = tc.worst_case_stackup()
        rss = tc.rss_stackup()
        assert rss["sigma_band"] < wc["band"]

    def test_rss_exact_value(self):
        dims = [(100.0, 0.3), (50.0, 0.4)]
        tc = ToleranceChain(dims, "mm")
        rss = tc.rss_stackup()
        expected_band = math.sqrt(0.3**2 + 0.4**2)
        assert rss["sigma_band"] == pytest.approx(expected_band)

    def test_huge_tolerances(self):
        dims = [(1000.0, 500.0), (2000.0, 300.0)]
        tc = ToleranceChain(dims, "mm")
        wc = tc.worst_case_stackup()
        assert wc["band"] == 800.0
        assert wc["upper"] == 3800.0
        assert wc["lower"] == 2200.0

    def test_zero_tolerances(self):
        dims = [(100.0, 0.0), (50.0, 0.0)]
        tc = ToleranceChain(dims, "mm")
        wc = tc.worst_case_stackup()
        assert wc["band"] == 0.0
        assert wc["nominal"] == 150.0
        rss = tc.rss_stackup()
        assert rss["sigma_band"] == 0.0

    def test_negative_nominal(self):
        dims = [(-10.0, 0.1)]
        tc = ToleranceChain(dims, "mm")
        wc = tc.worst_case_stackup()
        assert wc["nominal"] == -10.0

    def test_negative_tolerance_becomes_abs(self):
        dims = [(100.0, -0.5)]
        tc = ToleranceChain(dims, "mm")
        wc = tc.worst_case_stackup()
        assert wc["band"] == 0.5

    def test_many_dimensions_worst_case(self):
        dims = [(10.0, 0.1) for _ in range(50)]
        tc = ToleranceChain(dims, "mm")
        wc = tc.worst_case_stackup()
        assert wc["nominal"] == 500.0
        assert wc["band"] == pytest.approx(5.0)

    def test_many_dimensions_rss(self):
        dims = [(10.0, 0.1) for _ in range(50)]
        tc = ToleranceChain(dims, "mm")
        rss = tc.rss_stackup()
        expected_sigma = math.sqrt(50 * 0.1**2)
        assert rss["sigma_band"] == pytest.approx(expected_sigma)

    def test_thermal_expansion_single_dim(self):
        tc = ToleranceChain([(100.0, 0.1)], "mm")
        result = tc.thermal_expansion_delta(12e-6, 50.0)
        assert result["state"] == STATE_OK
        assert result["delta"] == pytest.approx(100.0 * 12e-6 * 50.0)

    def test_thermal_expansion_multi_dim(self):
        tc = ToleranceChain([(100.0, 0.1), (50.0, 0.05)], "mm")
        result = tc.thermal_expansion_delta(12e-6, 50.0)
        assert result["delta"] == pytest.approx(150.0 * 12e-6 * 50.0)

    def test_thermal_expansion_zero_alpha(self):
        tc = ToleranceChain([(100.0, 0.1)], "mm")
        result = tc.thermal_expansion_delta(0.0, 100.0)
        assert result["delta"] == 0.0

    def test_thermal_expansion_zero_delta_T(self):
        tc = ToleranceChain([(100.0, 0.1)], "mm")
        result = tc.thermal_expansion_delta(12e-6, 0.0)
        assert result["delta"] == 0.0

    def test_thermal_expansion_negative_delta_T(self):
        tc = ToleranceChain([(100.0, 0.1)], "mm")
        result = tc.thermal_expansion_delta(12e-6, -30.0)
        assert result["delta"] < 0

    def test_thermal_expansion_empty_chain(self):
        tc = ToleranceChain([], "mm")
        result = tc.thermal_expansion_delta(12e-6, 50.0)
        assert result["delta"] == 0.0

    def test_thermal_compensation_is_negative_delta(self):
        tc = ToleranceChain([(100.0, 0.1)], "mm")
        growth = tc.thermal_expansion_delta(12e-6, 50.0)
        comp = tc.thermal_compensation(12e-6, 50.0)
        assert comp["compensation"] == -growth["delta"]

    def test_thermal_compensation_negative_delta_T(self):
        tc = ToleranceChain([(100.0, 0.1)], "mm")
        comp = tc.thermal_compensation(12e-6, -30.0)
        assert comp["compensation"] > 0

    def test_worst_case_inputs_contain_dims_with_units(self):
        tc = ToleranceChain([(25.0, 0.05)], "mm")
        result = tc.worst_case_stackup()
        assert len(result["inputs"]["dims"]) == 1
        assert result["inputs"]["dims"][0]["unit"] == "mm"

    def test_rss_inputs_contain_dims_with_units(self):
        tc = ToleranceChain([(25.0, 0.05)], "mm")
        result = tc.rss_stackup()
        assert result["inputs"]["dims"][0]["unit"] == "mm"

    def test_thermal_expansion_inputs_contains_L0(self):
        tc = ToleranceChain([(25.0, 0.05)], "mm")
        result = tc.thermal_expansion_delta(12e-6, 50.0)
        assert result["inputs"]["L0"]["value"] == 25.0
        assert result["inputs"]["L0"]["unit"] == "mm"

    def test_equation_ids_match_constants(self):
        tc = ToleranceChain([(10.0, 0.1)], "mm")
        assert tc.worst_case_stackup()["equation_id"] == tc.equation_id_worst
        assert tc.rss_stackup()["equation_id"] == tc.equation_id_rss
        assert tc.thermal_expansion_delta(12e-6, 50.0)["equation_id"] == tc.equation_id_thermal

    def test_dims_not_copied_are_independent(self):
        original = [(10.0, 0.1)]
        tc = ToleranceChain(original, "mm")
        original[0] = (99.0, 99.0)  # type: ignore[index]
        result = tc.worst_case_stackup()
        assert result["nominal"] == 10.0

    def test_unit_preserved_in_all_results(self):
        tc = ToleranceChain([(10.0, 0.1)], "inch")
        for result in [
            tc.worst_case_stackup(),
            tc.rss_stackup(),
            tc.thermal_expansion_delta(12e-6, 50.0),
        ]:
            assert result["unit"] == "inch"

    def test_rss_assumptions_documented(self):
        tc = ToleranceChain([(10.0, 0.1)], "mm")
        result = tc.rss_stackup()
        assert any("independent" in a for a in result["assumptions"])
        assert any("normal" in a.lower() for a in result["assumptions"])

    def test_worst_case_assumptions_documented(self):
        tc = ToleranceChain([(10.0, 0.1)], "mm")
        result = tc.worst_case_stackup()
        assert any("worst-case" in a.lower() for a in result["assumptions"])

    def test_thermal_assumptions_documented(self):
        tc = ToleranceChain([(10.0, 0.1)], "mm")
        result = tc.thermal_expansion_delta(12e-6, 50.0)
        assert any("free expansion" in a.lower() for a in result["assumptions"])

    def test_very_small_tolerance(self):
        tc = ToleranceChain([(1e-6, 1e-9)], "m")
        result = tc.worst_case_stackup()
        assert result["band"] == 1e-9

    def test_very_large_tolerance(self):
        tc = ToleranceChain([(1000.0, 500.0)], "mm")
        result = tc.worst_case_stackup()
        assert result["band"] == 500.0
        assert result["upper"] == 1500.0
        assert result["lower"] == 500.0


# ── process_capability ─────────────────────────────────────────────────────


class TestProcessCapabilityDeep:
    def test_centered_process(self):
        result = process_capability(10.0, 20.0, 1.0)
        assert result["state"] == STATE_OK
        assert result["Cp"] == pytest.approx((20 - 10) / (6 * 1.0))
        assert result["Cpk"] == result["Cp"]

    def test_shifted_mean(self):
        result = process_capability(10.0, 20.0, 1.0, mean=12.0)
        assert result["Cpk"] < result["Cp"]

    def test_shifted_toward_usl(self):
        result = process_capability(10.0, 20.0, 1.0, mean=18.0)
        assert result["Cpk"] == pytest.approx((20 - 18) / (3 * 1.0))

    def test_shifted_toward_lsl(self):
        result = process_capability(10.0, 20.0, 1.0, mean=12.0)
        assert result["Cpk"] == pytest.approx((12 - 10) / (3 * 1.0))

    def test_mean_outside_spec(self):
        result = process_capability(10.0, 20.0, 1.0, mean=5.0)
        assert result["Cpk"] < 0

    def test_cpk_never_exceeds_cp(self):
        for mean in [10.0, 12.0, 15.0, 18.0, 20.0]:
            result = process_capability(10.0, 20.0, 1.0, mean=mean)
            assert result["Cpk"] <= result["Cp"] + 1e-12

    def test_zero_sigma_fail_closed(self):
        result = process_capability(10.0, 20.0, 0.0)
        assert result["state"] == STATE_FAIL_CLOSED
        assert "sigma" in result["reason"].lower()

    def test_negative_sigma_fail_closed(self):
        result = process_capability(10.0, 20.0, -1.0)
        assert result["state"] == STATE_FAIL_CLOSED

    def test_reversed_limits_fail_closed(self):
        result = process_capability(20.0, 10.0, 1.0)
        assert result["state"] == STATE_FAIL_CLOSED
        assert "exceed" in result["reason"]

    def test_equal_limits_fail_closed(self):
        result = process_capability(10.0, 10.0, 1.0)
        assert result["state"] == STATE_FAIL_CLOSED

    def test_nan_sigma_fail_closed(self):
        result = process_capability(10.0, 20.0, float("nan"))
        assert result["state"] == STATE_FAIL_CLOSED

    def test_inf_sigma_fail_closed(self):
        result = process_capability(10.0, 20.0, float("inf"))
        assert result["state"] == STATE_FAIL_CLOSED

    def test_wide_spec(self):
        result = process_capability(0.0, 100.0, 1.0)
        assert result["Cp"] == pytest.approx(100.0 / 6.0)

    def test_narrow_spec(self):
        result = process_capability(9.9, 10.1, 0.1)
        assert result["Cp"] == pytest.approx(0.2 / (6 * 0.1))

    def test_tiny_sigma(self):
        result = process_capability(10.0, 20.0, 1e-12)
        assert result["Cp"] > 1e9

    def test_huge_sigma(self):
        result = process_capability(10.0, 20.0, 100.0)
        assert result["Cp"] < 1.0

    def test_none_mean_centered(self):
        result = process_capability(10.0, 20.0, 1.0, mean=None)
        assert result["Cpk"] == result["Cp"]

    def test_inputs_contain_all_params(self):
        result = process_capability(10.0, 20.0, 1.0, mean=14.0)
        assert result["inputs"]["spec_lower"] == 10.0
        assert result["inputs"]["spec_upper"] == 20.0
        assert result["inputs"]["sigma"] == 1.0

    def test_effective_mean_in_inputs(self):
        result = process_capability(10.0, 20.0, 1.0, mean=14.0)
        assert result["inputs"]["effective_mean"] == 14.0

    def test_equation_id_present(self):
        result = process_capability(10.0, 20.0, 1.0)
        assert "cp/cpk" in result["equation_id"].lower()

    def test_assumptions_documented(self):
        result = process_capability(10.0, 20.0, 1.0)
        assert any("statistical control" in a for a in result["assumptions"])


# ── assess_assembly ─────────────────────────────────────────────────────────


class TestAssessAssemblyDeep:
    def test_clearance_fit(self):
        result = assess_assembly(10.0, 0.05, 9.8, 0.05, "mm")
        assert result["state"] == STATE_OK
        assert result["fit_class"] == "clearance"
        assert result["min_clearance"] > 0
        assert result["max_clearance"] > result["min_clearance"]

    def test_interference_fit(self):
        result = assess_assembly(10.0, 0.05, 10.2, 0.05, "mm")
        assert result["fit_class"] == "interference"
        assert result["max_clearance"] < 0

    def test_transition_fit(self):
        result = assess_assembly(10.0, 0.5, 10.0, 0.5, "mm")
        assert result["fit_class"] == "transition"
        assert result["min_clearance"] < 0
        assert result["max_clearance"] > 0

    def test_exact_min_clearance_value(self):
        result = assess_assembly(10.0, 0.1, 9.5, 0.1, "mm")
        hole_min = 10.0 - 0.1
        shaft_max = 9.5 + 0.1
        assert result["min_clearance"] == pytest.approx(hole_min - shaft_max)

    def test_exact_max_clearance_value(self):
        result = assess_assembly(10.0, 0.1, 9.5, 0.1, "mm")
        hole_max = 10.0 + 0.1
        shaft_min = 9.5 - 0.1
        assert result["max_clearance"] == pytest.approx(hole_max - shaft_min)

    def test_zero_tolerance(self):
        result = assess_assembly(10.0, 0.0, 9.9, 0.0, "mm")
        assert result["fit_class"] == "clearance"
        assert result["min_clearance"] == 0.1
        assert result["max_clearance"] == 0.1

    def test_exact_size_match_with_zero_tol(self):
        result = assess_assembly(10.0, 0.0, 10.0, 0.0, "mm")
        assert result["min_clearance"] == 0.0
        assert result["max_clearance"] == 0.0

    def test_negative_tolerance_uses_abs(self):
        result = assess_assembly(10.0, -0.1, 9.5, -0.05, "mm")
        hole_min = 10.0 - 0.1
        shaft_max = 9.5 + 0.05
        assert result["min_clearance"] == pytest.approx(hole_min - shaft_max)

    def test_large_clearance(self):
        result = assess_assembly(100.0, 1.0, 50.0, 1.0, "mm")
        assert result["fit_class"] == "clearance"
        assert result["min_clearance"] > 0

    def test_large_interference(self):
        result = assess_assembly(10.0, 0.1, 20.0, 0.1, "mm")
        assert result["fit_class"] == "interference"

    def test_inputs_carry_units(self):
        result = assess_assembly(10.0, 0.1, 9.5, 0.1, "inch")
        assert result["unit"] == "inch"
        assert result["inputs"]["hole"]["unit"] == "inch"
        assert result["inputs"]["shaft"]["unit"] == "inch"

    def test_equation_id_present(self):
        result = assess_assembly(10.0, 0.1, 9.5, 0.1, "mm")
        assert "assembly" in result["equation_id"]

    def test_assumptions_documented(self):
        result = assess_assembly(10.0, 0.1, 9.5, 0.1, "mm")
        assert any("worst-case" in a for a in result["assumptions"])
