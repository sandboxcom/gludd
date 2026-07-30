"""Pydantic contracts for the materials engineering collection (spec MATE-001 §5).

Implements the five data contracts required by MATE-P1:

  - :class:`DesignRequirements`   (§5.1) — normalized loads/env/life/manufacturing
  - :class:`MaterialCandidate`    (§5.2) — a screened material with margins/hazards
  - :class:`ProcessPlan`          (§5.3) — reviewable manufacturing route card
  - :class:`SimulationPlan`       (§5.4) — falsifiable multiphysics question
  - :class:`EngineeringVerdict`   (§5.5) — state-machine output of the decision

Invariants enforced here (spec §7/§8):

  - MATE-DEC-004: every numeric property carries ``unit``, ``basis``, ``method``,
    and non-negative ``uncertainty``.
  - MATE-SAFE-003: no fabricated precision — missing unit/basis is a validation
    error, not a silent default.
  - MATE-SAFE-001: ``failure_consequence == "safety_critical"`` auto-sets
    ``requires_human_review`` on DesignRequirements.
  - Every contract carries ``schema_version`` for forward-compatible JSON.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from general_ludd.materials.core import SCHEMA_VERSION
from general_ludd.materials.units import dim_of

# ─── enums (string literals for JSON clarity + forward-compat) ────────────────

FAILURE_CONSEQUENCES: frozenset[str] = frozenset({"noncritical", "significant", "safety_critical", "unknown"})

PROCESS_FAMILIES: frozenset[str] = frozenset(
    {
        "requirements_capture",
        "material_select",
        "polymer_process_plan",
        "metal_forming_plan",
        "joining_plan",
        "welding_plan",
        "machining_plan",
        "additive_plan",
        "textile_plan",
        "molding_plan",
        "strength_assess",
        "multiphysics_model",
        "tolerance_model",
        "failure_analyze",
        "manufacturing_plan",
        "inspection_plan",
    }
)

VERDICT_STATES: frozenset[str] = frozenset({"infeasible", "insufficient_data", "candidate", "validated_for_scope"})


class _ContractBase(BaseModel):
    """Common config: forbid unknown keys, validate on assignment."""

    model_config = ConfigDict(
        extra="forbid",
        validate_assignment=True,
        str_strip_whitespace=True,
    )


# ─── DesignRequirements (§5.1) ────────────────────────────────────────────────


class GeometryRef(_ContractBase):
    uri: str = Field(min_length=1)
    digest: str = Field(min_length=1)
    coordinate_system: str | None = None


class LoadCase(_ContractBase):
    id: str = Field(min_length=1)
    type: str = Field(min_length=1)
    magnitude: float
    unit: str = Field(min_length=1)
    direction: str | None = None
    spectrum: dict[str, Any] | None = None
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)


class EnvironmentFactor(_ContractBase):
    factor: str = Field(min_length=1)
    range: list[float] | None = None
    unit: str | None = None
    duration: str | None = None
    cycle: str | None = None


class DesignLife(_ContractBase):
    value: float
    unit: str = Field(min_length=1)
    reliability_target: float | None = Field(default=None, ge=0.0, le=1.0)


class ManufacturingConstraints(_ContractBase):
    quantity: int = Field(ge=0)
    rate: float | None = Field(default=None, ge=0.0)
    processes_allowed: list[str] = Field(default_factory=list)
    processes_forbidden: list[str] = Field(default_factory=list)


class InterfaceSpec(_ContractBase):
    material: str | None = None
    finish: str | None = None
    contact: str | None = None
    movement: str | None = None


class ToleranceSpec(_ContractBase):
    feature: str = Field(min_length=1)
    value: float
    unit: str = Field(min_length=1)
    statistical_basis: str | None = None


class InspectionSpec(_ContractBase):
    access: str
    methods_allowed: list[str] = Field(default_factory=list)
    sampling: str | None = None


class CostSustainability(_ContractBase):
    limits: dict[str, Any] = Field(default_factory=dict)
    repair: bool = False
    recycled_content: float = Field(default=0.0, ge=0.0, le=1.0)
    end_of_life: str | None = None


class Assumption(_ContractBase):
    id: str = Field(min_length=1)
    statement: str = Field(min_length=1)
    owner: str | None = None
    validation: str | None = None


class DesignRequirements(_ContractBase):
    schema_version: str = Field(default=SCHEMA_VERSION)
    geometry_refs: list[GeometryRef] | str = Field(default_factory=list)
    load_cases: list[LoadCase] | str = Field(default_factory=list)
    environment: list[EnvironmentFactor] | str = Field(default_factory=list)
    design_life: DesignLife | str = "unknown"
    failure_consequence: str = "unknown"
    manufacturing: ManufacturingConstraints | str = "unknown"
    interfaces: list[InterfaceSpec] | str = Field(default_factory=list)
    tolerances: list[ToleranceSpec] | str = Field(default_factory=list)
    inspection: InspectionSpec | str = "unknown"
    cost_sustainability: CostSustainability | str = "unknown"
    assumptions: list[Assumption] = Field(default_factory=list)
    requires_human_review: bool = False

    @field_validator("failure_consequence")
    @classmethod
    def _check_failure_consequence(cls, v: str) -> str:
        if v not in FAILURE_CONSEQUENCES:
            raise ValueError(f"failure_consequence must be one of {sorted(FAILURE_CONSEQUENCES)}, got {v!r}")
        return v

    @model_validator(mode="before")
    @classmethod
    def _propagate_human_review(cls, values: dict[str, Any]) -> dict[str, Any]:
        if isinstance(values, dict) and values.get("failure_consequence") == "safety_critical":
            values["requires_human_review"] = True
        return values


# ─── MaterialCandidate (§5.2) ─────────────────────────────────────────────────


class MaterialCondition(_ContractBase):
    product_form: str | None = None
    direction: str | None = None
    temper_or_cure: str | None = None
    moisture: str | None = None
    temperature: dict[str, Any] | None = None


class MaterialProperty(_ContractBase):
    name: str = Field(min_length=1)
    value_or_range: float | list[float]
    unit: str = Field(min_length=1)
    basis: str = Field(min_length=1)
    method: str = Field(min_length=1)
    uncertainty: float = Field(ge=0.0)
    state: str = "ok"

    @field_validator("unit")
    @classmethod
    def _unit_must_be_known(cls, v: str) -> str:
        try:
            dim_of(v)
        except ValueError as exc:
            raise ValueError(f"unknown unit {v!r}: {exc}") from exc
        return v


class MaterialSource(_ContractBase):
    uri: str | None = None
    publisher: str | None = None
    revision: str | None = None
    retrieved_at: str | None = None
    digest: str | None = None
    license: str | None = None


class RequirementMargin(_ContractBase):
    requirement_id: str = Field(min_length=1)
    margin: float
    state: str = Field(min_length=1)
    calculation_id: str | None = None


class ManufacturingCompatibility(_ContractBase):
    process: str = Field(min_length=1)
    state: str = Field(min_length=1)
    reason: str | None = None


class JoiningCompatibility(_ContractBase):
    process: str = Field(min_length=1)
    pairing: str | None = None
    state: str = Field(min_length=1)
    reason: str | None = None


class Hazard(_ContractBase):
    id: str = Field(min_length=1)
    severity: str = Field(min_length=1)
    controls: list[str] = Field(default_factory=list)
    authoritative_source: str | None = None


class UnknownField(_ContractBase):
    field: str = Field(min_length=1)
    impact: str = Field(min_length=1)
    required_test: str = Field(min_length=1)


class MaterialCandidate(_ContractBase):
    schema_version: str = Field(default=SCHEMA_VERSION)
    material_id: str = Field(min_length=1)
    condition: MaterialCondition
    properties: list[MaterialProperty]
    source: MaterialSource
    requirement_margins: list[RequirementMargin] = Field(default_factory=list)
    manufacturing_compatibility: list[ManufacturingCompatibility] = Field(default_factory=list)
    joining_compatibility: list[JoiningCompatibility] = Field(default_factory=list)
    hazards: list[Hazard] = Field(default_factory=list)
    confidence: int = Field(ge=0, le=100)
    unknowns: list[UnknownField] = Field(default_factory=list)


# ─── ProcessPlan (§5.3) ───────────────────────────────────────────────────────


class MaterialInput(_ContractBase):
    material_id: str = Field(min_length=1)
    lot_required: bool = False
    condition: dict[str, Any] = Field(default_factory=dict)


class ProcessStep(_ContractBase):
    id: str = Field(min_length=1)
    operation: str = Field(min_length=1)
    inputs: list[Any] = Field(default_factory=list)
    parameter_window: dict[str, Any] = Field(default_factory=dict)
    hold_point: bool = False
    outputs: list[Any] = Field(default_factory=list)


class Tooling(_ContractBase):
    id: str = Field(min_length=1)
    material: str | None = None
    geometry_ref: str | None = None
    life_assumption: str | None = None


class ControlLoop(_ContractBase):
    parameter: str = Field(min_length=1)
    sensor: str | None = None
    bounds: dict[str, Any] | None = None
    sampling: str | None = None
    reaction_plan: str | None = None


class ProcessInspection(_ContractBase):
    stage: str = Field(min_length=1)
    method: str = Field(min_length=1)
    acceptance: str | None = None
    calibration: str | None = None


class Qualification(_ContractBase):
    coupon_or_trial: str = Field(min_length=1)
    standard_or_internal_method: str | None = None
    approver: str | None = None


class ProcessHazard(_ContractBase):
    hazard: str = Field(min_length=1)
    control: str | None = None
    residual_risk: str | None = None
    approval: str | None = None


class Provenance(_ContractBase):
    source: str = Field(min_length=1)
    revision: str | None = None
    digest: str | None = None


class ProcessPlan(_ContractBase):
    schema_version: str = Field(default=SCHEMA_VERSION)
    plan_id: UUID
    process_family: str
    equipment_class: str
    material_inputs: list[MaterialInput]
    steps: list[ProcessStep]
    tooling: list[Tooling] = Field(default_factory=list)
    controls: list[ControlLoop] = Field(default_factory=list)
    inspection: list[ProcessInspection] = Field(default_factory=list)
    qualification: list[Qualification] = Field(default_factory=list)
    hazards: list[ProcessHazard] = Field(default_factory=list)
    provenance: list[Provenance] = Field(default_factory=list)

    @field_validator("process_family")
    @classmethod
    def _check_process_family(cls, v: str) -> str:
        if v not in PROCESS_FAMILIES:
            raise ValueError(f"process_family must be one of {sorted(PROCESS_FAMILIES)}, got {v!r}")
        return v


# ─── SimulationPlan (§5.4) ────────────────────────────────────────────────────


class MaterialModel(_ContractBase):
    region: str = Field(min_length=1)
    model: str = Field(min_length=1)
    data_source: str | None = None
    calibration_range: list[float] | None = None


class LoadOrBoundary(_ContractBase):
    id: str = Field(min_length=1)
    type: str = Field(min_length=1)
    value: float
    unit: str = Field(min_length=1)
    basis: str | None = None


class MeshSpec(_ContractBase):
    element_family: str = Field(min_length=1)
    target_size: float = Field(gt=0.0)
    convergence_plan: str | None = None


class ContactOrJoint(_ContractBase):
    regions: list[str] = Field(default_factory=list)
    model: str = Field(min_length=1)
    parameters: dict[str, Any] = Field(default_factory=dict)
    evidence: str | None = None


class Coupling(_ContractBase):
    physics: str = Field(min_length=1)
    direction: str | None = None
    time_scale: str | None = None


class VerificationSpec(_ContractBase):
    benchmarks: list[str] = Field(default_factory=list)
    conservation_checks: list[str] = Field(default_factory=list)
    convergence: str | None = None


class ValidationSpec(_ContractBase):
    experiment: str | None = None
    measurements: list[str] = Field(default_factory=list)
    acceptance: dict[str, Any] = Field(default_factory=dict)


class UncertaintySpec(_ContractBase):
    variables: list[str] = Field(default_factory=list)
    distributions_or_bounds: str | None = None
    propagation_method: str | None = None


class SimulationOutput(_ContractBase):
    quantity: str = Field(min_length=1)
    location: str | None = None
    unit: str = Field(min_length=1)
    acceptance: str | None = None


class SimulationPlan(_ContractBase):
    schema_version: str = Field(default=SCHEMA_VERSION)
    model_id: UUID
    question: str = Field(min_length=1)
    solver_adapter: str = Field(min_length=1)
    geometry_digest: str = Field(min_length=1)
    material_models: list[MaterialModel]
    loads_and_boundaries: list[LoadOrBoundary]
    mesh: MeshSpec
    contacts_and_joints: list[ContactOrJoint] = Field(default_factory=list)
    coupling: list[Coupling] = Field(default_factory=list)
    verification: VerificationSpec
    validation: ValidationSpec
    uncertainty: UncertaintySpec
    outputs: list[SimulationOutput]


# ─── EngineeringVerdict (§5.5) ────────────────────────────────────────────────


class GoverningCase(_ContractBase):
    case_id: str = Field(min_length=1)
    failure_mode: str = Field(min_length=1)
    margin: float
    uncertainty: float


class RequiredTest(_ContractBase):
    id: str = Field(min_length=1)
    purpose: str = Field(min_length=1)
    specimen: str | None = None
    method: str | None = None
    acceptance: str | None = None


class RequiredHumanReview(_ContractBase):
    discipline: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    state: str = Field(min_length=1)


class EngineeringVerdict(_ContractBase):
    schema_version: str = Field(default=SCHEMA_VERSION)
    request_id: UUID
    state: str
    candidate_ids: list[str] = Field(default_factory=list)
    governing_cases: list[GoverningCase] = Field(default_factory=list)
    manufacturing_route_id: UUID | None = None
    inspection_plan_id: UUID | None = None
    required_tests: list[RequiredTest] = Field(default_factory=list)
    required_human_reviews: list[RequiredHumanReview] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    evidence_bundle_uri: str

    @field_validator("state")
    @classmethod
    def _check_state(cls, v: str) -> str:
        if v not in VERDICT_STATES:
            raise ValueError(f"state must be one of {sorted(VERDICT_STATES)}, got {v!r}")
        return v


# fix the missing import for model_validator used above
# (no longer needed — model_validator imported at top)


__all__ = [
    "FAILURE_CONSEQUENCES",
    "PROCESS_FAMILIES",
    "SCHEMA_VERSION",
    "VERDICT_STATES",
    "Assumption",
    "ContactOrJoint",
    "CostSustainability",
    "Coupling",
    "DesignLife",
    "DesignRequirements",
    "EngineeringVerdict",
    "EnvironmentFactor",
    "GeometryRef",
    "GoverningCase",
    "Hazard",
    "InspectionSpec",
    "InterfaceSpec",
    "JoiningCompatibility",
    "LoadCase",
    "LoadOrBoundary",
    "ManufacturingCompatibility",
    "ManufacturingConstraints",
    "MaterialCandidate",
    "MaterialCondition",
    "MaterialInput",
    "MaterialModel",
    "MaterialProperty",
    "MaterialSource",
    "MeshSpec",
    "ProcessHazard",
    "ProcessInspection",
    "ProcessPlan",
    "ProcessStep",
    "Provenance",
    "Qualification",
    "RequiredHumanReview",
    "RequiredTest",
    "RequirementMargin",
    "SimulationOutput",
    "SimulationPlan",
    "ToleranceSpec",
    "Tooling",
    "UncertaintySpec",
    "UnknownField",
    "ValidationSpec",
    "VerificationSpec",
]
