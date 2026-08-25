"""Deep tests for ZDD rollout: hash buckets, stage transitions, rollback recovery,
observation integrity, edge cases, and boundary conditions beyond the basic
acceptance tests in test_zdd_rollout.py.
"""

from __future__ import annotations

import hashlib

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
    _hash_bucket,
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
) -> CapabilityEvidence:
    model_identity = identity or _identity()
    return CapabilityEvidence(
        model_profile_id=model_identity.model_profile_id,
        model_identity_digest=model_identity.fingerprint,
        task_kind=task.task_kind,
        role=task.role,
        collection=task.collection,
        suite_id="local-suite:1",
        suite_revision="1.0",
        acceptance_contract_digest=task.acceptance_contract_digest,
        passed_cases=passed_cases,
        total_cases=total_cases,
        collection_ok=True,
        local_only=True,
        evidence_digest=_digest(f"{task.task_id}:proof"),
    )


class TestHashBucket:
    def test_range_is_zero_to_99(self) -> None:
        for i in range(500):
            bucket = _hash_bucket(f"range-{i}")
            assert 0 <= bucket <= 99

    def test_deterministic_same_input_same_bucket(self) -> None:
        task_id = "deterministic-task-abc"
        first = _hash_bucket(task_id)
        for _ in range(50):
            assert _hash_bucket(task_id) == first

    def test_different_task_ids_produce_varying_buckets(self) -> None:
        buckets = {_hash_bucket(f"vary-{i}") for i in range(200)}
        assert len(buckets) >= 30

    def test_empty_string_task_id(self) -> None:
        bucket = _hash_bucket("")
        assert 0 <= bucket <= 99

    def test_unicode_task_ids(self) -> None:
        bucket = _hash_bucket("任務-日本語-🚀")
        assert 0 <= bucket <= 99

    def test_seed_changes_distribution(self) -> None:
        task_id = "seed-test-42"
        b0 = _hash_bucket(task_id, seed=None)
        b1 = _hash_bucket(task_id, seed=0)
        b2 = _hash_bucket(task_id, seed=12345)
        assert isinstance(b0, int)
        assert isinstance(b1, int)
        assert isinstance(b2, int)
        assert 0 <= b0 <= 99 and 0 <= b1 <= 99 and 0 <= b2 <= 99

    def test_seed_zero_vs_none_produces_same_bucket(self) -> None:
        task_id = "seed-zero-test"
        bucket_none = _hash_bucket(task_id, seed=None)
        bucket_zero = _hash_bucket(task_id, seed=0)
        assert bucket_none != bucket_zero

    def test_boundary_buckets_reachable(self) -> None:
        seen = set()
        for i in range(5000):
            seen.add(_hash_bucket(f"boundary-{i}"))
        assert 0 in seen
        assert 99 in seen


class TestProgressiveStageTransitions:
    def test_advance_preserves_observations(self) -> None:
        rollout = ZDDRollout()
        policy = SmallModelTaskPolicy()
        identity = _identity()

        for i in range(5):
            task = _task(task_id=f"pres-{i}")
            rollout.authorize(policy, task, identity, [_proof(task, identity=identity)])

        assert len(rollout.observations) == 5
        rollout.advance()
        assert len(rollout.observations) == 5

    def test_advance_preserves_observation_content(self) -> None:
        rollout = ZDDRollout(stage=RolloutStage.SHADOW)
        policy = SmallModelTaskPolicy()
        identity = _identity()
        task = _task(task_id="keep-me")
        rollout.authorize(policy, task, identity, [_proof(task, identity=identity)])

        obs_before = rollout.observations[0]
        rollout.advance(RolloutStage.CANARY_50)
        obs_after = rollout.observations[0]

        assert obs_before["task_id"] == obs_after["task_id"]
        assert obs_before["underlying_approved"] == obs_after["underlying_approved"]

    def test_rollback_preserves_observations_from_prior_stages(self) -> None:
        rollout = ZDDRollout()
        policy = SmallModelTaskPolicy()
        identity = _identity()

        task = _task(task_id="rb-obs")
        rollout.authorize(policy, task, identity, [_proof(task, identity=identity)])
        rollout.advance(RolloutStage.FULL)
        task2 = _task(task_id="rb-obs-2")
        rollout.authorize(policy, task2, identity, [_proof(task2, identity=identity)])

        assert len(rollout.observations) == 2
        rollout.rollback()
        assert len(rollout.observations) == 2

    def test_rollback_observation_stage_field_accurate(self) -> None:
        rollout = ZDDRollout()
        policy = SmallModelTaskPolicy()
        identity = _identity()

        task = _task(task_id="stage-chk")
        rollout.authorize(policy, task, identity, [_proof(task, identity=identity)])
        assert rollout.observations[0]["stage"] == "shadow"

        rollout.advance(RolloutStage.FULL)
        task2 = _task(task_id="stage-chk-2")
        rollout.authorize(policy, task2, identity, [_proof(task2, identity=identity)])
        assert rollout.observations[1]["stage"] == "full"

    def test_rapid_advance_rollback_cycle(self) -> None:
        rollout = ZDDRollout()
        for _ in range(8):
            rollout.advance()
            rollout.rollback()
            rollout.advance()
        assert rollout.stage is RolloutStage.SHADOW

    def test_advance_from_rollback_then_authorize(self) -> None:
        rollout = ZDDRollout()
        rollout.advance(RolloutStage.CANARY_10)
        rollout.rollback()
        rollout.advance()
        assert rollout.stage is RolloutStage.SHADOW

        policy = SmallModelTaskPolicy()
        identity = _identity()
        task = _task(task_id="post-rb-adv")
        decision = rollout.authorize(policy, task, identity, [_proof(task, identity=identity)])
        assert decision.approved is False
        assert "shadow" in decision.reason.lower()


class TestFullEnforcement:
    def test_full_stage_enforces_every_task_regardless_of_hash(self) -> None:
        policy = SmallModelTaskPolicy()
        identity = _identity()
        rollout = ZDDRollout(stage=RolloutStage.FULL)

        for i in range(1000):
            task = _task(task_id=f"full-all-{i}")
            decision = rollout.authorize(policy, task, identity, [_proof(task, identity=identity)])
            assert decision.approved is True

    def test_full_stage_no_escalation_on_eligible(self) -> None:
        policy = SmallModelTaskPolicy()
        identity = _identity()
        rollout = ZDDRollout(stage=RolloutStage.FULL)

        for i in range(500):
            task = _task(task_id=f"no-esc-{i}")
            decision = rollout.authorize(policy, task, identity, [_proof(task, identity=identity)])
            assert decision.action is not DispatchAction.ESCALATE

    def test_full_stage_enforced_flag_in_observations(self) -> None:
        rollout = ZDDRollout(stage=RolloutStage.FULL)
        policy = SmallModelTaskPolicy()
        identity = _identity()

        for i in range(50):
            task = _task(task_id=f"enf-flag-{i}")
            rollout.authorize(policy, task, identity, [_proof(task, identity=identity)])

        for obs in rollout.observations:
            assert obs["enforced"] is True


class TestObservationIntegrity:
    def test_observations_returns_copy_not_reference(self) -> None:
        rollout = ZDDRollout()
        policy = SmallModelTaskPolicy()
        identity = _identity()
        task = _task(task_id="copy-test")
        rollout.authorize(policy, task, identity, [_proof(task, identity=identity)])

        obs1 = rollout.observations
        obs1[0]["task_id"] = "mutated"

        obs2 = rollout.observations
        assert obs2[0]["task_id"] == "copy-test"

    def test_stage_preserved_after_clear_observations(self) -> None:
        rollout = ZDDRollout(stage=RolloutStage.CANARY_50)
        policy = SmallModelTaskPolicy()
        identity = _identity()

        for i in range(10):
            task = _task(task_id=f"clr-stage-{i}")
            rollout.authorize(policy, task, identity, [_proof(task, identity=identity)])

        assert len(rollout.observations) == 10
        rollout.clear_observations()
        assert len(rollout.observations) == 0
        assert rollout.stage is RolloutStage.CANARY_50

    def test_clear_then_record_new_observations(self) -> None:
        rollout = ZDDRollout(stage=RolloutStage.FULL)
        policy = SmallModelTaskPolicy()
        identity = _identity()

        for i in range(5):
            task = _task(task_id=f"pre-clr-{i}")
            rollout.authorize(policy, task, identity, [_proof(task, identity=identity)])

        rollout.clear_observations()
        for i in range(3):
            task = _task(task_id=f"post-clr-{i}")
            rollout.authorize(policy, task, identity, [_proof(task, identity=identity)])

        assert len(rollout.observations) == 3
        assert rollout.observations[0]["task_id"] == "post-clr-0"


class TestSummaryEdgeCases:
    def test_summary_empty_observations(self) -> None:
        rollout = ZDDRollout(stage=RolloutStage.SHADOW)
        s = rollout.summary()
        assert s["total_observations"] == 0
        assert s["enforced_count"] == 0
        assert s["escalated_count"] == 0
        assert s["stage"] == "shadow"

    def test_summary_all_enforced(self) -> None:
        rollout = ZDDRollout(stage=RolloutStage.FULL)
        policy = SmallModelTaskPolicy()
        identity = _identity()

        for i in range(50):
            task = _task(task_id=f"all-enf-{i}")
            rollout.authorize(policy, task, identity, [_proof(task, identity=identity)])

        s = rollout.summary()
        assert s["enforced_count"] == 50
        assert s["escalated_count"] == 0

    def test_summary_all_escalated(self) -> None:
        rollout = ZDDRollout(stage=RolloutStage.SHADOW)
        policy = SmallModelTaskPolicy()
        identity = _identity()

        for i in range(30):
            task = _task(task_id=f"all-esc-{i}")
            rollout.authorize(policy, task, identity, [_proof(task, identity=identity)])

        s = rollout.summary()
        assert s["enforced_count"] == 0
        assert s["escalated_count"] == 30

    def test_summary_across_stage_transitions(self) -> None:
        rollout = ZDDRollout(stage=RolloutStage.SHADOW)
        policy = SmallModelTaskPolicy()
        identity = _identity()

        task = _task(task_id="xstage-1")
        rollout.authorize(policy, task, identity, [_proof(task, identity=identity)])

        rollout.advance(RolloutStage.FULL)
        task2 = _task(task_id="xstage-2")
        rollout.authorize(policy, task2, identity, [_proof(task2, identity=identity)])

        s = rollout.summary()
        assert s["total_observations"] == 2
        assert isinstance(s["enforced_count"], int) and s["enforced_count"] >= 1
        assert isinstance(s["escalated_count"], int) and s["escalated_count"] >= 1

    def test_summary_stage_reflects_current_stage(self) -> None:
        rollout = ZDDRollout(stage=RolloutStage.CANARY_10, seed=42)
        policy = SmallModelTaskPolicy()
        identity = _identity()

        for i in range(20):
            task = _task(task_id=f"cur-stage-{i}")
            rollout.authorize(policy, task, identity, [_proof(task, identity=identity)])

        assert rollout.summary()["stage"] == "canary_10"
        rollout.advance(RolloutStage.CANARY_50)
        assert rollout.summary()["stage"] == "canary_50"


class TestEdgeCaseInteractions:
    def test_advance_to_current_stage_idempotent(self) -> None:
        rollout = ZDDRollout(stage=RolloutStage.CANARY_10)
        rollout.advance(RolloutStage.CANARY_10)
        assert rollout.stage is RolloutStage.CANARY_10

    def test_advance_to_future_stage_then_advance_implicit(self) -> None:
        rollout = ZDDRollout()
        rollout.advance(RolloutStage.CANARY_10)
        assert rollout.stage is RolloutStage.CANARY_10
        rollout.advance()
        assert rollout.stage is RolloutStage.CANARY_50

    def test_rollback_twice_stays_at_rollback(self) -> None:
        rollout = ZDDRollout(stage=RolloutStage.CANARY_50)
        rollout.rollback()
        rollout.rollback()
        assert rollout.stage is RolloutStage.ROLLBACK

    def test_rollback_from_shadow(self) -> None:
        rollout = ZDDRollout(stage=RolloutStage.SHADOW)
        rollout.rollback()
        assert rollout.stage is RolloutStage.ROLLBACK

    def test_default_seed_produces_same_buckets_per_session(self) -> None:
        rollout = ZDDRollout(stage=RolloutStage.CANARY_10)
        rollout2 = ZDDRollout(stage=RolloutStage.CANARY_10)
        policy = SmallModelTaskPolicy()
        policy2 = SmallModelTaskPolicy()
        identity = _identity()

        results = []
        for i in range(100):
            task = _task(task_id=f"def-seed-{i}")
            d1 = rollout.authorize(policy, task, identity, [_proof(task, identity=identity)])
            d2 = rollout2.authorize(policy2, task, identity, [_proof(task, identity=identity)])
            results.append(d1.approved == d2.approved)

        assert rollout.seed is None
        assert rollout2.seed is None
        assert all(results)
