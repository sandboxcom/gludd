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
from typing import Generic, Protocol, TypeVar, cast, runtime_checkable

from general_ludd.self_improve.azure_backend import (
    AzureApprovedPrompt,
    AzureCandidateResponse,
    AzureOpenAIConfig,
    build_azure_openai_candidate_backend,
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
from general_ludd.self_improve.model_candidates import (
    AzureFoundryCandidateIdentity,
    BackendCallBudget,
    BoundedCandidateSession,
    CandidateBackend,
    ModelCandidateProvider,
)

LIVE_CANDIDATE_WIRING_PROTOCOL = "gludd-live-candidate-wiring-v1"
_MAX_COST_MICROUSD = 1_000_000_000_000

_RequestT = TypeVar("_RequestT")
_ResponseT = TypeVar("_ResponseT")
_DEFAULT_AZURE_BACKEND_FACTORY = cast(
    "AzureCandidateBackendFactory",
    build_azure_openai_candidate_backend,
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

    def __post_init__(self) -> None:
        """Reject ambiguous provider, opt-in, and budget combinations."""
        if not isinstance(self.local_budget, BackendCallBudget):
            raise ValueError("local_budget must be a BackendCallBudget")
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
        if ModelCandidateProvider.LOCAL_GGUF not in self.required_providers:
            raise ValueError("the legacy managed runner requires the local provider")
        if (
            isinstance(self.azure_estimated_cost_microusd, bool)
            or not isinstance(self.azure_estimated_cost_microusd, int)
            or not 0 <= self.azure_estimated_cost_microusd <= _MAX_COST_MICROUSD
        ):
            raise ValueError("azure_estimated_cost_microusd is outside its hard bound")

        if self.azure_config is None:
            if self.azure_budget is not None or self.azure_estimated_cost_microusd != 0:
                raise ValueError("azure_config is required for Azure budget or cost")
            if ModelCandidateProvider.AZURE_FOUNDRY in self.required_providers:
                raise ValueError("required Azure provider needs explicit Azure configuration")
            return
        if not isinstance(self.azure_config, AzureOpenAIConfig):
            raise ValueError("azure_config must be an AzureOpenAIConfig")
        if not self.azure_config.azure_enabled:
            raise ValueError("Azure candidate wiring must be explicitly enabled")
        if not isinstance(self.azure_budget, BackendCallBudget):
            raise ValueError("azure_budget must be a BackendCallBudget")
        if self.azure_estimated_cost_microusd > self.azure_budget.max_cost_microusd:
            raise ValueError("Azure estimated cost exceeds its configured budget")


class LiveManagedCandidateSet(Generic[_RequestT, _ResponseT]):
    """One authorized assembly plus sessions whose ownership is explicit."""

    def __init__(
        self,
        assembly: ManagedCandidateAssembly,
        local_session: BoundedCandidateSession[_RequestT, _ResponseT],
        azure_session: BoundedCandidateSession[
            AzureApprovedPrompt, AzureCandidateResponse
        ]
        | None,
        azure_backend: AzureCandidateBackend | None,
    ) -> None:
        """Retain only the resources needed until the caller leaves the scope."""
        self.assembly = assembly
        self.local_session = local_session
        self.azure_session = azure_session
        self._azure_backend = azure_backend
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
        if self._azure_backend is not None:
            self._azure_backend.close()

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


class LiveManagedCandidateWiring:
    """Lazily discover and assemble an explicitly enabled live candidate set."""

    def __init__(
        self,
        policy: LiveCandidateWiringPolicy,
        *,
        azure_backend_factory: AzureCandidateBackendFactory = (
            _DEFAULT_AZURE_BACKEND_FACTORY
        ),
        event_sink: Callable[[dict[str, object]], None] = _discard_event,
    ) -> None:
        """Snapshot approved non-secret configuration without provider effects."""
        if not isinstance(policy, LiveCandidateWiringPolicy):
            raise ValueError("policy must be a LiveCandidateWiringPolicy")
        if not isinstance(azure_backend_factory, AzureCandidateBackendFactory):
            raise ValueError("azure_backend_factory must implement its protocol")
        if not callable(event_sink):
            raise ValueError("event_sink must be callable")
        self._policy = policy
        self._azure_backend_factory = azure_backend_factory
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

    def assemble(
        self,
        classification: CandidateTaskClassification,
        *,
        expected_classification_digest: str,
        local_backend: CandidateBackend[_RequestT, _ResponseT],
        privacy_state: CandidatePrivacyState,
        input_tokens: int,
        max_output_tokens: int,
    ) -> LiveManagedCandidateSet[_RequestT, _ResponseT]:
        """Authorize, discover, and assemble once without provider fallback."""
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

        current_azure_digest = (
            None
            if self._policy.azure_config is None
            else self._current_configuration_digest(
                ModelCandidateProvider.AZURE_FOUNDRY,
            )
        )
        if not (
            current_azure_digest is None
            and self._approved_azure_configuration_digest is None
        ) and (
            current_azure_digest is None
            or self._approved_azure_configuration_digest is None
            or not hmac.compare_digest(
                current_azure_digest,
                self._approved_azure_configuration_digest,
            )
        ):
            raise CandidateAssemblyError(CandidateAssemblyFailure.CONFIGURATION_DRIFT)

        azure_backend: AzureCandidateBackend | None = None
        try:
            azure_session: BoundedCandidateSession[
                AzureApprovedPrompt, AzureCandidateResponse
            ] | None = None
            azure_source: ManagedCandidateSource | None = None
            if self._policy.azure_config is not None:
                azure_backend = self._azure_backend_factory(self._policy.azure_config)
                if not isinstance(azure_backend, AzureCandidateBackend):
                    raise ValueError("Azure discovery returned an invalid backend")
                azure_session = BoundedCandidateSession(
                    azure_backend,
                    cast(BackendCallBudget, self._policy.azure_budget),
                    azure_enabled=True,
                )
            local_session.authorize(
                input_tokens=input_tokens,
                max_output_tokens=max_output_tokens,
                estimated_cost_microusd=0,
            )
            if azure_session is not None:
                azure_session.authorize(
                    input_tokens=input_tokens,
                    max_output_tokens=max_output_tokens,
                    estimated_cost_microusd=(
                        self._policy.azure_estimated_cost_microusd
                    ),
                )
                azure_source = self._azure_source(azure_session, privacy_state)
            sources = [self._local_source(local_session, privacy_state)]
            if azure_source is not None:
                sources.append(azure_source)
            assembly = assemble_managed_candidates(
                classification,
                tuple(sources),
                expected_classification_digest=expected_classification_digest,
                required_providers=self._policy.required_providers,
                azure_enabled=self._policy.azure_config is not None,
            )
            candidate_set = LiveManagedCandidateSet(
                assembly,
                local_session,
                azure_session,
                azure_backend,
            )
            self._emit(classification.event_payload())
            for event in assembly.event_payloads():
                self._emit(event)
            return candidate_set
        except BaseException:
            if azure_backend is not None:
                with suppress(Exception):
                    azure_backend.close()
            raise


def build_live_managed_candidate_wiring(
    policy: LiveCandidateWiringPolicy | None,
    *,
    azure_backend_factory: AzureCandidateBackendFactory | None,
    progress_sink: Callable[[str], None],
) -> LiveManagedCandidateWiring | None:
    """Build the default-off live boundary without performing discovery."""
    if not callable(progress_sink):
        raise ValueError("progress_sink must be callable")
    if policy is None:
        if azure_backend_factory is not None:
            raise ValueError("azure_backend_factory requires live_candidate_policy")
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
    return LiveManagedCandidateWiring(
        policy,
        azure_backend_factory=selected_factory,
        event_sink=emit,
    )


__all__ = (
    "LIVE_CANDIDATE_WIRING_PROTOCOL",
    "AzureCandidateBackend",
    "AzureCandidateBackendFactory",
    "LiveCandidateWiringPolicy",
    "LiveManagedCandidateSet",
    "LiveManagedCandidateWiring",
    "build_live_managed_candidate_wiring",
)
