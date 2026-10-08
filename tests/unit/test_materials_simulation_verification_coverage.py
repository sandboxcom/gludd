"""Typed branch coverage for materials simulation verification."""

from __future__ import annotations

from types import SimpleNamespace
from typing import cast

import pytest

from general_ludd.materials.contracts import SimulationPlan
from general_ludd.materials.simulation import (
    SolverAdapter,
    check_convergence,
    verification,
)


def _plan() -> SimulationPlan:
    """Build a complete verification-ready simulation plan."""
    return SimulationPlan.model_validate(
        {
            "model_id": "22222222-2222-2222-2222-222222222222",
            "question": "Does bracket stress exceed yield at 200 MPa?",
            "solver_adapter": "fe_linear_static_v1",
            "geometry_digest": "sha256:geom",
            "material_models": [
                {
                    "region": "bracket",
                    "model": "elastic_isotropic",
                    "data_source": "asm2",
                    "calibration_range": [200, 400],
                }
            ],
            "loads_and_boundaries": [
                {"id": "lb1", "type": "pressure", "value": 200.0, "unit": "MPa", "basis": "design_load"}
            ],
            "mesh": {"element_family": "tet4", "target_size": 1.0, "convergence_plan": "h-refine x3"},
            "contacts_and_joints": [],
            "coupling": [],
            "verification": {
                "benchmarks": ["nafems_le1"],
                "conservation_checks": ["force_balance"],
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
            "outputs": [
                {"quantity": "von_mises_stress", "location": "fillet", "unit": "MPa", "acceptance": "< yield"}
            ],
        }
    )


def _adapter() -> SolverAdapter:
    """Return the only adapter field consumed by plan verification."""
    return cast(SolverAdapter, SimpleNamespace(capability_id="fe_linear_static_v1"))


def test_zero_reference_uses_absolute_error() -> None:
    """Avoid division by zero for zero-reference convergence studies."""
    results = [
        {"mesh_size": 2.0, "quantity": 0.2, "reference": 0.0},
        {"mesh_size": 1.0, "quantity": 0.1, "reference": 0.0},
    ]
    out = check_convergence(results, tolerance=0.2)
    assert out.errors == [0.2, 0.1]
    assert out.converged is True


def test_monotonic_error_with_large_relative_change_is_not_converged() -> None:
    """Reject monotonic sequences whose final change exceeds tolerance."""
    results = [
        {"mesh_size": 2.0, "quantity": 100.0, "reference": 0.0},
        {"mesh_size": 1.0, "quantity": 1.0, "reference": 0.0},
    ]
    out = check_convergence(results, tolerance=0.01)
    assert out.converged is False
    assert "exceeds tolerance" in out.reason


def test_missing_verification_metadata_fails_closed() -> None:
    """Fail every verification category when its evidence is absent."""
    plan = _plan()
    metadata = plan.verification.model_copy(
        update={"benchmarks": [], "convergence": "", "conservation_checks": []}
    )
    mesh = plan.mesh.model_copy(update={"convergence_plan": ""})
    incomplete = plan.model_copy(update={"verification": metadata, "mesh": mesh})

    verdict = verification.verify_simulation(_adapter(), incomplete)

    assert verdict.state == "fail"
    assert all(check["state"] == "fail" for check in verdict.checks.values())


def test_inconclusive_check_produces_inconclusive_verdict(monkeypatch: pytest.MonkeyPatch) -> None:
    """Preserve an inconclusive check when no category failed."""
    monkeypatch.setattr(
        verification,
        "_run_conservation",
        lambda _plan: {"state": "inconclusive", "reason": "solver evidence pending"},
    )

    verdict = verification.verify_simulation(_adapter(), _plan())

    assert verdict.state == "inconclusive"
    assert "inconclusive" in verdict.reason
