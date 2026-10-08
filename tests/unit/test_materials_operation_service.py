"""Tests for the bounded materials operation service adapter."""

from __future__ import annotations

from uuid import UUID

import pytest

from general_ludd.materials.operations import (
    MaterialsRequestError,
    dispatch_materials_operation,
)


@pytest.mark.parametrize(
    ("operation", "payload", "expected_key"),
    [
        ("requirements_capture", {"failure_consequence": "noncritical"}, "schema_version"),
        ("material_select", {"requirements": {}, "candidates": ["abs"]}, "candidates"),
        ("polymer_process_plan", {"material_id": "abs", "process_family": "injection_molding"}, "process_window"),
        ("molding_plan", {"material_id": "abs", "process_family": "injection_molding"}, "process_window"),
        ("metal_forming_plan", {"material_id": "aa6061_t6", "operation": "bending"}, "springback"),
        (
            "strength_assess",
            {
                "material_id": "aa6061_t6",
                "load_case": {"type": "yield", "unit": "MPa", "magnitude": 100},
            },
            "margin",
        ),
        ("additive_plan", {"material_id": "abs", "requirements": {"detail": "high"}}, "process"),
        ("failure_analyze", {"load_case": {}, "material": {}}, "hypotheses"),
        ("joining_plan", {"material_a": "abs", "material_b": "abs", "process": "adhesive_bonding"}, "compatible"),
        ("welding_plan", {"material_a": "aa6061_t6", "material_b": "aa6061_t6", "process": "gtaw"}, "compatible"),
        ("machining_plan", {"material_id": "abs", "process": "milling"}, "datum_scheme"),
        ("manufacturing_plan", {"material_id": "abs", "operations": ["injection_molding"], "quantity": 2}, "steps"),
        ("inspection_plan", {"material_id": "abs", "operations": ["injection_molding"], "quantity": 2}, "measurements"),
        ("textile_plan", {"analysis": "weave", "weave": "plain"}, "drape_score"),
        ("tolerance_model", {"analysis": "worst_case", "dims": [[10.0, 0.1]], "unit": "mm"}, "band"),
        ("textile_plan", {"analysis": "fiber", "fiber": "cotton"}, "tenacity_cntex"),
        ("textile_plan", {"analysis": "yarn", "fiber": "cotton", "yarn_count_tex": 20.0}, "estimated_breaking_load_N"),
        ("textile_plan", {"analysis": "knit", "knit": "weft_knit"}, "elongation_pct"),
        ("textile_plan", {"analysis": "seam", "seam_type": "plain"}, "seam_efficiency"),
        ("textile_plan", {"analysis": "drape", "weave": "plain"}, "drape_rating"),
        ("textile_plan", {"analysis": "directional_strength", "weave": "plain"}, "balanced"),
        ("tolerance_model", {"analysis": "rss", "dims": [[10.0, 0.1]], "unit": "mm"}, "sigma_band"),
        (
            "tolerance_model",
            {
                "analysis": "thermal",
                "dims": [[10.0, 0.1]],
                "unit": "mm",
                "alpha_per_K": 0.00001,
                "delta_T_K": 20.0,
            },
            "delta",
        ),
        (
            "tolerance_model",
            {
                "analysis": "thermal_compensation",
                "dims": [[10.0, 0.1]],
                "unit": "mm",
                "alpha_per_K": 0.00001,
                "delta_T_K": 20.0,
            },
            "compensation",
        ),
        (
            "tolerance_model",
            {
                "analysis": "process_capability",
                "spec_lower": 9.8,
                "spec_upper": 10.2,
                "sigma": 0.02,
            },
            "Cpk",
        ),
        (
            "tolerance_model",
            {
                "analysis": "assembly",
                "hole_nominal": 10.0,
                "hole_tol": 0.1,
                "shaft_nominal": 9.8,
                "shaft_tol": 0.05,
                "unit": "mm",
            },
            "fit_class",
        ),
    ],
)
def test_dispatch_materials_operation_executes_existing_domain_logic(
    operation: str,
    payload: dict[str, object],
    expected_key: str,
) -> None:
    result = dispatch_materials_operation(operation, payload)

    assert expected_key in result


def test_multiphysics_operation_validates_the_typed_plan() -> None:
    result = dispatch_materials_operation(
        "multiphysics_model",
        {
            "model_id": str(UUID(int=1)),
            "question": "Will peak displacement stay below 1 mm?",
            "solver_adapter": "validated-linear-static",
            "geometry_digest": "sha256:fixture",
            "material_models": [{"region": "body", "model": "linear_elastic"}],
            "loads_and_boundaries": [{"id": "load", "type": "force", "value": 1.0, "unit": "N"}],
            "mesh": {"element_family": "tet10", "target_size": 1.0},
            "verification": {"benchmarks": ["patch_test"]},
            "validation": {"measurements": ["displacement"]},
            "uncertainty": {"variables": ["elastic_modulus"]},
            "outputs": [{"quantity": "displacement", "unit": "mm"}],
        },
    )

    assert result["state"] == "candidate"
    assert result["human_approval_required"] is True


@pytest.mark.parametrize("operation", ["machining_plan", "tolerance_model", "unknown"])
def test_invalid_or_incomplete_requests_fail_closed(operation: str) -> None:
    with pytest.raises(MaterialsRequestError):
        dispatch_materials_operation(operation, {})


@pytest.mark.parametrize(
    ("operation", "payload"),
    [
        ("material_select", {"requirements": {}, "candidates": "abs"}),
        ("manufacturing_plan", {"material_id": "abs", "operations": [], "quantity": 0}),
        ("textile_plan", {"analysis": "unsupported"}),
        ("tolerance_model", {"analysis": "worst_case", "dims": [[1.0]], "unit": "mm"}),
        ("tolerance_model", {"analysis": "process_capability", "spec_lower": True, "spec_upper": 2, "sigma": 1}),
    ],
)
def test_malformed_nested_input_fails_closed(
    operation: str,
    payload: dict[str, object],
) -> None:
    with pytest.raises(MaterialsRequestError):
        dispatch_materials_operation(operation, payload)


def test_request_count_bound_fails_closed() -> None:
    with pytest.raises(MaterialsRequestError, match="bounded payload"):
        dispatch_materials_operation(
            "requirements_capture",
            {f"field_{index}": index for index in range(129)},
        )
