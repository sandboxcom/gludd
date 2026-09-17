"""Universal executor and polymer adapter acceptance tests (S83.168)."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from general_ludd.ai_ml.accelerators import (
    AcceleratorKind,
    AcceleratorPlanner,
    HardwareDescriptor,
)
from general_ludd.ai_ml.policy import PolicyEngine
from general_ludd.chemistry.polymer_design import PolymerDesignAdapter
from general_ludd.execution.universal_task import (
    ExecutionTarget,
    TaskStatus,
    UniversalTaskExecutor,
    UniversalTaskRequest,
)
from general_ludd.scheduling.scheduler import Scheduler, WorkItem

CAPABILITY = "polymer_design"


class _Gateway:
    def __init__(self, content: str, *, cost: float = 0.2) -> None:
        self.content = content
        self.cost = cost
        self.calls: list[dict[str, Any]] = []

    def call_model(
        self,
        profile_id: str,
        messages: list[dict[str, str]],
        **kwargs: Any,
    ) -> SimpleNamespace:
        self.calls.append(
            {"profile_id": profile_id, "messages": messages, "kwargs": kwargs}
        )
        return SimpleNamespace(content=self.content, cost_estimate=self.cost)


class _ToolRunner:
    def __init__(self, passed: bool = True) -> None:
        self.passed = passed
        self.calls: list[tuple[str, dict[str, object]]] = []

    def run(
        self,
        tool_name: str,
        payload: dict[str, object],
    ) -> dict[str, object]:
        self.calls.append((tool_name, payload))
        return {"passed": self.passed, "tool": tool_name}


def _candidate() -> dict[str, object]:
    return {
        "candidate_id": "poly-glucose-001",
        "monomers": ["glucose"],
        "repeat_unit": "glucose-repeat-unit",
        "properties": [
            {
                "name": "glass_transition_temperature",
                "value": 118.0,
                "unit": "degC",
                "uncertainty": 4.0,
                "method_id": "bounded-qspr-v1",
            }
        ],
        "synthesis_scale": "lab",
        "facility_controls": [],
        "validation": {
            "checks": ["unit_consistency", "convergence"],
            "converged": True,
            "iterations": 12,
        },
        "provenance": {
            "source": {
                "locator": "doi:10.0000/polymer-example",
                "citation": "Bounded polymer example",
                "accessed_at": "2026-09-16",
            },
            "method": "bounded-qspr-v1",
            "conditions": {"temperature": {"value": 25.0, "unit": "degC"}},
            "code": {
                "repository": "gludd",
                "commit": "0123456789abcdef",
                "module": "general_ludd.chemistry.polymer_design",
            },
            "raw_artifact": {
                "uri": "artifact://polymer/example-001",
                "digest": "a" * 64,
            },
        },
    }


def _planner() -> AcceleratorPlanner:
    local = HardwareDescriptor(
        kind=AcceleratorKind.GPU,
        name="Apple local GPU",
        sku="local-mps",
        region="local",
        provider="local",
        approved=True,
    )
    azure = HardwareDescriptor(
        kind=AcceleratorKind.CLOUD,
        name="Azure A100",
        sku="Standard_NC24ads_A100_v4",
        region="eastus",
        provider="azure",
        approved=True,
    )
    return AcceleratorPlanner(
        approved_cloud_skus=frozenset({azure.sku}),
        local_hardware=(local,),
        cloud_catalog=(azure,),
    )


def _local_target(*, healthy: bool = True, cost: float = 0.2) -> ExecutionTarget:
    return ExecutionTarget(
        profile_id="local-polymer",
        provider="local",
        accelerator_sku="local-mps",
        capabilities=frozenset({CAPABILITY}),
        allowed_data_classifications=frozenset(
            {"public", "internal", "confidential", "restricted"}
        ),
        estimated_cost_usd=cost,
        healthy=healthy,
        health_evidence="local model heartbeat",
        capability_evidence="polymer schema benchmark",
        cost_evidence="local measured token cost",
        privacy_evidence="no network egress",
        offline=True,
    )


def _azure_target(*, healthy: bool = True, cost: float = 0.1) -> ExecutionTarget:
    return ExecutionTarget(
        profile_id="azure-polymer",
        provider="azure",
        accelerator_sku="Standard_NC24ads_A100_v4",
        capabilities=frozenset({CAPABILITY}),
        allowed_data_classifications=frozenset({"public", "internal"}),
        estimated_cost_usd=cost,
        healthy=healthy,
        health_evidence="deployment health probe",
        capability_evidence="polymer schema benchmark",
        cost_evidence="catalog price snapshot",
        privacy_evidence="approved public/internal egress policy",
        offline=False,
    )


def _request(
    *,
    classification: str = "public",
    budget: float = 1.0,
    allowed_tools: frozenset[str] = frozenset(),
) -> UniversalTaskRequest:
    return UniversalTaskRequest(
        task_id="polymer-task-1",
        capability=CAPABILITY,
        instruction="Design a glucose-derived polymer candidate with bounded evidence.",
        budget_usd=budget,
        data_classification=classification,
        allowed_tools=allowed_tools,
        resources=frozenset({"polymer-design"}),
        metadata={"requested_properties": ["glass_transition_temperature"]},
    )


def _executor(
    gateway: _Gateway,
    *,
    targets: tuple[ExecutionTarget, ...] | None = None,
    tool_runner: _ToolRunner | None = None,
) -> UniversalTaskExecutor:
    return UniversalTaskExecutor(
        gateway=gateway,
        scheduler=Scheduler(),
        accelerator_planner=_planner(),
        target_source=lambda: targets
        or (_local_target(), _azure_target()),
        tool_runner=tool_runner,
    )


def _adapter(*, required_tool: str | None = None) -> PolymerDesignAdapter:
    return PolymerDesignAdapter(
        policy_engine=PolicyEngine(),
        required_tool=required_tool,
    )


def test_restricted_work_selects_local_and_requires_all_evidence() -> None:
    gateway = _Gateway(json.dumps(_candidate()))

    result = _executor(gateway).execute(
        _request(classification="restricted"),
        _adapter(),
    )

    assert result.status is TaskStatus.SUCCEEDED
    assert result.route is not None
    assert result.route.selected_profile_id == "local-polymer"
    assert result.route.selected_provider == "local"
    selected_evaluation = next(
        item
        for item in result.route.evaluations
        if item.profile_id == result.route.selected_profile_id
    )
    assert selected_evaluation.evidence["health"] == "local model heartbeat"
    assert selected_evaluation.evidence["capability"] == "polymer schema benchmark"
    assert selected_evaluation.evidence["cost"] == "local measured token cost"
    assert selected_evaluation.evidence["privacy"] == "no network egress"
    assert selected_evaluation.evidence["accelerator_approved"] is True
    assert gateway.calls[0]["profile_id"] == "local-polymer"
    safety = result.evidence["safety"]
    validation = result.evidence["validation"]
    provenance = result.evidence["provenance"]
    assert isinstance(safety, dict)
    assert isinstance(validation, dict)
    assert isinstance(provenance, dict)
    assert safety["refused_reason"] is None
    assert validation["status"] == "validated"
    assert provenance["complete"] is True


def test_public_work_falls_back_to_healthy_azure_target() -> None:
    gateway = _Gateway(json.dumps(_candidate()))
    targets = (_local_target(healthy=False), _azure_target())

    result = _executor(gateway, targets=targets).execute(_request(), _adapter())

    assert result.status is TaskStatus.SUCCEEDED
    assert result.route is not None
    assert result.route.selected_profile_id == "azure-polymer"
    assert result.route.evaluations[0].reasons == ("unhealthy",)


def test_execute_uses_one_evidence_snapshot_for_route_and_invocation() -> None:
    calls = 0

    def target_source() -> tuple[ExecutionTarget, ...]:
        nonlocal calls
        calls += 1
        if calls > 1:
            return ()
        return (_local_target(),)

    executor = UniversalTaskExecutor(
        gateway=_Gateway(json.dumps(_candidate())),
        scheduler=Scheduler(),
        accelerator_planner=_planner(),
        target_source=target_source,
    )

    result = executor.execute(_request(), _adapter())

    assert result.status is TaskStatus.SUCCEEDED
    assert calls == 1


@pytest.mark.parametrize(
    "content",
    [
        "Here is a promising polymer scaffold; validate it later.",
        json.dumps({"candidate_id": "draft", "status": "TODO"}),
        json.dumps([]),
    ],
)
def test_model_prose_or_scaffold_is_never_success(content: str) -> None:
    result = _executor(_Gateway(content)).execute(_request(), _adapter())

    assert result.status is TaskStatus.REFUSED
    assert result.candidate is None
    assert any("structured_candidate" in reason for reason in result.reasons)


def test_unknown_monomer_fails_closed_at_safety_gate() -> None:
    candidate = _candidate()
    candidate["monomers"] = ["inventedium monomer"]

    result = _executor(_Gateway(json.dumps(candidate))).execute(
        _request(),
        _adapter(),
    )

    assert result.status is TaskStatus.REFUSED
    assert any("safety" in reason for reason in result.reasons)


@pytest.mark.parametrize("failed_gate", ["validation", "provenance"])
def test_invalid_validation_or_provenance_fails_closed(failed_gate: str) -> None:
    candidate = _candidate()
    if failed_gate == "validation":
        validation_value = candidate["validation"]
        assert isinstance(validation_value, dict)
        validation = dict(validation_value)
        validation["converged"] = False
        candidate["validation"] = validation
    else:
        provenance_value = candidate["provenance"]
        assert isinstance(provenance_value, dict)
        provenance = dict(provenance_value)
        provenance.pop("raw_artifact")
        candidate["provenance"] = provenance

    result = _executor(_Gateway(json.dumps(candidate))).execute(
        _request(),
        _adapter(),
    )

    assert result.status is TaskStatus.REFUSED
    assert any(failed_gate in reason for reason in result.reasons)


def test_required_tool_is_injected_allowlisted_and_fail_closed() -> None:
    tool_name = "rdkit_polymer_check"
    request = _request(allowed_tools=frozenset({tool_name}))
    content = json.dumps(_candidate())

    unavailable = _executor(_Gateway(content)).execute(
        request,
        _adapter(required_tool=tool_name),
    )
    assert unavailable.status is TaskStatus.REFUSED
    assert "required_tool_unavailable" in unavailable.reasons

    runner = _ToolRunner()
    accepted = _executor(_Gateway(content), tool_runner=runner).execute(
        request,
        _adapter(required_tool=tool_name),
    )
    assert accepted.status is TaskStatus.SUCCEEDED
    assert runner.calls[0][0] == tool_name
    tool_evidence = accepted.evidence["tool"]
    assert isinstance(tool_evidence, dict)
    assert tool_evidence["passed"] is True

    denied = _executor(_Gateway(content), tool_runner=runner).execute(
        replace(request, allowed_tools=frozenset()),
        _adapter(required_tool=tool_name),
    )
    assert denied.status is TaskStatus.REFUSED
    assert "required_tool_not_allowed" in denied.reasons

    refused = _executor(
        _Gateway(content),
        tool_runner=_ToolRunner(passed=False),
    ).execute(request, _adapter(required_tool=tool_name))
    assert refused.status is TaskStatus.REFUSED
    assert "tool_check_refused" in refused.reasons

    class _BrokenToolRunner(_ToolRunner):
        def run(
            self,
            tool_name: str,
            payload: dict[str, object],
        ) -> dict[str, object]:
            raise RuntimeError("tool internals must not escape")

    failed = _executor(
        _Gateway(content),
        tool_runner=_BrokenToolRunner(),
    ).execute(request, _adapter(required_tool=tool_name))
    assert failed.status is TaskStatus.REFUSED
    assert failed.reasons == ("tool_check_failed:RuntimeError",)


def test_route_refuses_missing_capability_health_budget_or_hardware() -> None:
    bad_capability = replace(
        _local_target(), capabilities=frozenset({"text_summary"})
    )
    unhealthy = _azure_target(healthy=False)
    over_budget = _azure_target(cost=5.0)
    missing_hardware = replace(_local_target(), accelerator_sku="missing-sku")
    gateway = _Gateway(json.dumps(_candidate()))

    result = _executor(
        gateway,
        targets=(bad_capability, unhealthy, over_budget, missing_hardware),
    ).execute(_request(budget=1.0), _adapter())

    assert result.status is TaskStatus.REFUSED
    assert result.route is not None
    reasons = {reason for item in result.route.evaluations for reason in item.reasons}
    assert {
        "capability_not_supported",
        "unhealthy",
        "over_budget",
        "accelerator_unavailable",
    } <= reasons
    assert gateway.calls == []


def test_gateway_failure_and_excess_actual_cost_do_not_claim_success() -> None:
    class _BrokenGateway(_Gateway):
        def call_model(
            self,
            profile_id: str,
            messages: list[dict[str, str]],
            **kwargs: Any,
        ) -> SimpleNamespace:
            raise RuntimeError("provider details must not escape")

    failed = _executor(_BrokenGateway("")).execute(_request(), _adapter())
    assert failed.status is TaskStatus.FAILED
    assert failed.reasons == ("model_call_failed:RuntimeError",)

    overrun = _executor(_Gateway(json.dumps(_candidate()), cost=2.0)).execute(
        _request(budget=1.0),
        _adapter(),
    )
    assert overrun.status is TaskStatus.REFUSED
    assert overrun.reasons == ("actual_cost_exceeds_budget",)

    invalid_cost = _executor(
        _Gateway(json.dumps(_candidate()), cost=float("nan"))
    ).execute(_request(), _adapter())
    assert invalid_cost.status is TaskStatus.REFUSED
    assert invalid_cost.reasons == ("invalid_actual_cost_evidence",)


def test_executor_refuses_adapter_policy_and_scheduler_mismatches() -> None:
    class _WrongCapabilityAdapter(PolymerDesignAdapter):
        capability = "other_capability"

    gateway = _Gateway(json.dumps(_candidate()))
    mismatch = _executor(gateway).execute(
        _request(),
        _WrongCapabilityAdapter(policy_engine=PolicyEngine()),
    )
    assert mismatch.status is TaskStatus.REFUSED
    assert mismatch.reasons == ("adapter_capability_mismatch",)
    assert gateway.calls == []

    policy = PolicyEngine(required_tools={"simulate": ("approved_simulator",)})
    policy_refusal = _executor(_Gateway(json.dumps(_candidate()))).execute(
        _request(),
        PolymerDesignAdapter(policy_engine=policy),
    )
    assert policy_refusal.status is TaskStatus.REFUSED
    assert policy_refusal.reasons
    assert all(reason.startswith("policy_refused:") for reason in policy_refusal.reasons)

    class _RejectingScheduler(Scheduler):
        def plan(self, _items: list[WorkItem]) -> list[list[str]]:
            return []

    scheduler_refusal = UniversalTaskExecutor(
        gateway=_Gateway(json.dumps(_candidate())),
        scheduler=_RejectingScheduler(),
        accelerator_planner=_planner(),
        target_source=lambda: (_local_target(),),
    ).execute(_request(), _adapter())
    assert scheduler_refusal.status is TaskStatus.FAILED
    assert scheduler_refusal.reasons == ("scheduler_rejected_task",)


def test_route_rejects_accelerator_provider_mismatch() -> None:
    executor = _executor(
        _Gateway(json.dumps(_candidate())),
        targets=(replace(_local_target(), provider="azure"),),
    )

    route = executor.route(_request())

    assert route.selected_profile_id is None
    assert route.evaluations[0].reasons == ("accelerator_provider_mismatch",)


def test_adapter_rejects_non_candidate_objects() -> None:
    assessment = _adapter().assess_candidate(_request(), {"candidate": "draft"}, None)

    assert assessment.accepted is False
    assert assessment.reasons == ("structured_candidate_required",)


@pytest.mark.parametrize("deadline", ["eventually", 0, True])
def test_invalid_deadline_metadata_fails_closed_before_model_call(
    deadline: object,
) -> None:
    gateway = _Gateway(json.dumps(_candidate()))
    request = replace(_request(), metadata={"deadline_s": deadline})

    result = _executor(gateway).execute(request, _adapter())

    assert result.status is TaskStatus.REFUSED
    assert result.reasons == ("policy_refused:deadline_s must be a positive integer",)
    assert gateway.calls == []


def test_universal_modules_never_import_self_improvement() -> None:
    root = Path(__file__).resolve().parents[2]
    for relative in (
        "src/general_ludd/execution/universal_task.py",
        "src/general_ludd/chemistry/polymer_design.py",
    ):
        text = (root / relative).read_text(encoding="utf-8")
        assert "self_improve" not in text
