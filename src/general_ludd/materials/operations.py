"""Bounded operation adapter for the materials expert service.

The adapter contains no engineering equations. It validates transport input
and delegates each allowlisted operation to the existing typed materials
implementation.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict
from typing import Any

from ansible_collections.general_ludd.materials.plugins.module_utils.tolerance_model import (
    ToleranceModelError,
    evaluate_tolerance_model,
)
from pydantic import ValidationError

from .additive import AdditiveManufacturingAdvisor
from .contracts import SimulationPlan
from .core import (
    assess_strength,
    normalize_requirements,
    plan_metal_forming,
    plan_polymer_process,
    select_materials,
)
from .failure import FailureAnalyzer
from .joining import JoiningAdvisor
from .machining import MachiningAdvisor
from .process_planning import plan_inspection, plan_manufacturing
from .textiles import TextileAdvisor

MATERIALS_OPERATIONS = frozenset(
    {
        "requirements_capture",
        "material_select",
        "polymer_process_plan",
        "metal_forming_plan",
        "strength_assess",
        "joining_plan",
        "welding_plan",
        "machining_plan",
        "additive_plan",
        "textile_plan",
        "molding_plan",
        "multiphysics_model",
        "tolerance_model",
        "failure_analyze",
        "manufacturing_plan",
        "inspection_plan",
    }
)


class MaterialsRequestError(ValueError):
    """Signal invalid materials operation input at the service boundary."""


def _string(request: dict[str, Any], key: str, *, maximum: int = 256) -> str:
    value = request.get(key)
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise MaterialsRequestError(f"{key} must be a non-empty string of at most {maximum} characters")
    return value.strip()


def _mapping(request: dict[str, Any], key: str, *, required: bool = True) -> dict[str, Any]:
    value = request.get(key)
    if value is None and not required:
        return {}
    if not isinstance(value, dict):
        raise MaterialsRequestError(f"{key} must be an object")
    return value


def _list(request: dict[str, Any], key: str, *, maximum: int = 128) -> list[Any]:
    value = request.get(key)
    if not isinstance(value, list) or len(value) > maximum:
        raise MaterialsRequestError(f"{key} must be a list with at most {maximum} entries")
    return value


def _string_list(request: dict[str, Any], key: str, *, maximum: int = 128) -> list[str]:
    values = _list(request, key, maximum=maximum)
    if any(not isinstance(value, str) or not value.strip() for value in values):
        raise MaterialsRequestError(f"{key} entries must be non-empty strings")
    return [value.strip() for value in values]


def _number(value: object, key: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise MaterialsRequestError(f"{key} must be a finite number")
    result = float(value)
    if not math.isfinite(result):
        raise MaterialsRequestError(f"{key} must be a finite number")
    return result


def _stable_route_id(operation: str, request: dict[str, Any]) -> str:
    encoded = json.dumps(
        {"operation": operation, "request": request},
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode()
    return f"route-{hashlib.sha256(encoded).hexdigest()[:12]}"


def _route_card(operation: str, request: dict[str, Any]) -> Any:
    material_id = _string(request, "material_id")
    operations = _string_list(request, "operations", maximum=64)
    raw_quantity = request.get("quantity", 1)
    quantity = int(_number(raw_quantity, "quantity"))
    if quantity < 1 or quantity > 1_000_000:
        raise MaterialsRequestError("quantity must be between 1 and 1000000")
    refs_raw = request.get("requirement_refs", [])
    if not isinstance(refs_raw, list):
        raise MaterialsRequestError("requirement_refs must be a list")
    refs_request = {"requirement_refs": refs_raw}
    refs = _string_list(refs_request, "requirement_refs", maximum=128)
    route = plan_manufacturing(material_id, operations, quantity, refs)
    route.route_id = _stable_route_id(operation, request)
    return route


def _dispatch_tolerance(request: dict[str, Any]) -> dict[str, Any]:
    analysis = request.get("analysis")
    native_request = {
        key: value for key, value in request.items() if key != "analysis"
    }
    try:
        result: dict[str, Any] = evaluate_tolerance_model(analysis, native_request)
    except ToleranceModelError as exc:
        raise MaterialsRequestError(str(exc)) from exc
    return result


def _dispatch_textile(request: dict[str, Any]) -> dict[str, Any]:
    analysis = _string(request, "analysis")
    advisor = TextileAdvisor()
    if analysis == "fiber":
        return advisor.fiber_properties(_string(request, "fiber"))
    if analysis == "yarn":
        return advisor.yarn_linear_density(
            _string(request, "fiber"),
            _number(request.get("yarn_count_tex"), "yarn_count_tex"),
        )
    if analysis == "weave":
        return advisor.weave_properties(_string(request, "weave"))
    if analysis == "knit":
        return advisor.classify_knit(_string(request, "knit"))
    if analysis == "seam":
        return advisor.seam_efficiency(_string(request, "seam_type"))
    if analysis == "drape":
        return advisor.assess_drape(_string(request, "weave"))
    if analysis == "directional_strength":
        return advisor.directional_strength_ratio(_string(request, "weave"))
    raise MaterialsRequestError(f"unsupported textile analysis: {analysis}")


def dispatch_materials_operation(operation: str, request: dict[str, Any]) -> dict[str, Any]:
    """Validate and execute one allowlisted, non-mutating materials operation."""
    if operation not in MATERIALS_OPERATIONS:
        raise MaterialsRequestError(f"unsupported materials operation: {operation}")
    if len(request) > 128 or len(json.dumps(request, default=str)) > 262_144:
        raise MaterialsRequestError("materials request exceeds the bounded payload size")

    try:
        if operation == "requirements_capture":
            return normalize_requirements(request)
        if operation == "material_select":
            requirements = _mapping(request, "requirements")
            candidates = _string_list(request, "candidates") if "candidates" in request else None
            return select_materials(requirements, candidates)
        if operation in {"polymer_process_plan", "molding_plan"}:
            return plan_polymer_process(
                _string(request, "material_id"),
                _string(request, "process_family"),
            )
        if operation == "metal_forming_plan":
            return plan_metal_forming(
                _string(request, "material_id"),
                _string(request, "operation"),
            )
        if operation == "strength_assess":
            return assess_strength(
                _string(request, "material_id"),
                _mapping(request, "load_case"),
            )
        if operation == "additive_plan":
            return AdditiveManufacturingAdvisor().select_process(
                _string(request, "material_id"),
                _mapping(request, "requirements", required=False),
            )
        if operation == "failure_analyze":
            analyzer = FailureAnalyzer()
            hypotheses = analyzer.develop_hypotheses(
                _mapping(request, "load_case"),
                _mapping(request, "material"),
            )
            return {"hypotheses": hypotheses, "test_plan": analyzer.prescribe_tests(hypotheses)}
        if operation in {"joining_plan", "welding_plan"}:
            return JoiningAdvisor().assess_compatibility(
                _string(request, "material_a"),
                _string(request, "material_b"),
                _string(request, "process"),
            )
        if operation == "machining_plan":
            return MachiningAdvisor().plan(
                _string(request, "material_id"),
                _string(request, "process"),
            )
        if operation in {"manufacturing_plan", "inspection_plan"}:
            route = _route_card(operation, request)
            return asdict(route) if operation == "manufacturing_plan" else plan_inspection(route)
        if operation == "multiphysics_model":
            plan = SimulationPlan.model_validate(request)
            return {
                "state": "candidate",
                "human_approval_required": True,
                "plan": plan.model_dump(mode="json"),
            }
        if operation == "textile_plan":
            return _dispatch_textile(request)
        if operation == "tolerance_model":
            return _dispatch_tolerance(request)
    except (TypeError, ValueError, ValidationError) as exc:
        if isinstance(exc, MaterialsRequestError):
            raise
        raise MaterialsRequestError("invalid materials operation input") from exc
    raise MaterialsRequestError(f"unsupported materials operation: {operation}")


__all__ = [
    "MATERIALS_OPERATIONS",
    "MaterialsRequestError",
    "dispatch_materials_operation",
]
