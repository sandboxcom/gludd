"""Runtime-context safety contracts for decision codification."""

from __future__ import annotations

from general_ludd.decision_codification.normalize import (
    FEATURE_SCHEMA_V1_DIGEST,
    normalize_decision_context,
)
from general_ludd.decision_codification.schema import (
    DecisionContextV1,
    DecisionKind,
    DecisionRuleBundleV1,
    NormalizationRefusalV1,
    canonical_decision_json,
    canonical_sha256,
)

POLICY = "sha256:" + "a" * 64


def _features(*, work_type: str = "code") -> dict[str, object]:
    return {
        "work_type": work_type,
        "risk_band": "low",
        "operation_class": "review",
        "reversible": True,
    }


def test_live_runtime_context_never_requires_a_prior_decision_or_outcome() -> None:
    """A decision lookup input cannot contain the answer it is meant to choose."""
    result = normalize_decision_context(
        project_id="project-1",
        expected_project_id="project-1",
        decision_kind=DecisionKind.REVIEW,
        policy_digest=POLICY,
        features=_features(),
    )

    assert isinstance(result, DecisionContextV1)
    assert result.feature_schema == FEATURE_SCHEMA_V1_DIGEST
    assert result.exact_guards == {
        "action_vocabulary": "review.v1",
        "operation_class": "review",
        "risk_band": "low",
    }
    serialized = canonical_decision_json(result)
    assert "decision" not in DecisionContextV1.model_fields
    assert "verified_outcome" not in DecisionContextV1.model_fields
    assert "source_run_id" not in DecisionContextV1.model_fields
    assert "approve" not in serialized


def test_live_runtime_context_refuses_cross_project_and_unknown_features() -> None:
    cross_project = normalize_decision_context(
        project_id="project-2",
        expected_project_id="project-1",
        decision_kind=DecisionKind.REVIEW,
        policy_digest=POLICY,
        features=_features(),
    )
    unknown = normalize_decision_context(
        project_id="project-1",
        expected_project_id="project-1",
        decision_kind=DecisionKind.REVIEW,
        policy_digest=POLICY,
        features={**_features(), "prompt": "never-persist-this"},
    )

    assert isinstance(cross_project, NormalizationRefusalV1)
    assert isinstance(unknown, NormalizationRefusalV1)
    assert "never-persist-this" not in canonical_decision_json(unknown)


def test_rule_artifact_binds_exact_observed_contexts_without_digest_cycle() -> None:
    """Approval binds the later report; the candidate must not hash that report."""
    assert "observed_context_digests" in DecisionRuleBundleV1.model_fields
    assert "evaluator_report_digest" not in DecisionRuleBundleV1.model_fields

    first = DecisionContextV1.create(
        schema="gludd.decision-context/v1",
        project_id="project-1",
        decision_kind=DecisionKind.REVIEW,
        feature_schema=FEATURE_SCHEMA_V1_DIGEST,
        policy_digest=POLICY,
        exact_guards={
            "action_vocabulary": "review.v1",
            "operation_class": "review",
            "risk_band": "low",
        },
        features={"reversible": True, "work_type": "code"},
    )
    second = DecisionContextV1.create(
        schema="gludd.decision-context/v1",
        project_id="project-1",
        decision_kind=DecisionKind.REVIEW,
        feature_schema=FEATURE_SCHEMA_V1_DIGEST,
        policy_digest=POLICY,
        exact_guards=first.exact_guards,
        features={"reversible": True, "work_type": "documentation"},
    )

    assert first.context_id != second.context_id
    assert first.context_signature == canonical_sha256({
        "exact_guards": first.exact_guards,
        "features": first.features,
    })
