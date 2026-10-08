"""Stable data shapes and encoders for strict decision-tree export."""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from general_ludd.decision_codification.miner import DecisionEvidence
from general_ludd.decision_codification.schema import (
    FeatureValue,
    OutcomeCountsV1,
    VerifiedOutcome,
)


class TreeExportError(ValueError):
    """Raised when an estimator cannot be exported as a safe strict rule tree."""


class _TreeState(Protocol):
    children_left: Sequence[int]
    children_right: Sequence[int]
    feature: Sequence[int]
    threshold: Sequence[float]
    value: Sequence[Sequence[Sequence[float]]]
    max_depth: int


class _DecisionTree(Protocol):
    tree_: _TreeState
    classes_: Sequence[object]

    def fit(
        self, features: Sequence[Sequence[float]], targets: Sequence[str]
    ) -> object: ...

    def apply(self, features: Sequence[Sequence[float]]) -> Sequence[int]: ...


@dataclass(frozen=True, slots=True)
class OneHotColumn:
    """One stable boolean column derived from a closed categorical feature."""

    feature_id: str
    value: FeatureValue


@dataclass(frozen=True, slots=True)
class _ExportMetadata:
    training_recipe_digest: str
    dependency_lock_digest: str
    created_at: datetime
    expires_at: datetime
    maximum_use_count: int
    corpus_digest: str


def _value_key(value: FeatureValue) -> tuple[int, str]:
    if type(value) is bool:
        return (0, str(value).lower())
    return (1, value)


def _columns(records: Sequence[DecisionEvidence]) -> tuple[OneHotColumn, ...]:
    values: dict[str, set[FeatureValue]] = defaultdict(set)
    for record in records:
        for feature_id, value in record.envelope.features.items():
            values[feature_id].add(value)
    return tuple(
        OneHotColumn(feature_id=feature_id, value=value)
        for feature_id in sorted(values)
        for value in sorted(values[feature_id], key=_value_key)
    )


def _matrix(
    records: Sequence[DecisionEvidence], columns: Sequence[OneHotColumn]
) -> list[list[float]]:
    return [
        [
            1.0
            if column.feature_id in record.envelope.features
            and record.envelope.features[column.feature_id] == column.value
            else 0.0
            for column in columns
        ]
        for record in records
    ]


def _scope_key(record: DecisionEvidence) -> tuple[object, ...]:
    envelope = record.envelope
    return (
        envelope.project_id,
        envelope.decision_kind,
        envelope.feature_schema,
        envelope.policy_digest,
        tuple(envelope.exact_guards.items()),
    )


def _outcome_counts(records: Sequence[DecisionEvidence]) -> OutcomeCountsV1:
    counts = Counter(record.envelope.verified_outcome for record in records)
    return OutcomeCountsV1(
        success=counts[VerifiedOutcome.SUCCESS],
        failure=counts[VerifiedOutcome.FAILURE],
        reverted=counts[VerifiedOutcome.REVERTED],
        unknown=counts[VerifiedOutcome.UNKNOWN],
        unsafe=counts[VerifiedOutcome.UNSAFE],
    )


def _majority_decision(
    classifier: _DecisionTree, node_index: int
) -> tuple[str, float]:
    weights = classifier.tree_.value[node_index][0]
    class_index = max(range(len(weights)), key=lambda index: (weights[index], -index))
    total = float(sum(weights))
    if total <= 0.0:
        raise TreeExportError("decision tree emitted an empty leaf")
    return str(classifier.classes_[class_index]), float(weights[class_index]) / total


__all__ = ["OneHotColumn", "TreeExportError"]
