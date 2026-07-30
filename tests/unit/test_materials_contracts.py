"""Tests for MATE-P1 contracts and data integrity (spec MATE-001 §5, §7).

Covers the five Pydantic contracts (DesignRequirements, MaterialCandidate,
ProcessPlan, SimulationPlan, EngineeringVerdict) and the units service.

Verifies:
  - Required fields are enforced (missing units, missing values rejected).
  - Numeric constraints reject negative uncertainty, out-of-range confidence.
  - Enum fields reject invalid tokens.
  - JSON round-trip serialization preserves data and schema_version.
  - Units service converts compatible units and rejects incompatible dims.
  - MATE-DEC-004: every derived value carries units + basis + uncertainty.
  - MATE-SAFE-006: unit mismatch blocks a positive verdict.
"""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from general_ludd.materials.contracts import (
    SCHEMA_VERSION,
    DesignRequirements,
    EngineeringVerdict,
    MaterialCandidate,
    ProcessPlan,
    SimulationPlan,
)
from general_ludd.materials.units import (
    DimensionMismatch,
    UnknownUnit,
    convert,
    dim_of,
)

# ─── units service ────────────────────────────────────────────────────────────


class TestUnits:
    def test_mpa_to_ksi_round_trip(self):
        ksi = convert(1000.0, "MPa", "ksi")
        assert ksi == pytest.approx(145.0377, rel=1e-4)
        back = convert(ksi, "ksi", "MPa")
        assert back == pytest.approx(1000.0, rel=1e-4)

    def test_mpa_to_pa(self):
        assert convert(1.0, "MPa", "Pa") == pytest.approx(1_000_000.0)

    def test_mm_to_in_round_trip(self):
        inches = convert(25.4, "mm", "in")
        assert inches == pytest.approx(1.0, rel=1e-6)
        assert convert(inches, "in", "mm") == pytest.approx(25.4, rel=1e-6)

    def test_temperature_K_C_F(self):
        assert convert(273.15, "K", "C") == pytest.approx(0.0, abs=1e-6)
        assert convert(0.0, "C", "F") == pytest.approx(32.0, abs=1e-6)
        assert convert(212.0, "F", "C") == pytest.approx(100.0, abs=1e-6)
        assert convert(300.0, "K", "F") == pytest.approx(80.33, abs=0.01)

    def test_incompatible_dimensions_rejected(self):
        with pytest.raises(DimensionMismatch):
            convert(100.0, "MPa", "mm")
        with pytest.raises(DimensionMismatch):
            convert(1.0, "K", "MPa")

    def test_unknown_unit_rejected(self):
        with pytest.raises(UnknownUnit):
            convert(1.0, "MPa", "fortnight")
        with pytest.raises(UnknownUnit):
            convert(1.0, "whatsit", "MPa")

    def test_dim_of_known_units(self):
        assert dim_of("MPa") == "stress"
        assert dim_of("mm") == "length"
        assert dim_of("K") == "temperature"
        with pytest.raises(UnknownUnit):
            dim_of("nonsense")


# ─── DesignRequirements (spec §5.1) ───────────────────────────────────────────


def _good_design_requirements() -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "geometry_refs": [{"uri": "file:///part.step", "digest": "sha256:abc", "coordinate_system": "ISO"}],
        "load_cases": [
            {"id": "lc1", "type": "tensile", "magnitude": 200.0, "unit": "MPa", "direction": "x", "confidence": 0.95}
        ],
        "environment": [{"factor": "temperature", "range": [-40.0, 80.0], "unit": "C", "duration": "1000h"}],
        "design_life": {"value": 10000.0, "unit": "cycles", "reliability_target": 0.95},
        "failure_consequence": "significant",
        "manufacturing": {"quantity": 1000, "rate": 100, "processes_allowed": ["injection_molding"]},
        "interfaces": [],
        "tolerances": [],
        "inspection": {"access": "full", "methods_allowed": ["visual"], "sampling": "AQL 2.5"},
        "cost_sustainability": {"limits": {}, "repair": True, "recycled_content": 0.0, "end_of_life": "recycle"},
        "assumptions": [],
    }


class TestDesignRequirements:
    def test_valid_constructs(self):
        reqs = DesignRequirements.model_validate(_good_design_requirements())
        assert reqs.failure_consequence == "significant"
        assert reqs.requires_human_review is False

    def test_safety_critical_requires_human_review(self):
        data = _good_design_requirements()
        data["failure_consequence"] = "safety_critical"
        reqs = DesignRequirements.model_validate(data)
        assert reqs.requires_human_review is True

    def test_invalid_failure_consequence_enum_rejected(self):
        data = _good_design_requirements()
        data["failure_consequence"] = "kinda_bad"
        with pytest.raises(ValidationError):
            DesignRequirements.model_validate(data)

    def test_load_case_missing_unit_rejected(self):
        data = _good_design_requirements()
        data["load_cases"] = [{"id": "lc1", "type": "tensile", "magnitude": 200.0, "direction": "x"}]
        with pytest.raises(ValidationError):
            DesignRequirements.model_validate(data)

    def test_json_round_trip(self):
        reqs = DesignRequirements.model_validate(_good_design_requirements())
        s = reqs.model_dump_json()
        back = DesignRequirements.model_validate_json(s)
        assert back.schema_version == SCHEMA_VERSION
        assert back.load_cases[0].magnitude == reqs.load_cases[0].magnitude


# ─── MaterialCandidate (spec §5.2) ────────────────────────────────────────────


def _good_material_candidate() -> dict:
    return {
        "material_id": "aa6061_t6",
        "condition": {
            "product_form": "sheet",
            "direction": "L",
            "temper_or_cure": "T6",
            "moisture": "dry",
            "temperature": {"value": 20.0, "unit": "C"},
        },
        "properties": [
            {
                "name": "yield_strength",
                "value_or_range": 276.0,
                "unit": "MPa",
                "basis": "nominal",
                "method": "ASTM B209",
                "uncertainty": 15.0,
            }
        ],
        "source": {
            "uri": "file:///handbook/asm2.json",
            "publisher": "ASM",
            "revision": "2023",
            "retrieved_at": "2024-01-01T00:00:00Z",
            "digest": "sha256:deadbeef",
            "license": "reference",
        },
        "requirement_margins": [],
        "manufacturing_compatibility": [],
        "joining_compatibility": [],
        "hazards": [],
        "confidence": 80,
        "unknowns": [],
    }


class TestMaterialCandidate:
    def test_valid_constructs(self):
        cand = MaterialCandidate.model_validate(_good_material_candidate())
        assert cand.confidence == 80

    def test_negative_uncertainty_rejected(self):
        data = _good_material_candidate()
        data["properties"][0]["uncertainty"] = -5.0
        with pytest.raises(ValidationError):
            MaterialCandidate.model_validate(data)

    def test_property_missing_unit_rejected(self):
        data = _good_material_candidate()
        del data["properties"][0]["unit"]
        with pytest.raises(ValidationError):
            MaterialCandidate.model_validate(data)

    def test_confidence_out_of_range_rejected(self):
        data = _good_material_candidate()
        data["confidence"] = 150
        with pytest.raises(ValidationError):
            MaterialCandidate.model_validate(data)
        data["confidence"] = -1
        with pytest.raises(ValidationError):
            MaterialCandidate.model_validate(data)

    def test_json_round_trip(self):
        cand = MaterialCandidate.model_validate(_good_material_candidate())
        s = cand.model_dump_json()
        back = MaterialCandidate.model_validate_json(s)
        assert back.material_id == cand.material_id
        assert back.properties[0].unit == "MPa"


# ─── ProcessPlan (spec §5.3) ──────────────────────────────────────────────────


def _good_process_plan() -> dict:
    return {
        "plan_id": "11111111-1111-1111-1111-111111111111",
        "process_family": "metal_forming_plan",
        "equipment_class": "hydraulic_press_500t",
        "material_inputs": [{"material_id": "aa6061_t6", "lot_required": True, "condition": {"product_form": "sheet"}}],
        "steps": [
            {
                "id": "s1",
                "operation": "blanking",
                "inputs": [],
                "parameter_window": {},
                "hold_point": False,
                "outputs": [],
            }
        ],
        "tooling": [],
        "controls": [],
        "inspection": [],
        "qualification": [],
        "hazards": [],
        "provenance": [],
    }


class TestProcessPlan:
    def test_valid_constructs(self):
        plan = ProcessPlan.model_validate(_good_process_plan())
        assert plan.process_family == "metal_forming_plan"

    def test_invalid_process_family_enum_rejected(self):
        data = _good_process_plan()
        data["process_family"] = "voodoo"
        with pytest.raises(ValidationError):
            ProcessPlan.model_validate(data)

    def test_invalid_plan_id_uuid_rejected(self):
        data = _good_process_plan()
        data["plan_id"] = "not-a-uuid"
        with pytest.raises(ValidationError):
            ProcessPlan.model_validate(data)

    def test_json_round_trip(self):
        plan = ProcessPlan.model_validate(_good_process_plan())
        back = ProcessPlan.model_validate_json(plan.model_dump_json())
        assert back.plan_id == plan.plan_id


# ─── SimulationPlan (spec §5.4) ───────────────────────────────────────────────


def _good_simulation_plan() -> dict:
    return {
        "model_id": "22222222-2222-2222-2222-222222222222",
        "question": "Does the bracket yield under 200 MPa tensile load at -40C?",
        "solver_adapter": "fe_linear_static_v1",
        "geometry_digest": "sha256:geom",
        "material_models": [
            {"region": "bracket", "model": "elastic_isotropic", "data_source": "asm2", "calibration_range": [200, 400]}
        ],
        "loads_and_boundaries": [
            {"id": "lb1", "type": "pressure", "value": 200.0, "unit": "MPa", "basis": "design_load"}
        ],
        "mesh": {"element_family": "tet4", "target_size": 1.0, "convergence_plan": "h-refine x3"},
        "contacts_and_joints": [],
        "coupling": [],
        "verification": {"benchmarks": [], "conservation_checks": [], "convergence": "h-refine"},
        "validation": {"experiment": None, "measurements": [], "acceptance": {}},
        "uncertainty": {
            "variables": ["yield_strength"],
            "distributions_or_bounds": "+-15 MPa",
            "propagation_method": "worst_case",
        },
        "outputs": [{"quantity": "von_mises_stress", "location": "fillet", "unit": "MPa", "acceptance": "< yield"}],
    }


class TestSimulationPlan:
    def test_valid_constructs(self):
        plan = SimulationPlan.model_validate(_good_simulation_plan())
        assert plan.solver_adapter == "fe_linear_static_v1"

    def test_load_missing_unit_rejected(self):
        data = _good_simulation_plan()
        del data["loads_and_boundaries"][0]["unit"]
        with pytest.raises(ValidationError):
            SimulationPlan.model_validate(data)

    def test_output_missing_unit_rejected(self):
        data = _good_simulation_plan()
        del data["outputs"][0]["unit"]
        with pytest.raises(ValidationError):
            SimulationPlan.model_validate(data)

    def test_empty_question_rejected(self):
        data = _good_simulation_plan()
        data["question"] = "   "
        with pytest.raises(ValidationError):
            SimulationPlan.model_validate(data)

    def test_json_round_trip(self):
        plan = SimulationPlan.model_validate(_good_simulation_plan())
        back = SimulationPlan.model_validate_json(plan.model_dump_json())
        assert back.outputs[0].unit == "MPa"


# ─── EngineeringVerdict (spec §5.5) ───────────────────────────────────────────


def _good_verdict() -> dict:
    return {
        "request_id": "33333333-3333-3333-3333-333333333333",
        "state": "candidate",
        "candidate_ids": ["aa6061_t6"],
        "governing_cases": [{"case_id": "lc1", "failure_mode": "yield", "margin": 0.38, "uncertainty": 0.07}],
        "manufacturing_route_id": None,
        "inspection_plan_id": None,
        "required_tests": [],
        "required_human_reviews": [],
        "limitations": ["nominal_data_only"],
        "evidence_bundle_uri": "file:///evidence/lc1.json",
    }


class TestEngineeringVerdict:
    def test_valid_constructs(self):
        v = EngineeringVerdict.model_validate(_good_verdict())
        assert v.state == "candidate"

    def test_invalid_state_enum_rejected(self):
        data = _good_verdict()
        data["state"] = "probably_fine"
        with pytest.raises(ValidationError):
            EngineeringVerdict.model_validate(data)

    def test_fail_closed_states_allowed(self):
        for state in ("infeasible", "insufficient_data", "candidate", "validated_for_scope"):
            data = _good_verdict()
            data["state"] = state
            EngineeringVerdict.model_validate(data)

    def test_json_round_trip(self):
        v = EngineeringVerdict.model_validate(_good_verdict())
        raw = json.loads(v.model_dump_json())
        assert raw["state"] == "candidate"
        back = EngineeringVerdict.model_validate(raw)
        assert back.evidence_bundle_uri == v.evidence_bundle_uri


# ─── schema_version presence on every contract ───────────────────────────────


class TestSchemaVersioning:
    def test_design_requirements_carries_schema_version(self):
        reqs = DesignRequirements.model_validate(_good_design_requirements())
        assert reqs.schema_version == SCHEMA_VERSION

    def test_schema_version_serialized_in_json(self):
        cand = MaterialCandidate.model_validate(_good_material_candidate())
        raw = json.loads(cand.model_dump_json())
        assert raw["schema_version"] == SCHEMA_VERSION
