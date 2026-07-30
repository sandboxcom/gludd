"""Pydantic schemas for the chemistry expert (CHEM Phase A).

Implements the JSON contracts in
``docs/specs/FEATURE_CHEMISTRY_EXPERT.md`` §4.1-§4.3 with strict validation:

* Every numerical value carries a unit, an uncertainty, and a method id.
* Chemical entity structures never erase the submitted representation -
  stereochemistry, isotopes, salts, solvates, and mixtures remain distinct
  records rather than being silently canonicalized away (§4.1).
* Conditions require non-empty units; uncertainty is non-negative.
* Constraints (deadline, budget, data classification) are validated.
* Status / kind / representation enumerations reject unknown values, blocking
  the "unknown mutating field" class of bug called out by CHEM-AT-001.
"""

from __future__ import annotations

import uuid
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

SCHEMA_VERSION = "1.0"


def _new_uuid() -> str:
    return str(uuid.uuid4())


# ---------------------------------------------------------------------------
# Enumerations (bounded, reject unknown mutating fields)
# ---------------------------------------------------------------------------


class EntityKind(StrEnum):
    compound = "compound"
    mixture = "mixture"
    polymer = "polymer"
    material = "material"
    reaction = "reaction"
    biomolecule = "biomolecule"


class StereoStatus(StrEnum):
    specified = "specified"
    partial = "partial"
    unknown = "unknown"


class IsotopeStatus(StrEnum):
    specified = "specified"
    natural = "natural"
    unknown = "unknown"


class StructureRepresentation(StrEnum):
    smiles = "smiles"
    cxsmiles = "cxsmiles"
    inchi = "inchi"
    molfile = "molfile"
    cif = "cif"
    sequence = "sequence"
    composition = "composition"
    unknown = "unknown"


class TaskKind(StrEnum):
    identity = "identity"
    research = "research"
    property = "property"
    reaction = "reaction"
    protocol = "protocol"
    stoichiometry = "stoichiometry"
    hazard = "hazard"
    inventory = "inventory"
    compute = "compute"
    spectra = "spectra"
    analytical = "analytical"
    electrochemistry = "electrochemistry"
    process = "process"


class ResultStatus(StrEnum):
    succeeded = "succeeded"
    degraded = "degraded"
    refused = "refused"
    failed = "failed"
    awaiting_approval = "awaiting_approval"


class RiskTier(StrEnum):
    low = "low"
    moderate = "moderate"
    high = "high"
    prohibited = "prohibited"


class DataClassification(StrEnum):
    public = "public"
    internal = "internal"
    confidential = "confidential"
    restricted = "restricted"


class FractionBasis(StrEnum):
    mass = "mass"
    mole = "mole"
    volume = "volume"


class ValidationStatus(StrEnum):
    pass_ = "pass"
    fail = "fail"
    warning = "warning"


# ---------------------------------------------------------------------------
# 4.1 Chemical entity
# ---------------------------------------------------------------------------


class NameRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    value: str = Field(min_length=1)
    type: str = Field(default="preferred")
    source_id: str | None = None


class ChemicalStructure(BaseModel):
    """A single representation of a molecule/material.

    ``submitted`` preserves the exact string the caller provided. ``value`` may
    be canonicalized by a downstream tool, but the original is never erased
    (spec §4.1: "Canonicalization never erases the submitted representation").
    """

    model_config = ConfigDict(extra="forbid")

    representation: StructureRepresentation
    value: str = Field(min_length=1)
    submitted: str = Field(min_length=1)
    canonicalizer: str = Field(min_length=1)
    stereochemistry: StereoStatus
    isotopes: IsotopeStatus = IsotopeStatus.natural
    charge: int = 0

    @field_validator("submitted")
    @classmethod
    def _submitted_must_match_when_canonical_missing(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("submitted representation must not be empty")
        return v


class ComponentRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    entity_id: str = Field(min_length=1)
    fraction: float = Field(ge=0.0, le=1.0)
    basis: FractionBasis = FractionBasis.mole
    uncertainty: float = Field(default=0.0, ge=0.0)


class IdentifierRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scheme: str = Field(min_length=1)
    value: str = Field(min_length=1)
    source_id: str | None = None


class ValidationRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    check: str = Field(min_length=1)
    status: ValidationStatus
    note: str | None = None


class ChemicalEntity(BaseModel):
    """A chemical entity record (spec §4.1)."""

    model_config = ConfigDict(extra="forbid")

    schema_version: str = Field(default=SCHEMA_VERSION)
    entity_id: str = Field(default_factory=_new_uuid, min_length=1)
    kind: EntityKind
    names: list[NameRecord] = Field(default_factory=list)
    structure: ChemicalStructure
    components: list[ComponentRecord] = Field(default_factory=list)
    identifiers: list[IdentifierRecord] = Field(default_factory=list)
    validation: list[ValidationRecord] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# 4.2 Chemistry request
# ---------------------------------------------------------------------------


class ArtifactInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    uri: str = Field(min_length=1)
    sha256: str | None = None
    media_type: str | None = None


class ConditionRecord(BaseModel):
    """A measured/declared experimental condition.

    Units are mandatory per spec §4.3 ("The system rejects unitless inputs
    where physical meaning depends on units"). Uncertainty is non-negative.
    """

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    value: float
    unit: str = Field(min_length=1)
    uncertainty: float = Field(default=0.0, ge=0.0)
    condition_id: str | None = None


class ChemistryConstraints(BaseModel):
    model_config = ConfigDict(extra="forbid")

    deadline_s: int = Field(default=300, ge=0)
    budget_usd: float = Field(default=0.0, ge=0.0)
    data_classification: DataClassification = DataClassification.internal
    allowed_tools: list[str] = Field(default_factory=list)
    allowed_licenses: list[str] = Field(default_factory=list)


class ChemistryRequest(BaseModel):
    """A chemistry service request (spec §4.2)."""

    model_config = ConfigDict(extra="forbid")

    schema_version: str = Field(default=SCHEMA_VERSION)
    request_id: str = Field(min_length=1)
    tenant_id: str = Field(min_length=1)
    task: TaskKind
    entities: list[str] = Field(min_length=1)
    inputs: list[ArtifactInput] = Field(default_factory=list)
    conditions: list[ConditionRecord] = Field(default_factory=list)
    facility_profile_id: str | None = None
    constraints: ChemistryConstraints = Field(default_factory=ChemistryConstraints)
    approval_token: str | None = None


# ---------------------------------------------------------------------------
# 4.3 Chemistry result
# ---------------------------------------------------------------------------


class ValueRecord(BaseModel):
    """A measured/predicted numerical value with units + uncertainty + method.

    Every value carries ``unit``, ``uncertainty`` (>= 0), and ``method_id`` —
    spec §4.3 requires units, conditions, method, uncertainty, and provenance
    on every numerical result.
    """

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    value: float
    unit: str = Field(min_length=1)
    uncertainty: float = Field(default=0.0, ge=0.0)
    conditions: list[str] = Field(default_factory=list)
    method_id: str = Field(min_length=1)


class CitationRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_id: str = Field(min_length=1)
    locator: str = Field(min_length=1)
    claim_ids: list[str] = Field(default_factory=list)


class SafetyRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    risk_tier: RiskTier = RiskTier.low
    review_id: str | None = None
    approvals: list[str] = Field(default_factory=list)


class VerificationRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    check: str = Field(min_length=1)
    status: str  # pass | fail | not_run — left open intentionally per spec §4.3
    artifact_uri: str | None = None


class ErrorRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str = Field(min_length=1)
    retryable: bool = False
    message: str = Field(min_length=1)


class ChemistryResult(BaseModel):
    """A chemistry service result (spec §4.3)."""

    model_config = ConfigDict(extra="forbid")

    schema_version: str = Field(default=SCHEMA_VERSION)
    request_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    status: ResultStatus
    summary: str = ""
    values: list[ValueRecord] = Field(default_factory=list)
    artifacts: list[ArtifactInput] = Field(default_factory=list)
    citations: list[CitationRecord] = Field(default_factory=list)
    safety: SafetyRecord = Field(default_factory=SafetyRecord)
    verification: list[VerificationRecord] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    errors: list[ErrorRecord] = Field(default_factory=list)
    provenance: dict[str, Any] = Field(default_factory=dict)


__all__ = [
    "SCHEMA_VERSION",
    "ArtifactInput",
    "ChemicalEntity",
    "ChemicalStructure",
    "ChemistryConstraints",
    "ChemistryRequest",
    "ChemistryResult",
    "ComponentRecord",
    "ConditionRecord",
    "DataClassification",
    "EntityKind",
    "ErrorRecord",
    "FractionBasis",
    "IdentifierRecord",
    "IsotopeStatus",
    "NameRecord",
    "ResultStatus",
    "RiskTier",
    "SafetyRecord",
    "StereoStatus",
    "StructureRepresentation",
    "TaskKind",
    "ValidationRecord",
    "ValidationStatus",
    "ValueRecord",
    "VerificationRecord",
]
