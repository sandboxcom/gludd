"""Deterministic acceptance tests for ZDD shadow-authorization rollout."""

from __future__ import annotations

import hashlib

import pytest

from general_ludd.routing_roles.small_model_policy import (
    CapabilityEvidence,
    DispatchAction,
    ModelIdentity,
    SmallModelTaskPolicy,
    SmallModelTaskSpec,
    TaskImpact,
)
from general_ludd.schemas.benchmark import TaskRole
from general_ludd.small_models.zdd_rollout import (
    RolloutStage,
    ZDDRollout,
)


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _identity(model_profile_id: str = "local-profile", *, artifact: str = "weights-v1") -> ModelIdentity:
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
        suite_id="local-suite:1",
        suite_revision="1.0",
        acceptance_contract_digest=contract_digest or task.acceptance_contract_digest,
        passed_cases=passed_cases,
        total_cases=total_cases,
        collection_ok=collection_ok,
        local_only=local_only,
        evidence_digest=_digest(f"{task.task_id}:proof"),
    )


class TestRolloutStage:
    def test_enum_values(self) -> None:
        assert RolloutStage.SHADOW.value == "shadow"
        assert RolloutStage.CANARY_1.value == "canary_1"
        assert RolloutStage.CANARY_10.value == "canary_10"
        assert RolloutStage.CANARY_50.value == "canary_50"
        assert RolloutStage.FULL.value == "full"
        assert RolloutStage.ROLLBACK.value == "rollback"

    def test_canary_percentage(self) -> None:
        assert RolloutStage.SHADOW.canary_pct == 0
        assert RolloutStage.CANARY_1.canary_pct == 1
        assert RolloutStage.CANARY_10.canary_pct == 10
        assert RolloutStage.CANARY_50.canary_pct == 50
        assert RolloutStage.FULL.canary_pct == 100
        assert RolloutStage.ROLLBACK.canary_pct == 0

    def test_progression_order(self) -> None:
        stages = list(RolloutStage)
        assert stages == [
            RolloutStage.SHADOW,
            RolloutStage.CANARY_1,
            RolloutStage.CANARY_10,
            RolloutStage.CANARY_50,
            RolloutStage.FULL,
            RolloutStage.ROLLBACK,
        ]


class TestZDDRolloutInitialization:
    def test_defaults_to_shadow(self) -> None:
        rollout = ZDDRollout()
        assert rollout.stage is RolloutStage.SHADOW

    def test_starts_with_no_observations(self) -> None:
        rollout = ZDDRollout()
        assert len(rollout.observations) == 0

    def test_accepts_initial_stage(self) -> None:
        rollout = ZDDRollout(stage=RolloutStage.CANARY_10)
        assert rollout.stage is RolloutStage.CANARY_10

    def test_seed_determinism(self) -> None:
        r1 = ZDDRollout(stage=RolloutStage.CANARY_50, seed=42)
        r2 = ZDDRollout(stage=RolloutStage.CANARY_50, seed=42)
        identity = _identity()
        for i in range(100):
            task = _task(task_id=f"t-{i}")
            proof = _proof(task, identity=identity)
            p1 = SmallModelTaskPolicy()
            p2 = SmallModelTaskPolicy()
            d1 = r1.authorize(p1, task, identity, [proof])
            d2 = r2.authorize(p2, task, identity, [proof])
            assert d1.approved == d2.approved

    def test_different_seeds_produce_different_distributions(self) -> None:
        policy = SmallModelTaskPolicy()
        identity = _identity()
        r1 = ZDDRollout(stage=RolloutStage.CANARY_50, seed=1)
        r2 = ZDDRollout(stage=RolloutStage.CANARY_50, seed=999)
        r1_apps = [
            r1.authorize(
                policy, _task(task_id=f"s-{i}"), identity, [_proof(_task(task_id=f"s-{i}"), identity=identity)]
            ).approved
            for i in range(100)
        ]
        r2_apps = [
            r2.authorize(
                policy, _task(task_id=f"s-{i}"), identity, [_proof(_task(task_id=f"s-{i}"), identity=identity)]
            ).approved
            for i in range(100)
        ]
        match = sum(1 for a, b in zip(r1_apps, r2_apps, strict=False) if a == b)
        assert match < 90


class TestZDDRolloutShadowMode:
    def test_shadow_never_approves(self) -> None:
        rollout = ZDDRollout(stage=RolloutStage.SHADOW)
        policy = SmallModelTaskPolicy()
        identity = _identity()
        task = _task()
        proof = _proof(task, identity=identity)

        decision = rollout.authorize(policy, task, identity, [proof])
        assert decision.approved is False
        assert decision.action is DispatchAction.ESCALATE
        assert "shadow" in decision.reason.lower()

    def test_shadow_records_observations(self) -> None:
        rollout = ZDDRollout(stage=RolloutStage.SHADOW)
        policy = SmallModelTaskPolicy()
        identity = _identity()
        task = _task(task_id="obs-1")
        proof = _proof(task, identity=identity)

        rollout.authorize(policy, task, identity, [proof])
        assert len(rollout.observations) == 1
        obs = rollout.observations[0]
        assert obs["task_id"] == "obs-1"
        assert "underlying_approved" in obs
        assert obs["underlying_approved"] is True

    def test_shadow_records_escalation_reason(self) -> None:
        rollout = ZDDRollout(stage=RolloutStage.SHADOW)
        policy = SmallModelTaskPolicy()
        identity = _identity()
        task = _task(task_id="bad-1", task_kind="unknown_kind")
        proof = _proof(task, identity=identity)

        rollout.authorize(policy, task, identity, [proof])
        assert len(rollout.observations) == 1
        assert rollout.observations[0]["underlying_approved"] is False


class TestZDDRolloutCanaryMode:
    def test_canary_1_approximation(self) -> None:
        policy = SmallModelTaskPolicy()
        identity = _identity()
        rollout = ZDDRollout(stage=RolloutStage.CANARY_1, seed=42)

        approved = 0
        total = 1000
        for i in range(total):
            task = _task(task_id=f"ct-{i}")
            proof = _proof(task, identity=identity)
            decision = rollout.authorize(policy, task, identity, [proof])
            if decision.approved:
                approved += 1
        ratio = approved / total
        assert 0.005 <= ratio <= 0.025, f"expected ~1%, got {ratio:.3f}"

    def test_canary_10_approximation(self) -> None:
        policy = SmallModelTaskPolicy()
        identity = _identity()
        rollout = ZDDRollout(stage=RolloutStage.CANARY_10, seed=42)

        approved = 0
        total = 1000
        for i in range(total):
            task = _task(task_id=f"ct10-{i}")
            proof = _proof(task, identity=identity)
            decision = rollout.authorize(policy, task, identity, [proof])
            if decision.approved:
                approved += 1
        ratio = approved / total
        assert 0.05 <= ratio <= 0.15, f"expected ~10%, got {ratio:.3f}"

    def test_canary_50_approximation(self) -> None:
        policy = SmallModelTaskPolicy()
        identity = _identity()
        rollout = ZDDRollout(stage=RolloutStage.CANARY_50, seed=42)

        approved = 0
        total = 1000
        for i in range(total):
            task = _task(task_id=f"ct50-{i}")
            proof = _proof(task, identity=identity)
            decision = rollout.authorize(policy, task, identity, [proof])
            if decision.approved:
                approved += 1
        ratio = approved / total
        assert 0.40 <= ratio <= 0.60, f"expected ~50%, got {ratio:.3f}"

    def test_canary_ineligible_task_always_escalates(self) -> None:
        policy = SmallModelTaskPolicy()
        identity = _identity()
        rollout = ZDDRollout(stage=RolloutStage.CANARY_50, seed=42)
        task = _task(task_id="bad-impact", impacts=frozenset({TaskImpact.EXECUTE_COMMAND}))
        proof = _proof(task, identity=identity)

        decision = rollout.authorize(policy, task, identity, [proof])
        assert decision.approved is False

    def test_canary_records_observations(self) -> None:
        rollout = ZDDRollout(stage=RolloutStage.CANARY_10, seed=42)
        policy = SmallModelTaskPolicy()
        identity = _identity()

        for i in range(10):
            task = _task(task_id=f"co-{i}")
            proof = _proof(task, identity=identity)
            rollout.authorize(policy, task, identity, [proof])

        assert len(rollout.observations) == 10
        approved_count = sum(1 for o in rollout.observations if o["enforced"])
        assert 0 <= approved_count <= 10


class TestZDDRolloutFullMode:
    def test_full_always_approves_eligible_tasks(self) -> None:
        rollout = ZDDRollout(stage=RolloutStage.FULL)
        policy = SmallModelTaskPolicy()
        identity = _identity()

        for i in range(50):
            task = _task(task_id=f"full-{i}")
            proof = _proof(task, identity=identity)
            decision = rollout.authorize(policy, task, identity, [proof])
            assert decision.approved is True

    def test_full_does_not_approve_ineligible_tasks(self) -> None:
        rollout = ZDDRollout(stage=RolloutStage.FULL)
        policy = SmallModelTaskPolicy()
        identity = _identity()
        task = _task(task_id="bad-f", task_kind="unknown_kind")
        proof = _proof(task, identity=identity)

        decision = rollout.authorize(policy, task, identity, [proof])
        assert decision.approved is False

    def test_full_passes_through_underlying_approval(self) -> None:
        rollout = ZDDRollout(stage=RolloutStage.FULL)
        identity = _identity()
        task = _task()
        proof = _proof(task, identity=identity)

        direct = SmallModelTaskPolicy().authorize(task, identity, [proof])
        rolled = rollout.authorize(SmallModelTaskPolicy(), task, identity, [proof])
        assert rolled.approved is direct.approved
        assert rolled.reason == direct.reason


class TestZDDRolloutRollback:
    def test_rollback_never_approves(self) -> None:
        rollout = ZDDRollout(stage=RolloutStage.ROLLBACK)
        policy = SmallModelTaskPolicy()
        identity = _identity()
        task = _task()
        proof = _proof(task, identity=identity)

        decision = rollout.authorize(policy, task, identity, [proof])
        assert decision.approved is False
        assert "rollback" in decision.reason.lower()

    def test_rollback_method_changes_stage(self) -> None:
        rollout = ZDDRollout(stage=RolloutStage.FULL)
        assert rollout.stage is RolloutStage.FULL

        rollout.rollback()
        assert rollout.stage is RolloutStage.ROLLBACK

    def test_rollback_from_canary(self) -> None:
        rollout = ZDDRollout(stage=RolloutStage.CANARY_10)
        rollout.rollback()
        assert rollout.stage is RolloutStage.ROLLBACK


class TestZDDRolloutAdvance:
    def test_advance_through_full_progression(self) -> None:
        rollout = ZDDRollout()
        expected = [
            RolloutStage.SHADOW,
            RolloutStage.CANARY_1,
            RolloutStage.CANARY_10,
            RolloutStage.CANARY_50,
            RolloutStage.FULL,
        ]
        for stage in expected:
            assert rollout.stage is stage
            if stage is not RolloutStage.FULL:
                rollout.advance()

    def test_advance_past_full_stays_at_full(self) -> None:
        rollout = ZDDRollout(stage=RolloutStage.FULL)
        rollout.advance()
        assert rollout.stage is RolloutStage.FULL

    def test_advance_from_rollback_goes_to_shadow(self) -> None:
        rollout = ZDDRollout(stage=RolloutStage.ROLLBACK)
        rollout.advance()
        assert rollout.stage is RolloutStage.SHADOW

    def test_advance_to_specific_stage(self) -> None:
        rollout = ZDDRollout()
        rollout.advance(RolloutStage.CANARY_50)
        assert rollout.stage is RolloutStage.CANARY_50

    def test_advance_to_earlier_stage_raises(self) -> None:
        rollout = ZDDRollout(stage=RolloutStage.CANARY_10)
        with pytest.raises(ValueError, match="Cannot advance backward"):
            rollout.advance(RolloutStage.SHADOW)


class TestZDDRolloutObservations:
    def test_observation_structure(self) -> None:
        rollout = ZDDRollout(stage=RolloutStage.SHADOW)
        policy = SmallModelTaskPolicy()
        identity = _identity()
        task = _task(task_id="struct-1")
        proof = _proof(task, identity=identity)

        rollout.authorize(policy, task, identity, [proof])
        obs = rollout.observations[0]

        assert obs["stage"] == RolloutStage.SHADOW.value
        assert obs["task_id"] == "struct-1"
        assert obs["task_kind"] == "context_compaction"
        assert obs["enforced"] is False
        assert isinstance(obs["underlying_approved"], bool)
        assert isinstance(obs["underlying_reason"], str)
        assert isinstance(obs["model_profile_id"], str)

    def test_clear_observations(self) -> None:
        rollout = ZDDRollout()
        policy = SmallModelTaskPolicy()
        identity = _identity()

        for i in range(5):
            task = _task(task_id=f"clr-{i}")
            proof = _proof(task, identity=identity)
            rollout.authorize(policy, task, identity, [proof])

        assert len(rollout.observations) == 5
        rollout.clear_observations()
        assert len(rollout.observations) == 0


class TestZDDRolloutSummary:
    def test_summary_counts(self) -> None:
        rollout = ZDDRollout(stage=RolloutStage.CANARY_10, seed=42)
        policy = SmallModelTaskPolicy()
        identity = _identity()

        for i in range(100):
            task = _task(task_id=f"sum-{i}")
            proof = _proof(task, identity=identity)
            rollout.authorize(policy, task, identity, [proof])

        summary = rollout.summary()
        assert summary["stage"] == RolloutStage.CANARY_10.value
        assert summary["total_observations"] == 100
        assert 0 <= summary["enforced_count"] <= 100
        assert 0 <= summary["escalated_count"] <= 100
        assert summary["enforced_count"] + summary["escalated_count"] == summary["total_observations"]


class TestRolloutIntegrationWithPolicy:
    def test_shadow_then_advance_to_full(self) -> None:
        rollout = ZDDRollout()
        policy = SmallModelTaskPolicy()
        identity = _identity()
        task = _task(task_id="int-1")
        proof = _proof(task, identity=identity)

        d1 = rollout.authorize(policy, task, identity, [proof])
        assert d1.approved is False
        assert d1.action is DispatchAction.ESCALATE

        rollout.advance(RolloutStage.FULL)
        d2 = rollout.authorize(policy, task, identity, [proof])
        assert d2.approved is False
        assert d2.action is DispatchAction.ESCALATE

    def test_duplicate_task_in_canary(self) -> None:
        rollout = ZDDRollout(stage=RolloutStage.CANARY_50, seed=42)
        policy = SmallModelTaskPolicy()
        identity = _identity()
        task = _task(task_id="dup-1")
        proof = _proof(task, identity=identity)

        d1 = rollout.authorize(policy, task, identity, [proof])
        d2 = rollout.authorize(policy, task, identity, [proof])

        if d1.approved:
            assert d2.approved is False
        else:
            assert d1.action is DispatchAction.ESCALATE
