"""Opt-in live discovery and assembly for managed model candidates.

The managed runner keeps this boundary disabled unless an explicit wiring policy
is supplied.  When enabled, it consumes the local identity already established by
the model lease, discovers only the configured Azure deployment, authorizes both
provider sessions, and emits content-free assembly evidence.  It never selects a
replacement provider or invokes Azure as a fallback for the legacy local runner.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from collections.abc import Callable, Mapping
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Generic, Protocol, TypeVar, cast, runtime_checkable

from general_ludd.self_improve._candidate_execution_types import (
    CandidateExecutionBoundary,
)
from general_ludd.self_improve.azure_backend import (
    AzureApprovedPrompt,
    AzureCandidateResponse,
    AzureOpenAIConfig,
    build_azure_openai_candidate_backend,
)
from general_ludd.self_improve.azure_containerapp_backend import (
    build_azure_containerapp_candidate_backend,
)
from general_ludd.self_improve.candidate_classification import (
    CandidateTaskClassification,
)
from general_ludd.self_improve.managed_candidate_assembly import (
    CandidateAssemblyError,
    CandidateAssemblyFailure,
    CandidateBudgetState,
    CandidateHealthState,
    CandidatePrivacyState,
    ManagedCandidateAssembly,
    ManagedCandidateSource,
    assemble_managed_candidates,
)
from general_ludd.self_improve.managed_candidate_routing_types import (
    ManagedCandidateProposalEnvelope,
)
from general_ludd.self_improve.model_candidates import (
    AzureContainerAppCandidateIdentity,
    AzureFoundryCandidateIdentity,
    BackendCallBudget,
    BackendInfrastructureError,
    BoundedCandidateSession,
    CandidateBackend,
    ModelCandidateProvider,
)
from general_ludd.self_improve.private_policy import SelfImproveRuntimePolicyGuard

LIVE_CANDIDATE_WIRING_PROTOCOL = "gludd-live-candidate-wiring-v1"
_MAX_COST_MICROUSD = 1_000_000_000_000

_RequestT = TypeVar("_RequestT")
_ResponseT = TypeVar("_ResponseT")
_DEFAULT_AZURE_BACKEND_FACTORY = cast(
    "AzureCandidateBackendFactory",
    build_azure_openai_candidate_backend,
)
_DEFAULT_CONTAINERAPP_BACKEND_FACTORY = cast(
    "ContainerAppCandidateBackendFactory",
    build_azure_containerapp_candidate_backend,
)


@runtime_checkable
class AzureCandidateBackend(Protocol):
    """Owned exact-deployment backend returned by live Azure discovery."""

    @property
    def candidate_identity(self) -> AzureFoundryCandidateIdentity:
        """Return the immutable deployment identity captured by discovery."""
        ...

    def generate(
        self,
        request: AzureApprovedPrompt,
        *,
        max_output_tokens: int,
        timeout_seconds: float,
    ) -> AzureCandidateResponse:
        """Invoke only the discovered deployment under explicit limits."""
        ...

    def close(self) -> None:
        """Release every SDK resource owned by discovery."""
        ...


@runtime_checkable
class AzureCandidateBackendFactory(Protocol):
    """Construct exactly one configured live Azure backend."""

    def __call__(self, config: AzureOpenAIConfig) -> AzureCandidateBackend:
        """Discover the configured deployment without fallback."""
        ...


@runtime_checkable
class ContainerAppCandidateBackend(Protocol):
    """Owned exact Container App backend returned after model discovery."""

    @property
    def candidate_identity(self) -> AzureContainerAppCandidateIdentity:
        """Return the immutable app revision identity."""
        ...

    def generate(
        self,
        request: AzureApprovedPrompt,
        *,
        max_output_tokens: int,
        timeout_seconds: float,
    ) -> AzureCandidateResponse:
        """Invoke only the exact Container App model under explicit limits."""
        ...

    def close(self) -> None:
        """Release the bounded HTTP client."""
        ...


@runtime_checkable
class ContainerAppCandidateBackendFactory(Protocol):
    """Construct exactly one discovered Container App backend."""

    def __call__(
        self,
        identity: AzureContainerAppCandidateIdentity,
    ) -> ContainerAppCandidateBackend:
        """Bind the configured app revision without fallback."""
        ...


@runtime_checkable
class ContainerAppCandidateBootstrapFactory(Protocol):
    """Lazily provision one exact Container App candidate and own its cleanup."""

    @property
    def deployment_digest(self) -> str:
        """Return the immutable approved infrastructure configuration digest."""
        ...

    def __call__(self) -> ContainerAppCandidateBackend:
        """Create missing infrastructure and return its lifecycle-owning backend."""
        ...


def _discard_event(_event: dict[str, object]) -> None:
    return


def _budget_payload(budget: BackendCallBudget) -> dict[str, object]:
    return {
        "max_calls": budget.max_calls,
        "max_cost_microusd": budget.max_cost_microusd,
        "max_input_tokens": budget.max_input_tokens,
        "max_output_tokens": budget.max_output_tokens,
        "max_total_tokens": budget.max_total_tokens,
        "timeout_seconds": budget.timeout_seconds,
    }


def _configuration_digest(
    policy: LiveCandidateWiringPolicy,
    provider: ModelCandidateProvider,
) -> str:
    payload: dict[str, object] = {
        "budget": _budget_payload(policy.local_budget),
        "continue_on_remote_infrastructure_failure": (
            policy.continue_on_remote_infrastructure_failure
        ),
        "protocol": LIVE_CANDIDATE_WIRING_PROTOCOL,
        "provider": provider.value,
        "required_providers": [item.value for item in policy.required_providers],
    }
    if provider is ModelCandidateProvider.AZURE_FOUNDRY:
        config = policy.azure_config
        budget = policy.azure_budget
        if config is None or budget is None:
            raise ValueError("Azure configuration is incomplete")
        payload = {
            "account_name": config.account_name,
            "api_family": config.api_family.value,
            "api_version": config.api_version,
            "azure_enabled": config.azure_enabled,
            "budget": _budget_payload(budget),
            "continue_on_remote_infrastructure_failure": (
                policy.continue_on_remote_infrastructure_failure
            ),
            "credential_environment": config.credential.environment_variable,
            "credential_source": config.credential.source.value,
            "deployment": config.deployment,
            "discovery_timeout_seconds": config.discovery_timeout_seconds,
            "endpoint": config.endpoint,
            "estimated_cost_microusd": policy.azure_estimated_cost_microusd,
            "protocol": LIVE_CANDIDATE_WIRING_PROTOCOL,
            "provider": provider.value,
            "required_providers": [item.value for item in policy.required_providers],
            "resource_group": config.resource_group,
            "subscription_id": config.subscription_id,
        }
    elif provider is ModelCandidateProvider.AZURE_CONTAINER_APP:
        identity = policy.containerapp_identity
        bootstrap_digest = policy.containerapp_bootstrap_digest
        budget = policy.containerapp_budget
        if (identity is None) == (bootstrap_digest is None) or budget is None:
            raise ValueError("Azure Container App configuration is incomplete")
        payload = {
            "azure_enabled": True,
            "budget": _budget_payload(budget),
            "continue_on_remote_infrastructure_failure": (
                policy.continue_on_remote_infrastructure_failure
            ),
            "estimated_cost_microusd": (
                policy.containerapp_estimated_cost_microusd
            ),
            "protocol": LIVE_CANDIDATE_WIRING_PROTOCOL,
            "provider": provider.value,
            "required_providers": [item.value for item in policy.required_providers],
        }
        if identity is not None:
            payload["candidate_identity_digest"] = identity.identity_digest
            payload["deployment_mode"] = "attach"
        else:
            payload["bootstrap_digest"] = bootstrap_digest
            payload["deployment_mode"] = "bootstrap"
    encoded = json.dumps(
        payload,
        allow_nan=False,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True, slots=True)
class LiveCandidateWiringPolicy:
    """Explicit bounded policy whose presence enables live candidate wiring."""

    local_budget: BackendCallBudget
    required_providers: tuple[ModelCandidateProvider, ...] = (
        ModelCandidateProvider.LOCAL_GGUF,
    )
    azure_config: AzureOpenAIConfig | None = None
    azure_budget: BackendCallBudget | None = None
    azure_estimated_cost_microusd: int = 0
    containerapp_identity: AzureContainerAppCandidateIdentity | None = None
    containerapp_bootstrap_digest: str | None = None
    containerapp_budget: BackendCallBudget | None = None
    containerapp_estimated_cost_microusd: int = 0
    continue_on_remote_infrastructure_failure: bool = False

    def __post_init__(self) -> None:
        """Reject ambiguous provider, opt-in, and budget combinations."""
        if not isinstance(self.local_budget, BackendCallBudget):
            raise ValueError("local_budget must be a BackendCallBudget")
        if not isinstance(self.continue_on_remote_infrastructure_failure, bool):
            raise ValueError(
                "continue_on_remote_infrastructure_failure must be an explicit boolean"
            )
        if (
            type(self.required_providers) is not tuple
            or not self.required_providers
            or any(
                not isinstance(provider, ModelCandidateProvider)
                for provider in self.required_providers
            )
            or len(set(self.required_providers)) != len(self.required_providers)
        ):
            raise ValueError("required_providers must be one unique typed tuple")
        if (
            isinstance(self.azure_estimated_cost_microusd, bool)
            or not isinstance(self.azure_estimated_cost_microusd, int)
            or not 0 <= self.azure_estimated_cost_microusd <= _MAX_COST_MICROUSD
        ):
            raise ValueError("azure_estimated_cost_microusd is outside its hard bound")
        if (
            isinstance(self.containerapp_estimated_cost_microusd, bool)
            or not isinstance(self.containerapp_estimated_cost_microusd, int)
            or not 0
            <= self.containerapp_estimated_cost_microusd
            <= _MAX_COST_MICROUSD
        ):
            raise ValueError(
                "containerapp_estimated_cost_microusd is outside its hard bound"
            )
        if self.azure_config is None:
            if self.azure_budget is not None or self.azure_estimated_cost_microusd != 0:
                raise ValueError("azure_config is required for Azure budget or cost")
            if ModelCandidateProvider.AZURE_FOUNDRY in self.required_providers:
                raise ValueError("required Azure provider needs explicit Azure configuration")
        else:
            if not isinstance(self.azure_config, AzureOpenAIConfig):
                raise ValueError("azure_config must be an AzureOpenAIConfig")
            if not self.azure_config.azure_enabled:
                raise ValueError("Azure candidate wiring must be explicitly enabled")
            if not isinstance(self.azure_budget, BackendCallBudget):
                raise ValueError("azure_budget must be a BackendCallBudget")
            if self.azure_estimated_cost_microusd > self.azure_budget.max_cost_microusd:
                raise ValueError("Azure estimated cost exceeds its configured budget")

        configured_containerapp = (
            self.containerapp_identity is not None
            or self.containerapp_bootstrap_digest is not None
        )
        if (
            self.containerapp_identity is not None
            and self.containerapp_bootstrap_digest is not None
        ):
            raise ValueError(
                "Container App configuration must select exactly one attach or bootstrap mode"
            )
        if self.containerapp_bootstrap_digest is not None and (
            len(self.containerapp_bootstrap_digest) != 64
            or any(
                character not in "0123456789abcdef"
                for character in self.containerapp_bootstrap_digest
            )
        ):
            raise ValueError("containerapp_bootstrap_digest must be a lowercase SHA-256")
        if not configured_containerapp:
            if (
                self.containerapp_budget is not None
                or self.containerapp_estimated_cost_microusd != 0
            ):
                raise ValueError(
                    "containerapp_identity or bootstrap digest is required for Container App budget or cost"
                )
            if ModelCandidateProvider.AZURE_CONTAINER_APP in self.required_providers:
                raise ValueError(
                    "required Azure Container App provider needs an explicit identity"
                )
        else:
            if (
                self.containerapp_identity is not None
                and type(self.containerapp_identity)
                is not AzureContainerAppCandidateIdentity
            ):
                raise ValueError(
                    "containerapp_identity must be an AzureContainerAppCandidateIdentity"
                )
            if not isinstance(self.containerapp_budget, BackendCallBudget):
                raise ValueError("containerapp_budget must be a BackendCallBudget")
            if (
                self.containerapp_estimated_cost_microusd
                > self.containerapp_budget.max_cost_microusd
            ):
                raise ValueError(
                    "Container App estimated cost exceeds its configured budget"
                )


class LiveManagedCandidateSet(Generic[_RequestT, _ResponseT]):
    """One authorized assembly plus sessions whose ownership is explicit."""

    def __init__(
        self,
        assembly: ManagedCandidateAssembly,
        local_session: BoundedCandidateSession[_RequestT, _ResponseT] | None,
        azure_session: BoundedCandidateSession[
            AzureApprovedPrompt, AzureCandidateResponse
        ]
        | None,
        azure_backend: AzureCandidateBackend | None,
        containerapp_session: BoundedCandidateSession[
            AzureApprovedPrompt, AzureCandidateResponse
        ]
        | None,
        containerapp_backend: ContainerAppCandidateBackend | None,
    ) -> None:
        """Retain only the resources needed until the caller leaves the scope."""
        self.assembly = assembly
        self.local_session = local_session
        self.azure_session = azure_session
        self.containerapp_session = containerapp_session
        self._azure_backend = azure_backend
        self._containerapp_backend = containerapp_backend
        self._closed = False

    @property
    def protocol(self) -> str:
        """Return the versioned live-wiring contract identifier."""
        return LIVE_CANDIDATE_WIRING_PROTOCOL

    def close(self) -> None:
        """Close the optional Azure SDK owner exactly once."""
        if self._closed:
            return
        self._closed = True
        failed = False
        for backend in (self._containerapp_backend, self._azure_backend):
            if backend is None:
                continue
            try:
                backend.close()
            except Exception:
                failed = True
        if failed:
            raise RuntimeError("managed candidate backend cleanup failed")

    def __enter__(self) -> LiveManagedCandidateSet[_RequestT, _ResponseT]:
        """Return this bounded assembly scope."""
        return self

    def __exit__(
        self,
        _exc_type: object,
        _exc_value: object,
        _traceback: object,
    ) -> None:
        """Release Azure resources while the outer owner retains the local lease."""
        self.close()


def bind_managed_candidate_execution_boundary(
    *,
    repo_root: Path | None,
    policy_digest: str,
    progress_sink: Callable[[str], None],
    violation_factory: Callable[[], Exception],
    request_text: str,
    source_paths: tuple[str, ...],
    expected_project_identity_digest: str,
    project_identity_probe: Callable[[], str],
    remote_enabled: bool,
    proposal_envelope: ManagedCandidateProposalEnvelope | None = None,
) -> tuple[CandidateExecutionBoundary, AzureApprovedPrompt | None]:
    """Bind project identity, privacy policy, and optional remote prompt authority."""
    if repo_root is None:
        raise violation_factory()
    policy_guard = SelfImproveRuntimePolicyGuard.bound(
        repo_root,
        policy_digest,
        progress_sink,
        violation_factory,
    )
    if proposal_envelope is not None and (
        type(proposal_envelope) is not ManagedCandidateProposalEnvelope
        or proposal_envelope.request_text != request_text
    ):
        raise violation_factory()
    approved_prompt = None
    if remote_enabled:
        approved_prompt = (
            AzureApprovedPrompt.approve_envelope(
                envelope=proposal_envelope,
                source_paths=source_paths,
                policy_guard=policy_guard,
            )
            if proposal_envelope is not None
            else AzureApprovedPrompt.approve(
                prompt=request_text,
                source_paths=source_paths,
                policy_guard=policy_guard,
            )
        )
    return (
        CandidateExecutionBoundary(
            policy_guard=policy_guard,
            source_paths=source_paths,
            expected_project_identity_digest=expected_project_identity_digest,
            project_identity_probe=project_identity_probe,
        ),
        approved_prompt,
    )


@dataclass(slots=True)
class _DiscoveredRemoteCandidates:
    """Remote backends and sessions owned during fail-closed assembly."""

    azure_backend: AzureCandidateBackend | None = None
    azure_session: BoundedCandidateSession[
        AzureApprovedPrompt, AzureCandidateResponse
    ] | None = None
    containerapp_backend: ContainerAppCandidateBackend | None = None
    containerapp_session: BoundedCandidateSession[
        AzureApprovedPrompt, AzureCandidateResponse
    ] | None = None

    def close_safely(self) -> None:
        """Suppress cleanup details while preserving the triggering failure."""
        for backend in (self.containerapp_backend, self.azure_backend):
            if backend is not None:
                with suppress(Exception):
                    backend.close()


class LiveManagedCandidateWiring:
    """Lazily discover and assemble an explicitly enabled live candidate set."""

    def __init__(
        self,
        policy: LiveCandidateWiringPolicy,
        *,
        azure_backend_factory: AzureCandidateBackendFactory = (
            _DEFAULT_AZURE_BACKEND_FACTORY
        ),
        containerapp_backend_factory: ContainerAppCandidateBackendFactory = (
            _DEFAULT_CONTAINERAPP_BACKEND_FACTORY
        ),
        containerapp_bootstrap_factory: (
            ContainerAppCandidateBootstrapFactory | None
        ) = None,
        event_sink: Callable[[dict[str, object]], None] = _discard_event,
    ) -> None:
        """Snapshot approved non-secret configuration without provider effects."""
        if not isinstance(policy, LiveCandidateWiringPolicy):
            raise ValueError("policy must be a LiveCandidateWiringPolicy")
        if not isinstance(azure_backend_factory, AzureCandidateBackendFactory):
            raise ValueError("azure_backend_factory must implement its protocol")
        if not isinstance(
            containerapp_backend_factory,
            ContainerAppCandidateBackendFactory,
        ):
            raise ValueError(
                "containerapp_backend_factory must implement its protocol"
            )
        if not callable(event_sink):
            raise ValueError("event_sink must be callable")
        bootstrap_digest = policy.containerapp_bootstrap_digest
        if bootstrap_digest is None and containerapp_bootstrap_factory is not None:
            raise ValueError("Container App bootstrap factory is not configured by policy")
        if bootstrap_digest is not None:
            if containerapp_bootstrap_factory is None:
                raise ValueError("Container App bootstrap factory is required")
            if not isinstance(
                containerapp_bootstrap_factory,
                ContainerAppCandidateBootstrapFactory,
            ):
                raise ValueError("Container App bootstrap factory has an invalid boundary")
            if not hmac.compare_digest(
                containerapp_bootstrap_factory.deployment_digest,
                bootstrap_digest,
            ):
                raise ValueError("Container App bootstrap factory digest does not match policy")
        self._policy = policy
        self._azure_backend_factory = azure_backend_factory
        self._containerapp_backend_factory = containerapp_backend_factory
        self._containerapp_bootstrap_factory = containerapp_bootstrap_factory
        self._event_sink = event_sink
        self._approved_local_configuration_digest = _configuration_digest(
            policy,
            ModelCandidateProvider.LOCAL_GGUF,
        )
        self._approved_azure_configuration_digest = (
            None
            if policy.azure_config is None
            else _configuration_digest(policy, ModelCandidateProvider.AZURE_FOUNDRY)
        )
        self._approved_containerapp_configuration_digest = (
            None
            if (
                policy.containerapp_identity is None
                and policy.containerapp_bootstrap_digest is None
            )
            else _configuration_digest(
                policy,
                ModelCandidateProvider.AZURE_CONTAINER_APP,
            )
        )

    @property
    def policy(self) -> LiveCandidateWiringPolicy:
        """Return the immutable activation policy captured at construction."""
        return self._policy

    def _emit(self, event: Mapping[str, object]) -> None:
        try:
            self._event_sink(dict(event))
        except Exception:
            raise RuntimeError("managed candidate event publication failed") from None

    def _current_configuration_digest(
        self,
        provider: ModelCandidateProvider,
    ) -> str:
        """Re-derive current configuration or expose only typed drift."""
        try:
            return _configuration_digest(self._policy, provider)
        except Exception:
            raise CandidateAssemblyError(
                CandidateAssemblyFailure.CONFIGURATION_DRIFT
            ) from None

    def _local_source(
        self,
        local_session: BoundedCandidateSession[_RequestT, _ResponseT],
        privacy_state: CandidatePrivacyState,
    ) -> ManagedCandidateSource:
        identity = local_session.candidate_identity
        return ManagedCandidateSource(
            identity=identity,
            expected_identity_digest=identity.identity_digest,
            approved_configuration_digest=(
                self._approved_local_configuration_digest
            ),
            current_configuration_digest=self._current_configuration_digest(
                ModelCandidateProvider.LOCAL_GGUF,
            ),
            health_state=CandidateHealthState.READY,
            budget_state=CandidateBudgetState.WITHIN_LIMITS,
            privacy_state=privacy_state,
        )

    def _azure_source(
        self,
        azure_session: BoundedCandidateSession[
            AzureApprovedPrompt, AzureCandidateResponse
        ],
        privacy_state: CandidatePrivacyState,
    ) -> ManagedCandidateSource:
        approved_digest = self._approved_azure_configuration_digest
        if approved_digest is None:
            raise ValueError("Azure configuration was not approved")
        identity = azure_session.candidate_identity
        return ManagedCandidateSource(
            identity=identity,
            expected_identity_digest=identity.identity_digest,
            approved_configuration_digest=approved_digest,
            current_configuration_digest=self._current_configuration_digest(
                ModelCandidateProvider.AZURE_FOUNDRY,
            ),
            health_state=CandidateHealthState.READY,
            budget_state=CandidateBudgetState.WITHIN_LIMITS,
            privacy_state=privacy_state,
        )

    def _containerapp_source(
        self,
        containerapp_session: BoundedCandidateSession[
            AzureApprovedPrompt, AzureCandidateResponse
        ],
        privacy_state: CandidatePrivacyState,
    ) -> ManagedCandidateSource:
        approved_digest = self._approved_containerapp_configuration_digest
        if approved_digest is None:
            raise ValueError("Azure Container App configuration was not approved")
        identity = containerapp_session.candidate_identity
        return ManagedCandidateSource(
            identity=identity,
            expected_identity_digest=identity.identity_digest,
            approved_configuration_digest=approved_digest,
            current_configuration_digest=self._current_configuration_digest(
                ModelCandidateProvider.AZURE_CONTAINER_APP,
            ),
            health_state=CandidateHealthState.READY,
            budget_state=CandidateBudgetState.WITHIN_LIMITS,
            privacy_state=privacy_state,
        )

    def _require_configuration_unchanged(
        self,
        provider: ModelCandidateProvider,
        *,
        configured: bool,
        approved_digest: str | None,
    ) -> None:
        current_digest = (
            self._current_configuration_digest(provider) if configured else None
        )
        both_absent = current_digest is None and approved_digest is None
        matches = bool(
            current_digest is not None
            and approved_digest is not None
            and hmac.compare_digest(current_digest, approved_digest)
        )
        if not both_absent and not matches:
            raise CandidateAssemblyError(CandidateAssemblyFailure.CONFIGURATION_DRIFT)

    def _discover_remote_candidates(self) -> _DiscoveredRemoteCandidates:
        """Construct only explicitly configured remote sessions."""
        discovered = _DiscoveredRemoteCandidates()
        try:
            if self._policy.azure_config is not None:
                azure_backend = self._azure_backend_factory(self._policy.azure_config)
                if not isinstance(azure_backend, AzureCandidateBackend):
                    raise ValueError("Azure discovery returned an invalid backend")
                discovered.azure_backend = azure_backend
                discovered.azure_session = BoundedCandidateSession(
                    azure_backend,
                    cast(BackendCallBudget, self._policy.azure_budget),
                    azure_enabled=True,
                )
            if (
                self._policy.containerapp_identity is not None
                or self._policy.containerapp_bootstrap_digest is not None
            ):
                if self._policy.containerapp_bootstrap_digest is not None:
                    bootstrap_factory = self._containerapp_bootstrap_factory
                    if (
                        bootstrap_factory is None
                        or not hmac.compare_digest(
                            bootstrap_factory.deployment_digest,
                            self._policy.containerapp_bootstrap_digest,
                        )
                    ):
                        raise CandidateAssemblyError(
                            CandidateAssemblyFailure.CONFIGURATION_DRIFT
                        )
                    containerapp_backend = bootstrap_factory()
                else:
                    identity = self._policy.containerapp_identity
                    if identity is None:
                        raise CandidateAssemblyError(
                            CandidateAssemblyFailure.CONFIGURATION_DRIFT
                        )
                    containerapp_backend = self._containerapp_backend_factory(identity)
                if (
                    not isinstance(containerapp_backend, ContainerAppCandidateBackend)
                    or type(containerapp_backend.candidate_identity)
                    is not AzureContainerAppCandidateIdentity
                ):
                    raise ValueError(
                        "Azure Container App discovery returned an invalid backend"
                    )
                discovered.containerapp_backend = containerapp_backend
                discovered.containerapp_session = BoundedCandidateSession(
                    containerapp_backend,
                    cast(BackendCallBudget, self._policy.containerapp_budget),
                    azure_enabled=True,
                )
            return discovered
        except BaseException:
            discovered.close_safely()
            raise

    def _authorized_assembly(
        self,
        classification: CandidateTaskClassification,
        expected_classification_digest: str,
        local_session: BoundedCandidateSession[_RequestT, _ResponseT] | None,
        discovered: _DiscoveredRemoteCandidates,
        privacy_state: CandidatePrivacyState,
        *,
        input_tokens: int,
        max_output_tokens: int,
    ) -> ManagedCandidateAssembly:
        """Reauthorize all sessions after discovery and assemble exact sources."""
        sources: list[ManagedCandidateSource] = []
        if local_session is not None:
            local_session.authorize(
                input_tokens=input_tokens,
                max_output_tokens=max_output_tokens,
                estimated_cost_microusd=0,
            )
            sources.append(self._local_source(local_session, privacy_state))
        if discovered.azure_session is not None:
            discovered.azure_session.authorize(
                input_tokens=input_tokens,
                max_output_tokens=max_output_tokens,
                estimated_cost_microusd=self._policy.azure_estimated_cost_microusd,
            )
            sources.append(self._azure_source(discovered.azure_session, privacy_state))
        if discovered.containerapp_session is not None:
            discovered.containerapp_session.authorize(
                input_tokens=input_tokens,
                max_output_tokens=max_output_tokens,
                estimated_cost_microusd=(
                    self._policy.containerapp_estimated_cost_microusd
                ),
            )
            sources.append(
                self._containerapp_source(
                    discovered.containerapp_session,
                    privacy_state,
                )
            )
        return assemble_managed_candidates(
            classification,
            tuple(sources),
            expected_classification_digest=expected_classification_digest,
            required_providers=self._policy.required_providers,
            azure_enabled=(
                self._policy.azure_config is not None
                or self._policy.containerapp_identity is not None
                or self._policy.containerapp_bootstrap_digest is not None
            ),
        )

    def _local_only_after_remote_infrastructure_failure(
        self,
        classification: CandidateTaskClassification,
        expected_classification_digest: str,
        local_session: BoundedCandidateSession[_RequestT, _ResponseT],
        privacy_state: CandidatePrivacyState,
        error: BackendInfrastructureError,
    ) -> LiveManagedCandidateSet[_RequestT, _ResponseT]:
        """Reauthorize and expose the local candidate after a typed cloud outage."""
        local_source = self._local_source(local_session, privacy_state)
        assembly = assemble_managed_candidates(
            classification,
            (local_source,),
            expected_classification_digest=expected_classification_digest,
            required_providers=(ModelCandidateProvider.LOCAL_GGUF,),
            azure_enabled=False,
        )
        self._emit(
            {
                "event": "self_improve_remote_infrastructure_skipped",
                "failure": error.failure.value,
                "schema_version": 1,
            }
        )
        self._emit(classification.event_payload())
        for event in assembly.event_payloads():
            self._emit(event)
        return LiveManagedCandidateSet(
            assembly,
            local_session,
            None,
            None,
            None,
            None,
        )

    def assemble(
        self,
        classification: CandidateTaskClassification,
        *,
        expected_classification_digest: str,
        local_backend: CandidateBackend[_RequestT, _ResponseT] | None,
        privacy_state: CandidatePrivacyState,
        input_tokens: int,
        max_output_tokens: int,
    ) -> LiveManagedCandidateSet[_RequestT, _ResponseT]:
        """Authorize, discover, and assemble once without provider fallback."""
        local_session: BoundedCandidateSession[_RequestT, _ResponseT] | None = None
        if local_backend is not None:
            local_session = BoundedCandidateSession(
                local_backend,
                self._policy.local_budget,
                azure_enabled=False,
            )
            local_session.authorize(
                input_tokens=input_tokens,
                max_output_tokens=max_output_tokens,
                estimated_cost_microusd=0,
            )
            local_source = self._local_source(local_session, privacy_state)
            assemble_managed_candidates(
                classification,
                (local_source,),
                expected_classification_digest=expected_classification_digest,
                required_providers=(ModelCandidateProvider.LOCAL_GGUF,),
                azure_enabled=False,
            )

        self._require_configuration_unchanged(
            ModelCandidateProvider.AZURE_FOUNDRY,
            configured=self._policy.azure_config is not None,
            approved_digest=self._approved_azure_configuration_digest,
        )
        self._require_configuration_unchanged(
            ModelCandidateProvider.AZURE_CONTAINER_APP,
            configured=(
                self._policy.containerapp_identity is not None
                or self._policy.containerapp_bootstrap_digest is not None
            ),
            approved_digest=self._approved_containerapp_configuration_digest,
        )

        discovered = _DiscoveredRemoteCandidates()
        try:
            discovered = self._discover_remote_candidates()
            assembly = self._authorized_assembly(
                classification,
                expected_classification_digest,
                local_session,
                discovered,
                privacy_state,
                input_tokens=input_tokens,
                max_output_tokens=max_output_tokens,
            )
            candidate_set = LiveManagedCandidateSet(
                assembly,
                local_session,
                discovered.azure_session,
                discovered.azure_backend,
                discovered.containerapp_session,
                discovered.containerapp_backend,
            )
            self._emit(classification.event_payload())
            for event in assembly.event_payloads():
                self._emit(event)
            return candidate_set
        except BackendInfrastructureError as error:
            discovered.close_safely()
            if (
                not self._policy.continue_on_remote_infrastructure_failure
                or local_session is None
            ):
                raise
            return self._local_only_after_remote_infrastructure_failure(
                classification,
                expected_classification_digest,
                local_session,
                privacy_state,
                error,
            )
        except BaseException:
            discovered.close_safely()
            raise


def build_live_managed_candidate_wiring(
    policy: LiveCandidateWiringPolicy | None,
    *,
    azure_backend_factory: AzureCandidateBackendFactory | None,
    containerapp_backend_factory: ContainerAppCandidateBackendFactory | None = None,
    containerapp_bootstrap_factory: ContainerAppCandidateBootstrapFactory | None = None,
    progress_sink: Callable[[str], None],
) -> LiveManagedCandidateWiring | None:
    """Build the default-off live boundary without performing discovery."""
    if not callable(progress_sink):
        raise ValueError("progress_sink must be callable")
    if policy is None:
        if (
            azure_backend_factory is not None
            or containerapp_backend_factory is not None
            or containerapp_bootstrap_factory is not None
        ):
            raise ValueError("backend factory requires live_candidate_policy")
        return None

    def emit(event: dict[str, object]) -> None:
        progress_sink(
            "SELF_IMPROVE_MANAGED_CANDIDATE_EVENT "
            + json.dumps(
                event,
                allow_nan=False,
                ensure_ascii=True,
                separators=(",", ":"),
                sort_keys=True,
            )
        )

    selected_factory = (
        _DEFAULT_AZURE_BACKEND_FACTORY
        if azure_backend_factory is None
        else azure_backend_factory
    )
    selected_containerapp_factory = (
        _DEFAULT_CONTAINERAPP_BACKEND_FACTORY
        if containerapp_backend_factory is None
        else containerapp_backend_factory
    )
    return LiveManagedCandidateWiring(
        policy,
        azure_backend_factory=selected_factory,
        containerapp_backend_factory=selected_containerapp_factory,
        containerapp_bootstrap_factory=containerapp_bootstrap_factory,
        event_sink=emit,
    )


__all__ = (
    "LIVE_CANDIDATE_WIRING_PROTOCOL",
    "AzureCandidateBackend",
    "AzureCandidateBackendFactory",
    "ContainerAppCandidateBackend",
    "ContainerAppCandidateBackendFactory",
    "ContainerAppCandidateBootstrapFactory",
    "LiveCandidateWiringPolicy",
    "LiveManagedCandidateSet",
    "LiveManagedCandidateWiring",
    "bind_managed_candidate_execution_boundary",
    "build_live_managed_candidate_wiring",
)
