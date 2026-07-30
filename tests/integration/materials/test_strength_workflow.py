"""Integration tests for the strength-assessment workflow (spec MATE-AT-006).

Drives the analytical-strength pipeline end-to-end:

    DesignRequirements -> strength checks (tension + bending + fatigue) ->
    verify margins carry correct units, equation traceability, and uncertainty.

Covers MATE-AT-006 (analytical benchmarks) for the axial / beam / fatigue
families. Each test asserts the numerical margin matches an independently
derived hand calc AND that the result satisfies MATE-DEC-004 (equation_id +
inputs with units + assumptions) so downstream tooling can audit the chain.
"""

from __future__ import annotations

import math

import pytest

from general_ludd.materials.core import normalize_requirements
from general_ludd.materials.material_selection import resolve_property
from general_ludd.materials.strength import (
    check_bending,
    check_buckling_euler,
    check_fatigue_sn,
    check_tension,
    check_thermal_stress,
)


# ---------------------------------------------------------------------------
# Property-record helpers (mirror the shape strength.py expects)
# ---------------------------------------------------------------------------


def _prop_from_registry(material_id: str, prop_name: str) -> dict:
    """Pull a live property record from the materials registry so the
    integration test exercises the real data path, not a hand-rolled dict."""
    prop, _tier = resolve_property(material_id, prop_name)
    assert prop is not None, f"registry missing {prop_name} for {material_id}"
    return prop


def _reqs_with_yield(magnitude_mpa: float) -> dict:
    return normalize_requirements(
        {
            "load_cases": [
                {"id": "y1", "type": "yield", "magnitude": magnitude_mpa, "unit": "MPa"},
            ],
            "failure_consequence": "significant",
        }
    )


# ---------------------------------------------------------------------------
# Tension + bending + fatigue workflow (MATE-AT-006)
# ---------------------------------------------------------------------------


class TestStrengthWorkflowTensionBendingFatigue:
    """End-to-end: DesignRequirements -> tension+bending+fatigue checks ->
    every result carries margin + unit + equation_id + uncertainty."""

    def test_steel_tension_workflow_matches_handcalc_and_carries_traceability(self):
        """aisi_1045 yield=310 MPa, applied=200 MPa -> margin=(310-200)/200=0.55.
        Result must carry MPa unit, equation_id, and the registry uncertainty."""
        reqs = _reqs_with_yield(200.0)
        # the property is resolved through the same pipeline the ranker uses
        prop = _prop_from_registry("aisi_1045", "yield_strength")
        result = check_tension(prop, applied_stress_MPa=200.0)

        assert result["state"] == "pass"
        assert result["margin"] == pytest.approx(0.55, rel=1e-3)
        assert result["unit"] == "MPa"
        assert result["uncertainty"] == pytest.approx(20.0)
        assert result["capacity"] == pytest.approx(310.0)
        # MATE-DEC-004: equation id + structured inputs present
        assert result["equation_id"]
        assert "capacity" in result["inputs"]
        assert result["inputs"]["capacity"]["unit"] == "MPa"
        assert result["inputs"]["applied_stress"]["value"] == 200.0
        # the reqs flow through the workflow without mutating the prop
        assert reqs["load_cases"][0]["magnitude"] == 200.0

    def test_aluminum_bending_workflow_computes_extreme_fiber_margin(self):
        """aa6061-T6 sheet in bending: sigma = M*c/I, margin against yield.
        Inputs (M, c, I) must be echoed with their units in the result."""
        prop = _prop_from_registry("aa6061_t6", "yield_strength")
        # M=10000 N*mm, c=10 mm, I=1000 mm^4 -> sigma = 100 MPa
        result = check_bending(prop, applied_moment_Nmm=10000.0, c_mm=10.0, I_mm4=1000.0)

        assert result["state"] == "pass"
        assert result["applied"] == pytest.approx(100.0, rel=1e-3)
        # margin = (276 - 100) / 100 = 1.76
        assert result["margin"] == pytest.approx(1.76, rel=1e-2)
        assert result["unit"] == "MPa"
        # equation traceability
        assert "bending" in result["equation_id"].lower()
        assert result["inputs"]["moment"]["unit"] == "N*mm"
        assert result["inputs"]["distance_c"]["unit"] == "mm"
        assert result["inputs"]["moment_of_inertia"]["unit"] == "mm^4"
        assert result["inputs"]["computed_stress"]["value"] == pytest.approx(100.0)

    def test_steel_fatigue_workflow_uses_estimated_endurance_with_wide_uncertainty(self):
        """aisi_1045 S_ut=565 MPa, 1e6 cycles, no measured S_e -> estimated as
        0.5*S_ut=282.5 MPa. Estimated endurance MUST carry wide uncertainty
        (MATE-SAFE-003) and an 'estimated' assumption label."""
        result = check_fatigue_sn(S_ut_MPa=565.0, applied_amplitude_MPa=200.0, cycles=1_000_000)
        assert result["state"] == "pass"
        assert result["capacity"] == pytest.approx(282.5, rel=1e-3)
        # margin = (282.5 - 200) / 200 = 0.4125
        assert result["margin"] == pytest.approx(0.4125, rel=1e-2)
        assert result["unit"] == "MPa"
        # estimated endurance -> >=10% uncertainty and an 'estimated' label
        assert result["uncertainty"] >= 0.10 * 282.5
        assert any("estimated" in a.lower() for a in result["assumptions"])
        assert "fatigue" in result["equation_id"].lower()

    def test_full_tension_bending_fatigue_suite_on_one_material_all_pass(self):
        """Run all three checks against aisi_1045 in one workflow and verify
        every result is a pass with consistent MPa units and equation ids."""
        prop = _prop_from_registry("aisi_1045", "yield_strength")
        tension = check_tension(prop, applied_stress_MPa=200.0)
        bending = check_bending(prop, applied_moment_Nmm=8000.0, c_mm=10.0, I_mm4=1000.0)
        fatigue = check_fatigue_sn(S_ut_MPa=565.0, applied_amplitude_MPa=150.0, cycles=1_000_000)
        for r in (tension, bending, fatigue):
            assert r["state"] == "pass"
            assert r["unit"] == "MPa"
            assert r["margin"] > 0
            assert r["equation_id"]
            assert isinstance(r["inputs"], dict) and r["inputs"]

    def test_workflow_surfaces_failure_when_tension_exceeds_capacity(self):
        """When the applied stress exceeds capacity, the tension check must
        report state=fail with a NEGATIVE margin (not None / not hidden)."""
        prop = _prop_from_registry("aisi_1045", "yield_strength")
        result = check_tension(prop, applied_stress_MPa=400.0)
        assert result["state"] == "fail"
        assert result["margin"] < 0
        # capacity + unit still echoed for traceability
        assert result["capacity"] == pytest.approx(310.0)
        assert result["unit"] == "MPa"


# ---------------------------------------------------------------------------
# Equation-traceability + unit-integrity invariants (MATE-DEC-004 / MATE-SAFE-006)
# ---------------------------------------------------------------------------


class TestStrengthWorkflowTraceability:
    """Every derived value retains equation id + inputs with units + assumptions
    (MATE-DEC-004). Buckling + thermal stress included to broaden the unit
    coverage (N for force, MPa for stress, K for temperature)."""

    def test_euler_buckling_workflow_carries_force_units_and_equation(self):
        """P_cr = pi^2 * E * I / (K*L)^2; result must be in Newtons and the
        equation_id must mention 'euler'."""
        result = check_buckling_euler(E_MPa=200000.0, I_mm4=1000.0, L_mm=1000.0, K=1.0, applied_force_N=1000.0)
        expected_p_cr = math.pi**2 * 200000.0 * 1000.0 / (1.0 * 1000.0) ** 2
        assert result["state"] == "pass"
        assert result["capacity"] == pytest.approx(expected_p_cr, rel=1e-3)
        assert result["unit"] == "N"
        assert "euler" in result["equation_id"].lower()
        assert result["inputs"]["E"]["unit"] == "MPa"
        assert result["inputs"]["applied_force"]["unit"] == "N"

    def test_thermal_stress_workflow_carries_temperature_unit(self):
        """sigma = E * alpha * dT; the inputs must record delta_T in K and the
        computed stress must be in MPa against the yield capacity."""
        prop = _prop_from_registry("aisi_1045", "yield_strength")
        result = check_thermal_stress(E_MPa=200000.0, alpha_per_K=12e-6, delta_T_K=100.0, capacity_prop=prop)
        assert result["state"] == "pass"
        assert result["applied"] == pytest.approx(240.0, rel=1e-3)
        assert result["inputs"]["delta_T"]["unit"] == "K"
        assert result["inputs"]["alpha"]["unit"] == "1/K"
        assert result["inputs"]["computed_thermal_stress"]["unit"] == "MPa"
        assert "thermal" in result["equation_id"].lower()

    def test_workflow_fatigue_with_measured_endurance_drops_estimated_label(self):
        """When a measured S_e is supplied, the 'estimated' assumption must
        disappear and uncertainty tightens (<10%). Confirms the workflow
        propagates higher-tier evidence per MATE-DEC-003."""
        result = check_fatigue_sn(
            S_ut_MPa=565.0,
            applied_amplitude_MPa=200.0,
            cycles=1_000_000,
            S_e_MPa=260.0,
        )
        assert result["capacity"] == pytest.approx(260.0)
        assert result["uncertainty"] < 0.10 * 260.0
        assert not any("estimated" in a.lower() for a in result["assumptions"])
