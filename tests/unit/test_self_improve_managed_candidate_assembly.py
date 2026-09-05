"""Tests for deterministic managed local/Azure candidate-set assembly."""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import FrozenInstanceError, replace
from typing import cast

import pytest

from general_ludd.self_improve.candidate_classification import (
    CandidateTaskClassification,
    classify_candidate_task,
)
from general_ludd.self_improve.managed_candidate_assembly import (
    MANAGED_CANDIDATE_ASSEMBLY_PROTOCOL,
    MAX_MANAGED_CANDIDATES,
    AssembledManagedCandidate,
    CandidateAssemblyError,
    CandidateAssemblyFailure,
    CandidateBudgetState,
    CandidateCleanupAction,
    CandidateHealthState,
    CandidatePrivacyState,
    CandidateResourceOwnership,
    ManagedCandidateAssembly,
    ManagedCandidateSource,
    assemble_managed_candidates,
)
from general_ludd.self_improve.model_candidates import (
    AzureFoundryAPIFamily,
    AzureFoundryCandidateIdentity,
    LocalGGUFCandidateIdentity,
    ModelCandidateProvider,
)


def _digest(label: str) -> str:
    return hashlib.sha256(label.encode("utf-8")).hexdigest()


def _classification(
    task_text: str = "implement a bounded feature",
) -> CandidateTaskClassification:
    return classify_candidate_task(task_text)


def _local_identity(
    *,
    model_id: str = "local-coder",
    artifact_label: str = "local-artifact",
) -> LocalGGUFCandidateIdentity:
    return LocalGGUFCandidateIdentity(
        model_id=model_id,
        repo_id="acme/local-coder",
        revision="a" * 40,
        filename=f"{model_id}.Q4_K_M.gguf",
        artifact_sha256=_digest(artifact_label),
    )


def _azure_identity() -> AzureFoundryCandidateIdentity:
    return AzureFoundryCandidateIdentity(
        endpoint="https://unit-test.openai.azure.com",
        api_family=AzureFoundryAPIFamily.AZURE_OPENAI,
        deployment="coder-deployment",
        api_version="v1",
        model_version="2026-08-30",
        etag='"immutable-etag"',
    )


def _source(
    identity: LocalGGUFCandidateIdentity | AzureFoundryCandidateIdentity,
    *,
    expected_identity_digest: str | None = None,
    approved_configuration_digest: str | None = None,
    current_configuration_digest: str | None = None,
    health_state: CandidateHealthState = CandidateHealthState.READY,
    budget_state: CandidateBudgetState = CandidateBudgetState.WITHIN_LIMITS,
    privacy_state: CandidatePrivacyState = CandidatePrivacyState.APPROVED_PUBLIC,
) -> ManagedCandidateSource:
    approved = approved_configuration_digest or _digest(
        f"approved-config-{identity.identity_digest}"
    )
    return ManagedCandidateSource(
        identity=identity,
        expected_identity_digest=(
            expected_identity_digest or identity.identity_digest
        ),
        approved_configuration_digest=approved,
        current_configuration_digest=(
            current_configuration_digest or approved
        ),
        health_state=health_state,
        budget_state=budget_state,
        privacy_state=privacy_state,
    )


def _copy_source(
    source: ManagedCandidateSource,
    *,
    expected_identity_digest: str | None = None,
    approved_configuration_digest: str | None = None,
    current_configuration_digest: str | None = None,
    health_state: CandidateHealthState | None = None,
    budget_state: CandidateBudgetState | None = None,
    privacy_state: CandidatePrivacyState | None = None,
) -> ManagedCandidateSource:
    return ManagedCandidateSource(
        identity=source.identity,
        expected_identity_digest=(
            source.expected_identity_digest
            if expected_identity_digest is None
            else expected_identity_digest
        ),
        approved_configuration_digest=(
            source.approved_configuration_digest
            if approved_configuration_digest is None
            else approved_configuration_digest
        ),
        current_configuration_digest=(
            source.current_configuration_digest
            if current_configuration_digest is None
            else current_configuration_digest
        ),
        health_state=source.health_state if health_state is None else health_state,
        budget_state=source.budget_state if budget_state is None else budget_state,
        privacy_state=(
            source.privacy_state if privacy_state is None else privacy_state
        ),
    )


def _assemble(
    *sources: ManagedCandidateSource,
    required_providers: tuple[ModelCandidateProvider, ...] = (
        ModelCandidateProvider.LOCAL_GGUF,
    ),
    azure_enabled: bool = False,
    classification: CandidateTaskClassification | None = None,
    expected_classification_digest: str | None = None,
) -> ManagedCandidateAssembly:
    selected_classification = classification or _classification()
    return assemble_managed_candidates(
        selected_classification,
        tuple(sources),
        expected_classification_digest=(
            expected_classification_digest
            or selected_classification.classification_digest
        ),
        required_providers=required_providers,
        azure_enabled=azure_enabled,
    )


def test_mixed_assembly_is_canonical_immutable_and_content_free() -> None:
    task_secret = "implement feature using password=never-retain-this"
    classification = _classification(task_secret)
    local = _source(_local_identity())
    azure = _source(_azure_identity())

    reverse_order = _assemble(
        azure,
        local,
        required_providers=(
            ModelCandidateProvider.AZURE_FOUNDRY,
            ModelCandidateProvider.LOCAL_GGUF,
        ),
        azure_enabled=True,
        classification=classification,
    )
    canonical_order = _assemble(
        local,
        azure,
        required_providers=(
            ModelCandidateProvider.LOCAL_GGUF,
            ModelCandidateProvider.AZURE_FOUNDRY,
        ),
        azure_enabled=True,
        classification=classification,
    )

    assert reverse_order == canonical_order
    assert reverse_order.assembly_digest == canonical_order.assembly_digest
    assert reverse_order.protocol == MANAGED_CANDIDATE_ASSEMBLY_PROTOCOL
    assert tuple(candidate.provider for candidate in reverse_order.candidates) == (
        ModelCandidateProvider.LOCAL_GGUF,
        ModelCandidateProvider.AZURE_FOUNDRY,
    )
    assert tuple(candidate.ordinal for candidate in reverse_order.candidates) == (0, 1)
    serialized = repr(reverse_order.payload()) + repr(reverse_order.event_payloads())
    assert task_secret not in serialized
    assert "unit-test.openai.azure.com" not in serialized
    assert "local-coder.Q4_K_M.gguf" not in serialized
    assert "credential" not in serialized


def test_assembly_records_explicit_non_owning_cleanup_obligations() -> None:
    assembly = _assemble(
        _source(_local_identity()),
        _source(_azure_identity()),
        required_providers=(
            ModelCandidateProvider.LOCAL_GGUF,
            ModelCandidateProvider.AZURE_FOUNDRY,
        ),
        azure_enabled=True,
    )
    local, azure = assembly.candidates

    assert local.resource_ownership is CandidateResourceOwnership.CALLER_OWNED
    assert local.cleanup_action is CandidateCleanupAction.RELEASE_LOCAL_LEASE
    assert azure.resource_ownership is CandidateResourceOwnership.EXTERNAL_PROVIDER
    assert azure.cleanup_action is CandidateCleanupAction.NONE
    assert all(not candidate.assembler_owns_resource for candidate in assembly.candidates)


def test_events_are_deterministic_complete_and_replayable() -> None:
    classification = _classification()
    local = _source(_local_identity())
    azure = _source(_azure_identity())
    assembly = _assemble(
        local,
        azure,
        required_providers=(
            ModelCandidateProvider.LOCAL_GGUF,
            ModelCandidateProvider.AZURE_FOUNDRY,
        ),
        azure_enabled=True,
        classification=classification,
    )

    first = assembly.event_payloads()
    second = assembly.event_payloads()

    assert first == second
    assert [event["event"] for event in first] == [
        "self_improve_managed_candidate_admitted",
        "self_improve_managed_candidate_admitted",
        "self_improve_managed_candidates_assembled",
    ]
    assert [event["ordinal"] for event in first[:-1]] == [0, 1]
    assert first[-1] == {
        "assembly_digest": assembly.assembly_digest,
        "candidate_count": 2,
        "classification_digest": classification.classification_digest,
        "event": "self_improve_managed_candidates_assembled",
        "protocol": MANAGED_CANDIDATE_ASSEMBLY_PROTOCOL,
        "providers": ["local_gguf", "azure_foundry"],
        "required_providers": ["local_gguf", "azure_foundry"],
        "task_text_digest": classification.task_text_digest,
    }
    replay_candidates = tuple(
        (
            event["ordinal"],
            event["candidate_identity_digest"],
            event["configuration_digest"],
            event["provider"],
        )
        for event in first[:-1]
    )
    assert replay_candidates == tuple(
        (
            candidate.ordinal,
            candidate.candidate_identity_digest,
            candidate.configuration_digest,
            candidate.provider.value,
        )
        for candidate in assembly.candidates
    )


def test_local_only_assembly_needs_no_azure_opt_in() -> None:
    assembly = _assemble(_source(_local_identity()))

    assert len(assembly.candidates) == 1
    assert assembly.azure_enabled is False
    assert assembly.providers == (ModelCandidateProvider.LOCAL_GGUF,)


_SourceMutation = Callable[[ManagedCandidateSource], ManagedCandidateSource]
_INELIGIBLE_MUTATIONS: tuple[
    tuple[_SourceMutation, CandidateAssemblyFailure], ...
] = (
    (
        lambda source: _copy_source(
            source,
            expected_identity_digest=_digest("different-identity"),
        ),
        CandidateAssemblyFailure.IDENTITY_DRIFT,
    ),
    (
        lambda source: _copy_source(
            source,
            current_configuration_digest=_digest("different-configuration"),
        ),
        CandidateAssemblyFailure.CONFIGURATION_DRIFT,
    ),
    (
        lambda source: _copy_source(
            source,
            health_state=CandidateHealthState.UNHEALTHY,
        ),
        CandidateAssemblyFailure.HEALTH_INELIGIBLE,
    ),
    (
        lambda source: _copy_source(
            source,
            health_state=CandidateHealthState.UNKNOWN,
        ),
        CandidateAssemblyFailure.HEALTH_INELIGIBLE,
    ),
    (
        lambda source: _copy_source(
            source,
            budget_state=CandidateBudgetState.EXHAUSTED,
        ),
        CandidateAssemblyFailure.BUDGET_INELIGIBLE,
    ),
    (
        lambda source: _copy_source(
            source,
            budget_state=CandidateBudgetState.UNKNOWN,
        ),
        CandidateAssemblyFailure.BUDGET_INELIGIBLE,
    ),
    (
        lambda source: _copy_source(
            source,
            privacy_state=CandidatePrivacyState.BLOCKED,
        ),
        CandidateAssemblyFailure.PRIVACY_INELIGIBLE,
    ),
    (
        lambda source: _copy_source(
            source,
            privacy_state=CandidatePrivacyState.UNKNOWN,
        ),
        CandidateAssemblyFailure.PRIVACY_INELIGIBLE,
    ),
)


@pytest.mark.parametrize(("mutate", "failure"), _INELIGIBLE_MUTATIONS)
def test_candidate_drift_or_ineligible_state_fails_closed(
    mutate: _SourceMutation,
    failure: CandidateAssemblyFailure,
) -> None:
    source = mutate(_source(_local_identity()))

    with pytest.raises(CandidateAssemblyError) as raised:
        _assemble(source)

    assert raised.value.failure is failure
    assert str(raised.value) == f"managed candidate assembly blocked: {failure.value}"


def test_classification_drift_fails_before_candidate_admission() -> None:
    classification = _classification()

    with pytest.raises(CandidateAssemblyError) as raised:
        _assemble(
            _source(_local_identity()),
            classification=classification,
            expected_classification_digest=_digest("stale-classification"),
        )

    assert raised.value.failure is CandidateAssemblyFailure.CLASSIFICATION_DRIFT


def test_duplicate_identity_fails_closed_even_when_configs_differ() -> None:
    identity = _local_identity()

    with pytest.raises(CandidateAssemblyError) as raised:
        _assemble(
            _source(identity),
            _source(
                identity,
                approved_configuration_digest=_digest("second-config"),
            ),
        )

    assert raised.value.failure is CandidateAssemblyFailure.DUPLICATE_CANDIDATE


def test_missing_required_provider_and_disabled_azure_fail_closed() -> None:
    with pytest.raises(CandidateAssemblyError) as missing:
        _assemble(
            _source(_local_identity()),
            required_providers=(ModelCandidateProvider.AZURE_FOUNDRY,),
            azure_enabled=True,
        )
    assert missing.value.failure is CandidateAssemblyFailure.REQUIRED_PROVIDER_MISSING

    with pytest.raises(CandidateAssemblyError) as disabled:
        _assemble(
            _source(_azure_identity()),
            required_providers=(ModelCandidateProvider.AZURE_FOUNDRY,),
            azure_enabled=False,
        )
    assert disabled.value.failure is CandidateAssemblyFailure.PROVIDER_DISABLED


def test_candidate_set_bounds_fail_closed() -> None:
    classification = _classification()

    with pytest.raises(CandidateAssemblyError) as empty:
        assemble_managed_candidates(
            classification,
            (),
            expected_classification_digest=classification.classification_digest,
            required_providers=(ModelCandidateProvider.LOCAL_GGUF,),
            azure_enabled=False,
        )
    assert empty.value.failure is CandidateAssemblyFailure.EMPTY_CANDIDATE_SET

    candidates = tuple(
        _source(
            _local_identity(model_id=f"coder-{ordinal}", artifact_label=str(ordinal))
        )
        for ordinal in range(MAX_MANAGED_CANDIDATES + 1)
    )
    with pytest.raises(CandidateAssemblyError) as excessive:
        _assemble(*candidates)
    assert excessive.value.failure is CandidateAssemblyFailure.CANDIDATE_LIMIT_EXCEEDED


@pytest.mark.parametrize(
    "required_providers",
    [
        [ModelCandidateProvider.LOCAL_GGUF],
        (),
        (
            ModelCandidateProvider.LOCAL_GGUF,
            ModelCandidateProvider.LOCAL_GGUF,
        ),
        ("local_gguf",),
    ],
)
def test_required_provider_contract_rejects_ambiguous_values(
    required_providers: object,
) -> None:
    classification = _classification()

    with pytest.raises(ValueError, match="required_providers"):
        assemble_managed_candidates(
            classification,
            (_source(_local_identity()),),
            expected_classification_digest=classification.classification_digest,
            required_providers=required_providers,  # type: ignore[arg-type]
            azure_enabled=False,
        )


def test_malformed_top_level_inputs_are_rejected() -> None:
    classification = _classification()
    source = _source(_local_identity())

    with pytest.raises(ValueError, match="CandidateTaskClassification"):
        assemble_managed_candidates(
            object(),  # type: ignore[arg-type]
            (source,),
            expected_classification_digest=classification.classification_digest,
            required_providers=(ModelCandidateProvider.LOCAL_GGUF,),
            azure_enabled=False,
        )
    with pytest.raises(ValueError, match="candidates must be a tuple"):
        assemble_managed_candidates(
            classification,
            [source],  # type: ignore[arg-type]
            expected_classification_digest=classification.classification_digest,
            required_providers=(ModelCandidateProvider.LOCAL_GGUF,),
            azure_enabled=False,
        )
    with pytest.raises(ValueError, match="ManagedCandidateSource"):
        assemble_managed_candidates(
            classification,
            (object(),),  # type: ignore[arg-type]
            expected_classification_digest=classification.classification_digest,
            required_providers=(ModelCandidateProvider.LOCAL_GGUF,),
            azure_enabled=False,
        )
    with pytest.raises(ValueError, match="azure_enabled"):
        assemble_managed_candidates(
            classification,
            (source,),
            expected_classification_digest=classification.classification_digest,
            required_providers=(ModelCandidateProvider.LOCAL_GGUF,),
            azure_enabled=1,  # type: ignore[arg-type]
        )
    with pytest.raises(ValueError, match="expected_classification_digest"):
        assemble_managed_candidates(
            classification,
            (source,),
            expected_classification_digest="not-a-digest",
            required_providers=(ModelCandidateProvider.LOCAL_GGUF,),
            azure_enabled=False,
        )


_MALFORMED_SOURCE_MUTATIONS: tuple[_SourceMutation, ...] = (
    lambda source: _copy_source(
        source,
        expected_identity_digest="not-a-digest",
    ),
    lambda source: _copy_source(
        source,
        approved_configuration_digest="A" * 64,
    ),
    lambda source: _copy_source(
        source,
        current_configuration_digest="0" * 63,
    ),
    lambda source: _copy_source(
        source,
        health_state=cast(CandidateHealthState, "ready"),
    ),
    lambda source: _copy_source(
        source,
        budget_state=cast(CandidateBudgetState, "within_limits"),
    ),
    lambda source: _copy_source(
        source,
        privacy_state=cast(CandidatePrivacyState, "approved_public"),
    ),
)


@pytest.mark.parametrize("mutate", _MALFORMED_SOURCE_MUTATIONS)
def test_source_rejects_malformed_digests_or_untyped_states(
    mutate: _SourceMutation,
) -> None:
    source = _source(_local_identity())

    with pytest.raises(ValueError):
        mutate(source)


def test_source_rejects_untyped_identity_without_observing_it() -> None:
    with pytest.raises(ValueError, match="typed model candidate identity"):
        ManagedCandidateSource(
            identity=object(),  # type: ignore[arg-type]
            expected_identity_digest=_digest("identity"),
            approved_configuration_digest=_digest("approved"),
            current_configuration_digest=_digest("current"),
            health_state=CandidateHealthState.READY,
            budget_state=CandidateBudgetState.WITHIN_LIMITS,
            privacy_state=CandidatePrivacyState.APPROVED_PUBLIC,
        )


def test_public_artifacts_are_frozen_and_payloads_are_defensive() -> None:
    assembly = _assemble(_source(_local_identity()))
    candidate = assembly.candidates[0]

    with pytest.raises(FrozenInstanceError):
        assembly.azure_enabled = True  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        candidate.ordinal = 2  # type: ignore[misc]

    payload = assembly.payload()
    payload["candidate_count"] = 99
    events = assembly.event_payloads()
    events[0]["provider"] = "substituted"

    assert assembly.payload()["candidate_count"] == 1
    assert assembly.event_payloads()[0]["provider"] == "local_gguf"


def test_manual_output_artifacts_reject_inconsistent_resource_contracts() -> None:
    digest = _digest("candidate")
    config = _digest("config")

    with pytest.raises(ValueError, match="ordinal"):
        AssembledManagedCandidate(
            ordinal=-1,
            candidate_identity_digest=digest,
            configuration_digest=config,
            provider=ModelCandidateProvider.LOCAL_GGUF,
            resource_ownership=CandidateResourceOwnership.CALLER_OWNED,
            cleanup_action=CandidateCleanupAction.RELEASE_LOCAL_LEASE,
        )
    with pytest.raises(ValueError, match="resource contract"):
        AssembledManagedCandidate(
            ordinal=0,
            candidate_identity_digest=digest,
            configuration_digest=config,
            provider=ModelCandidateProvider.AZURE_FOUNDRY,
            resource_ownership=CandidateResourceOwnership.CALLER_OWNED,
            cleanup_action=CandidateCleanupAction.RELEASE_LOCAL_LEASE,
        )


def test_output_candidate_rejects_untyped_or_ineligible_fields() -> None:
    candidate = _assemble(_source(_local_identity())).candidates[0]

    with pytest.raises(ValueError, match="provider"):
        replace(candidate, provider=cast(ModelCandidateProvider, "local_gguf"))
    with pytest.raises(ValueError, match="resource_ownership"):
        replace(
            candidate,
            resource_ownership=cast(
                CandidateResourceOwnership,
                "caller_owned",
            ),
        )
    with pytest.raises(ValueError, match="cleanup_action"):
        replace(
            candidate,
            cleanup_action=cast(CandidateCleanupAction, "release_local_lease"),
        )
    with pytest.raises(ValueError, match="eligible states"):
        replace(candidate, health_state=CandidateHealthState.UNHEALTHY)


def test_manual_assembly_rejects_noncanonical_or_inconsistent_artifacts() -> None:
    local_assembly = _assemble(_source(_local_identity()))
    mixed_assembly = _assemble(
        _source(_local_identity()),
        _source(_azure_identity()),
        required_providers=(
            ModelCandidateProvider.LOCAL_GGUF,
            ModelCandidateProvider.AZURE_FOUNDRY,
        ),
        azure_enabled=True,
    )
    local = mixed_assembly.candidates[0]
    azure = mixed_assembly.candidates[1]

    with pytest.raises(ValueError, match="protocol"):
        replace(local_assembly, protocol="future-protocol")
    with pytest.raises(ValueError, match="azure_enabled"):
        replace(local_assembly, azure_enabled=cast(bool, 1))
    with pytest.raises(ValueError, match="canonical provider order"):
        replace(
            mixed_assembly,
            required_providers=(
                ModelCandidateProvider.AZURE_FOUNDRY,
                ModelCandidateProvider.LOCAL_GGUF,
            ),
        )
    with pytest.raises(ValueError, match="bounded immutable"):
        replace(
            local_assembly,
            candidates=cast(
                tuple[AssembledManagedCandidate, ...],
                [local_assembly.candidates[0]],
            ),
        )
    with pytest.raises(ValueError, match="contiguous"):
        replace(local_assembly, candidates=(replace(local, ordinal=1),))
    with pytest.raises(ValueError, match="canonical provider"):
        replace(
            mixed_assembly,
            candidates=(
                replace(azure, ordinal=0),
                replace(local, ordinal=1),
            ),
        )
    with pytest.raises(ValueError, match="unique immutable"):
        replace(
            local_assembly,
            candidates=(
                local_assembly.candidates[0],
                replace(local_assembly.candidates[0], ordinal=1),
            ),
        )
    with pytest.raises(ValueError, match="required provider"):
        replace(
            local_assembly,
            required_providers=(ModelCandidateProvider.AZURE_FOUNDRY,),
            azure_enabled=True,
        )
    with pytest.raises(ValueError, match="explicit opt-in"):
        replace(mixed_assembly, azure_enabled=False)


def test_corrupt_classification_and_untyped_failure_fail_closed() -> None:
    classification = _classification()
    approved_digest = classification.classification_digest
    object.__setattr__(classification, "task_type", object())

    with pytest.raises(CandidateAssemblyError) as raised:
        _assemble(
            _source(_local_identity()),
            classification=classification,
            expected_classification_digest=approved_digest,
        )
    assert raised.value.failure is CandidateAssemblyFailure.CLASSIFICATION_DRIFT

    with pytest.raises(ValueError, match="CandidateAssemblyFailure"):
        CandidateAssemblyError(cast(CandidateAssemblyFailure, "identity_drift"))
