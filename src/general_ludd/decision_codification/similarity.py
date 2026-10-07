"""Bounded, deterministic similarity grouping for normalized decisions.

Similarity is used only during offline discovery.  Runtime rule application is
exact and never imports this module.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from importlib import import_module
from typing import Protocol, cast

import numpy as np
from rapidfuzz import fuzz

from general_ludd.decision_codification.schema import (
    DecisionEnvelopeV1,
    FeatureValue,
    canonical_sha256,
)

DEFAULT_SIMILARITY_SCORE_CUTOFF = 92.0
MAX_ENVELOPES = 100_000
MAX_UNIQUE_SIGNATURES = 5_000


class SimilarityError(ValueError):
    """Base class for fail-closed offline similarity errors."""


class SimilarityLimitError(SimilarityError):
    """Raised before an unbounded pairwise matrix can be allocated."""


class _CompleteLinkClustering(Protocol):
    def fit_predict(self, distances: object) -> Iterable[int]: ...


def _clustering_factory() -> Callable[..., _CompleteLinkClustering]:
    module = import_module("sklearn.cluster")
    factory = module.AgglomerativeClustering
    if not callable(factory):
        raise SimilarityError("scikit-learn AgglomerativeClustering is unavailable")
    return cast(Callable[..., _CompleteLinkClustering], factory)


@dataclass(frozen=True, slots=True)
class SimilarityCluster:
    """One canonical complete-link cluster within an exact scope partition."""

    partition_digest: str
    cluster_digest: str
    members: tuple[DecisionEnvelopeV1, ...]

    @property
    def member_ids(self) -> tuple[str, ...]:
        """Return canonical member identifiers without exposing source content."""
        return tuple(member.envelope_id for member in self.members)


def _typed_value(value: FeatureValue) -> str:
    if type(value) is bool:
        return f"b:{str(value).lower()}"
    return f"s:{value}"


def serialize_feature_tokens(envelope: DecisionEnvelopeV1) -> tuple[str, ...]:
    """Serialize bounded features as sorted, explicitly typed tokens."""
    return tuple(
        f"{name}={_typed_value(value)}"
        for name, value in sorted(envelope.features.items())
    )


def normalized_similarity(
    left: str, right: str, *, score_cutoff: float
) -> float:
    """Return one bounded RapidFuzz normalized score."""
    return float(fuzz.ratio(left, right, score_cutoff=score_cutoff))


def _partition_payload(envelope: DecisionEnvelopeV1) -> dict[str, object]:
    return {
        "project_id": envelope.project_id,
        "decision_kind": envelope.decision_kind.value,
        "feature_schema": envelope.feature_schema,
        "policy_digest": envelope.policy_digest,
        "exact_guards": [
            [name, _typed_value(value)]
            for name, value in sorted(envelope.exact_guards.items())
        ],
    }


def _partition_digest(envelope: DecisionEnvelopeV1) -> str:
    return canonical_sha256({
        "schema": "gludd.decision-similarity-partition/v1",
        **_partition_payload(envelope),
    })


def _signature(envelope: DecisionEnvelopeV1) -> str:
    return "\x1f".join(serialize_feature_tokens(envelope))


def _labels_for_signatures(
    signatures: Sequence[str], *, score_cutoff: float
) -> tuple[int, ...]:
    if len(signatures) == 1:
        return (0,)

    # float32 caps the largest permitted 5,000-row matrix at 100 MiB before
    # scikit-learn's own bounded working copy, rather than allocating millions
    # of heavyweight Python float objects.
    distances = np.zeros((len(signatures), len(signatures)), dtype=np.float32)
    for left_index, left in enumerate(signatures):
        for right_index in range(left_index + 1, len(signatures)):
            score = normalized_similarity(
                left,
                signatures[right_index],
                score_cutoff=score_cutoff,
            )
            distance = 100.0 - score if score else 100.0
            distances[left_index, right_index] = distance
            distances[right_index, left_index] = distance

    # A tiny inclusive adjustment implements the contract's "below 92" rule:
    # a score exactly at the cutoff remains eligible for a merge.
    estimator = _clustering_factory()(
        n_clusters=None,
        metric="precomputed",
        linkage="complete",
        distance_threshold=(100.0 - score_cutoff) + 1e-12,
        compute_full_tree=True,
    )
    return tuple(int(label) for label in estimator.fit_predict(distances))


def _cluster_partition(
    envelopes: Sequence[DecisionEnvelopeV1],
    *,
    score_cutoff: float,
    max_unique_signatures: int,
) -> tuple[SimilarityCluster, ...]:
    ordered = tuple(sorted(envelopes, key=lambda item: item.envelope_id))
    by_signature: dict[str, list[DecisionEnvelopeV1]] = defaultdict(list)
    for envelope in ordered:
        by_signature[_signature(envelope)].append(envelope)

    signature_items = sorted(
        by_signature.items(),
        key=lambda item: min(member.envelope_id for member in item[1]),
    )
    if len(signature_items) > max_unique_signatures:
        raise SimilarityLimitError(
            "similarity partition exceeds the bounded unique signatures limit"
        )

    labels = _labels_for_signatures(
        [signature for signature, _ in signature_items],
        score_cutoff=score_cutoff,
    )
    grouped: dict[int, list[DecisionEnvelopeV1]] = defaultdict(list)
    for label, (_, exact_members) in zip(labels, signature_items, strict=True):
        grouped[label].extend(exact_members)

    partition_digest = _partition_digest(ordered[0])
    clusters: list[SimilarityCluster] = []
    for members in grouped.values():
        canonical_members = tuple(sorted(members, key=lambda item: item.envelope_id))
        cluster_digest = canonical_sha256({
            "schema": "gludd.decision-similarity-cluster/v1",
            "partition_digest": partition_digest,
            "member_ids": [member.envelope_id for member in canonical_members],
        })
        clusters.append(
            SimilarityCluster(
                partition_digest=partition_digest,
                cluster_digest=cluster_digest,
                members=canonical_members,
            )
        )
    return tuple(sorted(clusters, key=lambda item: item.cluster_digest))


def group_similar_envelopes(
    envelopes: Iterable[DecisionEnvelopeV1],
    *,
    score_cutoff: float = DEFAULT_SIMILARITY_SCORE_CUTOFF,
    max_envelopes: int = MAX_ENVELOPES,
    max_unique_signatures: int = MAX_UNIQUE_SIGNATURES,
) -> tuple[SimilarityCluster, ...]:
    """Group envelopes using exact partitions and bounded complete linkage.

    Every input is sorted by its canonical envelope digest before any tie can
    reach RapidFuzz or scikit-learn.  Exact feature signatures are collapsed
    before matrix allocation, both giving them precedence and bounding memory.
    """
    if not DEFAULT_SIMILARITY_SCORE_CUTOFF <= score_cutoff <= 100.0:
        raise SimilarityError(
            "score_cutoff cannot be lowered below the safe default of 92"
        )
    if not 1 <= max_envelopes <= MAX_ENVELOPES:
        raise SimilarityLimitError(
            f"max_envelopes must be positive and cannot exceed {MAX_ENVELOPES}"
        )
    if max_unique_signatures < 1:
        raise SimilarityLimitError("max_unique_signatures must be positive")
    if max_unique_signatures > MAX_UNIQUE_SIGNATURES:
        raise SimilarityLimitError(
            "max_unique_signatures cannot exceed the hard safety limit"
        )

    partitions: dict[str, list[DecisionEnvelopeV1]] = defaultdict(list)
    for index, envelope in enumerate(envelopes, start=1):
        if index > max_envelopes:
            raise SimilarityLimitError(
                "similarity input exceeds the bounded envelope limit"
            )
        partitions[_partition_digest(envelope)].append(envelope)

    clusters: list[SimilarityCluster] = []
    for partition_digest in sorted(partitions):
        clusters.extend(
            _cluster_partition(
                partitions[partition_digest],
                score_cutoff=score_cutoff,
                max_unique_signatures=max_unique_signatures,
            )
        )
    return tuple(sorted(clusters, key=lambda item: item.cluster_digest))


__all__ = [
    "DEFAULT_SIMILARITY_SCORE_CUTOFF",
    "MAX_ENVELOPES",
    "MAX_UNIQUE_SIGNATURES",
    "SimilarityCluster",
    "SimilarityError",
    "SimilarityLimitError",
    "group_similar_envelopes",
    "normalized_similarity",
    "serialize_feature_tokens",
]
