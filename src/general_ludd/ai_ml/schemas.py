"""Typed schemas for the AI/ML expert service.

Implements the JSON-compatible request/result shapes from
docs/specs/FEATURE_AI_ML_EXPERT.md §4.1-4.2 plus the evidence, tool-candidate,
and verification records used by the top-five capabilities (AIML-001 router,
AIML-002 research discovery, AIML-003 evidence store, AIML-007 reasoning,
AIML-018 tool discovery).

All mutating constructors validate contract-level invariants (AIML-AT-001):
invalid enums, missing digests, negative budgets, out-of-range scores, and
empty identifying fields raise ``ValueError`` rather than producing a silently
malformed record.
"""

from __future__ import annotations

import enum
import re
from dataclasses import dataclass, field
from typing import Any

_HEX64 = re.compile(r"^[0-9a-f]{64}$")


class ExpertTask(enum.StrEnum):
    """The task kind an ExpertRequest is asking for (spec §4.1 ``task``)."""

    QUESTION = "question"
    RESEARCH = "research"
    DATASET = "dataset"
    TRAIN = "train"
    DISTILL = "distill"
    SPEECH = "speech"
    VISION = "vision"
    IMAGE = "image"
    WORLD_MODEL = "world_model"
    SIMULATE = "simulate"
    EVALUATE = "evaluate"
    DEPLOY = "deploy"


class ResultStatus(enum.StrEnum):
    """Terminal status of an ExpertResult (spec §4.2 ``status``)."""

    SUCCEEDED = "succeeded"
    DEGRADED = "degraded"
    REFUSED = "refused"
    FAILED = "failed"
    AWAITING_APPROVAL = "awaiting_approval"


class DataClassification(enum.StrEnum):
    """Data sensitivity classification (spec §4.1 ``constraints``)."""

    PUBLIC = "public"
    INTERNAL = "internal"
    CONFIDENTIAL = "confidential"
    RESTRICTED = "restricted"


class VerificationStatus(enum.StrEnum):
    """Status of a single verification check (spec §4.2 ``verification``)."""

    PASS = "pass"
    FAIL = "fail"
    NOT_RUN = "not_run"


def _coerce_enum(value: Any, enum_cls: type[enum.Enum], field_name: str) -> Any:
    """Coerce a string or enum member to ``enum_cls``; raise ValueError on miss."""
    if isinstance(value, enum_cls):
        return value
    if isinstance(value, str):
        try:
            return enum_cls(value)
        except ValueError as exc:
            raise ValueError(f"invalid {field_name}: {value!r}") from exc
    raise ValueError(f"invalid {field_name}: {value!r}")


def _require_nonempty_str(value: str, field_name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")


def _require_sha256(value: str, field_name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty sha256 hex digest")
    if not _HEX64.match(value):
        raise ValueError(f"{field_name} must be a 64-char lowercase hex sha256 digest")


@dataclass(frozen=True)
class ArtifactInput:
    """An input artifact referenced by an ExpertRequest (spec §4.1 ``inputs``)."""

    uri: str
    media_type: str
    sha256: str

    def __post_init__(self) -> None:
        _require_nonempty_str(self.uri, "uri")
        _require_nonempty_str(self.media_type, "media_type")
        _require_sha256(self.sha256, "sha256")


@dataclass(frozen=True)
class ArtifactOutput:
    """An output artifact produced by an ExpertResult (spec §4.2 ``artifacts``)."""

    uri: str
    sha256: str
    media_type: str

    def __post_init__(self) -> None:
        _require_nonempty_str(self.uri, "uri")
        _require_nonempty_str(self.media_type, "media_type")
        _require_sha256(self.sha256, "sha256")


@dataclass(frozen=True)
class Constraints:
    """Execution constraints (spec §4.1 ``constraints``)."""

    deadline_s: int = 300
    budget_usd: float = 0.0
    max_gpu_hours: float = 0.0
    data_classification: DataClassification = DataClassification.PUBLIC
    offline: bool = False
    allowed_licenses: tuple[str, ...] = ()
    allowed_tools: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.deadline_s <= 0:
            raise ValueError(f"deadline_s must be > 0, got {self.deadline_s}")
        if self.budget_usd < 0:
            raise ValueError(f"budget_usd must be >= 0, got {self.budget_usd}")
        if self.max_gpu_hours < 0:
            raise ValueError(f"max_gpu_hours must be >= 0, got {self.max_gpu_hours}")
        object.__setattr__(
            self,
            "data_classification",
            _coerce_enum(self.data_classification, DataClassification, "data_classification"),
        )


@dataclass(frozen=True)
class ExpertRequest:
    """Expert request contract (spec §4.1)."""

    schema_version: str = "1.0"
    request_id: str = ""
    tenant_id: str = ""
    task: ExpertTask = ExpertTask.QUESTION
    query: str = ""
    inputs: tuple[ArtifactInput, ...] = ()
    constraints: Constraints = field(default_factory=Constraints)
    requested_outputs: tuple[str, ...] = ("answer",)
    approval_token: str | None = None

    def __post_init__(self) -> None:
        _require_nonempty_str(self.request_id, "request_id")
        _require_nonempty_str(self.tenant_id, "tenant_id")
        _require_nonempty_str(self.query, "query")
        object.__setattr__(self, "task", _coerce_enum(self.task, ExpertTask, "task"))
        if not isinstance(self.constraints, Constraints):
            raise ValueError("constraints must be a Constraints instance")


@dataclass(frozen=True)
class Citation:
    """A citation linking an answer claim to evidence (spec §4.2 ``citations``)."""

    source_id: str
    locator: str
    claim_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _require_nonempty_str(self.source_id, "source_id")
        _require_nonempty_str(self.locator, "locator")


@dataclass(frozen=True)
class Verification:
    """One independent verification check (spec §4.2 ``verification``)."""

    check: str
    status: VerificationStatus
    artifact_uri: str | None = None

    def __post_init__(self) -> None:
        _require_nonempty_str(self.check, "check")
        object.__setattr__(self, "status", _coerce_enum(self.status, VerificationStatus, "status"))


@dataclass(frozen=True)
class Uncertainty:
    """Uncertainty calibration for an answer (spec §4.2 ``uncertainty``)."""

    score: float
    method: str
    limitations: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not (0.0 <= self.score <= 1.0):
            raise ValueError(f"uncertainty.score must be in [0.0, 1.0], got {self.score}")
        _require_nonempty_str(self.method, "method")


@dataclass(frozen=True)
class CostRecord:
    """Cost accounting for a run (spec §4.2 ``cost``)."""

    usd: float = 0.0
    gpu_seconds: int = 0
    tokens: int = 0

    def __post_init__(self) -> None:
        if self.usd < 0:
            raise ValueError(f"cost.usd must be >= 0, got {self.usd}")
        if self.gpu_seconds < 0:
            raise ValueError(f"cost.gpu_seconds must be >= 0, got {self.gpu_seconds}")
        if self.tokens < 0:
            raise ValueError(f"cost.tokens must be >= 0, got {self.tokens}")


@dataclass(frozen=True)
class PolicyDecision:
    """Policy decision reference (spec §4.2 ``policy``)."""

    decision_id: str
    ruleset_sha256: str

    def __post_init__(self) -> None:
        _require_nonempty_str(self.decision_id, "decision_id")
        _require_sha256(self.ruleset_sha256, "ruleset_sha256")


@dataclass(frozen=True)
class ErrorRecord:
    """A stable, safe error record (spec §4.2 ``errors``)."""

    code: str
    retryable: bool
    message: str

    def __post_init__(self) -> None:
        _require_nonempty_str(self.code, "code")
        _require_nonempty_str(self.message, "message")


@dataclass(frozen=True)
class ExpertResult:
    """Expert result contract (spec §4.2)."""

    schema_version: str = "1.0"
    request_id: str = ""
    run_id: str = ""
    status: ResultStatus = ResultStatus.SUCCEEDED
    answer: str | None = None
    artifacts: tuple[ArtifactOutput, ...] = ()
    citations: tuple[Citation, ...] = ()
    verification: tuple[Verification, ...] = ()
    uncertainty: Uncertainty = field(default_factory=lambda: Uncertainty(score=0.0, method="unspecified"))
    cost: CostRecord = field(default_factory=CostRecord)
    policy: PolicyDecision | None = None
    errors: tuple[ErrorRecord, ...] = ()

    def __post_init__(self) -> None:
        _require_nonempty_str(self.request_id, "request_id")
        _require_nonempty_str(self.run_id, "run_id")
        object.__setattr__(self, "status", _coerce_enum(self.status, ResultStatus, "status"))


@dataclass(frozen=True)
class EvidenceArtifact:
    """An immutable, citation-addressable evidence record (spec §4.3, §5.2).

    Content is content-addressed by ``sha256``; duplicate content produces one
    artifact with multiple ``locators`` (AIML-AT-002). Tenant isolation is
    enforced by the store, not by this record.

    Phase A extensions (spec §4.3, §5.2): ``creator`` provenance, ``supersedes``
    relation for corrections, and ``retracted``/``retraction_reason``/
    ``retracted_at`` for tracking retractions. All extensions are optional
    with defaults so existing construction remains backward compatible.
    """

    source_id: str
    sha256: str
    media_type: str
    locators: tuple[str, ...]
    fetched_at: int
    license: str
    authority_score: float = 0.0
    tenant_id: str = "default"
    creator: str = ""
    supersedes: str | None = None
    retracted: bool = False
    retraction_reason: str = ""
    retracted_at: int | None = None

    def __post_init__(self) -> None:
        _require_nonempty_str(self.source_id, "source_id")
        _require_sha256(self.sha256, "sha256")
        _require_nonempty_str(self.media_type, "media_type")
        if not self.locators:
            raise ValueError("evidence must have at least one locator")
        if not (0.0 <= self.authority_score <= 1.0):
            raise ValueError(f"authority_score must be in [0.0, 1.0], got {self.authority_score}")
        if self.retracted and not self.retraction_reason.strip():
            raise ValueError("a retracted evidence record must carry a non-empty retraction_reason")
        if self.retracted and self.retracted_at is None:
            raise ValueError("a retracted evidence record must set retracted_at")
        if self.retracted_at is not None and self.retracted_at < 0:
            raise ValueError(f"retracted_at must be non-negative, got {self.retracted_at}")
        if self.supersedes is not None and not self.supersedes.strip():
            raise ValueError("supersedes, when set, must be a non-empty source_id")


@dataclass(frozen=True)
class ToolCandidate:
    """A candidate tool assessed during tool discovery (spec §9, AIML-018)."""

    capability_id: str
    name: str
    version: str
    license: str
    maintenance_score: float
    security_score: float
    task_fit_score: float
    has_exit_strategy: bool = False
    rejection_reason: str = ""

    def __post_init__(self) -> None:
        for fname in ("capability_id", "name", "version", "license"):
            _require_nonempty_str(getattr(self, fname), fname)
        for score_name in ("maintenance_score", "security_score", "task_fit_score"):
            value = getattr(self, score_name)
            if not (0.0 <= value <= 1.0):
                raise ValueError(f"{score_name} must be in [0.0, 1.0], got {value}")


@dataclass(frozen=True)
class ToolDecisionRecord:
    """Decision record emitted by tool discovery (spec §9, AIML-AT-018)."""

    need: str
    selected: tuple[ToolCandidate, ...]
    rejected_alternatives: tuple[ToolCandidate, ...]
    integration_spike_required: bool = True
    selection_basis: tuple[str, ...] = (
        "task_fit",
        "maintenance",
        "security",
        "license",
        "exit_strategy",
    )

    def __post_init__(self) -> None:
        _require_nonempty_str(self.need, "need")


@dataclass(frozen=True)
class RouterDecision:
    """The typed router verdict for an ExpertRequest (AIML-001)."""

    request_id: str
    matched_roles: tuple[str, ...]
    refusal_reason: str | None = None

    def __post_init__(self) -> None:
        _require_nonempty_str(self.request_id, "request_id")


__all__ = [
    "ArtifactInput",
    "ArtifactOutput",
    "Citation",
    "Constraints",
    "CostRecord",
    "DataClassification",
    "ErrorRecord",
    "EvidenceArtifact",
    "ExpertRequest",
    "ExpertResult",
    "ExpertTask",
    "PolicyDecision",
    "ResultStatus",
    "RouterDecision",
    "ToolCandidate",
    "ToolDecisionRecord",
    "Uncertainty",
    "Verification",
    "VerificationStatus",
]
