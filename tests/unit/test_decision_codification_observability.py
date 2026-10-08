"""Tests for durable, privacy-safe decision-reuse observability."""

from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import pytest
from pydantic import ValidationError

from general_ludd.decision_codification.artifact_store import DecisionArtifactStore
from general_ludd.decision_codification.durable import DurableGenerationStore
from general_ludd.decision_codification.observability import (
    MAX_OBSERVABILITY_RECEIPT_BYTES,
    DecisionAbstentionCount,
    DecisionObservationStore,
    DecisionResolutionPath,
    DecisionReuseAggregate,
    DecisionReuseKindSummary,
    DecisionReuseObservability,
    DecisionReuseObservabilityError,
    DecisionReuseObservation,
    DecisionReuseStatusReceipt,
    DecisionVersionSource,
)
from general_ludd.decision_codification.rollout import GenerationPointer
from general_ludd.decision_codification.schema import (
    DecisionKind,
    FallbackReason,
    RolloutStage,
)

_POLICY = "sha256:" + "e" * 64
_CANDIDATE_A = "sha256:" + "a" * 64
_CANDIDATE_B = "sha256:" + "b" * 64
_NOW = datetime(2026, 10, 7, 15, tzinfo=UTC)


class _NoVersions:
    def current(
        self,
        project_id: str,
        decision_kind: DecisionKind,
    ) -> GenerationPointer | None:
        return None

    def verified_generation(self, pointer: GenerationPointer) -> object:
        raise AssertionError("an absent pointer must not be verified")

    def is_drift_held(self, candidate_digest: str) -> bool:
        raise AssertionError("an absent pointer cannot be drift-held")


def _observer(
    store: DurableGenerationStore,
    *,
    project_id: str = "project-alpha",
    policy_digest: str = _POLICY,
) -> DecisionReuseObservability:
    return DecisionReuseObservability(
        store,
        cast(DecisionVersionSource, _NoVersions()),
        DecisionArtifactStore(
            str(store.path.parent / "observability-artifacts"),
            key=b"observability-artifact-key",
        ),
        project_id=project_id,
        policy_digest=policy_digest,
        clock=lambda: _NOW,
    )


def _hit(observer: DecisionReuseObservability, *, candidate: str = _CANDIDATE_A) -> bool:
    return observer.record_resolution(
        project_id="project-alpha",
        policy_digest=_POLICY,
        decision_kind=DecisionKind.REVIEW,
        path=DecisionResolutionPath.EXACT_RULE,
        abstention_reason=None,
        candidate_digest=candidate,
        rollout_stage=RolloutStage.ACTIVE,
        latency_ns=1_500_000,
    )


def _fallback(
    observer: DecisionReuseObservability,
    reason: FallbackReason,
    *,
    candidate: str | None = None,
) -> bool:
    return observer.record_resolution(
        project_id="project-alpha",
        policy_digest=_POLICY,
        decision_kind=DecisionKind.REVIEW,
        path=DecisionResolutionPath.AGENT_FALLBACK,
        abstention_reason=reason,
        candidate_digest=candidate,
        rollout_stage=None,
        latency_ns=2_750_000,
    )


def _empty_aggregate_payload() -> dict[str, object]:
    return {
        "decision_kind": DecisionKind.REVIEW,
        "sequence": 0,
        "exact_rule_hits": 0,
        "typed_abstentions": 0,
        "fallback_calls": 0,
        "avoided_agent_calls": 0,
        "latency_observations": 0,
        "latency_total_us": 0,
        "latency_max_us": 0,
        "rule_version_changes": 0,
        "drift_events": 0,
        "abstentions": (),
        "last_candidate_digest": None,
        "last_observed_at": None,
    }


def test_shared_store_aggregates_workers_into_one_bounded_immutable_receipt(
    tmp_path: Path,
) -> None:
    state_path = tmp_path / "decision-state.sqlite3"
    first = _observer(DurableGenerationStore(state_path))
    second = _observer(DurableGenerationStore(state_path))

    def record_batch(observer: DecisionReuseObservability) -> None:
        for _ in range(25):
            assert _hit(observer)

    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(record_batch, (first, second)))
    assert _fallback(first, FallbackReason.SCOPE_MISS, candidate=_CANDIDATE_A)
    assert _fallback(second, FallbackReason.POLICY_CHANGED, candidate=_CANDIDATE_B)

    receipt = first.status_receipt(
        project_id="project-alpha",
        policy_digest=_POLICY,
    )
    review = next(
        summary
        for summary in receipt.summaries
        if summary.decision_kind is DecisionKind.REVIEW
    )
    assert review.sequence == 52
    assert review.exact_rule_hits == 50
    assert review.avoided_agent_calls == 50
    assert review.typed_abstentions == 2
    assert review.fallback_calls == 2
    assert review.latency_observations == 52
    assert review.latency_total_us == 80_500
    assert review.latency_max_us == 2_750
    assert review.rule_version_changes == 1
    assert review.drift_events == 1
    assert [(item.reason, item.count) for item in review.abstentions] == [
        (FallbackReason.POLICY_CHANGED, 1),
        (FallbackReason.SCOPE_MISS, 1),
    ]
    assert len(receipt.model_dump_json().encode("utf-8")) <= MAX_OBSERVABILITY_RECEIPT_BYTES
    assert receipt.authentication_tag.startswith("hmac-sha256:")
    assert receipt == first.status_receipt(
        project_id="project-alpha",
        policy_digest=_POLICY,
    )

    tampered = receipt.model_dump(mode="python")
    tampered["summaries"][0]["exact_rule_hits"] += 1  # type: ignore[index]
    with pytest.raises(ValidationError):
        DecisionReuseStatusReceipt.model_validate(tampered)


def test_scope_is_single_bound_and_raw_or_unbounded_values_are_not_accepted(
    tmp_path: Path,
) -> None:
    store = DurableGenerationStore(tmp_path / "decision-state.sqlite3")
    observer = _observer(store)

    assert not observer.record_resolution(
        project_id="other-project",
        policy_digest=_POLICY,
        decision_kind=DecisionKind.REVIEW,
        path=DecisionResolutionPath.EXACT_RULE,
        abstention_reason=None,
        candidate_digest=_CANDIDATE_A,
        rollout_stage=RolloutStage.ACTIVE,
        latency_ns=1,
    )
    assert not observer.record_resolution(
        project_id="project-alpha",
        policy_digest=_POLICY,
        decision_kind=DecisionKind.REVIEW,
        path=DecisionResolutionPath.AGENT_FALLBACK,
        abstention_reason=cast(FallbackReason, "prompt=do-not-store"),
        candidate_digest=None,
        rollout_stage=None,
        latency_ns=1,
    )
    assert not observer.record_resolution(
        project_id="project-alpha",
        policy_digest=_POLICY,
        decision_kind=DecisionKind.REVIEW,
        path=DecisionResolutionPath.EXACT_RULE,
        abstention_reason=None,
        candidate_digest=_CANDIDATE_A,
        rollout_stage=RolloutStage.ACTIVE,
        latency_ns=10**30,
    )
    receipt = observer.status_receipt(
        project_id="project-alpha",
        policy_digest=_POLICY,
    )
    assert receipt.total_observations == 0
    assert "prompt=do-not-store" not in receipt.model_dump_json()

    with pytest.raises(DecisionReuseObservabilityError):
        _observer(store, project_id="other-project")
    with pytest.raises(DecisionReuseObservabilityError):
        observer.status_receipt(
            project_id="project-alpha",
            policy_digest="sha256:" + "d" * 64,
        )


def test_write_failure_is_no_throw_and_never_fabricates_a_receipt(
    tmp_path: Path,
) -> None:
    class _FailingStore:
        def bind_decision_observability(self, project_id: str, policy_digest: str) -> None:
            return None

        def record_decision_observation(self, observation: object) -> None:
            raise RuntimeError("private database failure")

        def decision_observability_aggregates(
            self,
            project_id: str,
            policy_digest: str,
        ) -> tuple[object, ...]:
            raise RuntimeError("private database failure")

    observer = DecisionReuseObservability(
        cast(DecisionObservationStore, _FailingStore()),
        cast(DecisionVersionSource, _NoVersions()),
        DecisionArtifactStore(
            str(tmp_path / "artifacts"),
            key=b"observability-artifact-key",
        ),
        project_id="project-alpha",
        policy_digest=_POLICY,
        clock=lambda: _NOW,
    )

    assert not _hit(observer)
    with pytest.raises(DecisionReuseObservabilityError) as error:
        observer.status_receipt(
            project_id="project-alpha",
            policy_digest=_POLICY,
        )
    assert "private database failure" not in str(error.value)


def test_observation_and_aggregate_shapes_are_strict_and_fail_closed() -> None:
    observation = {
        "project_id": "project-alpha",
        "policy_digest": _POLICY,
        "decision_kind": DecisionKind.REVIEW,
        "path": DecisionResolutionPath.EXACT_RULE,
        "abstention_reason": None,
        "candidate_digest": _CANDIDATE_A,
        "rollout_stage": RolloutStage.ACTIVE,
        "latency_us": 1,
        "observed_at": _NOW,
    }
    with pytest.raises(ValidationError):
        DecisionReuseObservation.model_validate(
            observation | {"observed_at": _NOW.replace(tzinfo=None)},
            strict=True,
        )
    with pytest.raises(ValidationError):
        DecisionReuseObservation.model_validate(
            observation | {"candidate_digest": None},
            strict=True,
        )
    with pytest.raises(ValidationError):
        DecisionReuseObservation.model_validate(
            observation
            | {
                "path": DecisionResolutionPath.AGENT_FALLBACK,
                "candidate_digest": None,
                "rollout_stage": None,
            },
            strict=True,
        )

    unsorted = (
        DecisionAbstentionCount(reason=FallbackReason.SCOPE_MISS, count=1),
        DecisionAbstentionCount(reason=FallbackReason.POLICY_CHANGED, count=1),
    )
    with pytest.raises(ValidationError):
        DecisionReuseAggregate.model_validate(
            _empty_aggregate_payload()
            | {
                "sequence": 2,
                "typed_abstentions": 2,
                "fallback_calls": 2,
                "latency_observations": 2,
                "latency_total_us": 2,
                "latency_max_us": 1,
                "abstentions": unsorted,
            },
            strict=True,
        )
    with pytest.raises(ValidationError):
        DecisionReuseAggregate.model_validate(
            _empty_aggregate_payload()
            | {"last_observed_at": _NOW.replace(tzinfo=None)},
            strict=True,
        )


def test_status_models_reject_partial_versions_and_receipt_tampering(
    tmp_path: Path,
) -> None:
    summary = _empty_aggregate_payload() | {
        "current_candidate_digest": _CANDIDATE_A,
        "current_receipt_digest": None,
        "current_stage": None,
        "current_epoch": None,
        "current_drift_held": False,
    }
    with pytest.raises(ValidationError):
        DecisionReuseKindSummary.model_validate(summary, strict=True)
    with pytest.raises(ValidationError):
        DecisionReuseKindSummary.model_validate(
            summary
            | {
                "current_candidate_digest": None,
                "current_drift_held": True,
            },
            strict=True,
        )

    receipt = _observer(
        DurableGenerationStore(tmp_path / "decision-state.sqlite3")
    ).status_receipt(project_id="project-alpha", policy_digest=_POLICY)
    payload = receipt.model_dump(mode="python", by_alias=True)
    with pytest.raises(ValidationError):
        DecisionReuseStatusReceipt.model_validate(
            payload | {"summaries": tuple(reversed(receipt.summaries))},
            strict=True,
        )
    with pytest.raises(ValidationError):
        DecisionReuseStatusReceipt.model_validate(
            payload | {"total_observations": 1},
            strict=True,
        )
    with pytest.raises(ValidationError):
        DecisionReuseStatusReceipt.model_validate(
            payload | {"receipt_digest": _CANDIDATE_B},
            strict=True,
        )


def test_orphaned_durable_abstention_row_fails_status_closed(tmp_path: Path) -> None:
    state_path = tmp_path / "decision-state.sqlite3"
    observer = _observer(DurableGenerationStore(state_path))
    with closing(sqlite3.connect(state_path)) as connection:
        connection.execute(
            """
            INSERT INTO decision_observability_abstentions(
                decision_kind, reason, count
            ) VALUES (?, ?, ?)
            """,
            (DecisionKind.REVIEW.value, FallbackReason.SCOPE_MISS.value, 1),
        )
        connection.commit()

    with pytest.raises(DecisionReuseObservabilityError):
        observer.status_receipt(
            project_id="project-alpha",
            policy_digest=_POLICY,
        )
