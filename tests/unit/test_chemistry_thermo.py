"""Unit tests for ``general_ludd.chemistry.thermo_kinetics`` (Phase C) and
``general_ludd.chemistry.spectroscopy`` (Phase D).

Covers CHEM-013 (thermodynamics/kinetics) and CHEM-014 (spectroscopy) from
``docs/specs/FEATURE_CHEMISTRY_EXPERT.md`` §7.5 and §8.3. Maps to acceptance
criteria CHEM-AT-014 (thermo/kinetic/process fixtures pass unit, conservation,
limiting-case, convergence, and sensitivity checks) and CHEM-AT-015 (each
spectroscopy parser round-trips its supported open fixture and explicitly
rejects unsupported versions).

Modules are loaded by file path (mirroring ``test_chemistry_reactions.py``) so
the suite is robust to ``sys.path`` variations inside worktrees.
"""

from __future__ import annotations

import importlib.util
import math
import os

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
_CORE_PATH = os.path.join(_PROJECT_ROOT, "src", "general_ludd", "chemistry", "core.py")
_THERMO_PATH = os.path.join(_PROJECT_ROOT, "src", "general_ludd", "chemistry", "thermo_kinetics.py")
_SPEC_PATH = os.path.join(_PROJECT_ROOT, "src", "general_ludd", "chemistry", "spectroscopy.py")


def _load_module(path: str, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None, f"{name} spec failed"
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


core = _load_module(_CORE_PATH, "chemistry_core_thermo_test")
thermo = _load_module(_THERMO_PATH, "chemistry_thermo_under_test")
spectro = _load_module(_SPEC_PATH, "chemistry_spectroscopy_under_test")


# ---------------------------------------------------------------------------
# CHEM-013 equilibrium constant from Gibbs energy
# ---------------------------------------------------------------------------


class TestEquilibriumConstant:
    def test_kc_from_negative_gibbs_energy_large(self):
        # Strongly favorable reaction: ΔG° = -50 kJ/mol at 298.15 K -> K ~ 5.4e8
        rec = thermo.equilibrium_constant(
            delta_g_kJ_per_mol=-50.0,
            temperature_K=298.15,
            basis="concentration",
        )
        assert rec["name"] == "equilibrium_constant"
        assert rec["unit"] == "dimensionless"
        # K = exp(50000/(8.314*298.15)) ~ 5.36e8
        assert math.isclose(rec["value"], math.exp(50000.0 / (8.314462618 * 298.15)), rel_tol=1e-3)
        assert rec["value"] > 1.0e8

    def test_kc_from_positive_gibbs_energy_small(self):
        # Unfavorable: ΔG° = +50 kJ/mol -> K ~ 1.9e-9
        rec = thermo.equilibrium_constant(
            delta_g_kJ_per_mol=50.0,
            temperature_K=298.15,
        )
        assert rec["value"] < 1.0e-8
        assert rec["value"] > 0.0

    def test_zero_gibbs_energy_gives_unit_k(self):
        rec = thermo.equilibrium_constant(delta_g_kJ_per_mol=0.0, temperature_K=298.15)
        assert math.isclose(rec["value"], 1.0, abs_tol=1e-12)

    def test_kp_basis_returns_pressure_label(self):
        rec = thermo.equilibrium_constant(
            delta_g_kJ_per_mol=-10.0,
            temperature_K=298.15,
            basis="pressure",
        )
        assert rec["value"] > 1.0
        # Kp still dimensionless in thermodynamic convention, but basis field set
        assert rec.get("basis") == "pressure"

    def test_equilibrium_constant_uncertainty_propagated(self):
        rec = thermo.equilibrium_constant(
            delta_g_kJ_per_mol=-20.0,
            temperature_K=298.15,
            delta_g_uncertainty_kJ_per_mol=0.5,
        )
        assert rec["uncertainty"] > 0.0

    def test_equilibrium_constant_rejects_nonpositive_temperature(self):
        try:
            thermo.equilibrium_constant(delta_g_kJ_per_mol=-10.0, temperature_K=0.0)
            raise AssertionError("expected ValueError")
        except ValueError:
            pass


# ---------------------------------------------------------------------------
# CHEM-013 Arrhenius reaction rate
# ---------------------------------------------------------------------------


class TestArrheniusRate:
    def test_arrhenius_basic_value(self):
        # A=1e10 1/s, Ea=50 kJ/mol, T=298.15 K -> k ~ 1.73e1
        rec = thermo.arrhenius_rate(
            pre_exponential=1.0e10,
            activation_energy_kJ_per_mol=50.0,
            temperature_K=298.15,
        )
        assert rec["name"] == "rate_constant"
        expected = 1.0e10 * math.exp(-50000.0 / (8.314462618 * 298.15))
        assert math.isclose(rec["value"], expected, rel_tol=1e-6)

    def test_arrhenius_higher_temp_higher_rate(self):
        cold = thermo.arrhenius_rate(
            pre_exponential=1.0e10,
            activation_energy_kJ_per_mol=80.0,
            temperature_K=300.0,
        )
        hot = thermo.arrhenius_rate(
            pre_exponential=1.0e10,
            activation_energy_kJ_per_mol=80.0,
            temperature_K=400.0,
        )
        assert hot["value"] > cold["value"]

    def test_arrhenius_unit_propagated(self):
        rec = thermo.arrhenius_rate(
            pre_exponential=1.0e12,
            activation_energy_kJ_per_mol=40.0,
            temperature_K=298.15,
            unit="1/s",
        )
        assert rec["unit"] == "1/s"

    def test_arrhenius_uncertainty_propagated(self):
        rec = thermo.arrhenius_rate(
            pre_exponential=1.0e12,
            activation_energy_kJ_per_mol=60.0,
            temperature_K=298.15,
            activation_uncertainty_kJ_per_mol=2.0,
        )
        assert rec["uncertainty"] > 0.0

    def test_arrhenius_rejects_zero_temperature(self):
        try:
            thermo.arrhenius_rate(
                pre_exponential=1.0e10,
                activation_energy_kJ_per_mol=50.0,
                temperature_K=0.0,
            )
            raise AssertionError("expected ValueError")
        except ValueError:
            pass


# ---------------------------------------------------------------------------
# CHEM-013 phase stability
# ---------------------------------------------------------------------------


class TestPhaseStability:
    def test_water_liquid_at_room_conditions(self):
        rec = thermo.check_phase_stability(
            substance="water",
            temperature_K=298.15,
            pressure_Pa=101325.0,
        )
        assert rec["stable_phase"] == "liquid"
        assert rec["status"] == "succeeded"

    def test_water_gas_above_boiling_at_atm(self):
        rec = thermo.check_phase_stability(
            substance="water",
            temperature_K=400.0,
            pressure_Pa=101325.0,
        )
        assert rec["stable_phase"] == "gas"

    def test_water_solid_below_freezing_at_atm(self):
        rec = thermo.check_phase_stability(
            substance="water",
            temperature_K=250.0,
            pressure_Pa=101325.0,
        )
        assert rec["stable_phase"] == "solid"

    def test_unknown_substance_degraded(self):
        rec = thermo.check_phase_stability(
            substance="unobtainium",
            temperature_K=298.15,
            pressure_Pa=101325.0,
        )
        assert rec["status"] in {"degraded", "refused"}


# ---------------------------------------------------------------------------
# CHEM-013 mass and energy balance
# ---------------------------------------------------------------------------


class TestMassBalance:
    def test_balanced_reaction_passes(self):
        rec = thermo.mass_balance_check(
            reactants=[
                {"formula": "H2", "moles": 2.0},
                {"formula": "O2", "moles": 1.0},
            ],
            products=[
                {"formula": "H2O", "moles": 2.0},
            ],
        )
        check = next(v for v in rec["verification"] if v["check"] == "mass_balance")
        assert check["status"] == "pass"

    def test_imbalanced_reaction_fails(self):
        # 2 H2 + O2 -> 3 H2O (mass imbalance: H not conserved in the count)
        rec = thermo.mass_balance_check(
            reactants=[
                {"formula": "H2", "moles": 2.0},
                {"formula": "O2", "moles": 1.0},
            ],
            products=[
                {"formula": "H2O", "moles": 3.0},
            ],
        )
        check = next(v for v in rec["verification"] if v["check"] == "mass_balance")
        assert check["status"] == "fail"


class TestEnergyBalance:
    def test_closed_system_energy_conserved(self):
        rec = thermo.energy_balance_check(
            inputs=[{"energy_kJ": 100.0}],
            outputs=[{"energy_kJ": 60.0}, {"energy_kJ": 40.0}],
        )
        check = next(v for v in rec["verification"] if v["check"] == "energy_balance")
        assert check["status"] == "pass"

    def test_energy_imbalance_fails(self):
        rec = thermo.energy_balance_check(
            inputs=[{"energy_kJ": 100.0}],
            outputs=[{"energy_kJ": 50.0}],
        )
        check = next(v for v in rec["verification"] if v["check"] == "energy_balance")
        assert check["status"] == "fail"


# ---------------------------------------------------------------------------
# CHEM-013 limiting reactant + ideal gas law
# ---------------------------------------------------------------------------


class TestLimitingReactant:
    def test_stoichiometric_limit_identified(self):
        # 2 H2 + O2 -> 2 H2O. 5 mol H2 (coeff 2, ratio 2.5) + 3 mol O2 (coeff 1, ratio 3.0).
        # Lower ratio = H2, so H2 is limiting.
        rec = thermo.limiting_reactant(
            reactants=[
                {"formula": "H2", "moles": 5.0, "coefficient": 2},
                {"formula": "O2", "moles": 3.0, "coefficient": 1},
            ],
        )
        assert rec["limiting_reactant"] == "H2"
        assert rec["status"] == "succeeded"

    def test_no_reactants_refused(self):
        rec = thermo.limiting_reactant(reactants=[])
        assert rec["status"] == "refused"


class TestIdealGasLaw:
    def test_pv_equals_nrt(self):
        # n=1 mol, T=273.15 K -> P*V = nRT = 1*8.314*273.15 ~ 2270.98 J/mol*mol
        rec = thermo.ideal_gas_law(
            pressure_Pa=None,
            volume_m3=1.0,
            moles=1.0,
            temperature_K=273.15,
        )
        assert rec["name"] == "pressure"
        assert math.isclose(rec["value"], 8.314462618 * 273.15, rel_tol=1e-4)

    def test_solve_for_volume(self):
        rec = thermo.ideal_gas_law(
            pressure_Pa=101325.0,
            volume_m3=None,
            moles=1.0,
            temperature_K=273.15,
        )
        assert rec["name"] == "volume"
        assert math.isclose(rec["value"], 0.022414, rel_tol=1e-3)


class TestUnitConsistency:
    def test_value_record_has_unit(self):
        rec = thermo.arrhenius_rate(
            pre_exponential=1.0e10,
            activation_energy_kJ_per_mol=50.0,
            temperature_K=298.15,
        )
        assert rec.get("unit")

    def test_equilibrium_record_has_unit(self):
        rec = thermo.equilibrium_constant(
            delta_g_kJ_per_mol=-20.0,
            temperature_K=298.15,
        )
        assert rec["unit"] == "dimensionless"


# ---------------------------------------------------------------------------
# CHEM-014 Spectroscopy — peak detection
# ---------------------------------------------------------------------------


class TestPeakDetection:
    def test_detect_obvious_peaks(self):
        # Two clear peaks at x=2 and x=7 in a 10-point spectrum
        xs = [0.0, 1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0]
        ys = [0.0, 0.0, 10.0, 0.0, 0.0, 0.0, 0.0, 5.0, 0.0, 0.0]
        analyzer = spectro.SpectraAnalyzer(kind="generic")
        peaks = analyzer.detect_peaks(xs, ys, threshold=1.0)
        peak_xs = [p["x"] for p in peaks]
        assert 2.0 in peak_xs
        assert 7.0 in peak_xs
        assert all(p["height"] >= 1.0 for p in peaks)

    def test_threshold_filters_small_peaks(self):
        xs = [0.0, 1.0, 2.0, 3.0]
        ys = [0.0, 0.5, 0.0, 0.0]
        analyzer = spectro.SpectraAnalyzer(kind="generic")
        peaks = analyzer.detect_peaks(xs, ys, threshold=1.0)
        assert peaks == []

    def test_peak_area_integration(self):
        # triangular peak at x=1 (height=2, FWHM ~1)
        xs = [0.0, 1.0, 2.0]
        ys = [0.0, 2.0, 0.0]
        analyzer = spectro.SpectraAnalyzer(kind="generic")
        area = analyzer.integrate(xs, ys, x_min=0.0, x_max=2.0)
        assert area > 0.0
        # trapezoid: 0.5*(0+2)*1 + 0.5*(2+0)*1 = 2.0
        assert math.isclose(area, 2.0, rel_tol=1e-6)


# ---------------------------------------------------------------------------
# CHEM-014 Spectroscopy — reference assignment
# ---------------------------------------------------------------------------


class TestPeakAssignment:
    def test_assign_ir_peak_to_carbonyl(self):
        # C=O stretch ~1715 cm^-1
        reference = {
            "C=O stretch": 1715.0,
            "O-H stretch": 3300.0,
            "C-H stretch": 2900.0,
        }
        analyzer = spectro.SpectraAnalyzer(kind="IR", reference=reference, tolerance=20.0)
        assignment = analyzer.assign(1710.0)
        assert assignment["assignment"] == "C=O stretch"
        assert assignment["status"] == "succeeded"

    def test_assign_unknown_peak_flagged(self):
        reference = {"C=O stretch": 1715.0}
        analyzer = spectro.SpectraAnalyzer(kind="IR", reference=reference, tolerance=5.0)
        assignment = analyzer.assign(1500.0)
        assert assignment["status"] in {"degraded", "unassigned"}
        assert assignment["assignment"] in {None, "", "unknown"}


class TestMatchSpectrum:
    def test_identical_spectra_match_one(self):
        xs = [1.0, 2.0, 3.0]
        ys = [0.0, 10.0, 0.0]
        analyzer = spectro.SpectraAnalyzer(kind="generic")
        result = analyzer.match_spectrum(xs, ys, xs, ys)
        assert math.isclose(result["similarity"], 1.0, abs_tol=1e-6)

    def test_disjoint_spectra_match_low(self):
        xs_a = [1.0, 2.0, 3.0]
        ys_a = [10.0, 0.0, 0.0]
        xs_b = [1.0, 2.0, 3.0]
        ys_b = [0.0, 0.0, 10.0]
        analyzer = spectro.SpectraAnalyzer(kind="generic")
        result = analyzer.match_spectrum(xs_a, ys_a, xs_b, ys_b)
        assert result["similarity"] < 0.6


# ---------------------------------------------------------------------------
# CHEM-014 Spectroscopy — format support / rejection
# ---------------------------------------------------------------------------


class TestSpectraFormats:
    def test_nmr_kind_supported(self):
        analyzer = spectro.SpectraAnalyzer(kind="NMR")
        assert analyzer.kind == "NMR"
        assert analyzer.x_unit_labels()["NMR"] == "ppm"

    def test_ir_kind_supported(self):
        analyzer = spectro.SpectraAnalyzer(kind="IR")
        assert analyzer.x_unit_labels()["IR"] == "cm^-1"

    def test_ms_kind_supported(self):
        analyzer = spectro.SpectraAnalyzer(kind="MS")
        assert analyzer.x_unit_labels()["MS"] == "m/z"

    def test_unsupported_kind_rejected(self):
        try:
            spectro.SpectraAnalyzer(kind="voodoo")
            raise AssertionError("expected ValueError")
        except ValueError:
            pass
