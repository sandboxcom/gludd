"""Tests for the FreeLLMAPI rollback workflow boundary."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from threading import Event, Thread
from time import monotonic
from typing import cast

import pytest

import general_ludd.models.freellmapi_rollback_workflow as rollback_module
from general_ludd.models.freellmapi_rollback_workflow import (
    FreeLLMAPIRollbackWorkflow,
    RollbackArtifact,
    RollbackDrainHeartbeat,
    RollbackDrainResult,
    RollbackGeneration,
    RollbackWorkflowError,
)


def _artifact(artifact_id: str, generation_id: int, kind: str = "bundle") -> RollbackArtifact:
    return RollbackArtifact(artifact_id=artifact_id, generation_id=generation_id, kind=kind)


def test_empty_workflow_has_no_active_or_previous_generation() -> None:
    workflow = FreeLLMAPIRollbackWorkflow()

    assert workflow.active_generation is None
    assert workflow.previous_generation is None
    assert workflow.protected_artifacts() == frozenset()


def test_promote_sets_first_generation_as_active() -> None:
    workflow = FreeLLMAPIRollbackWorkflow()
    artifacts = ("sha256:1111111111111111111111111111111111111111111111111111111111111111",)

    generation = workflow.promote(artifacts)

    assert generation.generation_id == 1
    assert generation.artifacts == artifacts
    assert workflow.active_generation == generation
    assert workflow.previous_generation is None


def test_promote_advances_active_and_preserves_previous_generation() -> None:
    workflow = FreeLLMAPIRollbackWorkflow()
    blue = workflow.promote(("sha256:1111" + "0" * 60,))
    green = workflow.promote(("sha256:2222" + "0" * 60,))

    assert green.generation_id == 2
    assert workflow.active_generation == green
    assert workflow.previous_generation == blue


def test_rollback_swaps_active_and_previous_generations() -> None:
    workflow = FreeLLMAPIRollbackWorkflow()
    blue = workflow.promote(("sha256:1111" + "0" * 60,))
    green = workflow.promote(("sha256:2222" + "0" * 60,))

    rolled = workflow.rollback()

    assert rolled == blue
    assert workflow.active_generation == blue
    assert workflow.previous_generation == green


def test_double_rollback_returns_to_original_active_generation() -> None:
    workflow = FreeLLMAPIRollbackWorkflow()
    blue = workflow.promote(("sha256:1111" + "0" * 60,))
    green = workflow.promote(("sha256:2222" + "0" * 60,))

    workflow.rollback()
    rolled_again = workflow.rollback()

    assert rolled_again == green
    assert workflow.active_generation == green
    assert workflow.previous_generation == blue


def test_rollback_without_previous_generation_fails() -> None:
    workflow = FreeLLMAPIRollbackWorkflow()

    with pytest.raises(RollbackWorkflowError):
        workflow.rollback()


def test_promote_with_empty_artifacts_fails() -> None:
    workflow = FreeLLMAPIRollbackWorkflow()

    with pytest.raises(RollbackWorkflowError):
        workflow.promote(())


def test_promote_requires_unique_artifact_ids() -> None:
    workflow = FreeLLMAPIRollbackWorkflow()
    artifact = "sha256:1111111111111111111111111111111111111111111111111111111111111111"

    with pytest.raises(RollbackWorkflowError):
        workflow.promote((artifact, artifact))


def test_promote_requires_sha256_artifact_ids() -> None:
    workflow = FreeLLMAPIRollbackWorkflow()

    with pytest.raises(RollbackWorkflowError, match="artifact id must be a sha256 uri"):
        workflow.promote(("not-a-digest",))


def test_protected_artifacts_cover_active_and_previous_generations() -> None:
    workflow = FreeLLMAPIRollbackWorkflow()
    blue_artifact = "sha256:1111111111111111111111111111111111111111111111111111111111111111"
    green_artifact = "sha256:2222222222222222222222222222222222222222222222222222222222222222"
    workflow.promote((blue_artifact,))
    workflow.promote((green_artifact,))

    protected = workflow.protected_artifacts()

    assert protected == {blue_artifact, green_artifact}


def test_active_artifact_is_protected() -> None:
    workflow = FreeLLMAPIRollbackWorkflow()
    artifact = "sha256:1111111111111111111111111111111111111111111111111111111111111111"
    workflow.promote((artifact,))

    assert workflow.is_artifact_protected(artifact) is True
    assert (
        workflow.is_artifact_protected("sha256:0000000000000000000000000000000000000000000000000000000000000000")
        is False
    )


def test_previous_generation_artifact_is_protected_after_rollback() -> None:
    workflow = FreeLLMAPIRollbackWorkflow()
    blue_artifact = "sha256:1111111111111111111111111111111111111111111111111111111111111111"
    green_artifact = "sha256:2222222222222222222222222222222222222222222222222222222222222222"
    workflow.promote((blue_artifact,))
    workflow.promote((green_artifact,))
    workflow.rollback()

    assert workflow.is_artifact_protected(blue_artifact) is True
    assert workflow.is_artifact_protected(green_artifact) is True


def test_older_generations_are_eligible_for_cleanup() -> None:
    workflow = FreeLLMAPIRollbackWorkflow()
    g1_artifact = "sha256:1111111111111111111111111111111111111111111111111111111111111111"
    g2_artifact = "sha256:2222222222222222222222222222222222222222222222222222222222222222"
    g3_artifact = "sha256:3333333333333333333333333333333333333333333333333333333333333333"
    g1 = workflow.promote((g1_artifact,))
    g2 = workflow.promote((g2_artifact,))
    g3 = workflow.promote((g3_artifact,))

    eligible = workflow.cleanup_eligible_generations()

    assert eligible == (g1,)
    assert g2 not in eligible
    assert g3 not in eligible


def test_cleanup_after_rollback_keeps_both_switched_generations() -> None:
    workflow = FreeLLMAPIRollbackWorkflow()
    g1_artifact = "sha256:1111111111111111111111111111111111111111111111111111111111111111"
    g2_artifact = "sha256:2222222222222222222222222222222222222222222222222222222222222222"
    g3_artifact = "sha256:3333333333333333333333333333333333333333333333333333333333333333"
    g1 = workflow.promote((g1_artifact,))
    g2 = workflow.promote((g2_artifact,))
    g3 = workflow.promote((g3_artifact,))
    workflow.rollback()

    eligible = workflow.cleanup_eligible_generations()

    assert g1 in eligible
    assert g2 not in eligible
    assert g3 not in eligible


def test_generation_records_promotion_timestamp() -> None:
    before = datetime.now(UTC)
    workflow = FreeLLMAPIRollbackWorkflow()

    generation = workflow.promote(("sha256:1111111111111111111111111111111111111111111111111111111111111111",))

    assert before <= generation.promoted_at <= datetime.now(UTC)


def test_rollback_generation_is_immutable() -> None:
    workflow = FreeLLMAPIRollbackWorkflow()
    generation = workflow.promote(("sha256:1111111111111111111111111111111111111111111111111111111111111111",))

    assert isinstance(generation, RollbackGeneration)
    with pytest.raises(AttributeError):
        generation.artifacts = ()  # type: ignore[misc]


def test_inflight_lease_stays_on_green_while_rollback_routes_new_work_to_blue() -> None:
    workflow = FreeLLMAPIRollbackWorkflow(max_active_leases=2)
    blue = workflow.promote(("sha256:" + "1" * 64,))
    green = workflow.promote(("sha256:" + "2" * 64,))
    green_lease = "sha256:" + "a" * 64
    blue_lease = "sha256:" + "b" * 64

    assert workflow.acquire_lease(green_lease) == green
    assert workflow.rollback() == blue
    assert workflow.acquire_lease(blue_lease) == blue

    assert workflow.generation_for_lease(green_lease) == green
    assert workflow.generation_for_lease(blue_lease) == blue
    assert workflow.active_lease_count == 2


def test_leased_old_generation_is_protected_until_exact_release() -> None:
    workflow = FreeLLMAPIRollbackWorkflow()
    oldest_artifact = "sha256:" + "1" * 64
    oldest = workflow.promote((oldest_artifact,))
    lease_id = "sha256:" + "a" * 64
    workflow.acquire_lease(lease_id)
    workflow.promote(("sha256:" + "2" * 64,))
    workflow.promote(("sha256:" + "3" * 64,))

    assert oldest not in workflow.cleanup_eligible_generations()
    assert workflow.is_artifact_protected(oldest_artifact) is True

    assert workflow.release_lease(lease_id) == oldest
    assert workflow.active_lease_count == 0
    assert oldest in workflow.cleanup_eligible_generations()
    assert workflow.is_artifact_protected(oldest_artifact) is False


def test_lease_registry_is_bounded_and_duplicate_safe() -> None:
    workflow = FreeLLMAPIRollbackWorkflow(max_active_leases=1)
    workflow.promote(("sha256:" + "1" * 64,))
    first = "sha256:" + "a" * 64
    workflow.acquire_lease(first)

    with pytest.raises(RollbackWorkflowError, match="lease identity already active"):
        workflow.acquire_lease(first)
    with pytest.raises(RollbackWorkflowError, match="lease capacity exhausted"):
        workflow.acquire_lease("sha256:" + "b" * 64)

    assert workflow.active_lease_count == 1


@pytest.mark.parametrize(
    "lease_id",
    ["", "task-content", "sha256:" + "A" * 64, "sha256:" + "a" * 63],
)
def test_lease_identity_is_content_free_and_fail_closed(lease_id: str) -> None:
    workflow = FreeLLMAPIRollbackWorkflow()
    workflow.promote(("sha256:" + "1" * 64,))

    with pytest.raises(RollbackWorkflowError, match="lease identity must be a sha256 uri"):
        workflow.acquire_lease(lease_id)


def test_lease_operations_fail_closed_without_exact_ownership() -> None:
    workflow = FreeLLMAPIRollbackWorkflow()
    lease_id = "sha256:" + "a" * 64

    with pytest.raises(RollbackWorkflowError, match="no active generation"):
        workflow.acquire_lease(lease_id)
    with pytest.raises(RollbackWorkflowError, match="lease identity is not active"):
        workflow.generation_for_lease(lease_id)
    with pytest.raises(RollbackWorkflowError, match="lease identity is not active"):
        workflow.release_lease(lease_id)
    with pytest.raises(RollbackWorkflowError, match="lease identity must be a sha256 uri"):
        workflow.generation_for_lease("not-a-digest")
    with pytest.raises(RollbackWorkflowError, match="lease identity must be a sha256 uri"):
        workflow.release_lease("not-a-digest")


def test_lease_context_releases_exact_ownership_after_failure() -> None:
    workflow = FreeLLMAPIRollbackWorkflow()
    generation = workflow.promote(("sha256:" + "1" * 64,))
    lease_id = "sha256:" + "a" * 64

    with (
        pytest.raises(RuntimeError, match="work failed"),
        workflow.lease(lease_id) as leased_generation,
    ):
        assert leased_generation == generation
        assert workflow.active_lease_count == 1
        raise RuntimeError("work failed")

    assert workflow.active_lease_count == 0
    with pytest.raises(RollbackWorkflowError, match="lease identity is not active"):
        workflow.generation_for_lease(lease_id)


@pytest.mark.parametrize("limit", [True, 0, -1])
def test_lease_limit_must_be_a_positive_integer(limit: int) -> None:
    with pytest.raises(RollbackWorkflowError, match="max active leases must be positive"):
        FreeLLMAPIRollbackWorkflow(max_active_leases=limit)


def test_concurrent_rollback_switches_atomically_and_drains_only_pre_switch_leases() -> None:
    workflow = FreeLLMAPIRollbackWorkflow(max_active_leases=2)
    blue = workflow.promote(("sha256:" + "1" * 64,))
    green = workflow.promote(("sha256:" + "2" * 64,))
    green_lease = "sha256:" + "a" * 64
    blue_lease = "sha256:" + "b" * 64
    assert workflow.acquire_lease(green_lease) == green
    switched = Event()
    results: list[RollbackDrainResult] = []

    def heartbeat(event: RollbackDrainHeartbeat) -> None:
        assert event.remaining_leases == 1
        switched.set()

    thread = Thread(
        target=lambda: results.append(
            workflow.rollback_and_drain(
                1.0,
                heartbeat=heartbeat,
                heartbeat_interval_seconds=0.1,
            )
        ),
        name="gludd-s83163-rollback-drain",
    )
    thread.start()
    assert switched.wait(timeout=2.0)

    assert workflow.acquire_lease(blue_lease) == blue
    assert workflow.generation_for_lease(green_lease) == green
    assert workflow.generation_for_lease(blue_lease) == blue
    assert workflow.release_lease(green_lease) == green
    thread.join(timeout=2.0)

    assert thread.is_alive() is False
    assert results == [
        RollbackDrainResult(
            active_generation_id=blue.generation_id,
            draining_generation_id=green.generation_id,
            drained=True,
            remaining_leases=0,
            switched=True,
        )
    ]
    assert workflow.release_lease(blue_lease) == blue


def test_rollback_drain_uses_short_monotonic_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real_monotonic = monotonic
    monotonic_calls: list[float] = []

    def tracked_monotonic() -> float:
        value = real_monotonic()
        monotonic_calls.append(value)
        return value

    monkeypatch.setattr(rollback_module, "monotonic", tracked_monotonic)
    workflow = FreeLLMAPIRollbackWorkflow()
    workflow.promote(("sha256:" + "1" * 64,))
    green = workflow.promote(("sha256:" + "2" * 64,))
    workflow.acquire_lease("sha256:" + "a" * 64)

    started = real_monotonic()
    result = workflow.rollback_and_drain(0.01, heartbeat_interval_seconds=0.005)
    elapsed = real_monotonic() - started

    assert result.draining_generation_id == green.generation_id
    assert result.drained is False
    assert result.remaining_leases == 1
    assert result.runtime_admitted is False
    assert result.candidate_decision == "HOLD"
    assert len(monotonic_calls) >= 2
    assert 0.005 <= elapsed < 0.5


def test_timeout_keeps_green_protected_until_exact_release_and_retry() -> None:
    workflow = FreeLLMAPIRollbackWorkflow()
    blue = workflow.promote(("sha256:" + "1" * 64,))
    green_artifact = "sha256:" + "2" * 64
    green = workflow.promote((green_artifact,))
    green_lease = "sha256:" + "a" * 64
    workflow.acquire_lease(green_lease)

    timed_out = workflow.rollback_and_drain(0.005, heartbeat_interval_seconds=0.002)
    orange = workflow.promote(("sha256:" + "3" * 64,))

    assert timed_out.drained is False
    assert workflow.active_generation == orange
    assert workflow.is_artifact_protected(green_artifact) is True
    assert green not in workflow.cleanup_eligible_generations()
    with pytest.raises(RollbackWorkflowError, match="rollback drain is still pending"):
        workflow.rollback()

    assert workflow.release_lease(green_lease) == green
    assert workflow.is_artifact_protected(green_artifact) is True
    retried = workflow.rollback_and_drain(0.1)

    assert retried.active_generation_id == orange.generation_id
    assert retried.draining_generation_id == green.generation_id
    assert retried.drained is True
    assert retried.switched is False
    assert workflow.active_generation == orange
    assert green in workflow.cleanup_eligible_generations()
    assert workflow.is_artifact_protected(green_artifact) is False
    assert workflow.is_artifact_protected(blue.artifacts[0]) is True


def test_only_exact_lease_release_wakes_a_pending_drain() -> None:
    workflow = FreeLLMAPIRollbackWorkflow()
    workflow.promote(("sha256:" + "1" * 64,))
    workflow.promote(("sha256:" + "2" * 64,))
    lease_id = "sha256:" + "a" * 64
    workflow.acquire_lease(lease_id)
    waiting = Event()
    results: list[RollbackDrainResult] = []
    thread = Thread(
        target=lambda: results.append(
            workflow.rollback_and_drain(
                1.0,
                heartbeat=lambda _heartbeat: waiting.set(),
                heartbeat_interval_seconds=0.1,
            )
        ),
        name="gludd-s83163-exact-release",
    )
    thread.start()
    assert waiting.wait(timeout=2.0)

    with pytest.raises(RollbackWorkflowError, match="lease identity is not active"):
        workflow.release_lease("sha256:" + "b" * 64)
    assert thread.is_alive() is True

    workflow.release_lease(lease_id)
    thread.join(timeout=2.0)
    assert thread.is_alive() is False
    assert results[0].drained is True


@pytest.mark.parametrize("timeout_seconds", [True, 0.0, -0.1, 3600.1, float("inf"), float("nan")])
def test_rollback_drain_timeout_is_positive_and_bounded(timeout_seconds: float) -> None:
    workflow = FreeLLMAPIRollbackWorkflow()
    workflow.promote(("sha256:" + "1" * 64,))
    workflow.promote(("sha256:" + "2" * 64,))

    with pytest.raises(
        RollbackWorkflowError,
        match="rollback drain timeout must be greater than zero and at most 3600 seconds",
    ):
        workflow.rollback_and_drain(timeout_seconds)


def test_rollback_drain_timeout_and_heartbeat_upper_bounds_are_inclusive() -> None:
    workflow = FreeLLMAPIRollbackWorkflow()
    workflow.promote(("sha256:" + "1" * 64,))
    workflow.promote(("sha256:" + "2" * 64,))

    result = workflow.rollback_and_drain(
        3600.0,
        heartbeat_interval_seconds=30.0,
    )

    assert result.drained is True
    assert result.runtime_admitted is False
    assert result.candidate_decision == "HOLD"


def test_rollback_drain_rejects_non_callable_heartbeat_before_switch() -> None:
    workflow = FreeLLMAPIRollbackWorkflow()
    blue = workflow.promote(("sha256:" + "1" * 64,))
    green = workflow.promote(("sha256:" + "2" * 64,))
    not_callable = cast(Callable[[RollbackDrainHeartbeat], None], object())

    with pytest.raises(RollbackWorkflowError, match="heartbeat must be callable"):
        workflow.rollback_and_drain(1.0, heartbeat=not_callable)

    assert workflow.active_generation == green
    assert workflow.previous_generation == blue


@pytest.mark.parametrize("heartbeat_seconds", [True, 0.0, -0.1, 30.1, float("inf"), float("nan")])
def test_rollback_drain_heartbeat_interval_is_positive_and_at_most_30_seconds(
    heartbeat_seconds: float,
) -> None:
    workflow = FreeLLMAPIRollbackWorkflow()
    workflow.promote(("sha256:" + "1" * 64,))
    workflow.promote(("sha256:" + "2" * 64,))

    with pytest.raises(
        RollbackWorkflowError,
        match="heartbeat interval must be greater than zero and at most 30 seconds",
    ):
        workflow.rollback_and_drain(
            1.0,
            heartbeat_interval_seconds=heartbeat_seconds,
        )


def test_rollback_drain_heartbeat_contains_no_lease_or_artifact_identity() -> None:
    workflow = FreeLLMAPIRollbackWorkflow()
    workflow.promote(("sha256:" + "1" * 64,))
    workflow.promote(("sha256:" + "2" * 64,))
    workflow.acquire_lease("sha256:" + "a" * 64)
    heartbeats: list[RollbackDrainHeartbeat] = []

    workflow.rollback_and_drain(
        0.005,
        heartbeat=heartbeats.append,
        heartbeat_interval_seconds=0.002,
    )

    assert heartbeats
    assert all(heartbeat.remaining_leases == 1 for heartbeat in heartbeats)
    assert all(heartbeat.elapsed_seconds >= 0.0 for heartbeat in heartbeats)
    assert set(RollbackDrainHeartbeat.__dataclass_fields__) == {
        "active_generation_id",
        "draining_generation_id",
        "remaining_leases",
        "elapsed_seconds",
    }
