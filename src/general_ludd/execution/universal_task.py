"""Provider-neutral execution for capability adapters.

The core selects an evidenced target, admits the work through the shared
scheduler, invokes an injected model gateway, and accepts a result only after
the capability adapter returns a structured, gated assessment.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol

from general_ludd.scheduling.scheduler import WorkItem


class TaskStatus(StrEnum):
    """Terminal state of a universal task attempt."""

    SUCCEEDED = "succeeded"
    REFUSED = "refused"
    FAILED = "failed"


class ModelResponseProtocol(Protocol):
    """The normalized response surface supplied by ``ModelGateway``."""

    content: str
    cost_estimate: float


class ModelGatewayProtocol(Protocol):
    """Structural subset of ``ModelGateway`` needed by the executor."""

    def call_model(
        self,
        profile_id: str,
        messages: list[dict[str, str]],
        **kwargs: Any,
    ) -> ModelResponseProtocol: ...


class SchedulerProtocol(Protocol):
    """Structural subset of ``Scheduler`` used for work admission."""

    def plan(self, items: list[WorkItem]) -> list[list[str]]: ...


class AcceleratorPlannerProtocol(Protocol):
    """Read-only accelerator discovery; execution never provisions hardware."""

    def discover_hardware(self) -> Sequence[object]: ...


class ToolRunnerProtocol(Protocol):
    """Injected bounded tool surface available to capability adapters."""

    def run(
        self,
        tool_name: str,
        payload: dict[str, object],
    ) -> dict[str, object]:
        """Run one named tool with a structured payload."""
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


class TaskAdapterProtocol(Protocol):
    """Capability-owned translation and validation boundary."""

    capability: str

    def build_messages(
        self,
        request: UniversalTaskRequest,
        target: ExecutionTarget,
    ) -> list[dict[str, str]]:
        """Translate a provider-neutral request into model messages."""
        ...

    def preflight(
        self,
        request: UniversalTaskRequest,
        target: ExecutionTarget,
    ) -> AdapterDecision:
        """Decide whether a request may reach the selected target."""
        ...

    def parse_candidate(self, content: str) -> object:
        """Parse a model response into the adapter's structured candidate."""
        ...

    def assess_candidate(
        self,
        request: UniversalTaskRequest,
        candidate: object,
        tool_runner: ToolRunnerProtocol | None,
    ) -> CandidateAssessment:
        """Assess all required evidence before accepting a candidate."""
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


class UniversalTaskExecutor:
    """Execute any registered capability without provider-specific branching."""

    def __init__(
        self,
        *,
        gateway: ModelGatewayProtocol,
        scheduler: SchedulerProtocol,
        accelerator_planner: AcceleratorPlannerProtocol,
        target_source: Callable[[], Sequence[ExecutionTarget]],
        tool_runner: ToolRunnerProtocol | None = None,
    ) -> None:
        """Bind injected scheduling, routing, model, and tool dependencies."""
        self._gateway = gateway
        self._scheduler = scheduler
        self._accelerator_planner = accelerator_planner
        self._target_source = target_source
        self._tool_runner = tool_runner

    def route(self, request: UniversalTaskRequest) -> RouteDecision:
        """Select the cheapest eligible target from current evidence."""
        return self._route_from_targets(request, tuple(self._target_source()))

    def _route_from_targets(
        self,
        request: UniversalTaskRequest,
        targets: tuple[ExecutionTarget, ...],
    ) -> RouteDecision:
        """Evaluate one immutable target snapshot for an auditable decision."""
        discovered = {
            str(getattr(item, "sku", "")): item
            for item in self._accelerator_planner.discover_hardware()
        }
        evaluations: list[TargetEvaluation] = []
        eligible: list[ExecutionTarget] = []

        for target in targets:
            reasons: list[str] = []
            if request.capability not in target.capabilities:
                reasons.append("capability_not_supported")
            if request.data_classification not in target.allowed_data_classifications:
                reasons.append("privacy_not_allowed")
            if not target.healthy:
                reasons.append("unhealthy")
            if target.estimated_cost_usd > request.budget_usd:
                reasons.append("over_budget")
            hardware = discovered.get(target.accelerator_sku)
            accelerator_approved = hardware is not None and bool(
                getattr(hardware, "approved", False)
            )
            if not accelerator_approved:
                reasons.append("accelerator_unavailable")
            elif str(getattr(hardware, "provider", "")) != target.provider:
                reasons.append("accelerator_provider_mismatch")

            evaluation = TargetEvaluation(
                profile_id=target.profile_id,
                provider=target.provider,
                eligible=not reasons,
                reasons=tuple(reasons),
                evidence={
                    "health": target.health_evidence,
                    "healthy": target.healthy,
                    "capability": target.capability_evidence,
                    "privacy": target.privacy_evidence,
                    "cost": target.cost_evidence,
                    "estimated_cost_usd": target.estimated_cost_usd,
                    "accelerator_sku": target.accelerator_sku,
                    "accelerator_approved": accelerator_approved,
                },
            )
            evaluations.append(evaluation)
            if evaluation.eligible:
                eligible.append(target)

        if not eligible:
            return RouteDecision(None, None, tuple(evaluations))
        selected = min(
            eligible,
            key=lambda target: (target.estimated_cost_usd, target.profile_id),
        )
        return RouteDecision(
            selected.profile_id,
            selected.provider,
            tuple(evaluations),
        )

    def execute(
        self,
        request: UniversalTaskRequest,
        adapter: TaskAdapterProtocol,
    ) -> UniversalTaskResult:
        """Route, schedule, invoke, and gate one task fail-closed."""
        if adapter.capability != request.capability:
            return self._result(
                request,
                TaskStatus.REFUSED,
                None,
                reasons=("adapter_capability_mismatch",),
            )

        targets = tuple(self._target_source())
        route = self._route_from_targets(request, targets)
        if route.selected_profile_id is None:
            return self._result(
                request,
                TaskStatus.REFUSED,
                route,
                reasons=("no_eligible_target",),
            )

        target_by_profile = {target.profile_id: target for target in targets}
        target = target_by_profile[route.selected_profile_id]
        item = WorkItem(
            id=request.task_id,
            resources=request.resources | frozenset({f"accelerator:{target.accelerator_sku}"}),
        )
        batches = self._scheduler.plan([item])
        if not batches or request.task_id not in batches[0]:
            return self._result(
                request,
                TaskStatus.FAILED,
                route,
                reasons=("scheduler_rejected_task",),
            )

        preflight = adapter.preflight(request, target)
        evidence = dict(preflight.evidence)
        if not preflight.accepted:
            return self._result(
                request,
                TaskStatus.REFUSED,
                route,
                reasons=preflight.reasons,
                evidence=evidence,
            )

        try:
            response = self._gateway.call_model(
                target.profile_id,
                adapter.build_messages(request, target),
                estimated_cost=target.estimated_cost_usd,
                budget_remaining=request.budget_usd,
            )
        except Exception as exc:
            return self._result(
                request,
                TaskStatus.FAILED,
                route,
                reasons=(f"model_call_failed:{type(exc).__name__}",),
                evidence=evidence,
            )

        actual_cost = getattr(response, "cost_estimate", None)
        if not isinstance(actual_cost, (int, float)) or not math.isfinite(actual_cost):
            return self._result(
                request,
                TaskStatus.REFUSED,
                route,
                reasons=("invalid_actual_cost_evidence",),
                evidence=evidence,
            )
        if actual_cost > request.budget_usd:
            return self._result(
                request,
                TaskStatus.REFUSED,
                route,
                reasons=("actual_cost_exceeds_budget",),
                evidence=evidence,
            )

        try:
            candidate = adapter.parse_candidate(response.content)
        except (TypeError, ValueError):
            return self._result(
                request,
                TaskStatus.REFUSED,
                route,
                reasons=("structured_candidate_required",),
                evidence=evidence,
            )

        assessment = adapter.assess_candidate(
            request,
            candidate,
            self._tool_runner,
        )
        evidence.update(assessment.evidence)
        if not assessment.accepted:
            return self._result(
                request,
                TaskStatus.REFUSED,
                route,
                candidate=candidate,
                reasons=assessment.reasons,
                evidence=evidence,
            )
        return self._result(
            request,
            TaskStatus.SUCCEEDED,
            route,
            candidate=candidate,
            evidence=evidence,
        )

    @staticmethod
    def _result(
        request: UniversalTaskRequest,
        status: TaskStatus,
        route: RouteDecision | None,
        *,
        candidate: object | None = None,
        reasons: tuple[str, ...] = (),
        evidence: Mapping[str, object] | None = None,
    ) -> UniversalTaskResult:
        return UniversalTaskResult(
            task_id=request.task_id,
            status=status,
            route=route,
            candidate=candidate,
            reasons=reasons,
            evidence=dict(evidence or {}),
        )


__all__ = [
    "AdapterDecision",
    "CandidateAssessment",
    "ExecutionTarget",
    "RouteDecision",
    "TargetEvaluation",
    "TaskAdapterProtocol",
    "TaskStatus",
    "ToolRunnerProtocol",
    "UniversalTaskExecutor",
    "UniversalTaskRequest",
    "UniversalTaskResult",
]
