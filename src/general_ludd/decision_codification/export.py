"""Offline-only decision-tree training and strict JSON rule export."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Iterable, Sequence
from datetime import datetime
from importlib import import_module
from typing import Literal, cast

from general_ludd.decision_codification.export_support import (
    OneHotColumn,
    TreeExportError,
    _columns,
    _DecisionTree,
    _ExportMetadata,
    _majority_decision,
    _matrix,
    _outcome_counts,
    _scope_key,
)
from general_ludd.decision_codification.miner import (
    DecisionEvidence,
    MiningFloors,
    canonical_corpus_digest,
    canonicalize_evidence,
    wilson_lower_bound,
)
from general_ludd.decision_codification.schema import (
    DecisionRuleBundleV1,
    DecisionRuleLeafV1,
    DecisionRuleNodeV1,
    OutcomeCountsV1,
    VerifiedOutcome,
    canonical_decision_json,
    canonical_sha256,
)

_TREE_LEAF = -1
_RUNTIME_KNOWN_FEATURE = "codification_known"


def _decision_tree_factory() -> Callable[..., _DecisionTree]:
    # Import lazily so schema/mining-only callers do not load the offline learner.
    module = import_module("sklearn.tree")
    factory = module.DecisionTreeClassifier
    if not callable(factory):
        raise TreeExportError("scikit-learn DecisionTreeClassifier is unavailable")
    return cast(Callable[..., _DecisionTree], factory)


def _validate_training_records(
    records: Sequence[DecisionEvidence], floors: MiningFloors
) -> None:
    if len(records) < floors.minimum_support:
        raise TreeExportError(
            f"tree training requires at least {floors.minimum_support} independent records"
        )
    if len({_scope_key(record) for record in records}) != 1:
        raise TreeExportError("tree training records must have a single exact scope")
    if len({record.root_task_id for record in records}) < floors.minimum_root_tasks:
        raise TreeExportError("tree training does not meet root-task diversity")
    if (
        len({record.envelope.occurred_at.date() for record in records})
        < floors.minimum_utc_days
    ):
        raise TreeExportError("tree training does not meet UTC-day diversity")
    agents = {
        record.source_agent_id
        for record in records
        if record.source_agent_id is not None
    }
    if agents and len(agents) < floors.minimum_source_agents:
        raise TreeExportError("tree training does not meet source-agent diversity")

    context_decisions: dict[str, set[str]] = defaultdict(set)
    for record in records:
        context = canonical_sha256({
            "exact_guards": record.envelope.exact_guards,
            "features": record.envelope.features,
        })
        context_decisions[context].add(record.envelope.decision)
    if any(len(decisions) > 1 for decisions in context_decisions.values()):
        raise TreeExportError("identical contexts contain conflicting decisions")


def _fit(
    records: Sequence[DecisionEvidence], columns: Sequence[OneHotColumn]
) -> _DecisionTree:
    classifier = _decision_tree_factory()(
        splitter="best",
        random_state=0,
        max_depth=4,
        max_leaf_nodes=16,
        min_samples_leaf=16,
    )
    classifier.fit(
        _matrix(records, columns),
        [record.envelope.decision for record in records],
    )
    return classifier


def _leaf_is_safe(
    records: Sequence[DecisionEvidence], *, confidence: float, floors: MiningFloors
) -> bool:
    support = len(records)
    success_count = sum(
        record.envelope.verified_outcome is VerifiedOutcome.SUCCESS
        for record in records
    )
    return (
        support >= floors.minimum_support
        and confidence >= floors.minimum_confidence
        and wilson_lower_bound(round(confidence * support), support)
        >= floors.minimum_wilson_lower_bound
        and success_count / support >= floors.minimum_success_rate
        and all(
            record.envelope.verified_outcome is not VerifiedOutcome.UNSAFE
            for record in records
        )
        and all(
            record.envelope.exact_guards.get("risk_band") in {"low", "medium"}
            for record in records
        )
        and all(record.envelope.features.get("reversible") is True for record in records)
    )


def _export_fitted_tree(
    classifier: _DecisionTree,
    records: Sequence[DecisionEvidence],
    columns: Sequence[OneHotColumn],
    metadata: _ExportMetadata,
    floors: MiningFloors,
) -> DecisionRuleBundleV1:
    tree = classifier.tree_
    # The explicit runtime-known guard adds one level and a reachable default.
    if tree.max_depth > 3:
        raise TreeExportError(
            "learned tree is too deep to add the required abstaining default"
        )
    applied = tuple(int(node) for node in classifier.apply(_matrix(records, columns)))
    records_by_leaf: dict[int, list[DecisionEvidence]] = defaultdict(list)
    for node_index, record in zip(applied, records, strict=True):
        records_by_leaf[node_index].append(record)

    nodes: list[DecisionRuleNodeV1] = []
    leaves: list[DecisionRuleLeafV1] = []

    def walk(node_index: int) -> str:
        left = int(tree.children_left[node_index])
        right = int(tree.children_right[node_index])
        if left == _TREE_LEAF and right == _TREE_LEAF:
            leaf_id = f"leaf-model-{node_index:03d}"
            members = tuple(records_by_leaf.get(node_index, ()))
            decision, confidence = _majority_decision(classifier, node_index)
            safe = bool(members) and _leaf_is_safe(
                members, confidence=confidence, floors=floors
            )
            leaves.append(
                DecisionRuleLeafV1(
                    leaf_id=leaf_id,
                    decision=decision if safe else None,
                    support=len(members),
                    confidence=confidence,
                    outcome_counts=_outcome_counts(members),
                    abstain=not safe,
                )
            )
            return leaf_id
        if left == _TREE_LEAF or right == _TREE_LEAF:
            raise TreeExportError("decision tree contains a half-leaf node")

        threshold = float(tree.threshold[node_index])
        if threshold != 0.5:
            raise TreeExportError("exported one-hot splits must use threshold 0.5")
        feature_index = int(tree.feature[node_index])
        if not 0 <= feature_index < len(columns):
            raise TreeExportError("decision tree references an unknown feature column")
        column = columns[feature_index]
        miss_id = walk(left)
        match_id = walk(right)
        node_id = f"node-model-{node_index:03d}"
        nodes.append(
            DecisionRuleNodeV1(
                node_id=node_id,
                feature_id=column.feature_id,
                operator="eq",
                value=column.value,
                match_id=match_id,
                miss_id=miss_id,
            )
        )
        return node_id

    model_root_id = walk(0)
    default_leaf_id = "leaf-default"
    leaves.append(
        DecisionRuleLeafV1(
            leaf_id=default_leaf_id,
            decision=None,
            support=0,
            confidence=0.0,
            outcome_counts=OutcomeCountsV1(
                success=0,
                failure=0,
                reverted=0,
                unknown=0,
                unsafe=0,
            ),
            abstain=True,
        )
    )
    nodes.append(
        DecisionRuleNodeV1(
            node_id="node-root",
            feature_id=_RUNTIME_KNOWN_FEATURE,
            operator="eq",
            value=True,
            match_id=model_root_id,
            miss_id=default_leaf_id,
        )
    )
    if len(nodes) > 31 or len(leaves) > 16:
        raise TreeExportError("exported tree exceeds the strict node or leaf limit")
    if not any(not leaf.abstain for leaf in leaves):
        raise TreeExportError("every learned leaf failed the safety floors")

    envelope = records[0].envelope
    risk = envelope.exact_guards.get("risk_band")
    if risk not in {"low", "medium"}:
        raise TreeExportError("tree scope must be low or medium risk")
    risk_scope = cast(Literal["low", "medium"], risk)
    policies = tuple(sorted({record.envelope.policy_digest for record in records}))

    return DecisionRuleBundleV1.create(
        schema="gludd.decision-rule-bundle/v1",
        project_id=envelope.project_id,
        decision_kind=envelope.decision_kind,
        feature_schema=envelope.feature_schema,
        policy_compatibility=policies,
        risk_scope=risk_scope,
        observed_context_digests=tuple(sorted({
            record.envelope.context_signature for record in records
        })),
        root_id="node-root",
        default_leaf_id=default_leaf_id,
        nodes=tuple(nodes),
        leaves=tuple(leaves),
        corpus_digest=metadata.corpus_digest,
        training_recipe_digest=metadata.training_recipe_digest,
        dependency_lock_digest=metadata.dependency_lock_digest,
        created_at=metadata.created_at,
        expires_at=metadata.expires_at,
        maximum_use_count=metadata.maximum_use_count,
    )


def _fixed_permutation(records: Sequence[DecisionEvidence]) -> tuple[DecisionEvidence, ...]:
    return tuple(
        sorted(
            records,
            key=lambda record: canonical_sha256({
                "seed": "gludd.decision-tree-permutation/v1",
                "envelope_id": record.envelope.envelope_id,
            }),
        )
    )


def _validated_corpus_digest(value: str) -> str:
    prefix = "sha256:"
    hexadecimal = value.removeprefix(prefix)
    if (
        not value.startswith(prefix)
        or len(hexadecimal) != 64
        or any(character not in "0123456789abcdef" for character in hexadecimal)
    ):
        raise TreeExportError("corpus_digest must be a lowercase SHA-256 digest")
    return value


def train_and_export_tree(
    records: Iterable[DecisionEvidence],
    *,
    training_recipe_digest: str,
    dependency_lock_digest: str,
    created_at: datetime,
    expires_at: datetime,
    maximum_use_count: int,
    floors: MiningFloors | None = None,
    corpus_digest: str | None = None,
) -> DecisionRuleBundleV1:
    """Fit three order variants and return one byte-stable strict rule bundle."""
    policy = floors or MiningFloors()
    independent = canonicalize_evidence(records)
    canonical = tuple(
        sorted(independent, key=lambda record: record.envelope.envelope_id)
    )
    _validate_training_records(canonical, policy)
    columns = _columns(canonical)
    if not columns:
        raise TreeExportError("tree training requires at least one bounded feature")
    metadata = _ExportMetadata(
        training_recipe_digest=training_recipe_digest,
        dependency_lock_digest=dependency_lock_digest,
        created_at=created_at,
        expires_at=expires_at,
        maximum_use_count=maximum_use_count,
        corpus_digest=(
            canonical_corpus_digest(canonical)
            if corpus_digest is None
            else _validated_corpus_digest(corpus_digest)
        ),
    )
    variants = (
        canonical,
        tuple(reversed(canonical)),
        _fixed_permutation(canonical),
    )
    exports = tuple(
        _export_fitted_tree(
            _fit(variant, columns),
            variant,
            columns,
            metadata,
            policy,
        )
        for variant in variants
    )
    serialized = tuple(canonical_decision_json(bundle) for bundle in exports)
    if len(set(serialized)) != 1:
        raise TreeExportError("decision-tree export changed across input permutations")
    return exports[0]


export_decision_tree = train_and_export_tree

__all__ = [
    "OneHotColumn",
    "TreeExportError",
    "export_decision_tree",
    "train_and_export_tree",
]
