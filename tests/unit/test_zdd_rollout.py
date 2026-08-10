"""Deterministic unit tests for zero-downtime deployment rollout enforcement."""

from __future__ import annotations

import hashlib
from unittest.mock import Mock

import pytest

from general_ludd.routing_roles.small_model_policy import (
    CapabilityEvidence,
    DispatchAction,
    DispatchDecision,
    ModelIdentity,
    SmallModelTaskPolicy,
    SmallModelTaskSpec,
)
from general_ludd.schemas.benchmark import TaskRole
from general_ludd.small_models.zdd_rollout import (
    _STAGES_ORDERED,
    RolloutStage,
    ZDDRollout,
    _hash_bucket,
)

# ---- helpers ----------------------------------------------------------------


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _identity(model_profile_id: str = "test-model") -> ModelIdentity:
    return ModelIdentity(
        model_profile_id=model_profile_id,
        model_artifact_digest=_digest("artifact-v1"),
        runtime_config_digest=_digest("rt:v1"),
        prompt_contract_digest=_digest("prompt:v1"),
    )


def _task(
    task_id: str = "t-1",
    task_kind: str = "context_compaction",
    role: TaskRole = TaskRole.COMPACTOR,
    collection: str = "general_ludd.agent",
) -> SmallModelTaskSpec:
    return SmallModelTaskSpec(
        task_id=task_id,
        task_kind=task_kind,
        role=role,
        collection=collection,
        input_digest=_digest(f"input:{task_id}"),
        impacts=frozenset({_make_impact("READ_SOURCE")}),
        acceptance_checks=("facts_preserved",),
    )


def _make_impact(name: str):
    from general_ludd.routing_roles.small_model_policy import TaskImpact

    return getattr(TaskImpact, name)


def _evidence(model_profile_id: str = "test-model") -> CapabilityEvidence:
    identity = _identity(model_profile_id)
    return CapabilityEvidence(
        model_profile_id=identity.model_profile_id,
        model_identity_digest=identity.fingerprint,
        task_kind="context_compaction",
        role=TaskRole.COMPACTOR,
        collection="general_ludd.agent",
        suite_id="suite-v1",
        suite_revision="v1",
        acceptance_contract_digest=_digest("contract"),
        passed_cases=24,
        total_cases=24,
        collection_ok=True,
        local_only=True,
        evidence_digest=_digest("evidence-1"),
    )


def _approved_decision(task: SmallModelTaskSpec) -> DispatchDecision:
    return DispatchDecision(
        action=DispatchAction.LOCAL,
        task_fingerprint=task.fingerprint,
        reason="capability_proven",
        max_attempts=0,
    )


def _make_policy(approved: bool = True, task: SmallModelTaskSpec | None = None) -> SmallModelTaskPolicy:
    task = task or _task()
    decision = (
        _approved_decision(task)
        if approved
        else DispatchDecision(
            action=DispatchAction.ESCALATE,
            task_fingerprint=task.fingerprint,
            reason="capability_evidence_missing",
            max_attempts=0,
        )
    )
    policy = Mock(spec=SmallModelTaskPolicy)
    policy.authorize.return_value = decision
    return policy


# ---- RolloutStage -----------------------------------------------------------


class TestRolloutStage:
    def test_default_stage_is_shadow(self) -> None:
        assert ZDDRollout().stage is RolloutStage.SHADOW

    def test_stages_are_str_enum(self) -> None:
        assert RolloutStage.SHADOW.value == "shadow"
        assert RolloutStage.FULL.value == "full"

    @pytest.mark.parametrize(
        "stage,expected_pct",
        [
            (RolloutStage.SHADOW, 0),
            (RolloutStage.CANARY_1, 1),
            (RolloutStage.CANARY_10, 10),
            (RolloutStage.CANARY_50, 50),
            (RolloutStage.FULL, 100),
            (RolloutStage.ROLLBACK, 0),
        ],
    )
    def test_canary_pct(self, stage: RolloutStage, expected_pct: int) -> None:
        assert stage.canary_pct == expected_pct

    def test_stages_ordered_excludes_rollback(self) -> None:
        assert RolloutStage.ROLLBACK not in _STAGES_ORDERED
        assert RolloutStage.FULL in _STAGES_ORDERED
        assert len(_STAGES_ORDERED) == 5


# ---- _hash_bucket -----------------------------------------------------------


class TestHashBucket:
    def test_deterministic(self) -> None:
        b1 = _hash_bucket("task-123")
        b2 = _hash_bucket("task-123")
        assert b1 == b2

    def test_different_tasks_different_buckets(self) -> None:
        b1 = _hash_bucket("task-a")
        b2 = _hash_bucket("task-b")
        assert isinstance(b1, int)
        assert isinstance(b2, int)

    def test_bucket_in_range(self) -> None:
        for i in range(100):
            b = _hash_bucket(f"task-{i}")
            assert 0 <= b < 100

    def test_seed_changes_output(self) -> None:
        b1 = _hash_bucket("task-x", seed=1)
        b2 = _hash_bucket("task-x", seed=2)
        assert b1 != b2

    def test_none_seed_is_deterministic(self) -> None:
        b1 = _hash_bucket("task-n", seed=None)
        b2 = _hash_bucket("task-n")
        assert b1 == b2

    def test_empty_string(self) -> None:
        b = _hash_bucket("")
        assert 0 <= b < 100

    def test_unicode_task_id(self) -> None:
        b = _hash_bucket("tâche-é2é")
        assert 0 <= b < 100

    def test_seed_zero(self) -> None:
        b = _hash_bucket("task-z", seed=0)
        assert 0 <= b < 100

    def test_seed_negative(self) -> None:
        b1 = _hash_bucket("task-neg", seed=-1)
        b2 = _hash_bucket("task-neg", seed=-42)
        assert isinstance(b1, int)
        assert isinstance(b2, int)


# ---- ZDDRollout.authorize ---------------------------------------------------


class TestAuthorize:
    def test_shadow_mode_never_enforces(self) -> None:
        task = _task()
        policy = _make_policy(task=task)
        rollout = ZDDRollout()
        decision = rollout.authorize(policy, task, _identity(), [_evidence()])
        assert decision.action is DispatchAction.ESCALATE
        assert "shadow_not_enforcing" in decision.reason

    def test_full_mode_always_enforces(self) -> None:
        task = _task()
        policy = _make_policy(task=task)
        rollout = ZDDRollout(stage=RolloutStage.FULL)
        decision = rollout.authorize(policy, task, _identity(), [_evidence()])
        assert decision.action is DispatchAction.LOCAL
        assert decision.reason == "capability_proven"

    def test_canary_50_enforces_on_low_bucket(self) -> None:
        task = _task(task_id="task-low-bucket")
        while _hash_bucket(task.task_id) >= 50:
            task = _task(task_id=f"task-low-bucket-{_hash_bucket(task.task_id)}")
            if _hash_bucket(task.task_id) >= 50:
                task = _task(task_id=f"try-{_hash_bucket(task.task_id)}")
        policy = _make_policy(task=task)
        rollout = ZDDRollout(stage=RolloutStage.CANARY_50)
        decision = rollout.authorize(policy, task, _identity(), [_evidence()])
        if _hash_bucket(task.task_id) < 50:
            assert decision.action is DispatchAction.LOCAL

    def test_rollback_never_enforces(self) -> None:
        task = _task()
        policy = _make_policy(task=task)
        rollout = ZDDRollout(stage=RolloutStage.ROLLBACK)
        decision = rollout.authorize(policy, task, _identity(), [_evidence()])
        assert decision.action is DispatchAction.ESCALATE
        assert "rollback_not_enforcing" in decision.reason

    def test_records_observation(self) -> None:
        task = _task()
        policy = _make_policy(task=task)
        rollout = ZDDRollout()
        rollout.authorize(policy, task, _identity(), [_evidence()])
        assert len(rollout.observations) == 1
        obs = rollout.observations[0]
        assert obs["stage"] == "shadow"
        assert obs["task_id"] == task.task_id
        assert obs["underlying_approved"] is True
        assert obs["enforced"] is False

    def test_observation_records_underlying_reason(self) -> None:
        task = _task()
        policy = _make_policy(task=task)
        rollout = ZDDRollout(stage=RolloutStage.FULL)
        rollout.authorize(policy, task, _identity(), [_evidence()])
        assert rollout.observations[0]["underlying_reason"] == "capability_proven"

    def test_escalated_underlying_recorded_in_observation(self) -> None:
        task = _task()
        policy = _make_policy(approved=False, task=task)
        rollout = ZDDRollout(stage=RolloutStage.FULL)
        rollout.authorize(policy, task, _identity(), [_evidence()])
        obs = rollout.observations[0]
        assert obs["underlying_approved"] is False

    def test_multiple_authorizations_appended(self) -> None:
        task1 = _task(task_id="t-1")
        task2 = _task(task_id="t-2")
        policy = _make_policy(task=task1)
        rollout = ZDDRollout()
        rollout.authorize(policy, task1, _identity(), [_evidence()])
        rollout.authorize(policy, task2, _identity(), [_evidence()])
        assert len(rollout.observations) == 2

    def test_observation_has_model_profile_id(self) -> None:
        task = _task()
        identity = _identity("specific-model")
        policy = _make_policy(task=task)
        rollout = ZDDRollout()
        rollout.authorize(policy, task, identity, [_evidence()])
        assert rollout.observations[0]["model_profile_id"] == "specific-model"


# ---- ZDDRollout.advance -----------------------------------------------------


class TestAdvance:
    def test_advance_shadow_to_canary_1(self) -> None:
        rollout = ZDDRollout()
        rollout.advance()
        assert rollout.stage is RolloutStage.CANARY_1

    def test_advance_full_progression(self) -> None:
        rollout = ZDDRollout()
        expected = [
            RolloutStage.CANARY_1,
            RolloutStage.CANARY_10,
            RolloutStage.CANARY_50,
            RolloutStage.FULL,
        ]
        for exp in expected:
            rollout.advance()
            assert rollout.stage is exp

    def test_advance_beyond_full_is_noop(self) -> None:
        rollout = ZDDRollout(stage=RolloutStage.FULL)
        rollout.advance()
        assert rollout.stage is RolloutStage.FULL

    def test_advance_to_specific_target(self) -> None:
        rollout = ZDDRollout()
        rollout.advance(target=RolloutStage.CANARY_50)
        assert rollout.stage is RolloutStage.CANARY_50

    def test_advance_to_current_stage_is_noop(self) -> None:
        rollout = ZDDRollout(stage=RolloutStage.CANARY_10)
        rollout.advance(target=RolloutStage.CANARY_10)
        assert rollout.stage is RolloutStage.CANARY_10

    def test_advance_backward_raises_value_error(self) -> None:
        rollout = ZDDRollout(stage=RolloutStage.CANARY_50)
        with pytest.raises(ValueError, match="Cannot advance backward"):
            rollout.advance(target=RolloutStage.CANARY_1)

    def test_advance_from_rollback_goes_to_shadow(self) -> None:
        rollout = ZDDRollout(stage=RolloutStage.ROLLBACK)
        rollout.advance()
        assert rollout.stage is RolloutStage.SHADOW

    def test_advance_from_rollback_with_target_canary_50(self) -> None:
        rollout = ZDDRollout(stage=RolloutStage.ROLLBACK)
        rollout.advance(target=RolloutStage.CANARY_50)
        assert rollout.stage is RolloutStage.CANARY_50

    def test_advance_from_rollback_to_shadow_is_noop_index(self) -> None:
        rollout = ZDDRollout(stage=RolloutStage.ROLLBACK)
        rollout.advance(target=RolloutStage.SHADOW)
        assert rollout.stage is RolloutStage.SHADOW


# ---- ZDDRollout.rollback ----------------------------------------------------


class TestRollback:
    def test_rollback_from_any_stage(self) -> None:
        for stage in [RolloutStage.SHADOW, RolloutStage.CANARY_1, RolloutStage.CANARY_50, RolloutStage.FULL]:
            rollout = ZDDRollout(stage=stage)
            rollout.rollback()
            assert rollout.stage is RolloutStage.ROLLBACK

    def test_rollback_is_idempotent(self) -> None:
        rollout = ZDDRollout(stage=RolloutStage.ROLLBACK)
        rollout.rollback()
        assert rollout.stage is RolloutStage.ROLLBACK

    def test_rollback_disables_enforcement(self) -> None:
        task = _task()
        policy = _make_policy(task=task)
        rollout = ZDDRollout(stage=RolloutStage.FULL)
        rollout.rollback()
        decision = rollout.authorize(policy, task, _identity(), [_evidence()])
        assert decision.action is DispatchAction.ESCALATE


# ---- ZDDRollout.observations ------------------------------------------------


class TestObservations:
    def test_observations_empty_initially(self) -> None:
        assert ZDDRollout().observations == []

    def test_observations_are_a_copy(self) -> None:
        rollout = ZDDRollout()
        obs = rollout.observations
        obs.append({"mutated": True})
        assert len(rollout.observations) == 0

    def test_clear_observations(self) -> None:
        task = _task()
        policy = _make_policy(task=task)
        rollout = ZDDRollout()
        rollout.authorize(policy, task, _identity(), [_evidence()])
        assert len(rollout.observations) == 1
        rollout.clear_observations()
        assert len(rollout.observations) == 0

    def test_clear_observations_then_summary_is_zero(self) -> None:
        task = _task()
        policy = _make_policy(task=task)
        rollout = ZDDRollout()
        rollout.authorize(policy, task, _identity(), [_evidence()])
        rollout.clear_observations()
        s = rollout.summary()
        assert s["total_observations"] == 0


# ---- ZDDRollout.summary -----------------------------------------------------


class TestSummary:
    def test_summary_empty_rollout(self) -> None:
        s = ZDDRollout().summary()
        assert s["stage"] == "shadow"
        assert s["total_observations"] == 0
        assert s["enforced_count"] == 0
        assert s["escalated_count"] == 0

    def test_summary_full_with_approval(self) -> None:
        task = _task()
        policy = _make_policy(task=task)
        rollout = ZDDRollout(stage=RolloutStage.FULL)
        rollout.authorize(policy, task, _identity(), [_evidence()])
        s = rollout.summary()
        assert s["stage"] == "full"
        assert s["total_observations"] == 1
        assert s["enforced_count"] == 1
        assert s["escalated_count"] == 0

    def test_summary_shadow_never_enforced(self) -> None:
        task = _task()
        policy = _make_policy(task=task)
        rollout = ZDDRollout()
        for _ in range(5):
            rollout.authorize(policy, task, _identity(), [_evidence()])
        s = rollout.summary()
        assert s["total_observations"] == 5
        assert s["enforced_count"] == 0
        assert s["escalated_count"] == 5

    def test_summary_stage_reflects_current(self) -> None:
        rollout = ZDDRollout()
        rollout.advance()
        s = rollout.summary()
        assert s["stage"] == "canary_1"


# ---- ZDDRollout._should_enforce ---------------------------------------------


class TestShouldEnforce:
    def test_shadow_never_enforces(self) -> None:
        rollout = ZDDRollout()
        for i in range(100):
            assert rollout._should_enforce(_task(task_id=f"t-{i}")) is False

    def test_full_always_enforces(self) -> None:
        rollout = ZDDRollout(stage=RolloutStage.FULL)
        for i in range(100):
            assert rollout._should_enforce(_task(task_id=f"t-{i}")) is True

    def test_rollback_never_enforces(self) -> None:
        rollout = ZDDRollout(stage=RolloutStage.ROLLBACK)
        for i in range(100):
            assert rollout._should_enforce(_task(task_id=f"t-{i}")) is False

    def test_canary_1_enforces_roughly_1_percent(self) -> None:
        rollout = ZDDRollout(stage=RolloutStage.CANARY_1)
        enforced = sum(1 for i in range(500) if rollout._should_enforce(_task(task_id=f"ct-{i}")))
        assert 0 < enforced < 25

    def test_canary_50_enforces_roughly_50_percent(self) -> None:
        rollout = ZDDRollout(stage=RolloutStage.CANARY_50)
        enforced = sum(1 for i in range(500) if rollout._should_enforce(_task(task_id=f"c50-{i}")))
        assert 200 < enforced < 300

    def test_seed_makes_enforcement_reproducible(self) -> None:
        r1 = ZDDRollout(stage=RolloutStage.CANARY_10, seed=42)
        r2 = ZDDRollout(stage=RolloutStage.CANARY_10, seed=42)
        ids = [f"seed-t-{i}" for i in range(200)]
        e1 = [r1._should_enforce(_task(task_id=tid)) for tid in ids]
        e2 = [r2._should_enforce(_task(task_id=tid)) for tid in ids]
        assert e1 == e2

    def test_different_seeds_different_enforcement(self) -> None:
        r1 = ZDDRollout(stage=RolloutStage.CANARY_10, seed=1)
        r2 = ZDDRollout(stage=RolloutStage.CANARY_10, seed=99)
        ids = [f"diff-seed-{i}" for i in range(200)]
        e1 = [r1._should_enforce(_task(task_id=tid)) for tid in ids]
        e2 = [r2._should_enforce(_task(task_id=tid)) for tid in ids]
        assert e1 != e2


# ---- ZDDRollout._stage_index ------------------------------------------------


class TestStageIndex:
    def test_shadow_is_zero(self) -> None:
        assert ZDDRollout()._stage_index() == 0

    def test_canary_50_is_three(self) -> None:
        assert ZDDRollout(stage=RolloutStage.CANARY_50)._stage_index() == 3

    def test_full_is_four(self) -> None:
        assert ZDDRollout(stage=RolloutStage.FULL)._stage_index() == 4

    def test_rollback_is_negative_one(self) -> None:
        assert ZDDRollout(stage=RolloutStage.ROLLBACK)._stage_index() == -1


# ---- ZDDRollout._index_of ---------------------------------------------------


class TestIndexOf:
    def test_rollback_returns_zero(self) -> None:
        assert ZDDRollout._index_of(RolloutStage.ROLLBACK) == 0

    def test_shadow_returns_zero(self) -> None:
        assert ZDDRollout._index_of(RolloutStage.SHADOW) == 0

    def test_canary_50_returns_three(self) -> None:
        assert ZDDRollout._index_of(RolloutStage.CANARY_50) == 3

    def test_full_returns_four(self) -> None:
        assert ZDDRollout._index_of(RolloutStage.FULL) == 4


# ---- ZDDRollout.seed --------------------------------------------------------


class TestSeed:
    def test_default_seed_is_none(self) -> None:
        assert ZDDRollout().seed is None

    def test_custom_seed(self) -> None:
        assert ZDDRollout(seed=12345).seed == 12345

    def test_seed_is_stored(self) -> None:
        rollout = ZDDRollout(seed=7)
        assert rollout.seed == 7
        rollout.advance()
        assert rollout.seed == 7
        rollout.rollback()
        assert rollout.seed == 7
