"""Live, privacy-gated Azure OpenAI candidate backend.

The adapter uses Azure's management SDK to bind a deployment name to its
server-reported model version and ETag, then uses the maintained OpenAI client
against Azure's unified ``/openai/v1/`` data plane.  SDK creation is injectable
so ordinary tests and GitHub Actions never need credentials or network access.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Final, Protocol, cast
from urllib.parse import urlsplit

from general_ludd.self_improve.model_candidates import (
    AzureFoundryAPIFamily,
    AzureFoundryCandidateIdentity,
    BackendFailure,
    BackendInfrastructureError,
    BackendPolicyError,
    BackendPolicyFailure,
)
from general_ludd.self_improve.private_policy import SelfImproveRuntimePolicyGuard

AZURE_AI_TOKEN_SCOPE: Final = "https://ai.azure.com/.default"

_SUBSCRIPTION_RE: Final = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
)
_RESOURCE_GROUP_RE: Final = re.compile(r"^[A-Za-z0-9_.()\-]{1,90}$")
_ACCOUNT_RE: Final = re.compile(r"^[a-z0-9][a-z0-9-]{1,62}[a-z0-9]$")
_ENVIRONMENT_RE: Final = re.compile(r"^AZURE_[A-Z0-9_]{1,120}$")
_MAX_PROMPT_BYTES: Final = 1_048_576
_MAX_RESPONSE_BYTES: Final = 16_777_216
_MAX_PROVIDER_TOKENS: Final = 100_000_000
_APPROVAL_TOKEN: Final = object()
_MUTABLE_DISCOVERY_ALIASES: Final = frozenset({"default", "latest", "preview", "stable"})


class AzureCredentialSource(StrEnum):
    """Supported secret-reference modes for live Azure inference."""

    ENTRA_ID = "entra_id"
    API_KEY_ENV = "api_key_env"


@dataclass(frozen=True, slots=True)
class AzureCredentialReference:
    """A credential pointer, never a credential value."""

    source: AzureCredentialSource
    environment_variable: str | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        """Reject raw, cross-provider, or ambiguous secret references."""
        if not isinstance(self.source, AzureCredentialSource):
            raise ValueError("credential source must be a typed AzureCredentialSource")
        if self.source is AzureCredentialSource.ENTRA_ID:
            if self.environment_variable is not None:
                raise ValueError("Entra ID must not include an API-key pointer")
            return
        if (
            not isinstance(self.environment_variable, str)
            or _ENVIRONMENT_RE.fullmatch(self.environment_variable) is None
        ):
            raise ValueError("API-key credentials require one canonical Azure environment pointer")

    def resolve_api_key(self, environment: Mapping[str, str]) -> str:
        """Resolve and validate the referenced key without retaining it here."""
        if self.source is not AzureCredentialSource.API_KEY_ENV:
            raise ValueError("credential reference is not an API-key pointer")
        variable = cast(str, self.environment_variable)
        value: object = None
        invalid = True
        try:
            value = environment.get(variable)
            invalid = (
                type(value) is not str
                or len(value) < 8
                or len(value.encode("utf-8", errors="surrogatepass")) > 16_384
                or value != value.strip()
                or any(
                    ord(character) < 0x20 or ord(character) == 0x7F
                    for character in value
                )
            )
        except Exception:
            invalid = True
        if invalid:
            raise BackendInfrastructureError(BackendFailure.AUTHENTICATION)
        return cast(str, value)


@dataclass(frozen=True, slots=True)
class AzureOpenAIConfig:
    """Explicit, secret-free configuration for one Azure OpenAI deployment."""

    azure_enabled: bool
    endpoint: str
    api_family: AzureFoundryAPIFamily
    api_version: str
    subscription_id: str
    resource_group: str
    account_name: str
    deployment: str
    credential: AzureCredentialReference = field(repr=False)
    discovery_timeout_seconds: float = 15.0

    def __post_init__(self) -> None:
        """Require one canonical Azure OpenAI v1 resource and ARM lookup."""
        if not isinstance(self.azure_enabled, bool):
            raise ValueError("azure_enabled must be an explicit boolean")
        if self.api_family is not AzureFoundryAPIFamily.AZURE_OPENAI:
            raise ValueError("the live adapter supports only the Azure OpenAI API family")
        if self.api_version != "v1":
            raise ValueError("the live adapter requires the implicit-version Azure OpenAI v1 API")
        if not isinstance(self.subscription_id, str) or _SUBSCRIPTION_RE.fullmatch(
            self.subscription_id
        ) is None:
            raise ValueError("subscription_id must be one canonical lowercase UUID")
        if (
            not isinstance(self.resource_group, str)
            or _RESOURCE_GROUP_RE.fullmatch(self.resource_group) is None
            or self.resource_group.endswith(".")
        ):
            raise ValueError("resource_group is not a canonical Azure resource-group name")
        if not isinstance(self.account_name, str) or _ACCOUNT_RE.fullmatch(
            self.account_name
        ) is None:
            raise ValueError("account_name is not a canonical Azure resource name")
        if not isinstance(self.credential, AzureCredentialReference):
            raise ValueError("credential must be an AzureCredentialReference")
        if (
            isinstance(self.discovery_timeout_seconds, bool)
            or not isinstance(self.discovery_timeout_seconds, (int, float))
            or not 0.0 < float(self.discovery_timeout_seconds) <= 120.0
        ):
            raise ValueError("discovery_timeout_seconds must be in 0..120")

        # Reuse the provider-neutral validator rather than drifting into a
        # second endpoint/deployment grammar in this adapter.
        AzureFoundryCandidateIdentity(
            endpoint=self.endpoint,
            api_family=self.api_family,
            deployment=self.deployment,
            api_version=self.api_version,
            model_version="undiscovered-version",
            etag="undiscovered-etag",
        )
        hostname = urlsplit(self.endpoint).hostname
        if hostname != f"{self.account_name}.openai.azure.com":
            raise ValueError("account_name must exactly match the Azure OpenAI endpoint")
        object.__setattr__(
            self,
            "discovery_timeout_seconds",
            float(self.discovery_timeout_seconds),
        )


class AzurePromptApprovalError(ValueError):
    """Fixed-message denial raised before a prompt can reach a provider."""

    def __init__(self) -> None:
        """Exclude source paths, prompt text, and policy parser details."""
        super().__init__("Azure inference blocked by project privacy approval")


@dataclass(frozen=True, slots=True, init=False)
class AzureApprovedPrompt:
    """Opaque prompt capability created only after project-policy approval."""

    _prompt: str = field(repr=False)
    _source_paths: tuple[str, ...] = field(repr=False)
    _policy_guard: SelfImproveRuntimePolicyGuard = field(repr=False, compare=False)
    policy_digest: str
    approval_digest: str

    def __init__(
        self,
        *,
        prompt: str,
        source_paths: tuple[str, ...],
        policy_guard: SelfImproveRuntimePolicyGuard,
        policy_digest: str,
        approval_digest: str,
        _token: object,
    ) -> None:
        """Construct one policy-approved capability with the private token."""
        if _token is not _APPROVAL_TOKEN:
            raise AzurePromptApprovalError
        object.__setattr__(self, "_prompt", prompt)
        object.__setattr__(self, "_source_paths", source_paths)
        object.__setattr__(self, "_policy_guard", policy_guard)
        object.__setattr__(self, "policy_digest", policy_digest)
        object.__setattr__(self, "approval_digest", approval_digest)

    @classmethod
    def approve(
        cls,
        *,
        prompt: str,
        source_paths: tuple[str, ...],
        policy_guard: SelfImproveRuntimePolicyGuard,
    ) -> AzureApprovedPrompt:
        """Create a capability only for one complete, currently public scope."""
        if type(prompt) is not str:
            raise AzurePromptApprovalError
        prompt_bytes: bytes | None = None
        try:
            prompt_bytes = prompt.encode("utf-8")
        except UnicodeEncodeError:
            prompt_bytes = None
        if prompt_bytes is None:
            raise AzurePromptApprovalError
        if (
            not prompt.strip()
            or len(prompt_bytes) > _MAX_PROMPT_BYTES
            or "\x00" in prompt
            or not isinstance(source_paths, tuple)
            or not source_paths
            or len(set(source_paths)) != len(source_paths)
            or not all(type(path) is str and path for path in source_paths)
            or not isinstance(policy_guard, SelfImproveRuntimePolicyGuard)
        ):
            raise AzurePromptApprovalError
        policy = None
        try:
            policy = policy_guard.require(source_paths)
        except Exception:
            policy = None
        if policy is None:
            raise AzurePromptApprovalError
        approval_digest = _stable_digest(
            {
                "policy_digest": policy.digest,
                "prompt_sha256": hashlib.sha256(prompt_bytes).hexdigest(),
                "protocol": "gludd-azure-approved-prompt-v1",
                "source_path_sha256": tuple(
                    sorted(hashlib.sha256(path.encode("utf-8")).hexdigest() for path in source_paths)
                ),
            }
        )
        return cls(
            prompt=prompt,
            source_paths=source_paths,
            policy_guard=policy_guard,
            policy_digest=policy.digest,
            approval_digest=approval_digest,
            _token=_APPROVAL_TOKEN,
        )

    def _reveal_after_recheck(self) -> str:
        policy = None
        try:
            policy = self._policy_guard.require(self._source_paths)
        except Exception:
            policy = None
        if policy is None:
            raise AzurePromptApprovalError
        if policy.digest != self.policy_digest:
            raise AzurePromptApprovalError
        return self._prompt


class AzureTraceEvent(StrEnum):
    """Secret-free live-provider phases suitable for traces and events."""

    DISCOVERY_STARTED = "azure_discovery_started"
    DISCOVERY_SUCCEEDED = "azure_discovery_succeeded"
    DISCOVERY_FAILED = "azure_discovery_failed"
    IDENTITY_DRIFT = "azure_identity_drift"
    APPROVAL_BLOCKED = "azure_approval_blocked"
    REQUEST_STARTED = "azure_request_started"
    RESPONSE_ACCEPTED = "azure_response_accepted"
    REQUEST_FAILED = "azure_request_failed"


@dataclass(frozen=True, slots=True)
class AzureBackendTrace:
    """Content-free trace record for one adapter transition."""

    event: AzureTraceEvent
    candidate_digest: str | None = None
    request_number: int = 0
    failure: BackendFailure | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0


@dataclass(frozen=True, slots=True)
class AzureBackendAccounting:
    """Exact cumulative provider request/response counters."""

    requests_started: int = 0
    responses_received: int = 0
    responses_accepted: int = 0
    requests_failed: int = 0
    provider_input_tokens: int = 0
    provider_output_tokens: int = 0
    provider_total_tokens: int = 0


@dataclass(frozen=True, slots=True)
class AzureCandidateResponse:
    """Validated response text plus exact provider-reported token usage."""

    text: str = field(repr=False)
    input_tokens: int
    output_tokens: int
    total_tokens: int


class _DeploymentsOperations(Protocol):
    def get(
        self,
        *,
        resource_group_name: str,
        account_name: str,
        deployment_name: str,
        connection_timeout: float,
        read_timeout: float,
        retry_total: int,
        logging_enable: bool,
    ) -> object:
        """Read one Azure Cognitive Services deployment snapshot."""
        ...


class _ManagementClient(Protocol):
    deployments: _DeploymentsOperations

    def close(self) -> None:
        """Release the SDK transport."""
        ...


class _Closable(Protocol):
    def close(self) -> None:
        """Release an owned SDK resource."""
        ...


class _ResponsesOperations(Protocol):
    def create(
        self,
        *,
        model: str,
        input: str,
        max_output_tokens: int,
        store: bool,
        timeout: float,
    ) -> object:
        """Invoke the Azure-hosted OpenAI Responses endpoint."""
        ...


class _OpenAIClient(Protocol):
    responses: _ResponsesOperations

    def close(self) -> None:
        """Release the SDK transport."""
        ...


class _DefaultCredentialFactory(Protocol):
    def __call__(self) -> object:
        """Build a default Azure TokenCredential."""
        ...


class _ManagementClientFactory(Protocol):
    def __call__(
        self,
        *,
        credential: object,
        subscription_id: str,
        retry_total: int,
        logging_enable: bool,
    ) -> object:
        """Build the official Cognitive Services management client."""
        ...


class _BearerTokenProviderFactory(Protocol):
    def __call__(
        self,
        *,
        credential: object,
        scope: str,
    ) -> Callable[[], str]:
        """Build the Azure Identity token-provider callback."""
        ...


class _OpenAIClientFactory(Protocol):
    def __call__(
        self,
        *,
        base_url: str,
        api_key: str | Callable[[], str],
        max_retries: int,
    ) -> object:
        """Build the maintained OpenAI client for Azure's v1 route."""
        ...


@dataclass(frozen=True, slots=True)
class AzureSdkFactories:
    """Injectable construction seam matching the maintained official SDKs."""

    create_default_credential: _DefaultCredentialFactory
    create_management_client: _ManagementClientFactory
    create_bearer_token_provider: _BearerTokenProviderFactory
    create_openai_client: _OpenAIClientFactory

    def __post_init__(self) -> None:
        """Reject incomplete factory sets before any credential is requested."""
        if not all(
            callable(factory)
            for factory in (
                self.create_default_credential,
                self.create_management_client,
                self.create_bearer_token_provider,
                self.create_openai_client,
            )
        ):
            raise ValueError("every Azure SDK factory hook must be callable")

    @classmethod
    def official(cls) -> AzureSdkFactories:
        """Return lazy factories for Azure Identity, ARM, and OpenAI clients."""
        return cls(
            create_default_credential=_official_default_credential,
            create_management_client=_official_management_client,
            create_bearer_token_provider=_official_bearer_token_provider,
            create_openai_client=_official_openai_client,
        )


def _official_default_credential() -> object:
    from azure.identity import DefaultAzureCredential

    return DefaultAzureCredential()


def _official_management_client(
    *,
    credential: object,
    subscription_id: str,
    retry_total: int,
    logging_enable: bool,
) -> object:
    from azure.mgmt.cognitiveservices import CognitiveServicesManagementClient

    return CognitiveServicesManagementClient(
        credential=credential,
        subscription_id=subscription_id,
        retry_total=retry_total,
        logging_enable=logging_enable,
    )


def _official_bearer_token_provider(
    *,
    credential: object,
    scope: str,
) -> Callable[[], str]:
    from azure.identity import get_bearer_token_provider

    return cast(Callable[[], str], get_bearer_token_provider(credential, scope))


def _official_openai_client(
    *,
    base_url: str,
    api_key: str | Callable[[], str],
    max_retries: int,
) -> object:
    from openai import OpenAI

    return OpenAI(
        base_url=base_url,
        api_key=api_key,
        max_retries=max_retries,
    )


def _stable_digest(value: Mapping[str, object]) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("ascii")
    return hashlib.sha256(encoded).hexdigest()


_STATUS_FAILURES: Final[dict[int, BackendFailure]] = {
    400: BackendFailure.INVALID_RESPONSE,
    401: BackendFailure.AUTHENTICATION,
    403: BackendFailure.AUTHORIZATION,
    404: BackendFailure.NOT_FOUND,
    408: BackendFailure.TIMEOUT,
    409: BackendFailure.UNAVAILABLE,
    422: BackendFailure.INVALID_RESPONSE,
    429: BackendFailure.RATE_LIMITED,
}
_NAME_FAILURES: Final[dict[str, BackendFailure]] = {
    "APIConnectionError": BackendFailure.TRANSPORT,
    "APITimeoutError": BackendFailure.TIMEOUT,
    "AuthenticationError": BackendFailure.AUTHENTICATION,
    "ClientAuthenticationError": BackendFailure.AUTHENTICATION,
    "CredentialUnavailableError": BackendFailure.AUTHENTICATION,
    "NotFoundError": BackendFailure.NOT_FOUND,
    "PermissionDeniedError": BackendFailure.AUTHORIZATION,
    "RateLimitError": BackendFailure.RATE_LIMITED,
    "ResourceNotFoundError": BackendFailure.NOT_FOUND,
    "ServiceRequestError": BackendFailure.TRANSPORT,
    "ServiceResponseError": BackendFailure.TRANSPORT,
    "ServiceResponseTimeoutError": BackendFailure.TIMEOUT,
}


def _classify_sdk_error(error: BaseException) -> BackendFailure:
    if isinstance(error, (ImportError, ModuleNotFoundError)):
        return BackendFailure.UNAVAILABLE
    if isinstance(error, TimeoutError):
        return BackendFailure.TIMEOUT
    named = _NAME_FAILURES.get(type(error).__name__)
    if named is not None:
        return named
    try:
        status_code = getattr(error, "status_code", None)
    except Exception:
        status_code = None
    if isinstance(status_code, int) and not isinstance(status_code, bool):
        if status_code in _STATUS_FAILURES:
            return _STATUS_FAILURES[status_code]
        if status_code >= 500:
            return BackendFailure.UNAVAILABLE
    return BackendFailure.INTERNAL


def _censored_sdk_error(error: BaseException) -> BackendInfrastructureError:
    if isinstance(error, BackendInfrastructureError):
        try:
            failure = error.failure
        except Exception:
            failure = BackendFailure.INTERNAL
        if not isinstance(failure, BackendFailure):
            failure = BackendFailure.INTERNAL
        return BackendInfrastructureError(failure)
    return BackendInfrastructureError(_classify_sdk_error(error))


def _member(value: object, *names: str) -> object | None:
    if isinstance(value, Mapping):
        mapping = cast(Mapping[object, object], value)
        for name in names:
            try:
                if name in mapping:
                    return mapping[name]
            except Exception:
                return None
        return None
    for name in names:
        try:
            return cast(object, getattr(value, name))
        except AttributeError:
            continue
        except Exception:
            return None
    return None


def _emit(trace_sink: Callable[[AzureBackendTrace], None], trace: AzureBackendTrace) -> None:
    failed = False
    try:
        trace_sink(trace)
    except Exception:
        failed = True
    if failed:
        raise BackendInfrastructureError(BackendFailure.INTERNAL)


def _emit_failure(
    trace_sink: Callable[[AzureBackendTrace], None],
    trace: AzureBackendTrace,
) -> None:
    try:
        _emit(trace_sink, trace)
    except BackendInfrastructureError:
        # The provider failure remains the primary censored result.  There is no
        # additional operation to stop after a terminal failure notification.
        return


def _deployment_identity(
    config: AzureOpenAIConfig,
    deployments: _DeploymentsOperations,
    trace_sink: Callable[[AzureBackendTrace], None],
    *,
    known_digest: str | None,
) -> AzureFoundryCandidateIdentity:
    _emit(
        trace_sink,
        AzureBackendTrace(
            event=AzureTraceEvent.DISCOVERY_STARTED,
            candidate_digest=known_digest,
        ),
    )
    discovery_error: BackendInfrastructureError | None = None
    deployment: object = None
    try:
        deployment = deployments.get(
            resource_group_name=config.resource_group,
            account_name=config.account_name,
            deployment_name=config.deployment,
            connection_timeout=config.discovery_timeout_seconds,
            read_timeout=config.discovery_timeout_seconds,
            retry_total=0,
            logging_enable=False,
        )
    except Exception as error:
        discovery_error = _censored_sdk_error(error)
    if discovery_error is not None:
        _emit_failure(
            trace_sink,
            AzureBackendTrace(
                event=AzureTraceEvent.DISCOVERY_FAILED,
                candidate_digest=known_digest,
                failure=discovery_error.failure,
            ),
        )
        raise discovery_error

    try:
        name = _member(deployment, "name")
        etag = _member(deployment, "etag")
        properties = _member(deployment, "properties")
        state = _member(properties, "provisioning_state", "provisioningState")
        state = _member(state, "value") or state
        model = _member(properties, "model")
        version = _member(model, "version", "model_version", "modelVersion")
        if (
            name != config.deployment
            or state != "Succeeded"
            or type(etag) is not str
            or etag.casefold() in _MUTABLE_DISCOVERY_ALIASES
            or type(version) is not str
            or version.casefold() in _MUTABLE_DISCOVERY_ALIASES
        ):
            raise ValueError
        identity = AzureFoundryCandidateIdentity(
            endpoint=config.endpoint,
            api_family=config.api_family,
            deployment=config.deployment,
            api_version=config.api_version,
            model_version=version,
            etag=etag,
        )
    except Exception:
        censored = BackendInfrastructureError(BackendFailure.INVALID_RESPONSE)
        _emit_failure(
            trace_sink,
            AzureBackendTrace(
                event=AzureTraceEvent.DISCOVERY_FAILED,
                candidate_digest=known_digest,
                failure=censored.failure,
            ),
        )
        raise censored from None
    _emit(
        trace_sink,
        AzureBackendTrace(
            event=AzureTraceEvent.DISCOVERY_SUCCEEDED,
            candidate_digest=identity.identity_digest,
        ),
    )
    return identity


def _discard_trace(_trace: AzureBackendTrace) -> None:
    return


def _close_quietly(resource: object | None) -> bool:
    if resource is None:
        return False
    try:
        close = cast(_Closable, resource).close
        if callable(close):
            close()
            return False
    except Exception:
        return True
    return False


class AzureOpenAICandidateBackend:
    """One exact Azure deployment with rediscovery and no fallback surface."""

    def __init__(
        self,
        *,
        config: AzureOpenAIConfig,
        identity: AzureFoundryCandidateIdentity,
        credential: object,
        management_client: _ManagementClient,
        factories: AzureSdkFactories,
        environment: Mapping[str, str],
        trace_sink: Callable[[AzureBackendTrace], None],
    ) -> None:
        """Bind the already-discovered candidate and owned SDK resources."""
        self._config = config
        self._identity = identity
        self._credential = credential
        self._management_client = management_client
        self._factories = factories
        self._environment: Mapping[str, str] | None = environment
        self._trace_sink = trace_sink
        self._openai_client: _OpenAIClient | None = None
        self._closed = False
        self._requests_started = 0
        self._responses_received = 0
        self._responses_accepted = 0
        self._requests_failed = 0
        self._provider_input_tokens = 0
        self._provider_output_tokens = 0
        self._provider_total_tokens = 0

    @property
    def candidate_identity(self) -> AzureFoundryCandidateIdentity:
        """Return the immutable identity captured during initial discovery."""
        return self._identity

    @property
    def accounting(self) -> AzureBackendAccounting:
        """Return content-free cumulative provider accounting."""
        return AzureBackendAccounting(
            requests_started=self._requests_started,
            responses_received=self._responses_received,
            responses_accepted=self._responses_accepted,
            requests_failed=self._requests_failed,
            provider_input_tokens=self._provider_input_tokens,
            provider_output_tokens=self._provider_output_tokens,
            provider_total_tokens=self._provider_total_tokens,
        )

    @property
    def trace_sink(self) -> Callable[[AzureBackendTrace], None]:
        """Return the current content-free event consumer."""
        return self._trace_sink

    @trace_sink.setter
    def trace_sink(self, value: Callable[[AzureBackendTrace], None]) -> None:
        if not callable(value):
            raise ValueError("trace sink must be callable")
        self._trace_sink = value

    def _ensure_openai_client(self) -> _OpenAIClient:
        if self._openai_client is not None:
            return self._openai_client
        environment = self._environment
        if environment is None:
            raise BackendInfrastructureError(BackendFailure.UNAVAILABLE)
        candidate_client: _OpenAIClient | None = None
        construction_error: BackendInfrastructureError | None = None
        try:
            if self._config.credential.source is AzureCredentialSource.ENTRA_ID:
                api_key: str | Callable[[], str] = (
                    self._factories.create_bearer_token_provider(
                        credential=self._credential,
                        scope=AZURE_AI_TOKEN_SCOPE,
                    )
                )
            else:
                api_key = self._config.credential.resolve_api_key(environment)
            client = self._factories.create_openai_client(
                base_url=f"{self._config.endpoint}/openai/v1/",
                api_key=api_key,
                max_retries=0,
            )
            candidate_client = cast(_OpenAIClient, client)
            responses = candidate_client.responses
            if not callable(getattr(responses, "create", None)) or not callable(
                getattr(client, "close", None)
            ):
                raise TypeError
        except Exception as error:
            construction_error = _censored_sdk_error(error)
        if construction_error is not None:
            raise construction_error
        if candidate_client is None:
            raise BackendInfrastructureError(BackendFailure.INTERNAL)
        self._openai_client = candidate_client
        self._environment = None
        return self._openai_client

    def generate(
        self,
        request: AzureApprovedPrompt,
        *,
        max_output_tokens: int,
        timeout_seconds: float,
    ) -> AzureCandidateResponse:
        """Recheck identity/privacy, call once, and return validated accounting."""
        if self._closed:
            raise BackendInfrastructureError(BackendFailure.UNAVAILABLE)
        if not isinstance(request, AzureApprovedPrompt):
            raise AzurePromptApprovalError
        if (
            isinstance(max_output_tokens, bool)
            or not isinstance(max_output_tokens, int)
            or not 1 <= max_output_tokens <= _MAX_PROVIDER_TOKENS
            or isinstance(timeout_seconds, bool)
            or not isinstance(timeout_seconds, (int, float))
            or not 0.0 < float(timeout_seconds) <= 3_600.0
        ):
            raise ValueError("Azure generation limits are invalid")

        discovered = _deployment_identity(
            self._config,
            self._management_client.deployments,
            self._trace_sink,
            known_digest=self._identity.identity_digest,
        )
        if discovered.identity_digest != self._identity.identity_digest:
            _emit(
                self._trace_sink,
                AzureBackendTrace(
                    event=AzureTraceEvent.IDENTITY_DRIFT,
                    candidate_digest=self._identity.identity_digest,
                ),
            )
            raise BackendPolicyError(BackendPolicyFailure.IDENTITY_DRIFT)
        try:
            prompt = request._reveal_after_recheck()
        except AzurePromptApprovalError:
            _emit(
                self._trace_sink,
                AzureBackendTrace(
                    event=AzureTraceEvent.APPROVAL_BLOCKED,
                    candidate_digest=self._identity.identity_digest,
                ),
            )
            raise

        client = self._ensure_openai_client()
        request_number = self._requests_started + 1
        _emit(
            self._trace_sink,
            AzureBackendTrace(
                event=AzureTraceEvent.REQUEST_STARTED,
                candidate_digest=self._identity.identity_digest,
                request_number=request_number,
            ),
        )
        self._requests_started += 1
        response: object = None
        request_error: BackendInfrastructureError | None = None
        try:
            response = client.responses.create(
                model=self._config.deployment,
                input=prompt,
                max_output_tokens=max_output_tokens,
                store=False,
                timeout=float(timeout_seconds),
            )
        except Exception as error:
            self._requests_failed += 1
            request_error = _censored_sdk_error(error)
            _emit_failure(
                self._trace_sink,
                AzureBackendTrace(
                    event=AzureTraceEvent.REQUEST_FAILED,
                    candidate_digest=self._identity.identity_digest,
                    request_number=request_number,
                    failure=request_error.failure,
                ),
            )
        if request_error is not None:
            raise request_error
        self._responses_received += 1
        try:
            accepted = _validated_response(response, max_output_tokens=max_output_tokens)
        except Exception:
            self._requests_failed += 1
            failure = BackendFailure.INVALID_RESPONSE
            _emit_failure(
                self._trace_sink,
                AzureBackendTrace(
                    event=AzureTraceEvent.REQUEST_FAILED,
                    candidate_digest=self._identity.identity_digest,
                    request_number=request_number,
                    failure=failure,
                ),
            )
            raise BackendInfrastructureError(failure) from None
        self._responses_accepted += 1
        self._provider_input_tokens += accepted.input_tokens
        self._provider_output_tokens += accepted.output_tokens
        self._provider_total_tokens += accepted.total_tokens
        _emit(
            self._trace_sink,
            AzureBackendTrace(
                event=AzureTraceEvent.RESPONSE_ACCEPTED,
                candidate_digest=self._identity.identity_digest,
                request_number=request_number,
                input_tokens=accepted.input_tokens,
                output_tokens=accepted.output_tokens,
                total_tokens=accepted.total_tokens,
            ),
        )
        return accepted

    def close(self) -> None:
        """Idempotently release every SDK resource, even after one close fails."""
        if self._closed:
            return
        self._closed = True
        failed = False
        for resource in (
            self._openai_client,
            self._management_client,
            self._credential,
        ):
            failed = _close_quietly(resource) or failed
        self._openai_client = None
        self._environment = None
        if failed:
            raise BackendInfrastructureError(BackendFailure.INTERNAL)

    def __enter__(self) -> AzureOpenAICandidateBackend:
        """Return this owned backend for context-managed use."""
        return self

    def __exit__(
        self,
        _exc_type: object,
        _exc_value: object,
        _traceback: object,
    ) -> None:
        """Release all owned SDK resources when leaving the context."""
        self.close()


def _validated_token_count(value: object) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not 0 <= value <= _MAX_PROVIDER_TOKENS
    ):
        raise ValueError
    return value


def _validated_response(
    response: object,
    *,
    max_output_tokens: int,
) -> AzureCandidateResponse:
    text = _member(response, "output_text", "outputText")
    usage = _member(response, "usage")
    input_tokens = _validated_token_count(
        _member(usage, "input_tokens", "inputTokens")
    )
    output_tokens = _validated_token_count(
        _member(usage, "output_tokens", "outputTokens")
    )
    total_tokens = _validated_token_count(
        _member(usage, "total_tokens", "totalTokens")
    )
    if (
        not isinstance(text, str)
        or not text
        or len(text.encode("utf-8")) > _MAX_RESPONSE_BYTES
        or output_tokens > max_output_tokens
        or input_tokens + output_tokens != total_tokens
    ):
        raise ValueError
    return AzureCandidateResponse(
        text=text,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=total_tokens,
    )


def build_azure_openai_candidate_backend(
    config: AzureOpenAIConfig,
    *,
    factories: AzureSdkFactories | None = None,
    environment: Mapping[str, str] | None = None,
    trace_sink: Callable[[AzureBackendTrace], None] | None = None,
) -> AzureOpenAICandidateBackend:
    """Discover and bind one live Azure candidate without creating a fallback."""
    if not isinstance(config, AzureOpenAIConfig):
        raise ValueError("config must be an AzureOpenAIConfig")
    if not config.azure_enabled:
        raise BackendPolicyError(BackendPolicyFailure.AZURE_OPT_IN_REQUIRED)
    selected_factories = factories or AzureSdkFactories.official()
    if not isinstance(selected_factories, AzureSdkFactories):
        raise ValueError("factories must be an AzureSdkFactories instance")
    selected_environment = os.environ if environment is None else environment
    if not isinstance(selected_environment, Mapping):
        raise ValueError("environment must be a string mapping")
    selected_sink = _discard_trace if trace_sink is None else trace_sink
    if not callable(selected_sink):
        raise ValueError("trace sink must be callable")

    credential: object | None = None
    management: object | None = None
    identity: AzureFoundryCandidateIdentity | None = None
    construction_error: BackendInfrastructureError | None = None
    try:
        credential = selected_factories.create_default_credential()
        management = selected_factories.create_management_client(
            credential=credential,
            subscription_id=config.subscription_id,
            retry_total=0,
            logging_enable=False,
        )
        candidate_management = cast(_ManagementClient, management)
        deployments = candidate_management.deployments
        if not callable(getattr(deployments, "get", None)) or not callable(
            getattr(management, "close", None)
        ):
            raise TypeError
        identity = _deployment_identity(
            config,
            deployments,
            selected_sink,
            known_digest=None,
        )
    except BackendInfrastructureError as error:
        _close_quietly(management)
        _close_quietly(credential)
        construction_error = _censored_sdk_error(error)
    except Exception as error:
        _close_quietly(management)
        _close_quietly(credential)
        construction_error = _censored_sdk_error(error)
    if construction_error is not None:
        raise construction_error
    if identity is None:
        _close_quietly(management)
        _close_quietly(credential)
        raise BackendInfrastructureError(BackendFailure.INTERNAL)
    return AzureOpenAICandidateBackend(
        config=config,
        identity=identity,
        credential=credential,
        management_client=cast(_ManagementClient, management),
        factories=selected_factories,
        environment=selected_environment,
        trace_sink=selected_sink,
    )


__all__ = (
    "AZURE_AI_TOKEN_SCOPE",
    "AzureApprovedPrompt",
    "AzureBackendAccounting",
    "AzureBackendTrace",
    "AzureCandidateResponse",
    "AzureCredentialReference",
    "AzureCredentialSource",
    "AzureOpenAICandidateBackend",
    "AzureOpenAIConfig",
    "AzurePromptApprovalError",
    "AzureSdkFactories",
    "AzureTraceEvent",
    "build_azure_openai_candidate_backend",
)
