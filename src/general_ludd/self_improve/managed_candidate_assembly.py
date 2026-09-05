"""Deterministically admit pre-discovered managed model candidates."""

from __future__ import annotations

import hmac

from general_ludd.self_improve._managed_candidate_assembly_types import (
    _PROVIDER_ORDER,
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
    _canonical_providers,
    _require_digest,
    _require_value,
    _resource_contract,
)
from general_ludd.self_improve.candidate_classification import (
    CandidateTaskClassification,
)
from general_ludd.self_improve.model_candidates import ModelCandidateProvider


def _admit(condition: bool, failure: CandidateAssemblyFailure) -> None:
    if not condition:
        raise CandidateAssemblyError(failure)


def _classification_digest(
    classification: CandidateTaskClassification,
    expected_digest: str,
) -> str:
    _require_value(
        type(classification) is CandidateTaskClassification,
        "classification must be a CandidateTaskClassification",
    )
    expected = _require_digest(expected_digest, "expected_classification_digest")
    try:
        current = classification.classification_digest
        matches = hmac.compare_digest(current, expected)
    except Exception:
        raise CandidateAssemblyError(
            CandidateAssemblyFailure.CLASSIFICATION_DRIFT
        ) from None
    _admit(matches, CandidateAssemblyFailure.CLASSIFICATION_DRIFT)
    return current


def _ordered_sources(
    candidates: tuple[ManagedCandidateSource, ...],
) -> tuple[ManagedCandidateSource, ...]:
    _require_value(type(candidates) is tuple, "candidates must be a tuple")
    _admit(bool(candidates), CandidateAssemblyFailure.EMPTY_CANDIDATE_SET)
    _admit(
        len(candidates) <= MAX_MANAGED_CANDIDATES,
        CandidateAssemblyFailure.CANDIDATE_LIMIT_EXCEEDED,
    )
    _require_value(
        all(type(candidate) is ManagedCandidateSource for candidate in candidates),
        "candidates must contain ManagedCandidateSource values",
    )
    return tuple(
        sorted(
            candidates,
            key=lambda candidate: (
                _PROVIDER_ORDER[candidate.provider],
                candidate.expected_identity_digest,
            ),
        )
    )


def _admit_source(
    candidate: ManagedCandidateSource,
    seen: set[str],
    *,
    azure_enabled: bool,
) -> None:
    try:
        identity_digest = candidate.current_identity_digest
        identity_matches = hmac.compare_digest(
            identity_digest,
            candidate.expected_identity_digest,
        )
    except Exception:
        raise CandidateAssemblyError(CandidateAssemblyFailure.IDENTITY_DRIFT) from None
    _admit(identity_matches, CandidateAssemblyFailure.IDENTITY_DRIFT)
    _admit(identity_digest not in seen, CandidateAssemblyFailure.DUPLICATE_CANDIDATE)
    seen.add(identity_digest)
    try:
        configuration_matches = hmac.compare_digest(
            candidate.approved_configuration_digest,
            candidate.current_configuration_digest,
        )
    except Exception:
        raise CandidateAssemblyError(
            CandidateAssemblyFailure.CONFIGURATION_DRIFT
        ) from None
    _admit(configuration_matches, CandidateAssemblyFailure.CONFIGURATION_DRIFT)
    _admit(
        candidate.health_state is CandidateHealthState.READY,
        CandidateAssemblyFailure.HEALTH_INELIGIBLE,
    )
    _admit(
        candidate.budget_state is CandidateBudgetState.WITHIN_LIMITS,
        CandidateAssemblyFailure.BUDGET_INELIGIBLE,
    )
    _admit(
        candidate.privacy_state is CandidatePrivacyState.APPROVED_PUBLIC,
        CandidateAssemblyFailure.PRIVACY_INELIGIBLE,
    )
    _admit(
        candidate.provider is not ModelCandidateProvider.AZURE_FOUNDRY
        or azure_enabled,
        CandidateAssemblyFailure.PROVIDER_DISABLED,
    )


def _assembled_source(
    ordinal: int,
    candidate: ManagedCandidateSource,
) -> AssembledManagedCandidate:
    ownership, cleanup = _resource_contract(candidate.provider)
    return AssembledManagedCandidate(
        ordinal=ordinal,
        candidate_identity_digest=candidate.current_identity_digest,
        configuration_digest=candidate.current_configuration_digest,
        provider=candidate.provider,
        resource_ownership=ownership,
        cleanup_action=cleanup,
        health_state=candidate.health_state,
        budget_state=candidate.budget_state,
        privacy_state=candidate.privacy_state,
    )


def assemble_managed_candidates(
    classification: CandidateTaskClassification,
    candidates: tuple[ManagedCandidateSource, ...],
    *,
    expected_classification_digest: str,
    required_providers: tuple[ModelCandidateProvider, ...],
    azure_enabled: bool,
) -> ManagedCandidateAssembly:
    """Admit and canonically order one bounded local, Azure, or mixed set."""
    current_classification = _classification_digest(
        classification,
        expected_classification_digest,
    )
    ordered = _ordered_sources(candidates)
    canonical_required = _canonical_providers(required_providers)
    _require_value(
        isinstance(azure_enabled, bool),
        "azure_enabled must be an explicit boolean",
    )
    seen: set[str] = set()
    for candidate in ordered:
        _admit_source(candidate, seen, azure_enabled=azure_enabled)
    represented = {candidate.provider for candidate in ordered}
    _admit(
        set(canonical_required).issubset(represented),
        CandidateAssemblyFailure.REQUIRED_PROVIDER_MISSING,
    )
    assembled = tuple(
        _assembled_source(ordinal, candidate)
        for ordinal, candidate in enumerate(ordered)
    )
    return ManagedCandidateAssembly(
        classification_digest=current_classification,
        task_text_digest=classification.task_text_digest,
        required_providers=canonical_required,
        azure_enabled=azure_enabled,
        candidates=assembled,
    )


__all__ = (
    "MANAGED_CANDIDATE_ASSEMBLY_PROTOCOL",
    "MAX_MANAGED_CANDIDATES",
    "AssembledManagedCandidate",
    "CandidateAssemblyError",
    "CandidateAssemblyFailure",
    "CandidateBudgetState",
    "CandidateCleanupAction",
    "CandidateHealthState",
    "CandidatePrivacyState",
    "CandidateResourceOwnership",
    "ManagedCandidateAssembly",
    "ManagedCandidateSource",
    "assemble_managed_candidates",
)
