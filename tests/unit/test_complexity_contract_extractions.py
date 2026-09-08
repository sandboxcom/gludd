"""Compatibility checks for cohesive contract-module extractions."""

from __future__ import annotations


def test_environment_validation_uses_canonical_plan_contract() -> None:
    """Validation keeps one provider-schema source after its extraction."""
    from general_ludd.infra import azure_containerapp_environment_plan_contract as contract
    from general_ludd.infra import azure_containerapp_environment_validation as validation

    namespace = vars(validation)
    assert namespace["_PROVIDER_AFTER_FIELDS"] is contract.PROVIDER_AFTER_FIELDS
    assert namespace["_EMPTY_PROVIDER_FIELDS"] is contract.EMPTY_PROVIDER_FIELDS
    assert namespace["_PROVIDER_BOOLEAN_DEFAULTS"] is contract.PROVIDER_BOOLEAN_DEFAULTS
    assert namespace["_CHANGE_FIELDS"] is contract.CHANGE_FIELDS


def test_owned_candidate_reexports_canonical_lifecycle_contract() -> None:
    """Existing imports resolve to the extracted lifecycle contract objects."""
    from general_ludd.infra import azure_containerapp_owned_candidate as candidate
    from general_ludd.infra import azure_containerapp_owned_candidate_types as contract

    assert candidate.OwnedCandidateLifecycleError is contract.OwnedCandidateLifecycleError
    assert candidate.OwnedCandidateLifecycleEvent is contract.OwnedCandidateLifecycleEvent
    assert candidate.OwnedCandidateLifecycleTrace is contract.OwnedCandidateLifecycleTrace
