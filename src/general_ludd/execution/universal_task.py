"""Provider-neutral execution for capability adapters.

The core selects an evidenced target, admits the work through the shared
scheduler, invokes an injected model gateway, and accepts a result only after
the capability adapter returns a structured, gated assessment.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping, Sequence

from general_ludd.execution.universal_task_types import (
    AcceleratorPlannerProtocol,
    AdapterDecision,
    CandidateAssessment,
    ExecutionTarget,
    ModelGatewayProtocol,
    ModelResponseProtocol,
    ModelServicePlannerProtocol,
    ModelServicePlanProtocol,
    RouteDecision,
    SchedulerProtocol,
    TargetEvaluation,
    TaskAdapterProtocol,
    TaskStatus,
    ToolRunnerProtocol,
    UniversalTaskRequest,
    UniversalTaskResult,
)
from general_ludd.scheduling.scheduler import WorkItem


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
        model_service_planner: ModelServicePlannerProtocol | None = None,
    ) -> None:
        """Bind injected scheduling, routing, model, and tool dependencies."""
        self._gateway = gateway
        self._scheduler = scheduler
        self._accelerator_planner = accelerator_planner
        self._target_source = target_source
        self._tool_runner = tool_runner
        self._model_service_planner = model_service_planner

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
        service_plan: ModelServicePlanProtocol | None = None
        service_payload: dict[str, object] | None = None
        if request.model_workload is not None:
            if self._model_service_planner is None:
                return self._result(
                    request,
                    TaskStatus.REFUSED,
                    route,
                    reasons=("model_service_planner_unavailable",),
                )
            try:
                service_plan = self._model_service_planner.plan(request, target)
            except ValueError as exc:
                return self._result(
                    request,
                    TaskStatus.REFUSED,
                    route,
                    reasons=self._model_service_refusal_reasons(exc),
                )
            except Exception as exc:
                return self._result(
                    request,
                    TaskStatus.FAILED,
                    route,
                    reasons=(f"model_service_planner_failed:{type(exc).__name__}",),
                )
            if not self._valid_model_service_plan(service_plan):
                return self._result(
                    request,
                    TaskStatus.REFUSED,
                    route,
                    reasons=("invalid_model_service_plan",),
                )
            try:
                service_payload = dict(service_plan.to_dict())
            except (TypeError, ValueError):
                return self._result(
                    request,
                    TaskStatus.REFUSED,
                    route,
                    reasons=("invalid_model_service_plan",),
                )
        if not self._scheduled(request, target, service_plan):
            return self._result(
                request,
                TaskStatus.FAILED,
                route,
                reasons=("scheduler_rejected_task",),
            )

        preflight = adapter.preflight(request, target)
        evidence: dict[str, object] = {}
        if service_payload is not None:
            evidence["model_service"] = service_payload
        evidence.update(preflight.evidence)
        if not preflight.accepted:
            return self._result(
                request,
                TaskStatus.REFUSED,
                route,
                reasons=preflight.reasons,
                evidence=evidence,
            )
        return self._invoke_and_assess(
            request,
            adapter,
            target,
            route,
            evidence,
            service_payload,
        )

    def _scheduled(
        self,
        request: UniversalTaskRequest,
        target: ExecutionTarget,
        service_plan: ModelServicePlanProtocol | None,
    ) -> bool:
        """Admit one target-bound task through the shared scheduler."""
        resources = request.resources | frozenset(
            {f"accelerator:{target.accelerator_sku}"}
        )
        if service_plan is not None:
            resources |= frozenset(
                {
                    f"accelerator-resource:{service_plan.resource_key}",
                    f"model-runner:{service_plan.runner_id}",
                }
            )
        item = WorkItem(
            id=request.task_id,
            resources=resources,
        )
        batches = self._scheduler.plan([item])
        return bool(batches and request.task_id in batches[0])

    def _invoke_and_assess(
        self,
        request: UniversalTaskRequest,
        adapter: TaskAdapterProtocol,
        target: ExecutionTarget,
        route: RouteDecision,
        evidence: dict[str, object],
        service_payload: dict[str, object] | None,
    ) -> UniversalTaskResult:
        """Invoke the selected model and accept only a validated candidate."""
        try:
            call_options: dict[str, object] = {
                "estimated_cost": target.estimated_cost_usd,
                "budget_remaining": request.budget_usd,
            }
            if service_payload is not None:
                call_options["model_service_plan"] = service_payload
            response = self._gateway.call_model(
                target.profile_id,
                adapter.build_messages(request, target),
                **call_options,
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
    def _valid_model_service_plan(plan: object) -> bool:
        """Validate structural desired state before scheduling or invocation."""
        if not isinstance(plan, ModelServicePlanProtocol):
            return False
        if not plan.resource_key or not plan.runner_id:
            return False
        for value in (plan.replica_count, plan.devices_per_replica):
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                return False
        return True

    @staticmethod
    def _model_service_refusal_reasons(error: ValueError) -> tuple[str, ...]:
        """Normalize typed planner reasons without exposing exception content."""
        raw_reasons = getattr(error, "reason_codes", ())
        if not isinstance(raw_reasons, tuple):
            raw_reasons = ()
        safe = tuple(
            f"model_service:{reason}"
            for reason in raw_reasons
            if isinstance(reason, str) and reason and len(reason) <= 128
        )
        return safe or ("model_service_plan_refused",)

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
    "ModelResponseProtocol",
    "ModelServicePlanProtocol",
    "ModelServicePlannerProtocol",
    "RouteDecision",
    "TargetEvaluation",
    "TaskAdapterProtocol",
    "TaskStatus",
    "ToolRunnerProtocol",
    "UniversalTaskExecutor",
    "UniversalTaskRequest",
    "UniversalTaskResult",
]
