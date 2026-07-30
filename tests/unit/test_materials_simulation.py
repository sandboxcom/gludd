"""Tests for the materials simulation subpackage (spec MATE-001 §6, §5.4).

Verifies the tool/simulator adapter contract and the verification machinery:

  - :class:`SolverAdapter` Protocol declares every field required by §6
    (solver/version, license, supported physics, units, determinism,
    resource bounds, checkpoint/restart, schemas, validation cases,
    known limitations).
  - :class:`SimulationPlan` (from contracts) carries a falsifiable question,
    mesh-convergence plan, verification benchmarks, validation experiment
    reference, and uncertainty propagation method.
  - :func:`check_convergence` compares results across mesh densities and
    detects both converged and non-converged sequences.
  - :func:`verify_simulation` runs the full suite (patch, manufactured
    solution, mesh convergence, conservation) and aggregates a verdict.
  - Resource bounds are enforced: an adapter refuses work outside its
    declared bounds.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest
from pydantic import ValidationError

from general_ludd.materials.contracts import SimulationPlan
from general_ludd.materials.simulation import (
    CheckpointRestartSpec,
    ConvergenceResult,
    DeterminismSpec,
    ResourceBounds,
    SimulationVerdict,
    SolverAdapter,
    ValidationCase,
    check_convergence,
    is_question_falsifiable,
    verify_simulation,
)

# ─── adapter fixtures ─────────────────────────────────────────────────────────


def _good_resource_bounds() -> ResourceBounds:
    return ResourceBounds(cpu_cores=8, memory_mb=16_384, wall_time_s=3600, disk_gb=50.0)


def _good_checkpoint_restart() -> CheckpointRestartSpec:
    return CheckpointRestartSpec(supported=True, format="hdf5", max_checkpoints=4)


def _good_determinism() -> DeterminismSpec:
    return DeterminismSpec(reproducible=True, seed_controlled=True, version_pinned=True)


def _good_validation_cases() -> list[ValidationCase]:
    return [
        ValidationCase(
            name="NAFEMS LE1",
            benchmark_uri="https://examples/nafems-le1",
            tolerance={"rel": 0.02},
            status="passing",
        )
    ]


@dataclass
class _LinearStaticAdapter:
    """Reference implementation of :class:`SolverAdapter` for tests."""

    capability_id: str = "fe_linear_static_v1"
    solver_name: str = "MockFEM"
    version: str = "2024.1"
    license: str = "commercial"
    supported_physics: list[str] = field(default_factory=lambda: ["static_structural"])
    unit_conventions: dict[str, str] = field(default_factory=lambda: {"stress": "MPa", "length": "mm", "force": "N"})
    determinism: DeterminismSpec = field(default_factory=_good_determinism)
    resource_bounds: ResourceBounds = field(default_factory=_good_resource_bounds)
    checkpoint_restart: CheckpointRestartSpec = field(default_factory=_good_checkpoint_restart)
    input_schema: dict[str, Any] = field(default_factory=lambda: {"type": "object", "properties": {}})
    output_schema: dict[str, Any] = field(default_factory=lambda: {"type": "object", "properties": {}})
    validation_cases: list[ValidationCase] = field(default_factory=_good_validation_cases)
    known_limitations: list[str] = field(default_factory=lambda: ["no_contact_nonlinearity"])


@dataclass
class _IncompleteAdapter:
    """Adapter missing ``version`` and ``license`` — fails isinstance check."""

    capability_id: str = "broken_v0"


# ─── SimulationPlan fixture (mirrors contracts test) ─────────────────────────


def _good_simulation_plan() -> dict:
    return {
        "model_id": "22222222-2222-2222-2222-222222222222",
        "question": "Does the bracket peak von Mises stress exceed yield at 200 MPa?",
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
        "verification": {
            "benchmarks": ["nafems_le1"],
            "conservation_checks": ["force_balance", "energy_balance"],
            "convergence": "h-refine",
        },
        "validation": {
            "experiment": "lab_strain_gauge_2023",
            "measurements": ["fillet_strain"],
            "acceptance": {"max_rel_error": 0.05},
        },
        "uncertainty": {
            "variables": ["yield_strength"],
            "distributions_or_bounds": "+-15 MPa",
            "propagation_method": "worst_case",
        },
        "outputs": [{"quantity": "von_mises_stress", "location": "fillet", "unit": "MPa", "acceptance": "< yield"}],
    }


# ─── 1. SolverAdapter protocol completeness (spec §6) ────────────────────────


class TestSolverAdapterProtocol:
    def test_protocol_is_runtime_checkable(self):
        adapter = _LinearStaticAdapter()
        assert isinstance(adapter, SolverAdapter)

    def test_incomplete_adapter_fails_isinstance(self):
        adapter = _IncompleteAdapter()
        assert not isinstance(adapter, SolverAdapter)

    def test_adapter_declares_solver_name_and_version(self):
        adapter = _LinearStaticAdapter()
        assert adapter.solver_name == "MockFEM"
        assert adapter.version == "2024.1"
        assert adapter.capability_id == "fe_linear_static_v1"

    def test_adapter_declares_license(self):
        adapter = _LinearStaticAdapter()
        assert adapter.license == "commercial"

    def test_adapter_declares_supported_physics_nonempty(self):
        adapter = _LinearStaticAdapter()
        assert len(adapter.supported_physics) >= 1
        assert "static_structural" in adapter.supported_physics

    def test_adapter_declares_unit_conventions(self):
        adapter = _LinearStaticAdapter()
        assert adapter.unit_conventions["stress"] == "MPa"

    def test_adapter_declares_determinism(self):
        adapter = _LinearStaticAdapter()
        assert isinstance(adapter.determinism, DeterminismSpec)
        assert adapter.determinism.reproducible is True

    def test_adapter_declares_resource_bounds(self):
        adapter = _LinearStaticAdapter()
        assert isinstance(adapter.resource_bounds, ResourceBounds)
        assert adapter.resource_bounds.cpu_cores == 8

    def test_adapter_declares_checkpoint_restart(self):
        adapter = _LinearStaticAdapter()
        assert isinstance(adapter.checkpoint_restart, CheckpointRestartSpec)
        assert adapter.checkpoint_restart.supported is True

    def test_adapter_declares_input_output_schemas(self):
        adapter = _LinearStaticAdapter()
        assert adapter.input_schema["type"] == "object"
        assert adapter.output_schema["type"] == "object"

    def test_adapter_declares_validation_cases(self):
        adapter = _LinearStaticAdapter()
        assert len(adapter.validation_cases) >= 1
        case = adapter.validation_cases[0]
        assert isinstance(case, ValidationCase)
        assert case.status == "passing"

    def test_adapter_declares_known_limitations(self):
        adapter = _LinearStaticAdapter()
        assert len(adapter.known_limitations) >= 1
        assert "no_contact_nonlinearity" in adapter.known_limitations


# ─── 2. SimulationPlan falsifiability & verification metadata ────────────────


class TestSimulationPlanFalsifiable:
    def test_question_is_falsifiable(self):
        plan = SimulationPlan.model_validate(_good_simulation_plan())
        assert is_question_falsifiable(plan.question) is True

    def test_question_without_testable_predicate_is_not_falsifiable(self):
        # Pure declarative statements cannot be falsified by a simulation.
        assert is_question_falsifiable("the bracket is metal") is False
        assert is_question_falsifiable("stress analysis") is False

    def test_question_mark_alone_is_not_falsifiable(self):
        # A bare "what?" is open-ended, not a yes/no falsifiable question.
        assert is_question_falsifiable("how strong is it?") is False

    def test_plan_carries_mesh_convergence_plan(self):
        plan = SimulationPlan.model_validate(_good_simulation_plan())
        assert plan.mesh.convergence_plan == "h-refine x3"

    def test_plan_carries_verification_benchmark(self):
        plan = SimulationPlan.model_validate(_good_simulation_plan())
        assert "nafems_le1" in plan.verification.benchmarks

    def test_plan_carries_conservation_checks(self):
        plan = SimulationPlan.model_validate(_good_simulation_plan())
        assert "force_balance" in plan.verification.conservation_checks

    def test_plan_carries_validation_experiment_reference(self):
        plan = SimulationPlan.model_validate(_good_simulation_plan())
        assert plan.validation.experiment == "lab_strain_gauge_2023"

    def test_plan_carries_uncertainty_propagation_method(self):
        plan = SimulationPlan.model_validate(_good_simulation_plan())
        assert plan.uncertainty.propagation_method == "worst_case"


# ─── 3. check_convergence ─────────────────────────────────────────────────────


class TestCheckConvergence:
    def test_monotonic_decreasing_error_is_converged(self):
        # Richardson-like sequence: error halves with each refinement.
        results = [
            {"mesh_size": 2.0, "quantity": 195.0, "reference": 200.0},
            {"mesh_size": 1.0, "quantity": 198.0, "reference": 200.0},
            {"mesh_size": 0.5, "quantity": 199.5, "reference": 200.0},
        ]
        out = check_convergence(results, tolerance=0.01)
        assert isinstance(out, ConvergenceResult)
        assert out.converged is True
        assert out.relative_change < 0.01

    def test_non_monotonic_error_is_not_converged(self):
        results = [
            {"mesh_size": 2.0, "quantity": 195.0, "reference": 200.0},
            {"mesh_size": 1.0, "quantity": 180.0, "reference": 200.0},
            {"mesh_size": 0.5, "quantity": 210.0, "reference": 200.0},
        ]
        out = check_convergence(results, tolerance=0.01)
        assert out.converged is False

    def test_single_result_is_insufficient(self):
        results = [{"mesh_size": 1.0, "quantity": 199.0, "reference": 200.0}]
        out = check_convergence(results, tolerance=0.01)
        assert out.converged is False
        assert "insufficient" in out.reason.lower()

    def test_empty_results_rejected(self):
        with pytest.raises(ValueError):
            check_convergence([], tolerance=0.01)


# ─── 4. verify_simulation ─────────────────────────────────────────────────────


class TestVerifySimulation:
    def test_verify_simulation_runs_all_checks(self):
        adapter = _LinearStaticAdapter()
        plan = SimulationPlan.model_validate(_good_simulation_plan())
        verdict = verify_simulation(adapter, plan)
        assert isinstance(verdict, SimulationVerdict)
        # All four check categories must be reported.
        assert "patch_test" in verdict.checks
        assert "manufactured_solution" in verdict.checks
        assert "mesh_convergence" in verdict.checks
        assert "conservation" in verdict.checks

    def test_verify_simulation_aggregates_overall_state(self):
        adapter = _LinearStaticAdapter()
        plan = SimulationPlan.model_validate(_good_simulation_plan())
        verdict = verify_simulation(adapter, plan)
        assert verdict.state in {"pass", "fail", "inconclusive"}
        # Adapter ships at least one validation case, so state must not be fail.
        assert verdict.state != "fail"


# ─── 5. ResourceBounds enforcement ───────────────────────────────────────────


class TestResourceBounds:
    def test_within_bounds_accepted(self):
        bounds = _good_resource_bounds()
        assert bounds.allows(cpu_cores=4, memory_mb=8192, wall_time_s=1800, disk_gb=10.0) is True

    def test_exceeds_cpu_cores_rejected(self):
        bounds = _good_resource_bounds()
        assert bounds.allows(cpu_cores=16, memory_mb=8192, wall_time_s=1800, disk_gb=10.0) is False

    def test_exceeds_memory_rejected(self):
        bounds = _good_resource_bounds()
        assert bounds.allows(cpu_cores=4, memory_mb=32768, wall_time_s=1800, disk_gb=10.0) is False

    def test_resource_bounds_validates_nonnegative(self):
        with pytest.raises(ValidationError):
            ResourceBounds(cpu_cores=-1, memory_mb=0, wall_time_s=0, disk_gb=0.0)
