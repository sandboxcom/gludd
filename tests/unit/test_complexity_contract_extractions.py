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


def test_promotion_reexports_canonical_value_contracts() -> None:
    """Promotion callers keep the established public import surface."""
    from general_ludd.ai_ml import promotion
    from general_ludd.ai_ml import promotion_contracts as contract

    assert promotion.PromotionPhase is contract.PromotionPhase
    assert promotion.CanaryBudgets is contract.CanaryBudgets
    assert promotion.CanaryMetrics is contract.CanaryMetrics
    assert promotion.CanaryVerdict is contract.CanaryVerdict
    assert promotion.AliasSwap is contract.AliasSwap
    assert promotion.RollbackResult is contract.RollbackResult


def test_role_generator_reexports_canonical_pruner() -> None:
    """Role generation delegates pruning to one tested implementation."""
    from general_ludd.cloud import role_generator, role_pruning

    assert vars(role_generator)["_prune_by_resource_types"] is role_pruning.prune_by_resource_types


def test_windows_defender_uses_canonical_command_support() -> None:
    """The connector retains its helper seam after command support extraction."""
    from general_ludd.connectors import windows_defender
    from general_ludd.connectors import windows_defender_support as support

    namespace = vars(windows_defender)
    assert namespace["_validate_arg"] is support.validate_arg
    assert namespace["_default_runner"] is support.default_runner
    assert namespace["_run"] is support.run
    assert namespace["_normalize_record"] is support.normalize_record


def test_release_ops_reexports_canonical_readme_check() -> None:
    """Release operations retain the patchable README-check boundary."""
    from general_ludd.git_automation import release_checks, release_ops

    assert vars(release_ops)["_release_readme_check"] is release_checks.check_readme_status_inner


def test_pause_router_reexports_canonical_request_contracts() -> None:
    """Pause clients keep their established request-model identities."""
    from general_ludd.controllers import pause_contracts
    from general_ludd.routers import pause

    assert pause.PauseEntityRequest is pause_contracts.PauseEntityRequest
    assert pause.ResumeEntityRequest is pause_contracts.ResumeEntityRequest
    assert pause.PauseRequest is pause_contracts.PauseRequest
    assert pause.ResumeRequest is pause_contracts.ResumeRequest


def test_event_loop_uses_canonical_managed_dispatch_helpers() -> None:
    """Managed dispatch validation and decoding have one implementation."""
    from general_ludd.event_loop import loop
    from general_ludd.event_loop import managed_self_improve_dispatch as support

    namespace = vars(loop)
    assert namespace["_validate_managed_plan"] is support.validate_approved_plan
    assert (
        namespace["_configured_self_improve_execution_mode"]
        is support.configured_execution_mode
    )
    assert namespace["_decode_managed_worker_response"] is support.decode_worker_response
    assert namespace["_validate_managed_worker_result"] is support.validate_worker_result


def test_runtime_delegates_managed_runner_composition() -> None:
    """The runtime keeps one patch-compatible managed composition implementation."""
    from general_ludd.self_improve import runtime, runtime_builder

    assert (
        vars(runtime)["_build_managed_runner_composition"]
        is runtime_builder.build_managed_self_improve_runner
    )
