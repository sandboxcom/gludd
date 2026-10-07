"""Deterministic similarity grouping tests for decision codification."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import cast

import numpy as np
import pytest

from general_ludd.decision_codification import similarity
from general_ludd.decision_codification.schema import (
    DecisionEnvelopeV1,
    OutcomeEvidenceV1,
    RedactionSummaryV1,
    VerifiedOutcome,
    canonical_decision_json,
)
from general_ludd.decision_codification.similarity import (
    SimilarityError,
    SimilarityLimitError,
    group_similar_envelopes,
    normalized_similarity,
    serialize_feature_tokens,
)

pytestmark = pytest.mark.filterwarnings("error")

SHA_A = "sha256:" + "a" * 64
SHA_B = "sha256:" + "b" * 64
SHA_C = "sha256:" + "c" * 64


def _envelope(
    index: int,
    marker: str,
    *,
    project_id: str = "project-1",
    risk: str = "low",
) -> DecisionEnvelopeV1:
    event_digest = f"sha256:{index + 1:064x}"
    return DecisionEnvelopeV1.create(
        schema="gludd.decision-envelope/v1",
        source_run_id=f"run-{index}",
        source_event_digest=event_digest,
        source_bundle_digest=SHA_A,
        project_id=project_id,
        decision_kind="review",
        feature_schema=SHA_B,
        policy_digest=SHA_C,
        occurred_at=datetime(2026, 1, 1, index % 24, tzinfo=UTC),
        exact_guards={
            "action_vocabulary": "review.v1",
            "operation_class": "review",
            "risk_band": risk,
        },
        features={"marker": marker, "reversible": True},
        decision="approve",
        verified_outcome=VerifiedOutcome.SUCCESS,
        outcome_evidence=OutcomeEvidenceV1(
            decision_event_digest=event_digest,
            outcome=VerifiedOutcome.SUCCESS,
            terminal_event_ids=(f"terminal-{index}",),
            gate_digests=(SHA_A,),
            status_digests=(),
        ),
        redaction=RedactionSummaryV1(count=0, kinds=()),
    )


def _cluster_bytes(envelopes: list[DecisionEnvelopeV1]) -> bytes:
    clusters = group_similar_envelopes(envelopes)
    payload = [
        {
            "cluster_digest": cluster.cluster_digest,
            "member_ids": list(cluster.member_ids),
            "partition_digest": cluster.partition_digest,
        }
        for cluster in clusters
    ]
    return canonical_decision_json(payload).encode()


def test_tokens_are_typed_sorted_and_group_exports_are_order_invariant() -> None:
    first = _envelope(1, "alpha")
    duplicate = _envelope(2, "alpha")
    far = _envelope(3, "zzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzz")

    assert serialize_feature_tokens(first) == (
        "marker=s:alpha",
        "reversible=b:true",
    )
    baseline = _cluster_bytes([first, duplicate, far])
    assert baseline == _cluster_bytes([far, duplicate, first])
    assert baseline == _cluster_bytes([duplicate, first, far])

    clusters = group_similar_envelopes([first, duplicate, far])
    exact = next(cluster for cluster in clusters if len(cluster.members) == 2)
    assert exact.member_ids == tuple(sorted((first.envelope_id, duplicate.envelope_id)))


def test_complete_link_prevents_similarity_chaining(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    envelopes = [_envelope(1, marker) for marker in ("alpha", "bravo", "charlie")]

    def fake_ratio(left: str, right: str, *, score_cutoff: float) -> float:
        pair = frozenset(
            token.removeprefix("marker=s:")
            for token in (left.split("\x1f")[0], right.split("\x1f")[0])
        )
        scores = {
            frozenset(("alpha", "bravo")): 95.0,
            frozenset(("bravo", "charlie")): 95.0,
            frozenset(("alpha", "charlie")): 80.0,
        }
        score = 100.0 if len(pair) == 1 else scores[pair]
        return score if score >= score_cutoff else 0.0

    monkeypatch.setattr(similarity, "normalized_similarity", fake_ratio)
    clusters = group_similar_envelopes(envelopes)

    assert max(len(cluster.members) for cluster in clusters) == 2
    assert not any(
        {envelopes[0].envelope_id, envelopes[2].envelope_id}
        <= set(cluster.member_ids)
        for cluster in clusters
    )


def test_scope_partitions_are_exact_and_unique_signature_limit_is_bounded() -> None:
    low = _envelope(1, "alpha")
    medium = _envelope(2, "alpha", risk="medium")
    other_project = _envelope(3, "alpha", project_id="project-2")

    clusters = group_similar_envelopes([other_project, medium, low])
    assert len(clusters) == 3
    assert all(len(cluster.members) == 1 for cluster in clusters)

    with pytest.raises(SimilarityLimitError, match="unique signatures"):
        group_similar_envelopes(
            [_envelope(index, f"marker-{index}") for index in range(3)],
            max_unique_signatures=2,
        )


def test_similarity_options_fail_before_allocation_and_empty_input_is_stable() -> None:
    assert group_similar_envelopes([]) == ()
    assert normalized_similarity("same", "same", score_cutoff=92.0) == 100.0
    assert normalized_similarity("alpha", "omega", score_cutoff=92.0) == 0.0
    with pytest.raises(SimilarityError, match="score_cutoff"):
        group_similar_envelopes([], score_cutoff=0.0)
    with pytest.raises(SimilarityError, match="score_cutoff"):
        group_similar_envelopes([], score_cutoff=101.0)
    with pytest.raises(SimilarityError, match="cannot be lowered"):
        group_similar_envelopes([], score_cutoff=91.9)
    with pytest.raises(SimilarityLimitError, match="positive"):
        group_similar_envelopes([], max_unique_signatures=0)
    with pytest.raises(SimilarityLimitError, match="cannot exceed"):
        group_similar_envelopes(
            [], max_unique_signatures=similarity.MAX_UNIQUE_SIGNATURES + 1
        )
    with pytest.raises(SimilarityLimitError, match="envelope limit"):
        group_similar_envelopes(
            [_envelope(index, "same") for index in range(3)],
            max_envelopes=2,
        )


def test_pairwise_matrix_uses_bounded_float32_storage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[np.ndarray[tuple[int, int], np.dtype[np.float32]]] = []

    class FakeClustering:
        def fit_predict(self, distances: object) -> tuple[int, ...]:
            matrix = cast(
                np.ndarray[tuple[int, int], np.dtype[np.float32]], distances
            )
            captured.append(matrix)
            return (0, 1)

    monkeypatch.setattr(
        similarity,
        "_clustering_factory",
        lambda: lambda **_kwargs: FakeClustering(),
    )
    group_similar_envelopes([_envelope(1, "alpha"), _envelope(2, "omega")])

    assert captured[0].dtype == np.float32
    assert captured[0].nbytes == 16
