"""Deterministic acceptance tests for constrained/local model task routing."""

from __future__ import annotations

import hashlib

import pytest

from general_ludd.routing_roles.small_model_policy import (
    DEFAULT_TASK_CONTRACTS,
    CapabilityEvidence,
    CompletionAction,
    CompletionEvidence,
    DispatchAction,
    ModelIdentity,
    PolicyConfig,
    SmallModelTaskPolicy,
    SmallModelTaskSpec,
    TaskImpact,
)
from general_ludd.schemas.benchmark import TaskRole


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _identity(
    model_profile_id: str = "local-profile", *, artifact: str = "weights-v1"
) -> ModelIdentity:
    return ModelIdentity(
        model_profile_id=model_profile_id,
        model_artifact_digest=_digest(artifact),
        runtime_config_digest=_digest("llama.cpp:rev1:q4"),
        prompt_contract_digest=_digest("prompt:v1"),
    )


def _task(
    *,
    task_id: str = "todo-17",
    task_kind: str = "context_compaction",
    role: TaskRole = TaskRole.COMPACTOR,
    collection: str = "general_ludd.agent",
    impacts: frozenset[TaskImpact] = frozenset(
        {TaskImpact.READ_SOURCE, TaskImpact.WRITE_ARTIFACT}
    ),
    checks: tuple[str, ...] = (
        "facts_preserved",
        "token_budget_met",
        "schema_valid",
    ),
) -> SmallModelTaskSpec:
    return SmallModelTaskSpec(
        task_id=task_id,
        task_kind=task_kind,
        role=role,
        collection=collection,
        input_digest=_digest(f"input:{task_id}"),
        impacts=impacts,
        acceptance_checks=checks,
    )


def _proof(
    task: SmallModelTaskSpec,
    *,
    identity: ModelIdentity | None = None,
    passed_cases: int = 24,
    total_cases: int = 24,
    role: TaskRole | None = None,
    collection: str | None = None,
    contract_digest: str | None = None,
    collection_ok: bool = True,
    local_only: bool = True,
) -> CapabilityEvidence:
    model_identity = identity or _identity()
    return CapabilityEvidence(
        model_profile_id=model_identity.model_profile_id,
        model_identity_digest=model_identity.fingerprint,
        task_kind=task.task_kind,
        role=role or task.role,
        collection=collection or task.collection,
        suite_id="small-model-contract",
        suite_revision="v1",
        acceptance_contract_digest=(
            contract_digest or task.acceptance_contract_digest
        ),
        passed_cases=passed_cases,
        total_cases=total_cases,
        collection_ok=collection_ok,
        local_only=local_only,
        evidence_digest=_digest(
            f"proof:{model_identity.model_profile_id}:{task.task_id}:"
            f"{passed_cases}:{total_cases}"
        ),
    )


def _completion(
    fingerprint: str,
    *,
    attempt: int = 1,
    results: dict[str, bool] | None = None,
    collection_ok: bool = True,
    evidence_id: str = "completion-1",
) -> CompletionEvidence:
    return CompletionEvidence(
        task_fingerprint=fingerprint,
        attempt=attempt,
        artifact_digest=_digest(f"artifact:{attempt}"),
        acceptance_results=results
        or {
            "facts_preserved": True,
            "token_budget_met": True,
            "schema_valid": True,
        },
        collection_ok=collection_ok,
        evidence_digest=_digest(evidence_id),
    )


def test_default_contracts_only_grant_bounded_roles() -> None:
    assert set(DEFAULT_TASK_CONTRACTS) == {
        "bounded_enumeration",
        "coding",
        "context_compaction",
        "documentation_draft",
        "failure_classification",
        "format_normalization",
        "game_logic",
        "schema_extraction",
    }
    granted_roles = {
        role for contract in DEFAULT_TASK_CONTRACTS.values() for role in contract.allowed_roles
    }
    assert granted_roles == {
        TaskRole.COMPACTOR,
        TaskRole.CODER,
        TaskRole.EDITOR,
        TaskRole.ENUMERATOR,
        TaskRole.REVIEWER,
    }
    assert TaskRole.PLANNER not in granted_roles
    assert all(
        contract.allowed_impacts
        == frozenset({TaskImpact.READ_SOURCE, TaskImpact.WRITE_ARTIFACT})
        for contract in DEFAULT_TASK_CONTRACTS.values()
    )


@pytest.mark.parametrize("model_id", ["tiny-local-1b", "not-small-by-name"])
def test_authorization_uses_exact_evidence_not_model_name(model_id: str) -> None:
    task = _task(task_id=f"task-{model_id}")
    identity = _identity(model_id)
    decision = SmallModelTaskPolicy().authorize(
        task, model_identity=identity, evidence=[_proof(task, identity=identity)]
    )

    assert decision.action is DispatchAction.LOCAL
    assert decision.approved is True
    assert decision.reason == "capability_proven"
    assert len(decision.task_fingerprint) == 64


def test_missing_capability_evidence_escalates() -> None:
    decision = SmallModelTaskPolicy().authorize(
        _task(), model_identity=_identity("tiny-local-1b"), evidence=[]
    )

    assert decision.action is DispatchAction.ESCALATE
    assert decision.reason == "capability_evidence_missing"


@pytest.mark.parametrize(
    ("proof_change", "reason"),
    [
        ({"passed_cases": 19, "total_cases": 20}, "evaluation_suite_failed"),
        ({"passed_cases": 10, "total_cases": 10}, "evaluation_suite_too_small"),
        ({"collection_ok": False}, "evaluation_collection_failed"),
        ({"local_only": False}, "evaluation_not_local"),
        ({"role": TaskRole.EDITOR}, "capability_evidence_missing"),
        ({"collection": "other.collection"}, "capability_evidence_missing"),
        ({"contract_digest": _digest("wrong")}, "capability_evidence_missing"),
    ],
)
def test_inadequate_or_mismatched_evidence_fails_closed(
    proof_change: dict[str, object], reason: str
) -> None:
    task = _task()
    proof = _proof(task, **proof_change)  # type: ignore[arg-type]

    decision = SmallModelTaskPolicy().authorize(
        task, model_identity=_identity(), evidence=[proof]
    )

    assert decision.action is DispatchAction.ESCALATE
    assert decision.reason == reason


@pytest.mark.parametrize(
    "impact",
    [
        TaskImpact.MUTATE_REPOSITORY,
        TaskImpact.EXECUTE_COMMAND,
        TaskImpact.NETWORK_WRITE,
        TaskImpact.CREDENTIAL_ACCESS,
        TaskImpact.DEPLOYMENT,
        TaskImpact.RELEASE,
        TaskImpact.SECURITY_DECISION,
    ],
)
def test_high_impact_task_escalates_even_with_passing_proof(impact: TaskImpact) -> None:
    task = _task(impacts=frozenset({TaskImpact.READ_SOURCE, impact}))

    decision = SmallModelTaskPolicy().authorize(
        task,
        model_identity=_identity(),
        evidence=[_proof(task)],
    )

    assert decision.action is DispatchAction.ESCALATE
    assert decision.reason == "impact_requires_stronger_model"


def test_role_and_acceptance_contract_are_enforced_before_dispatch() -> None:
    wrong_role = _task(role=TaskRole.EDITOR)
    missing_check = _task(checks=("facts_preserved", "schema_valid"))

    role_decision = SmallModelTaskPolicy().authorize(
        wrong_role, model_identity=_identity(), evidence=[_proof(wrong_role)]
    )
    check_decision = SmallModelTaskPolicy().authorize(
        missing_check, model_identity=_identity(), evidence=[_proof(missing_check)]
    )

    assert role_decision.reason == "role_not_allowed_for_task"
    assert check_decision.reason == "acceptance_contract_incomplete"


def test_unknown_task_kind_escalates_without_claiming_task() -> None:
    task = _task(task_kind="arbitrary_shell_work")

    decision = SmallModelTaskPolicy().authorize(
        task, model_identity=_identity(), evidence=[_proof(task)]
    )

    assert decision.action is DispatchAction.ESCALATE
    assert decision.reason == "task_kind_not_proven_safe"


def test_duplicate_task_id_is_not_dispatched_twice() -> None:
    policy = SmallModelTaskPolicy()
    task = _task()
    first = policy.authorize(task, _identity(), [_proof(task)])
    duplicate = policy.authorize(task, _identity(), [_proof(task)])

    assert first.approved is True
    assert duplicate.action is DispatchAction.ESCALATE
    assert duplicate.reason == "duplicate_task_claim"
    assert duplicate.task_fingerprint == first.task_fingerprint


def test_complete_result_requires_all_acceptance_and_collection_evidence() -> None:
    policy = SmallModelTaskPolicy()
    task = _task()
    auth = policy.authorize(task, _identity(), [_proof(task)])

    result = policy.record_completion(_completion(auth.task_fingerprint))

    assert result.action is CompletionAction.ACCEPT
    assert result.reason == "acceptance_evidence_complete"
    assert result.attempts_used == 1


def test_incomplete_completion_retries_then_accepts_within_bound() -> None:
    policy = SmallModelTaskPolicy(PolicyConfig(max_attempts=2))
    task = _task()
    auth = policy.authorize(task, _identity(), [_proof(task)])
    failed = _completion(
        auth.task_fingerprint,
        results={
            "facts_preserved": False,
            "token_budget_met": True,
            "schema_valid": True,
        },
    )

    retry = policy.record_completion(failed)
    accepted = policy.record_completion(
        _completion(auth.task_fingerprint, attempt=2, evidence_id="completion-2")
    )

    assert retry.action is CompletionAction.RETRY
    assert retry.reason == "acceptance_evidence_failed"
    assert accepted.action is CompletionAction.ACCEPT
    assert accepted.attempts_used == 2


def test_retry_budget_exhaustion_escalates() -> None:
    policy = SmallModelTaskPolicy(PolicyConfig(max_attempts=1))
    task = _task()
    auth = policy.authorize(task, _identity(), [_proof(task)])

    result = policy.record_completion(
        _completion(auth.task_fingerprint, collection_ok=False)
    )

    assert result.action is CompletionAction.ESCALATE
    assert result.reason == "retry_budget_exhausted"
    assert result.attempts_used == 1


def test_replayed_completion_is_idempotent_and_does_not_spend_retry() -> None:
    policy = SmallModelTaskPolicy(PolicyConfig(max_attempts=2))
    task = _task()
    auth = policy.authorize(task, _identity(), [_proof(task)])
    failed = _completion(
        auth.task_fingerprint,
        results={
            "facts_preserved": False,
            "token_budget_met": True,
            "schema_valid": True,
        },
    )

    first = policy.record_completion(failed)
    replay = policy.record_completion(failed)

    assert first.action is CompletionAction.RETRY
    assert replay.action is CompletionAction.RETRY
    assert replay.reason == "duplicate_completion_evidence"
    assert replay.attempts_used == 1


def test_completion_with_missing_or_extra_checks_is_rejected() -> None:
    policy = SmallModelTaskPolicy(PolicyConfig(max_attempts=2))
    task = _task()
    auth = policy.authorize(task, _identity(), [_proof(task)])

    result = policy.record_completion(
        _completion(
            auth.task_fingerprint,
            results={
                "facts_preserved": True,
                "token_budget_met": True,
                "unexpected": True,
            },
        )
    )

    assert result.action is CompletionAction.RETRY
    assert result.reason == "acceptance_evidence_failed"


def test_completed_task_rejects_conflicting_second_result() -> None:
    policy = SmallModelTaskPolicy()
    task = _task()
    auth = policy.authorize(task, _identity(), [_proof(task)])
    policy.record_completion(_completion(auth.task_fingerprint))

    conflict = policy.record_completion(
        _completion(auth.task_fingerprint, evidence_id="different-result")
    )

    assert conflict.action is CompletionAction.ESCALATE
    assert conflict.reason == "task_already_completed"


def test_completion_for_unclaimed_task_escalates() -> None:
    result = SmallModelTaskPolicy().record_completion(
        _completion(_digest("unclaimed"))
    )

    assert result.action is CompletionAction.ESCALATE
    assert result.reason == "task_not_authorized"


def test_model_alias_cannot_reuse_proof_after_artifact_drift() -> None:
    task = _task()
    proven_identity = _identity(artifact="weights-v1")
    changed_identity = _identity(artifact="weights-v2")

    decision = SmallModelTaskPolicy().authorize(
        task,
        model_identity=changed_identity,
        evidence=[_proof(task, identity=proven_identity)],
    )

    assert decision.action is DispatchAction.ESCALATE
    assert decision.reason == "capability_evidence_missing"


@pytest.mark.parametrize("max_attempts", [0, 4])
def test_retry_configuration_is_bounded(max_attempts: int) -> None:
    with pytest.raises(ValueError, match="max_attempts"):
        PolicyConfig(max_attempts=max_attempts)


def test_task_identifiers_and_digests_are_validated() -> None:
    with pytest.raises(ValueError, match="task_id"):
        _task(task_id="")
    with pytest.raises(ValueError, match="input_digest"):
        SmallModelTaskSpec(
            task_id="task",
            task_kind="context_compaction",
            role=TaskRole.COMPACTOR,
            collection="general_ludd.agent",
            input_digest="not-a-digest",
            impacts=frozenset({TaskImpact.READ_SOURCE}),
            acceptance_checks=("facts_preserved", "token_budget_met", "schema_valid"),
        )


def test_runtime_types_fail_closed_instead_of_coercing_truthy_values() -> None:
    task = _task()
    with pytest.raises(ValueError, match="collection_ok"):
        _proof(task, collection_ok=1)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="local_only"):
        _proof(task, local_only="false")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="impacts"):
        _task(impacts=frozenset({"read_source"}))  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="collection_ok"):
        CompletionEvidence(
            task_fingerprint=_digest("task"),
            attempt=1,
            artifact_digest=_digest("artifact"),
            acceptance_results={"schema_valid": True},
            collection_ok=1,  # type: ignore[arg-type]
            evidence_digest=_digest("evidence"),
        )


def test_policy_surface_is_exported_from_routing_roles_package() -> None:
    from general_ludd import routing_roles

    assert routing_roles.SmallModelTaskPolicy is SmallModelTaskPolicy
    assert routing_roles.ModelIdentity is ModelIdentity
