"""Regression tests for the extracted managed mutation boundary."""

from __future__ import annotations

from general_ludd.self_improve import managed_mutation
from general_ludd.self_improve.managed_runner import (
    ModelPlanError,
    ModelPlanFailure,
    SelfImprovePolicyViolation,
    apply_proposal,
)


def test_managed_runner_reexports_the_canonical_mutation_contract() -> None:
    """Existing callers retain identity while implementation has one owner."""
    assert apply_proposal is managed_mutation.apply_proposal
    assert ModelPlanError is managed_mutation.ModelPlanError
    assert ModelPlanFailure is managed_mutation.ModelPlanFailure
    assert SelfImprovePolicyViolation is managed_mutation.SelfImprovePolicyViolation


def test_extracted_failures_keep_secret_safe_messages() -> None:
    """Moving the boundary must not weaken its content-free failures."""
    assert str(ModelPlanError(ModelPlanFailure.EXHAUSTED)) == (
        "managed model candidate plan failed: model_plan_exhausted"
    )
    assert str(SelfImprovePolicyViolation()) == (
        "self-improvement blocked by project privacy policy"
    )
