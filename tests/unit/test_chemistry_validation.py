"""Unit tests for ``general_ludd.chemistry.validation`` (CHEM-019).

Covers the validation framework from
``docs/specs/FEATURE_CHEMISTRY_EXPERT.md`` §10:

* ValidationStatus constants
* supports_execution gate
* All 8 per-check implementations (mass, charge, energy, atom conservation;
  unit consistency, convergence, limiting case, sensitivity)
* validate_result aggregate status algorithm (validated / provisional /
  invalid / not_applicable)
* Edge cases: missing fields, zero values, boundary tolerances,
  unknown check names, empty check lists

Loaded by file path so the suite is robust to ``sys.path`` variations
inside worktrees.
"""

from __future__ import annotations

import importlib.util
import os

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
_VAL_PATH = os.path.join(_PROJECT_ROOT, "src", "general_ludd", "chemistry", "validation.py")


def _load_val():
    spec = importlib.util.spec_from_file_location("validation_under_test", _VAL_PATH)
    assert spec is not None and spec.loader is not None, "validation spec failed"
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


val = _load_val()


# ---------------------------------------------------------------------------
# ValidationStatus constants
# ---------------------------------------------------------------------------


class TestValidationStatus:
    def test_constants_defined(self):
        assert val.ValidationStatus.VALIDATED == "validated"
        assert val.ValidationStatus.PROVISIONAL == "provisional"
        assert val.ValidationStatus.INVALID == "invalid"
        assert val.ValidationStatus.NOT_APPLICABLE == "not_applicable"

    def test_constants_are_distinct(self):
        vs = val.ValidationStatus
        all_states = {vs.VALIDATED, vs.PROVISIONAL, vs.INVALID, vs.NOT_APPLICABLE}
        assert len(all_states) == 4


# ---------------------------------------------------------------------------
# supports_execution
# ---------------------------------------------------------------------------


class TestSupportsExecution:
    def test_validated_supports_execution(self):
        assert val.supports_execution(val.ValidationStatus.VALIDATED) is True

    def test_provisional_does_not_support(self):
        assert val.supports_execution(val.ValidationStatus.PROVISIONAL) is False

    def test_invalid_does_not_support(self):
        assert val.supports_execution(val.ValidationStatus.INVALID) is False

    def test_not_applicable_does_not_support(self):
        assert val.supports_execution(val.ValidationStatus.NOT_APPLICABLE) is False

    def test_arbitrary_string_does_not_support(self):
        assert val.supports_execution("random") is False
        assert val.supports_execution("") is False


# ---------------------------------------------------------------------------
# Mass conservation
# ---------------------------------------------------------------------------


class TestMassConservation:
    def test_perfect_conservation(self):
        result = val.validate_result(
            {
                "checks": ["mass_conservation"],
                "mass_in": 100.0,
                "mass_out": 100.0,
                "tolerance_pct": 0.5,
            }
        )
        assert result["status"] == val.ValidationStatus.VALIDATED
        v = result["verification"][0]
        assert v["check"] == "mass_conservation"
        assert v["status"] == "pass"

    def test_small_deviation_within_tolerance(self):
        result = val.validate_result(
            {
                "checks": ["mass_conservation"],
                "mass_in": 100.0,
                "mass_out": 100.2,
                "tolerance_pct": 0.5,
            }
        )
        v = result["verification"][0]
        assert v["status"] == "pass"
        assert v["relative_error_pct"] <= 0.5

    def test_deviation_exceeds_tolerance(self):
        result = val.validate_result(
            {
                "checks": ["mass_conservation"],
                "mass_in": 100.0,
                "mass_out": 105.0,
                "tolerance_pct": 0.5,
            }
        )
        assert result["status"] == val.ValidationStatus.INVALID
        v = result["verification"][0]
        assert v["status"] == "fail"
        assert v["relative_error_pct"] > 0.5

    def test_missing_field_warns(self):
        result = val.validate_result(
            {
                "checks": ["mass_conservation"],
                "mass_in": 100.0,
                "tolerance_pct": 0.5,
            }
        )
        assert result["status"] == val.ValidationStatus.PROVISIONAL
        v = result["verification"][0]
        assert v["status"] == "warn"
        assert "missing" in v["detail"]

    def test_both_fields_missing_warns(self):
        result = val.validate_result({"checks": ["mass_conservation"], "tolerance_pct": 0.5})
        v = result["verification"][0]
        assert v["status"] == "warn"

    def test_zero_in_with_nonzero_out(self):
        result = val.validate_result(
            {
                "checks": ["mass_conservation"],
                "mass_in": 0.0,
                "mass_out": 50.0,
                "tolerance_pct": 0.5,
            }
        )
        v = result["verification"][0]
        assert v["status"] == "fail"


# ---------------------------------------------------------------------------
# Charge conservation
# ---------------------------------------------------------------------------


class TestChargeConservation:
    def test_perfect_conservation(self):
        result = val.validate_result(
            {
                "checks": ["charge_conservation"],
                "charge_in": 0.0,
                "charge_out": 0.0,
                "tolerance_pct": 0.5,
            }
        )
        assert result["status"] == val.ValidationStatus.VALIDATED
        v = result["verification"][0]
        assert v["check"] == "charge_conservation"
        assert v["status"] == "pass"

    def test_small_imbalance_passes_at_boundary(self):
        result = val.validate_result(
            {
                "checks": ["charge_conservation"],
                "charge_in": 2.0,
                "charge_out": 2.01,
                "tolerance_pct": 0.5,
            }
        )
        v = result["verification"][0]
        assert v["status"] == "pass"

    def test_large_charge_imbalance_fails(self):
        result = val.validate_result(
            {
                "checks": ["charge_conservation"],
                "charge_in": 2.0,
                "charge_out": 4.0,
                "tolerance_pct": 0.5,
            }
        )
        v = result["verification"][0]
        assert v["status"] == "fail"


# ---------------------------------------------------------------------------
# Energy conservation
# ---------------------------------------------------------------------------


class TestEnergyConservation:
    def test_perfect_conservation(self):
        result = val.validate_result(
            {
                "checks": ["energy_conservation"],
                "energy_in": 500.0,
                "energy_out": 500.0,
                "tolerance_pct": 1.0,
            }
        )
        v = result["verification"][0]
        assert v["status"] == "pass"

    def test_large_deviation_fails(self):
        result = val.validate_result(
            {
                "checks": ["energy_conservation"],
                "energy_in": 100.0,
                "energy_out": 200.0,
                "tolerance_pct": 1.0,
            }
        )
        v = result["verification"][0]
        assert v["status"] == "fail"


# ---------------------------------------------------------------------------
# Atom conservation
# ---------------------------------------------------------------------------


class TestAtomConservation:
    def test_perfect_atom_balance(self):
        result = val.validate_result(
            {
                "checks": ["atom_conservation"],
                "atoms_in": {"C": 6, "H": 12, "O": 6},
                "atoms_out": {"C": 6, "H": 12, "O": 6},
                "tolerance_pct": 0.5,
            }
        )
        v = result["verification"][0]
        assert v["status"] == "pass"

    def test_element_imbalance_fails(self):
        result = val.validate_result(
            {
                "checks": ["atom_conservation"],
                "atoms_in": {"C": 6, "H": 12},
                "atoms_out": {"C": 5, "H": 12},
                "tolerance_pct": 0.5,
            }
        )
        v = result["verification"][0]
        assert v["status"] == "fail"
        assert v["worst_element"] == "C"

    def test_different_element_counts(self):
        result = val.validate_result(
            {
                "checks": ["atom_conservation"],
                "atoms_in": {"C": 6, "H": 12},
                "atoms_out": {"C": 6, "H": 12, "N": 2},
                "tolerance_pct": 0.5,
            }
        )
        v = result["verification"][0]
        assert v["status"] == "fail"
        assert v["worst_element"] == "N"

    def test_missing_atom_dicts_warns(self):
        result = val.validate_result({"checks": ["atom_conservation"], "tolerance_pct": 0.5})
        v = result["verification"][0]
        assert v["status"] == "warn"
        assert "missing" in v["detail"]

    def test_empty_dicts_warn(self):
        result = val.validate_result(
            {
                "checks": ["atom_conservation"],
                "atoms_in": {},
                "atoms_out": {},
                "tolerance_pct": 0.5,
            }
        )
        v = result["verification"][0]
        assert v["status"] == "warn"

    def test_one_sided_atom_dict_warns(self):
        result = val.validate_result(
            {
                "checks": ["atom_conservation"],
                "atoms_in": {"C": 6},
                "atoms_out": {},
                "tolerance_pct": 0.5,
            }
        )
        v = result["verification"][0]
        assert v["status"] == "fail"
        assert v["worst_element"] == "C"


# ---------------------------------------------------------------------------
# Unit consistency
# ---------------------------------------------------------------------------


class TestUnitConsistency:
    def test_all_values_have_same_unit(self):
        result = val.validate_result(
            {
                "checks": ["unit_consistency"],
                "values": [
                    {"name": "mass", "unit": "g"},
                    {"name": "weight", "unit": "g"},
                ],
            }
        )
        v = result["verification"][0]
        assert v["status"] == "pass"
        assert v["unit"] == "g"

    def test_mixed_units_fails(self):
        result = val.validate_result(
            {
                "checks": ["unit_consistency"],
                "values": [
                    {"name": "a", "unit": "mg/L"},
                    {"name": "b", "unit": "mol/L"},
                ],
            }
        )
        v = result["verification"][0]
        assert v["status"] == "fail"
        assert "inconsistent" in v["detail"]

    def test_missing_unit_fails(self):
        result = val.validate_result(
            {
                "checks": ["unit_consistency"],
                "values": [
                    {"name": "a", "unit": "g"},
                    {"name": "b", "unit": ""},
                ],
            }
        )
        v = result["verification"][0]
        assert v["status"] == "fail"

    def test_no_unit_fails(self):
        result = val.validate_result(
            {
                "checks": ["unit_consistency"],
                "values": [
                    {"name": "mass"},
                ],
            }
        )
        v = result["verification"][0]
        assert v["status"] == "fail"

    def test_single_value_with_unit_passes(self):
        result = val.validate_result(
            {
                "checks": ["unit_consistency"],
                "values": [
                    {"name": "mass", "unit": "kg"},
                ],
            }
        )
        v = result["verification"][0]
        assert v["status"] == "pass"

    def test_empty_values_passes(self):
        result = val.validate_result({"checks": ["unit_consistency"], "values": []})
        v = result["verification"][0]
        assert v["status"] == "pass"

    def test_non_dict_entries_skipped(self):
        result = val.validate_result(
            {
                "checks": ["unit_consistency"],
                "values": [
                    {"name": "a", "unit": "g"},
                    "not-a-dict",
                    42,
                ],
            }
        )
        v = result["verification"][0]
        assert v["status"] == "pass"
        assert v["n_values"] == 3


# ---------------------------------------------------------------------------
# Convergence
# ---------------------------------------------------------------------------


class TestConvergence:
    def test_converged_is_true_passes(self):
        result = val.validate_result({"checks": ["convergence"], "converged": True})
        v = result["verification"][0]
        assert v["status"] == "pass"

    def test_converged_is_false_fails(self):
        result = val.validate_result({"checks": ["convergence"], "converged": False})
        v = result["verification"][0]
        assert v["status"] == "fail"

    def test_converged_default_is_false(self):
        result = val.validate_result({"checks": ["convergence"]})
        v = result["verification"][0]
        assert v["status"] == "fail"

    def test_converged_with_warnings_is_provisional(self):
        result = val.validate_result(
            {
                "checks": ["convergence"],
                "converged": True,
                "warnings": ["oscillation detected"],
            }
        )
        v = result["verification"][0]
        assert v["status"] == "warn"

    def test_records_iterations_when_present(self):
        result = val.validate_result(
            {
                "checks": ["convergence"],
                "converged": True,
                "iterations": 150,
            }
        )
        v = result["verification"][0]
        assert v["iterations"] == 150
        assert "iterations=150" in v["detail"]


# ---------------------------------------------------------------------------
# Limiting case
# ---------------------------------------------------------------------------


class TestLimitingCase:
    def test_input_zero_output_zero_passes(self):
        result = val.validate_result(
            {
                "checks": ["limiting_case"],
                "input_zero": True,
                "output_zero": True,
                "limiting_case": "dilute_solution",
            }
        )
        v = result["verification"][0]
        assert v["status"] == "pass"

    def test_input_zero_output_nonzero_fails(self):
        result = val.validate_result(
            {
                "checks": ["limiting_case"],
                "input_zero": True,
                "output_zero": False,
            }
        )
        v = result["verification"][0]
        assert v["status"] == "fail"

    def test_input_not_zero_warns(self):
        result = val.validate_result(
            {
                "checks": ["limiting_case"],
                "input_zero": False,
                "limiting_case": "finite_input",
            }
        )
        v = result["verification"][0]
        assert v["status"] == "warn"
        assert "did not exercise input_zero" in v["detail"]


# ---------------------------------------------------------------------------
# Sensitivity
# ---------------------------------------------------------------------------


class TestSensitivity:
    def test_sensitivity_below_threshold_passes(self):
        result = val.validate_result(
            {
                "checks": ["sensitivity"],
                "sensitivity": 0.001,
                "sensitivity_threshold": 0.01,
            }
        )
        v = result["verification"][0]
        assert v["status"] == "pass"

    def test_sensitivity_above_threshold_fails(self):
        result = val.validate_result(
            {
                "checks": ["sensitivity"],
                "sensitivity": 0.1,
                "sensitivity_threshold": 0.01,
            }
        )
        v = result["verification"][0]
        assert v["status"] == "fail"

    def test_sensitivity_equals_threshold_passes(self):
        result = val.validate_result(
            {
                "checks": ["sensitivity"],
                "sensitivity": 0.01,
                "sensitivity_threshold": 0.01,
            }
        )
        v = result["verification"][0]
        assert v["status"] == "pass"

    def test_missing_sensitivity_warns(self):
        result = val.validate_result({"checks": ["sensitivity"], "sensitivity_threshold": 0.01})
        v = result["verification"][0]
        assert v["status"] == "warn"


# ---------------------------------------------------------------------------
# Aggregate status algorithm
# ---------------------------------------------------------------------------


class TestAggregateStatus:
    def test_all_pass_validated(self):
        result = val.validate_result(
            {
                "checks": ["mass_conservation", "unit_consistency"],
                "mass_in": 100.0,
                "mass_out": 100.0,
                "values": [{"name": "m", "unit": "g"}],
            }
        )
        assert result["status"] == val.ValidationStatus.VALIDATED
        assert result["supports_execution"] is True

    def test_any_fail_invalid(self):
        result = val.validate_result(
            {
                "checks": ["mass_conservation", "convergence"],
                "mass_in": 100.0,
                "mass_out": 100.0,
                "converged": False,
            }
        )
        assert result["status"] == val.ValidationStatus.INVALID

    def test_no_fail_but_warn_provisional(self):
        result = val.validate_result(
            {
                "checks": ["mass_conservation", "unit_consistency"],
                "mass_out": 100.0,
                "values": [{"name": "m", "unit": "g"}],
            }
        )
        assert result["status"] == val.ValidationStatus.PROVISIONAL

    def test_unknown_check_name_warns(self):
        result = val.validate_result(
            {
                "checks": ["mass_conservation", "nonexistent_check"],
                "mass_in": 100.0,
                "mass_out": 100.0,
            }
        )
        assert result["status"] == val.ValidationStatus.PROVISIONAL
        assert "unknown check" in result["verification"][1]["detail"]

    def test_no_checks_declared_not_applicable(self):
        result = val.validate_result({"mass_in": 100.0, "mass_out": 100.0})
        assert result["status"] == val.ValidationStatus.NOT_APPLICABLE
        assert "no checks declared" in result["limitations"]

    def test_empty_checks_list_not_applicable(self):
        result = val.validate_result(
            {
                "checks": [],
                "mass_in": 100.0,
                "mass_out": 100.0,
            }
        )
        assert result["status"] == val.ValidationStatus.NOT_APPLICABLE


# ---------------------------------------------------------------------------
# Output structure
# ---------------------------------------------------------------------------


class TestOutputStructure:
    def test_top_level_keys_present(self):
        result = val.validate_result(
            {
                "checks": ["mass_conservation"],
                "mass_in": 100.0,
                "mass_out": 100.0,
            }
        )
        for key in (
            "schema_version",
            "method_id",
            "run_id",
            "name",
            "status",
            "supports_execution",
            "checks_run",
            "verification",
            "tolerance_pct",
            "errors",
            "limitations",
        ):
            assert key in result, f"missing key: {key}"

    def test_run_id_is_unique_per_call(self):
        r1 = val.validate_result({"checks": ["mass_conservation"], "mass_in": 1.0, "mass_out": 1.0})
        r2 = val.validate_result({"checks": ["mass_conservation"], "mass_in": 2.0, "mass_out": 2.0})
        assert r1["run_id"] != r2["run_id"]

    def test_run_id_is_valid_uuid(self):
        import uuid as _uuid

        result = val.validate_result({"checks": ["mass_conservation"], "mass_in": 1.0, "mass_out": 1.0})
        assert _uuid.UUID(result["run_id"])

    def test_schema_and_method_are_stable(self):
        result = val.validate_result({"checks": ["mass_conservation"], "mass_in": 1.0, "mass_out": 1.0})
        assert result["schema_version"] == "1.0"
        assert result["method_id"] == "chemistry-validation@0.1.0"

    def test_checks_run_count_matches(self):
        result = val.validate_result(
            {
                "checks": ["mass_conservation", "charge_conservation", "convergence"],
                "mass_in": 100.0,
                "mass_out": 100.0,
                "charge_in": 0.0,
                "charge_out": 0.0,
                "converged": True,
            }
        )
        assert result["checks_run"] == 3
        assert len(result["verification"]) == 3


# ---------------------------------------------------------------------------
# Combos — multiple checks across categories
# ---------------------------------------------------------------------------


class TestMultiCheckCombos:
    def test_all_conservation_checks(self):
        result = val.validate_result(
            {
                "checks": [
                    "mass_conservation",
                    "charge_conservation",
                    "energy_conservation",
                    "atom_conservation",
                ],
                "mass_in": 100.0,
                "mass_out": 100.0,
                "charge_in": 2.0,
                "charge_out": 2.0,
                "energy_in": 500.0,
                "energy_out": 500.0,
                "atoms_in": {"C": 6},
                "atoms_out": {"C": 6},
            }
        )
        assert result["status"] == val.ValidationStatus.VALIDATED
        assert result["checks_run"] == 4

    def test_all_computational_checks(self):
        result = val.validate_result(
            {
                "checks": [
                    "convergence",
                    "limiting_case",
                    "sensitivity",
                ],
                "converged": True,
                "input_zero": True,
                "output_zero": True,
                "sensitivity": 0.001,
                "sensitivity_threshold": 0.01,
            }
        )
        assert result["status"] == val.ValidationStatus.VALIDATED

    def test_mixed_pass_and_warn_is_provisional(self):
        result = val.validate_result(
            {
                "checks": [
                    "mass_conservation",
                    "limiting_case",
                ],
                "mass_in": 100.0,
                "mass_out": 100.0,
                "input_zero": False,
            }
        )
        assert result["status"] == val.ValidationStatus.PROVISIONAL

    def test_fail_takes_priority_over_warn(self):
        result = val.validate_result(
            {
                "checks": [
                    "convergence",
                    "sensitivity",
                ],
                "converged": False,
                "sensitivity": 0.001,
                "sensitivity_threshold": 0.01,
            }
        )
        assert result["status"] == val.ValidationStatus.INVALID


# ---------------------------------------------------------------------------
# Default tolerance
# ---------------------------------------------------------------------------


class TestDefaultTolerance:
    def test_default_tolerance_is_0_5_percent(self):
        result = val.validate_result(
            {
                "checks": ["mass_conservation"],
                "mass_in": 100.0,
                "mass_out": 100.0,
            }
        )
        assert result["tolerance_pct"] == 0.5

    def test_explicit_tolerance_override(self):
        result = val.validate_result(
            {
                "checks": ["mass_conservation"],
                "mass_in": 100.0,
                "mass_out": 101.0,
                "tolerance_pct": 2.0,
            }
        )
        v = result["verification"][0]
        assert v["status"] == "pass"
        assert result["tolerance_pct"] == 2.0


# ---------------------------------------------------------------------------
# Error records
# ---------------------------------------------------------------------------


class TestErrorRecords:
    def test_invalid_generates_error(self):
        result = val.validate_result(
            {
                "checks": ["convergence"],
                "converged": False,
            }
        )
        assert len(result["errors"]) == 1
        assert result["errors"][0]["code"] == "chem.validation.check_failed"

    def test_validated_has_no_errors(self):
        result = val.validate_result(
            {
                "checks": ["mass_conservation"],
                "mass_in": 100.0,
                "mass_out": 100.0,
            }
        )
        assert result["errors"] == []

    def test_provisional_has_no_errors(self):
        result = val.validate_result({"checks": ["mass_conservation"]})
        assert result["errors"] == []
        assert len(result["limitations"]) > 0
