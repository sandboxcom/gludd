"""Immutable artifacts for managed self-improvement candidate assembly."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Final

from general_ludd.self_improve.model_candidates import (
    AzureFoundryCandidateIdentity,
    LocalGGUFCandidateIdentity,
    ModelCandidateIdentity,
    ModelCandidateProvider,
)

MANAGED_CANDIDATE_ASSEMBLY_PROTOCOL: Final = "gludd-managed-candidate-assembly-v1"
MAX_MANAGED_CANDIDATES: Final = 16
_DIGEST_RE: Final = re.compile(r"^[0-9a-f]{64}$")
_PROVIDER_ORDER: Final = {
    ModelCandidateProvider.LOCAL_GGUF: 0,
    ModelCandidateProvider.AZURE_FOUNDRY: 1,
}


def _require_digest(value: object, field_name: str) -> str:
    if type(value) is not str or _DIGEST_RE.fullmatch(value) is None:
        raise ValueError(f"{field_name} must be a lowercase SHA-256 digest")
    return value


def _require_value(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def _stable_digest(payload: dict[str, object]) -> str:
    encoded = json.dumps(
        payload,
        allow_nan=False,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class CandidateHealthState(StrEnum):
    """Bounded health evidence supplied by an effect-free caller."""

    READY = "ready"
    UNHEALTHY = "unhealthy"
    UNKNOWN = "unknown"


class CandidateBudgetState(StrEnum):
    """Whether the caller's existing bounded session can admit a trial."""

    WITHIN_LIMITS = "within_limits"
    EXHAUSTED = "exhausted"
    UNKNOWN = "unknown"


class CandidatePrivacyState(StrEnum):
    """Whether the classified task passed the existing privacy boundary."""

    APPROVED_PUBLIC = "approved_public"
    BLOCKED = "blocked"
    UNKNOWN = "unknown"


class CandidateResourceOwnership(StrEnum):
    """The owner that remains responsible for an assembled resource."""

    CALLER_OWNED = "caller_owned"
    EXTERNAL_PROVIDER = "external_provider"


class CandidateCleanupAction(StrEnum):
    """Cleanup obligation retained by the owner after assembly."""

    RELEASE_LOCAL_LEASE = "release_local_lease"
    NONE = "none"


class CandidateAssemblyFailure(StrEnum):
    """Fixed, content-free reasons an assembly can be refused."""

    EMPTY_CANDIDATE_SET = "empty_candidate_set"
    CANDIDATE_LIMIT_EXCEEDED = "candidate_limit_exceeded"
    DUPLICATE_CANDIDATE = "duplicate_candidate"
    CLASSIFICATION_DRIFT = "classification_drift"
    IDENTITY_DRIFT = "identity_drift"
    CONFIGURATION_DRIFT = "configuration_drift"
    HEALTH_INELIGIBLE = "health_ineligible"
    BUDGET_INELIGIBLE = "budget_ineligible"
    PRIVACY_INELIGIBLE = "privacy_ineligible"
    PROVIDER_DISABLED = "provider_disabled"
    REQUIRED_PROVIDER_MISSING = "required_provider_missing"


class CandidateAssemblyError(RuntimeError):
    """Censored admission failure with no task or provider configuration text."""

    def __init__(self, failure: CandidateAssemblyFailure) -> None:
        """Retain exactly one fixed failure category."""
        _require_value(
            isinstance(failure, CandidateAssemblyFailure),
            "failure must be a CandidateAssemblyFailure",
        )
        super().__init__(f"managed candidate assembly blocked: {failure.value}")
        self.failure = failure


def _resource_contract(
    provider: ModelCandidateProvider,
) -> tuple[CandidateResourceOwnership, CandidateCleanupAction]:
    if provider is ModelCandidateProvider.LOCAL_GGUF:
        return (
            CandidateResourceOwnership.CALLER_OWNED,
            CandidateCleanupAction.RELEASE_LOCAL_LEASE,
        )
    return CandidateResourceOwnership.EXTERNAL_PROVIDER, CandidateCleanupAction.NONE


@dataclass(frozen=True, slots=True)
class ManagedCandidateSource:
    """One pre-discovered candidate and its effect-free admission attestations."""

    identity: ModelCandidateIdentity = field(repr=False)
    expected_identity_digest: str
    approved_configuration_digest: str
    current_configuration_digest: str
    health_state: CandidateHealthState
    budget_state: CandidateBudgetState
    privacy_state: CandidatePrivacyState

    def __post_init__(self) -> None:
        """Reject raw substitutes and ambiguous state before assembly begins."""
        _require_value(
            type(self.identity)
            in (LocalGGUFCandidateIdentity, AzureFoundryCandidateIdentity),
            "identity must be a typed model candidate identity",
        )
        _require_digest(self.expected_identity_digest, "expected_identity_digest")
        _require_digest(self.approved_configuration_digest, "approved_configuration_digest")
        _require_digest(self.current_configuration_digest, "current_configuration_digest")
        _require_value(
            isinstance(self.health_state, CandidateHealthState),
            "health_state must be a CandidateHealthState",
        )
        _require_value(
            isinstance(self.budget_state, CandidateBudgetState),
            "budget_state must be a CandidateBudgetState",
        )
        _require_value(
            isinstance(self.privacy_state, CandidatePrivacyState),
            "privacy_state must be a CandidatePrivacyState",
        )

    @property
    def provider(self) -> ModelCandidateProvider:
        """Return the provider from the existing typed identity contract."""
        return self.identity.provider

    @property
    def current_identity_digest(self) -> str:
        """Return the immutable identity observed at assembly time."""
        return self.identity.identity_digest


@dataclass(frozen=True, slots=True)
class AssembledManagedCandidate:
    """One admitted identity with no endpoint, path, credential, or task content."""

    ordinal: int
    candidate_identity_digest: str
    configuration_digest: str
    provider: ModelCandidateProvider
    resource_ownership: CandidateResourceOwnership
    cleanup_action: CandidateCleanupAction
    health_state: CandidateHealthState = CandidateHealthState.READY
    budget_state: CandidateBudgetState = CandidateBudgetState.WITHIN_LIMITS
    privacy_state: CandidatePrivacyState = CandidatePrivacyState.APPROVED_PUBLIC

    def __post_init__(self) -> None:
        """Require canonical, eligible output and provider-specific ownership."""
        _require_value(
            not isinstance(self.ordinal, bool)
            and isinstance(self.ordinal, int)
            and 0 <= self.ordinal < MAX_MANAGED_CANDIDATES,
            "ordinal is outside the bounded candidate range",
        )
        _require_digest(self.candidate_identity_digest, "candidate_identity_digest")
        _require_digest(self.configuration_digest, "configuration_digest")
        _require_value(
            isinstance(self.provider, ModelCandidateProvider),
            "provider must be a ModelCandidateProvider",
        )
        _require_value(
            isinstance(self.resource_ownership, CandidateResourceOwnership),
            "resource_ownership must be a CandidateResourceOwnership",
        )
        _require_value(
            isinstance(self.cleanup_action, CandidateCleanupAction),
            "cleanup_action must be a CandidateCleanupAction",
        )
        _require_value(
            self.health_state is CandidateHealthState.READY
            and self.budget_state is CandidateBudgetState.WITHIN_LIMITS
            and self.privacy_state is CandidatePrivacyState.APPROVED_PUBLIC,
            "assembled candidate must retain eligible states",
        )
        _require_value(
            (self.resource_ownership, self.cleanup_action)
            == _resource_contract(self.provider),
            "resource contract does not match candidate provider",
        )

    @property
    def assembler_owns_resource(self) -> bool:
        """State explicitly that this pure assembler acquires no resources."""
        return False

    def payload(self) -> dict[str, object]:
        """Return one canonical content-free candidate record."""
        return {
            "assembler_owns_resource": self.assembler_owns_resource,
            "budget_state": self.budget_state.value,
            "candidate_identity_digest": self.candidate_identity_digest,
            "cleanup_action": self.cleanup_action.value,
            "configuration_digest": self.configuration_digest,
            "health_state": self.health_state.value,
            "ordinal": self.ordinal,
            "privacy_state": self.privacy_state.value,
            "provider": self.provider.value,
            "resource_ownership": self.resource_ownership.value,
        }


def _canonical_providers(
    providers: tuple[ModelCandidateProvider, ...],
) -> tuple[ModelCandidateProvider, ...]:
    _require_value(
        type(providers) is tuple and bool(providers),
        "required_providers must be a non-empty tuple",
    )
    _require_value(
        all(isinstance(provider, ModelCandidateProvider) for provider in providers),
        "required_providers must contain ModelCandidateProvider values",
    )
    _require_value(
        len(set(providers)) == len(providers),
        "required_providers must not contain duplicates",
    )
    return tuple(sorted(providers, key=_PROVIDER_ORDER.__getitem__))


@dataclass(frozen=True, slots=True)
class ManagedCandidateAssembly:
    """Immutable, replayable result of one complete candidate-set admission."""

    classification_digest: str
    task_text_digest: str
    required_providers: tuple[ModelCandidateProvider, ...]
    azure_enabled: bool
    candidates: tuple[AssembledManagedCandidate, ...]
    protocol: str = MANAGED_CANDIDATE_ASSEMBLY_PROTOCOL

    def __post_init__(self) -> None:
        """Prevent manual construction of an ambiguous or reordered artifact."""
        _require_digest(self.classification_digest, "classification_digest")
        _require_digest(self.task_text_digest, "task_text_digest")
        _require_value(
            self.protocol == MANAGED_CANDIDATE_ASSEMBLY_PROTOCOL,
            "protocol is not supported",
        )
        _require_value(
            isinstance(self.azure_enabled, bool),
            "azure_enabled must be an explicit boolean",
        )
        canonical_required = _canonical_providers(self.required_providers)
        _require_value(
            canonical_required == self.required_providers,
            "required_providers must follow canonical provider order",
        )
        _require_value(
            type(self.candidates) is tuple
            and bool(self.candidates)
            and len(self.candidates) <= MAX_MANAGED_CANDIDATES
            and all(
                isinstance(candidate, AssembledManagedCandidate)
                for candidate in self.candidates
            ),
            "candidates must be one bounded immutable candidate set",
        )
        _require_value(
            tuple(candidate.ordinal for candidate in self.candidates)
            == tuple(range(len(self.candidates))),
            "candidate ordinals must be contiguous from zero",
        )
        canonical = tuple(
            sorted(
                self.candidates,
                key=lambda candidate: (
                    _PROVIDER_ORDER[candidate.provider],
                    candidate.candidate_identity_digest,
                ),
            )
        )
        _require_value(
            canonical == self.candidates,
            "candidates must follow canonical provider and identity order",
        )
        identities = tuple(
            candidate.candidate_identity_digest for candidate in self.candidates
        )
        _require_value(
            len(set(identities)) == len(identities),
            "candidates must have unique immutable identities",
        )
        _require_value(
            set(self.required_providers).issubset(self.providers),
            "required provider is missing from candidates",
        )
        _require_value(
            ModelCandidateProvider.AZURE_FOUNDRY not in self.providers
            or self.azure_enabled,
            "Azure candidates require explicit opt-in",
        )

    @property
    def providers(self) -> tuple[ModelCandidateProvider, ...]:
        """Return represented providers in canonical order without duplicates."""
        represented = {candidate.provider for candidate in self.candidates}
        return tuple(
            provider
            for provider in sorted(represented, key=_PROVIDER_ORDER.__getitem__)
        )

    def payload(self) -> dict[str, object]:
        """Return the complete canonical artifact used for digest replay."""
        return {
            "azure_enabled": self.azure_enabled,
            "candidate_count": len(self.candidates),
            "candidates": [candidate.payload() for candidate in self.candidates],
            "classification_digest": self.classification_digest,
            "protocol": self.protocol,
            "required_providers": [
                provider.value for provider in self.required_providers
            ],
            "task_text_digest": self.task_text_digest,
        }

    @property
    def assembly_digest(self) -> str:
        """Return a stable identity binding classification, policy, and order."""
        return _stable_digest(self.payload())

    def event_payloads(self) -> tuple[dict[str, object], ...]:
        """Return ordered, content-free records sufficient to replay assembly."""
        admitted = tuple(
            {
                "assembly_digest": self.assembly_digest,
                "classification_digest": self.classification_digest,
                "event": "self_improve_managed_candidate_admitted",
                "protocol": self.protocol,
                **candidate.payload(),
            }
            for candidate in self.candidates
        )
        completed: dict[str, object] = {
            "assembly_digest": self.assembly_digest,
            "candidate_count": len(self.candidates),
            "classification_digest": self.classification_digest,
            "event": "self_improve_managed_candidates_assembled",
            "protocol": self.protocol,
            "providers": [provider.value for provider in self.providers],
            "required_providers": [
                provider.value for provider in self.required_providers
            ],
            "task_text_digest": self.task_text_digest,
        }
        return (*admitted, completed)
