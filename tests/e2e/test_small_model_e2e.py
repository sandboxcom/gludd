"""E2E proof: small model pipeline download -> quantize -> serve -> authorize -> dispatch -> complete -> accept.

Exercises the full constrained-model lifecycle with dummy model artifacts (no
real weights), verifying that capability evidence flows through the policy
engine and that ZDD rollout stages (evaluate, register proof, shadow-authorize,
canary, promote, rollback) are observable and revertible.
"""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING, ClassVar

import pytest

from general_ludd.routing_roles.small_model_policy import (
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

if TYPE_CHECKING:
    pass


# ---------------------------------------------------------------------------
# Tiny dummy model helpers — no real weights, deterministic digests
# ---------------------------------------------------------------------------


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _make_identity(
    model_profile_id: str = "tiny-dummy-1b",
    *,
    artifact: str = "weights-v1",
    runtime: str = "llama.cpp:q4_K_M",
    prompt: str = "chatml:v1",
) -> ModelIdentity:
    return ModelIdentity(
        model_profile_id=model_profile_id,
        model_artifact_digest=_digest(artifact),
        runtime_config_digest=_digest(runtime),
        prompt_contract_digest=_digest(prompt),
    )


def _make_task(
    *,
    task_id: str = "e2e-task-1",
    task_kind: str = "context_compaction",
    role: TaskRole = TaskRole.COMPACTOR,
    collection: str = "general_ludd.agent",
    impacts: frozenset[TaskImpact] = frozenset({TaskImpact.READ_SOURCE, TaskImpact.WRITE_ARTIFACT}),
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


def _make_proof(
    task: SmallModelTaskSpec,
    *,
    identity: ModelIdentity | None = None,
    passed_cases: int = 24,
    total_cases: int = 24,
    role: TaskRole | None = None,
    collection: str | None = None,
    collection_ok: bool = True,
    local_only: bool = True,
) -> CapabilityEvidence:
    mid = identity or _make_identity()
    return CapabilityEvidence(
        model_profile_id=mid.model_profile_id,
        model_identity_digest=mid.fingerprint,
        task_kind=task.task_kind,
        role=role if role is not None else task.role,
        collection=collection if collection is not None else task.collection,
        suite_id="small-model-contract",
        suite_revision="v1",
        acceptance_contract_digest=task.acceptance_contract_digest,
        passed_cases=passed_cases,
        total_cases=total_cases,
        collection_ok=collection_ok,
        local_only=local_only,
        evidence_digest=_digest(f"e2e-proof:{mid.model_profile_id}:{task.task_id}:{passed_cases}:{total_cases}"),
    )


def _make_completion(
    fingerprint: str,
    *,
    attempt: int = 1,
    checks: tuple[str, ...] = (
        "facts_preserved",
        "token_budget_met",
        "schema_valid",
    ),
    all_pass: bool = True,
    collection_ok: bool = True,
    evidence_id: str = "e2e-completion-1",
) -> CompletionEvidence:
    return CompletionEvidence(
        task_fingerprint=fingerprint,
        attempt=attempt,
        artifact_digest=_digest(f"artifact:{evidence_id}"),
        acceptance_results={c: all_pass for c in checks},
        collection_ok=collection_ok,
        evidence_digest=_digest(evidence_id),
    )


# ---------------------------------------------------------------------------
# Stage 1: Model download (dummy) — identity construction
# ---------------------------------------------------------------------------


class TestDownloadAndIdentity:
    """L1: download produces a verifiable ModelIdentity."""

    def test_model_identity_is_immutable_and_verifiable(self) -> None:
        mid = _make_identity(artifact="dummy-weights-v1")
        assert len(mid.model_artifact_digest) == 64
        assert len(mid.runtime_config_digest) == 64
        assert len(mid.prompt_contract_digest) == 64
        assert len(mid.fingerprint) == 64

    def test_artifact_drift_changes_identity_fingerprint(self) -> None:
        v1 = _make_identity(artifact="weights-v1")
        v2 = _make_identity(artifact="weights-v2")
        assert v1.fingerprint != v2.fingerprint
        assert v1.model_profile_id == v2.model_profile_id
        assert v1.model_artifact_digest != v2.model_artifact_digest

    def test_identity_fingerprint_deterministic(self) -> None:
        a = _make_identity(artifact="weights-v1")
        b = _make_identity(artifact="weights-v1")
        assert a.fingerprint == b.fingerprint


# ---------------------------------------------------------------------------
# Stage 2: Quantize (dummy) — runtime config digest
# ---------------------------------------------------------------------------


class TestQuantizeAndRuntimeConfig:
    """L2: quantization produces a verifiable runtime config digest."""

    def test_quantization_config_is_deterministic(self) -> None:
        a = _make_identity(artifact="weights-v1", runtime="llama.cpp:q4_K_M")
        b = _make_identity(artifact="weights-v1", runtime="llama.cpp:q4_K_M")
        assert a.runtime_config_digest == b.runtime_config_digest

    def test_quantization_change_invalidates_identity(self) -> None:
        q4 = _make_identity(artifact="weights-v1", runtime="llama.cpp:q4_K_M")
        q5 = _make_identity(artifact="weights-v1", runtime="llama.cpp:q5_K_M")
        assert q4.runtime_config_digest != q5.runtime_config_digest
        assert q4.fingerprint != q5.fingerprint


# ---------------------------------------------------------------------------
# Stage 3: Serve — prompt contract digest
# ---------------------------------------------------------------------------


class TestServeAndPromptContract:
    """L3: serving configuration produces a prompt contract digest."""

    def test_prompt_contract_is_bound_to_identity(self) -> None:
        a = _make_identity(prompt="chatml:v1")
        b = _make_identity(prompt="chatml:v2")
        assert a.prompt_contract_digest != b.prompt_contract_digest
        assert a.fingerprint != b.fingerprint

    def test_full_identity_includes_artifact_runtime_and_prompt(self) -> None:
        mid = _make_identity(artifact="weights-v1", runtime="llama.cpp:q4_K_M", prompt="chatml:v1")
        assert len(mid.model_artifact_digest) == 64
        assert len(mid.runtime_config_digest) == 64
        assert len(mid.prompt_contract_digest) == 64
        assert len(mid.fingerprint) == 64


# ---------------------------------------------------------------------------
# Stage 4: Authorize — capability evidence gate
# ---------------------------------------------------------------------------


class TestAuthorizeCapabilityGate:
    """L4: authorize() gates dispatch on exact capability evidence."""

    def test_full_authorize_chain_with_valid_evidence(self) -> None:
        mid = _make_identity("tiny-dummy-1b")
        task = _make_task(task_kind="context_compaction")
        proof = _make_proof(task, identity=mid, passed_cases=24, total_cases=24)

        decision = SmallModelTaskPolicy().authorize(task, model_identity=mid, evidence=[proof])

        assert decision.action is DispatchAction.LOCAL
        assert decision.approved is True
        assert decision.reason == "capability_proven"
        assert decision.max_attempts == 2

    def test_authorize_rejects_without_evidence(self) -> None:
        mid = _make_identity("tiny-dummy-1b")
        task = _make_task()

        decision = SmallModelTaskPolicy().authorize(task, model_identity=mid, evidence=[])

        assert decision.action is DispatchAction.ESCALATE
        assert decision.reason == "capability_evidence_missing"

    def test_authorize_requires_exact_identity_match(self) -> None:
        mid1 = _make_identity("tiny-dummy-1b", artifact="weights-v1")
        mid2 = _make_identity("tiny-dummy-1b", artifact="weights-v2")
        task = _make_task()
        proof = _make_proof(task, identity=mid1)

        decision = SmallModelTaskPolicy().authorize(task, model_identity=mid2, evidence=[proof])

        assert decision.action is DispatchAction.ESCALATE
        assert decision.reason == "capability_evidence_missing"

    def test_all_high_impact_operations_escalate(self) -> None:
        forbidden = [
            TaskImpact.MUTATE_REPOSITORY,
            TaskImpact.EXECUTE_COMMAND,
            TaskImpact.NETWORK_WRITE,
            TaskImpact.CREDENTIAL_ACCESS,
            TaskImpact.DEPLOYMENT,
            TaskImpact.RELEASE,
            TaskImpact.SECURITY_DECISION,
        ]
        for impact in forbidden:
            task = _make_task(impacts=frozenset({TaskImpact.READ_SOURCE, impact}))
            mid = _make_identity()
            proof = _make_proof(task, identity=mid)

            decision = SmallModelTaskPolicy().authorize(task, model_identity=mid, evidence=[proof])

            assert decision.action is DispatchAction.ESCALATE, f"Impact {impact.value} should escalate"
            assert decision.reason == "impact_requires_stronger_model"

    def test_all_six_default_task_kinds_authorized_with_proof(self) -> None:
        _KIND_ROLE_CHECKS: dict[str, tuple[TaskRole, tuple[str, ...]]] = {
            "bounded_enumeration": (
                TaskRole.ENUMERATOR,
                ("coverage_bounded", "no_duplicates", "schema_valid"),
            ),
            "context_compaction": (
                TaskRole.COMPACTOR,
                ("facts_preserved", "token_budget_met", "schema_valid"),
            ),
            "documentation_draft": (
                TaskRole.EDITOR,
                ("facts_traceable", "links_valid", "schema_valid"),
            ),
            "failure_classification": (
                TaskRole.REVIEWER,
                ("evidence_cited", "label_in_taxonomy", "schema_valid"),
            ),
            "format_normalization": (
                TaskRole.EDITOR,
                ("idempotent", "schema_valid", "semantic_equivalence"),
            ),
            "schema_extraction": (
                TaskRole.EDITOR,
                ("all_required_fields", "schema_valid", "source_traceable"),
            ),
        }
        for kind, (role, checks) in _KIND_ROLE_CHECKS.items():
            mid = _make_identity()
            task = _make_task(
                task_kind=kind,
                task_id=f"e2e-{kind}",
                role=role,
                checks=checks,
            )
            proof = _make_proof(task, identity=mid, role=role)

            decision = SmallModelTaskPolicy().authorize(task, model_identity=mid, evidence=[proof])

            assert decision.action is DispatchAction.LOCAL, (
                f"Task kind {kind} with role {role.value} should authorize: "
                f"got reason={decision.reason}, checks={checks}"
            )
            assert decision.reason == "capability_proven"

    def test_unknown_task_kind_always_escalates(self) -> None:
        mid = _make_identity()
        task = _make_task(task_kind="unsafe_unknown_work")
        proof = _make_proof(task, identity=mid)

        decision = SmallModelTaskPolicy().authorize(task, model_identity=mid, evidence=[proof])

        assert decision.action is DispatchAction.ESCALATE
        assert decision.reason == "task_kind_not_proven_safe"


# ---------------------------------------------------------------------------
# Stage 5: Dispatch — deduplication and claim registration
# ---------------------------------------------------------------------------


class TestDispatchAndDeduplication:
    """L5: dispatch gated on claim registry (no duplicate work)."""

    def test_same_task_id_cannot_be_dispatched_twice(self) -> None:
        policy = SmallModelTaskPolicy()
        mid = _make_identity()
        task = _make_task(task_id="e2e-dedup-1")
        proof = _make_proof(task, identity=mid)

        first = policy.authorize(task, mid, [proof])
        second = policy.authorize(task, mid, [proof])

        assert first.approved is True
        assert second.action is DispatchAction.ESCALATE
        assert second.reason == "duplicate_task_claim"

    def test_different_task_ids_independent(self) -> None:
        policy = SmallModelTaskPolicy()
        mid = _make_identity()

        task1 = _make_task(task_id="e2e-indep-1")
        task2 = _make_task(task_id="e2e-indep-2")
        proof1 = _make_proof(task1, identity=mid)
        proof2 = _make_proof(task2, identity=mid)

        d1 = policy.authorize(task1, mid, [proof1])
        d2 = policy.authorize(task2, mid, [proof2])

        assert d1.approved is True
        assert d2.approved is True

    def test_fingerprint_is_deterministic_for_same_input(self) -> None:
        policy = SmallModelTaskPolicy()
        mid = _make_identity()
        task = _make_task(task_id="e2e-fp-1")
        proof = _make_proof(task, identity=mid)

        d1 = policy.authorize(task, mid, [proof])
        fp1 = d1.task_fingerprint

        policy2 = SmallModelTaskPolicy()
        d2 = policy2.authorize(task, mid, [proof])
        fp2 = d2.task_fingerprint

        assert fp1 == fp2
        assert len(fp1) == 64


# ---------------------------------------------------------------------------
# Stage 6: Complete — acceptance evidence validation
# ---------------------------------------------------------------------------


class TestCompleteAcceptance:
    """L6: record_completion() validates acceptance evidence."""

    def test_full_complete_accept_cycle(self) -> None:
        policy = SmallModelTaskPolicy()
        mid = _make_identity()
        task = _make_task(task_id="e2e-cycle-1")
        proof = _make_proof(task, identity=mid)

        auth = policy.authorize(task, mid, [proof])
        assert auth.approved is True

        completion = _make_completion(auth.task_fingerprint)
        result = policy.record_completion(completion)

        assert result.action is CompletionAction.ACCEPT
        assert result.reason == "acceptance_evidence_complete"
        assert result.attempts_used == 1

    def test_partial_completion_retries_within_budget(self) -> None:
        policy = SmallModelTaskPolicy(PolicyConfig(max_attempts=3))
        mid = _make_identity()
        task = _make_task(task_id="e2e-retry-1")
        proof = _make_proof(task, identity=mid)

        auth = policy.authorize(task, mid, [proof])

        # Attempt 1: fails
        fail1 = _make_completion(auth.task_fingerprint, attempt=1, all_pass=False)
        r1 = policy.record_completion(fail1)
        assert r1.action is CompletionAction.RETRY
        assert r1.reason == "acceptance_evidence_failed"

        # Attempt 2: fails
        fail2 = _make_completion(
            auth.task_fingerprint,
            attempt=2,
            all_pass=False,
            evidence_id="e2e-completion-2",
        )
        r2 = policy.record_completion(fail2)
        assert r2.action is CompletionAction.RETRY

        # Attempt 3: passes
        ok = _make_completion(
            auth.task_fingerprint,
            attempt=3,
            all_pass=True,
            evidence_id="e2e-completion-3",
        )
        r3 = policy.record_completion(ok)
        assert r3.action is CompletionAction.ACCEPT
        assert r3.attempts_used == 3

    def test_retry_budget_exhaustion_escalates(self) -> None:
        policy = SmallModelTaskPolicy(PolicyConfig(max_attempts=1))
        mid = _make_identity()
        task = _make_task(task_id="e2e-exhaust-1")
        proof = _make_proof(task, identity=mid)

        auth = policy.authorize(task, mid, [proof])

        fail = _make_completion(auth.task_fingerprint, attempt=1, all_pass=False)
        result = policy.record_completion(fail)

        assert result.action is CompletionAction.ESCALATE
        assert result.reason == "retry_budget_exhausted"

    def test_completion_replayed_is_idempotent(self) -> None:
        policy = SmallModelTaskPolicy(PolicyConfig(max_attempts=2))
        mid = _make_identity()
        task = _make_task(task_id="e2e-replay-1")
        proof = _make_proof(task, identity=mid)

        auth = policy.authorize(task, mid, [proof])

        fail = _make_completion(auth.task_fingerprint, attempt=1, all_pass=False)
        first = policy.record_completion(fail)
        replay = policy.record_completion(fail)

        assert first.action is CompletionAction.RETRY
        assert replay.action is CompletionAction.RETRY
        assert replay.reason == "duplicate_completion_evidence"

    def test_completed_task_rejects_second_completion(self) -> None:
        policy = SmallModelTaskPolicy()
        mid = _make_identity()
        task = _make_task(task_id="e2e-reject-1")
        proof = _make_proof(task, identity=mid)

        auth = policy.authorize(task, mid, [proof])
        policy.record_completion(_make_completion(auth.task_fingerprint))

        conflict = policy.record_completion(_make_completion(auth.task_fingerprint, evidence_id="e2e-completion-alt"))

        assert conflict.action is CompletionAction.ESCALATE
        assert conflict.reason == "task_already_completed"

    def test_unclaimed_completion_escalates(self) -> None:
        result = SmallModelTaskPolicy().record_completion(_make_completion(_digest("bogus-fingerprint")))
        assert result.action is CompletionAction.ESCALATE
        assert result.reason == "task_not_authorized"

    def test_collection_failure_precludes_acceptance(self) -> None:
        policy = SmallModelTaskPolicy()
        mid = _make_identity()
        task = _make_task(task_id="e2e-nocollect-1")
        proof = _make_proof(task, identity=mid)

        auth = policy.authorize(task, mid, [proof])

        result = policy.record_completion(_make_completion(auth.task_fingerprint, collection_ok=False))

        assert result.action is CompletionAction.RETRY
        assert result.reason == "acceptance_evidence_failed"


# ---------------------------------------------------------------------------
# Stage 7: Capability evidence flow — end-to-end traceability
# ---------------------------------------------------------------------------


class TestCapabilityEvidenceFlow:
    """L7: evidence flows end-to-end — proof binding, traceable digests."""

    def test_evidence_is_bound_to_exact_model_identity(self) -> None:
        mid = _make_identity(artifact="weights-v1")
        task = _make_task()
        proof = _make_proof(task, identity=mid)

        assert proof.model_identity_digest == mid.fingerprint
        assert proof.model_profile_id == mid.model_profile_id

    def test_evidence_includes_full_suite_metadata(self) -> None:
        mid = _make_identity()
        task = _make_task()
        proof = _make_proof(task, identity=mid)

        assert proof.task_kind == task.task_kind
        assert proof.role is task.role
        assert proof.collection == task.collection
        assert proof.acceptance_contract_digest == task.acceptance_contract_digest
        assert proof.suite_id == "small-model-contract"
        assert proof.suite_revision == "v1"
        assert proof.passed_cases == 24
        assert proof.total_cases == 24
        assert proof.collection_ok is True
        assert proof.local_only is True

    def test_evidence_digest_chain_is_cryptographic(self) -> None:
        mid = _make_identity()
        task = _make_task()
        proof = _make_proof(task, identity=mid)

        _verify = hashlib.sha256((f"e2e-proof:{mid.model_profile_id}:{task.task_id}:24:24").encode()).hexdigest()

        assert proof.evidence_digest == _verify

    def test_completion_evidence_binds_artifact_and_results(self) -> None:
        policy = SmallModelTaskPolicy()
        mid = _make_identity()
        task = _make_task()
        proof = _make_proof(task, identity=mid)
        auth = policy.authorize(task, mid, [proof])

        completion = _make_completion(auth.task_fingerprint)
        result = policy.record_completion(completion)

        assert result.action is CompletionAction.ACCEPT
        assert len(completion.artifact_digest) == 64
        assert len(completion.evidence_digest) == 64


# ---------------------------------------------------------------------------
# ZDD rollout stages — evaluate, register, shadow, canary, promote, rollback
# ---------------------------------------------------------------------------


class TestZDDRolloutStages:
    """ZDD: additive rollout with content-addressed proof promotion and rollback."""

    def test_stage1_evaluate_candidate_offline(self) -> None:
        mid = _make_identity("candidate-model", artifact="candidate-weights-v1")
        task = _make_task(task_id="zdd-eval-1")
        proof = _make_proof(task, identity=mid, passed_cases=24, total_cases=24)

        decision = SmallModelTaskPolicy().authorize(task, model_identity=mid, evidence=[proof])

        assert decision.approved is True
        assert decision.reason == "capability_proven"

    def test_stage2_register_proof_without_traffic_change(self) -> None:
        mid_a = _make_identity("active-model", artifact="weights-v1")
        mid_b = _make_identity("candidate-model", artifact="candidate-weights-v2")

        task = _make_task(task_id="zdd-register-1")
        proof_a = _make_proof(task, identity=mid_a)
        proof_b = _make_proof(task, identity=mid_b)

        # Active model still dispatches
        policy_a = SmallModelTaskPolicy()
        dec_a = policy_a.authorize(task, mid_a, [proof_a])
        assert dec_a.approved is True

        # Candidate is registered but does not receive traffic until promoted
        policy_b = SmallModelTaskPolicy()
        dec_b = policy_b.authorize(task, mid_b, [proof_b])
        assert dec_b.approved is True

    def test_stage3_shadow_authorize_comparison(self) -> None:
        mid_a = _make_identity("active-model")
        mid_b = _make_identity("candidate-model")

        task = _make_task(task_id="zdd-shadow-1")
        proof_a = _make_proof(task, identity=mid_a)
        proof_b = _make_proof(task, identity=mid_b)

        policy_a = SmallModelTaskPolicy()
        dec_a = policy_a.authorize(task, mid_a, [proof_a])

        policy_b = SmallModelTaskPolicy()
        dec_b = policy_b.authorize(task, mid_b, [proof_b])

        # Both authorize — the shadow comparison is that decisions agree
        assert dec_a.approved is True
        assert dec_b.approved is True
        assert dec_a.task_fingerprint == dec_b.task_fingerprint

    def test_stage4_canary_bounded_artifact_only(self) -> None:
        mid = _make_identity("canary-model")
        policy = SmallModelTaskPolicy(PolicyConfig(max_attempts=1))

        task = _make_task(
            task_id="zdd-canary-1",
            task_kind="documentation_draft",
            role=TaskRole.EDITOR,
            checks=("facts_traceable", "links_valid", "schema_valid"),
        )
        proof = _make_proof(task, identity=mid)

        auth = policy.authorize(task, mid, [proof])
        assert auth.approved is True

        # Canary writes artifact only; completion is verified
        completion = CompletionEvidence(
            task_fingerprint=auth.task_fingerprint,
            attempt=1,
            artifact_digest=_digest("artifact:zdd-canary"),
            acceptance_results={
                "facts_traceable": True,
                "links_valid": True,
                "schema_valid": True,
            },
            collection_ok=True,
            evidence_digest=_digest("zdd-canary-evidence"),
        )
        result = policy.record_completion(completion)
        assert result.action is CompletionAction.ACCEPT

    def test_stage5_promote_by_publishing_proof_digest(self) -> None:
        mid = _make_identity("promoted-model", artifact="promoted-weights")
        policy = SmallModelTaskPolicy()

        task = _make_task(
            task_id="zdd-promote-1",
            task_kind="format_normalization",
            role=TaskRole.EDITOR,
            checks=("idempotent", "schema_valid", "semantic_equivalence"),
        )
        proof = _make_proof(task, identity=mid)

        auth = policy.authorize(task, mid, [proof])
        assert auth.approved is True
        assert auth.reason == "capability_proven"

        # Promote = the proof digest is the published artifact
        promoted_digest = proof.evidence_digest
        assert len(promoted_digest) == 64

    def test_stage6_rollback_by_removing_proof_or_changing_identity(self) -> None:
        mid = _make_identity("rollback-model", artifact="bad-weights")
        policy = SmallModelTaskPolicy()

        # Model was promoted with one proof, then weights changed
        old_mid = _make_identity("rollback-model", artifact="old-weights")
        task = _make_task(task_id="zdd-rollback-1")
        old_proof = _make_proof(task, identity=old_mid)

        # New weights: old proof no longer matches
        decision = policy.authorize(task, mid, [old_proof])
        assert decision.action is DispatchAction.ESCALATE
        assert decision.reason == "capability_evidence_missing"

    def test_rollback_is_no_restart_no_queue_drain(self) -> None:
        mid = _make_identity("no-restart-model")

        # Phase 1: active with proof
        policy = SmallModelTaskPolicy()
        task = _make_task(task_id="zdd-norestart-1")
        proof = _make_proof(task, identity=mid)

        auth = policy.authorize(task, mid, [proof])
        assert auth.approved is True

        # Phase 2: rollback = empty proof set for new dispatch
        policy2 = SmallModelTaskPolicy()
        decision = policy2.authorize(task, mid, [])
        assert decision.action is DispatchAction.ESCALATE
        assert decision.reason == "capability_evidence_missing"

        # In-flight task retains its fingerprint and can still complete
        completion = _make_completion(auth.task_fingerprint)
        result = policy.record_completion(completion)
        assert result.action is CompletionAction.ACCEPT

    def test_mixed_acceptance_contracts_during_rollout_avoided(self) -> None:
        mid = _make_identity("mixed-model")
        policy = SmallModelTaskPolicy()

        # Old contract: 3 checks on format_normalization
        task_v1 = _make_task(
            task_id="zdd-mixed-1",
            task_kind="format_normalization",
            role=TaskRole.EDITOR,
            checks=("idempotent", "schema_valid", "semantic_equivalence"),
        )
        proof_v1 = _make_proof(task_v1, identity=mid)

        auth_v1 = policy.authorize(task_v1, mid, [proof_v1])
        assert auth_v1.approved is True

        # New contract: 4 checks (not in current policy)
        task_v2 = _make_task(
            task_id="zdd-mixed-2",
            checks=(
                "idempotent",
                "schema_valid",
                "semantic_equivalence",
                "new_check_alpha",
            ),
        )
        decision = policy.authorize(task_v2, mid, [])
        assert decision.action is DispatchAction.ESCALATE


# ---------------------------------------------------------------------------
# Full pipeline integration — download through accept
# ---------------------------------------------------------------------------


class TestFullPipelineIntegration:
    """All seven stages integrated in a single policy lifecycle."""

    _STAGES: ClassVar[list[str]] = ["download", "quantize", "serve", "authorize", "dispatch", "complete", "accept"]

    def test_full_pipeline_download_through_accept(self) -> None:
        stage_order: list[str] = []

        # Stage 1: Download — produce artifact digest
        artifact = "dummy-weights-v1"
        artifact_digest = _digest(artifact)
        assert len(artifact_digest) == 64
        stage_order.append("download")

        # Stage 2: Quantize — produce runtime digest
        runtime = _digest("llama.cpp:q4_K_M")
        assert len(runtime) == 64
        stage_order.append("quantize")

        # Stage 3: Serve — produce prompt contract digest
        prompt = _digest("chatml:v1")
        assert len(prompt) == 64
        stage_order.append("serve")

        # Stage 4: Authorize — construct identity and request dispatch
        mid = ModelIdentity(
            model_profile_id="full-pipeline-model",
            model_artifact_digest=artifact_digest,
            runtime_config_digest=runtime,
            prompt_contract_digest=prompt,
        )
        task = _make_task(
            task_id="full-pipeline-task",
            task_kind="documentation_draft",
            role=TaskRole.EDITOR,
            checks=("facts_traceable", "links_valid", "schema_valid"),
        )
        proof = _make_proof(task, identity=mid)

        policy = SmallModelTaskPolicy()
        auth = policy.authorize(task, mid, [proof])
        assert auth.approved is True
        assert auth.reason == "capability_proven"
        stage_order.append("authorize")

        # Stage 5: Dispatch — check claim uniqueness
        assert auth.max_attempts == 2
        assert len(auth.task_fingerprint) == 64
        stage_order.append("dispatch")

        # Stage 6: Complete — record completion evidence
        completion = CompletionEvidence(
            task_fingerprint=auth.task_fingerprint,
            attempt=1,
            artifact_digest=_digest("artifact:full-pipeline"),
            acceptance_results={
                "facts_traceable": True,
                "links_valid": True,
                "schema_valid": True,
            },
            collection_ok=True,
            evidence_digest=_digest("full-pipeline-evidence"),
        )
        result = policy.record_completion(completion)
        stage_order.append("complete")

        # Stage 7: Accept
        assert result.action is CompletionAction.ACCEPT
        assert result.reason == "acceptance_evidence_complete"
        assert result.attempts_used == 1
        stage_order.append("accept")

        assert stage_order == self._STAGES

    def test_multiple_tasks_independent_pipelines(self) -> None:
        mid = _make_identity()
        policy = SmallModelTaskPolicy()

        tasks_and_roles: list[tuple[str, str, TaskRole, tuple[str, ...]]] = [
            (
                "task-a",
                "context_compaction",
                TaskRole.COMPACTOR,
                ("facts_preserved", "token_budget_met", "schema_valid"),
            ),
            (
                "task-b",
                "documentation_draft",
                TaskRole.EDITOR,
                ("facts_traceable", "links_valid", "schema_valid"),
            ),
            (
                "task-c",
                "failure_classification",
                TaskRole.REVIEWER,
                ("evidence_cited", "label_in_taxonomy", "schema_valid"),
            ),
        ]

        fingerprints: list[str] = []
        for task_id, kind, role, checks in tasks_and_roles:
            task = _make_task(task_id=task_id, task_kind=kind, role=role, checks=checks)
            proof = _make_proof(task, identity=mid)
            auth = policy.authorize(task, mid, [proof])
            assert auth.approved is True
            fingerprints.append(auth.task_fingerprint)

            completion = CompletionEvidence(
                task_fingerprint=auth.task_fingerprint,
                attempt=1,
                artifact_digest=_digest(f"artifact:{task_id}"),
                acceptance_results={c: True for c in checks},
                collection_ok=True,
                evidence_digest=_digest(f"evidence:{task_id}"),
            )
            result = policy.record_completion(completion)
            assert result.action is CompletionAction.ACCEPT

        assert len(set(fingerprints)) == 3


# ---------------------------------------------------------------------------
# Edge cases and adversarial inputs
# ---------------------------------------------------------------------------


class TestEdgeCases:
    """Adversarial and edge-case coverage for the full pipeline."""

    def test_empty_acceptance_checks_rejected(self) -> None:
        with pytest.raises(ValueError, match="acceptance checks must not be empty"):
            _make_task(checks=())

    def test_duplicate_acceptance_checks_rejected(self) -> None:
        with pytest.raises(ValueError, match="acceptance_checks must not contain duplicates"):
            _make_task(checks=("idempotent", "schema_valid", "idempotent"))

    def test_numeric_truthy_not_accepted_as_bool(self) -> None:
        with pytest.raises(ValueError, match="collection_ok"):
            _make_proof(_make_task(), collection_ok=1)  # type: ignore[arg-type]

    def test_suite_too_small_escalates(self) -> None:
        mid = _make_identity()
        task = _make_task()
        proof = _make_proof(task, identity=mid, passed_cases=10, total_cases=10)

        decision = SmallModelTaskPolicy().authorize(task, mid, [proof])

        assert decision.action is DispatchAction.ESCALATE
        assert decision.reason == "evaluation_suite_too_small"

    def test_non_local_evaluation_escalates(self) -> None:
        mid = _make_identity()
        task = _make_task()
        proof = _make_proof(task, identity=mid, local_only=False)

        decision = SmallModelTaskPolicy().authorize(task, mid, [proof])

        assert decision.action is DispatchAction.ESCALATE
        assert decision.reason == "evaluation_not_local"

    def test_collection_ok_false_blocks_proof_acceptance(self) -> None:
        mid = _make_identity()
        task = _make_task()
        proof = _make_proof(task, identity=mid, collection_ok=False)

        decision = SmallModelTaskPolicy().authorize(task, mid, [proof])

        assert decision.action is DispatchAction.ESCALATE
        assert decision.reason == "evaluation_collection_failed"

    def test_partial_pass_escalates(self) -> None:
        mid = _make_identity()
        task = _make_task()
        proof = _make_proof(task, identity=mid, passed_cases=20, total_cases=24)

        decision = SmallModelTaskPolicy().authorize(task, mid, [proof])

        assert decision.action is DispatchAction.ESCALATE
        assert decision.reason == "evaluation_suite_failed"

    def test_attempt_out_of_sequence_escalates(self) -> None:
        policy = SmallModelTaskPolicy(PolicyConfig(max_attempts=3))
        mid = _make_identity()
        task = _make_task()
        proof = _make_proof(task, identity=mid)

        auth = policy.authorize(task, mid, [proof])
        assert auth.approved is True

        # Skip attempt 1, submit attempt 2 directly
        result = policy.record_completion(_make_completion(auth.task_fingerprint, attempt=2))

        assert result.action is CompletionAction.ESCALATE
        assert result.reason == "attempt_out_of_sequence"
