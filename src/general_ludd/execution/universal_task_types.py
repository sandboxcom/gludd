"""Provider-neutral contracts shared by universal task executors and adapters."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable

from general_ludd.scheduling.scheduler import WorkItem


class TaskStatus(StrEnum):
    """Terminal state of a universal task attempt."""

    SUCCEEDED = "succeeded"
    REFUSED = "refused"
    FAILED = "failed"


@runtime_checkable
class ModelResponseProtocol(Protocol):
    """The normalized response surface supplied by ``ModelGateway``."""

    content: str
    cost_estimate: float


@runtime_checkable
class ModelGatewayProtocol(Protocol):
    """Structural subset of ``ModelGateway`` needed by the executor."""

    def call_model(
        self,
        profile_id: str,
        messages: list[dict[str, str]],
        **kwargs: Any,
    ) -> ModelResponseProtocol:
        """Invoke one selected model profile and return a normalized response."""
        ...


@runtime_checkable
class SchedulerProtocol(Protocol):
    """Structural subset of ``Scheduler`` used for work admission."""

    def plan(self, items: list[WorkItem]) -> list[list[str]]:
        """Group admitted work items into conflict-free execution batches."""
        ...


@runtime_checkable
class AcceleratorPlannerProtocol(Protocol):
    """Read-only accelerator discovery; execution never provisions hardware."""

    def discover_hardware(self) -> Sequence[object]:
        """Return the currently observed provider-neutral accelerator inventory."""
        ...


@runtime_checkable
class ToolRunnerProtocol(Protocol):
    """Injected bounded tool surface available to capability adapters."""

    def run(
        self,
        tool_name: str,
        payload: dict[str, object],
    ) -> dict[str, object]:
        """Run one allowlisted tool with structured input and output."""
        ...


@dataclass(frozen=True)
class UniversalTaskRequest:
    """Provider-neutral task input and execution constraints."""

    task_id: str
    capability: str
    instruction: str
    budget_usd: float
    data_classification: str = "public"
    allowed_tools: frozenset[str] = field(default_factory=frozenset)
    resources: frozenset[str] = field(default_factory=frozenset)
    metadata: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Reject incomplete identities and unusable budget constraints."""
        for name in ("task_id", "capability", "instruction", "data_classification"):
            if not str(getattr(self, name)).strip():
                raise ValueError(f"{name} must be non-empty")
        if not math.isfinite(self.budget_usd) or self.budget_usd < 0:
            raise ValueError("budget_usd must be finite and non-negative")


@dataclass(frozen=True)
class ExecutionTarget:
    """One model target plus the evidence required to route to it."""

    profile_id: str
    provider: str
    accelerator_sku: str
    capabilities: frozenset[str]
    allowed_data_classifications: frozenset[str]
    estimated_cost_usd: float
    healthy: bool
    health_evidence: str
    capability_evidence: str
    cost_evidence: str
    privacy_evidence: str
    offline: bool

    def __post_init__(self) -> None:
        """Require complete routing evidence and a finite cost claim."""
        for name in (
            "profile_id",
            "provider",
            "accelerator_sku",
            "health_evidence",
            "capability_evidence",
            "cost_evidence",
            "privacy_evidence",
        ):
            if not str(getattr(self, name)).strip():
                raise ValueError(f"{name} must be non-empty")
        if not self.capabilities:
            raise ValueError("capabilities must be non-empty")
        if not self.allowed_data_classifications:
            raise ValueError("allowed_data_classifications must be non-empty")
        if not math.isfinite(self.estimated_cost_usd) or self.estimated_cost_usd < 0:
            raise ValueError("estimated_cost_usd must be finite and non-negative")


@dataclass(frozen=True)
class TargetEvaluation:
    """Auditable eligibility decision for one execution target."""

    profile_id: str
    provider: str
    eligible: bool
    reasons: tuple[str, ...]
    evidence: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class RouteDecision:
    """Selected target and all evaluated alternatives."""

    selected_profile_id: str | None
    selected_provider: str | None
    evaluations: tuple[TargetEvaluation, ...]


@dataclass(frozen=True)
class AdapterDecision:
    """Preflight verdict returned by a capability adapter."""

    accepted: bool
    reasons: tuple[str, ...] = ()
    evidence: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class CandidateAssessment:
    """Safety, validation, provenance, and tool verdict for a candidate."""

    accepted: bool
    reasons: tuple[str, ...] = ()
    evidence: Mapping[str, object] = field(default_factory=dict)


@runtime_checkable
class TaskAdapterProtocol(Protocol):
    """Capability-owned translation and validation boundary."""

    capability: str

    def build_messages(
        self,
        request: UniversalTaskRequest,
        target: ExecutionTarget,
    ) -> list[dict[str, str]]:
        """Translate a universal request into target-ready model messages."""
        ...

    def preflight(
        self,
        request: UniversalTaskRequest,
        target: ExecutionTarget,
    ) -> AdapterDecision:
        """Decide whether the request and target satisfy capability policy."""
        ...

    def parse_candidate(self, content: str) -> object:
        """Parse normalized model content into a capability-owned candidate."""
        ...

    def assess_candidate(
        self,
        request: UniversalTaskRequest,
        candidate: object,
        tool_runner: ToolRunnerProtocol | None,
    ) -> CandidateAssessment:
        """Validate one candidate with the adapter's bounded tool surface."""
        ...


@dataclass(frozen=True)
class UniversalTaskResult:
    """Terminal executor result; success always carries a gated candidate."""

    task_id: str
    status: TaskStatus
    route: RouteDecision | None
    candidate: object | None = None
    reasons: tuple[str, ...] = ()
    evidence: Mapping[str, object] = field(default_factory=dict)


__all__ = [
    "AcceleratorPlannerProtocol",
    "AdapterDecision",
    "CandidateAssessment",
    "ExecutionTarget",
    "ModelGatewayProtocol",
    "ModelResponseProtocol",
    "RouteDecision",
    "SchedulerProtocol",
    "TargetEvaluation",
    "TaskAdapterProtocol",
    "TaskStatus",
    "ToolRunnerProtocol",
    "UniversalTaskRequest",
    "UniversalTaskResult",
]
