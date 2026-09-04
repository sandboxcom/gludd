"""Provider-neutral identities and bounded self-improvement backend contracts.

The contracts in this module deliberately stop short of constructing a cloud
client.  They give local GGUF and Azure Foundry candidates the same immutable
identity boundary, while a single-candidate session enforces opt-in, token,
call, cost, and timeout policy before a backend can observe a request.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from enum import StrEnum
from pathlib import PurePosixPath
from typing import Generic, Protocol, TypeVar, runtime_checkable
from urllib.parse import urlsplit

_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
_LABEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_REPOSITORY_RE = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9._-]{0,95}/[A-Za-z0-9][A-Za-z0-9._-]{0,127}$"
)
_API_VERSION_RE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}(?:-preview)?$")
_MAX_CALLS = 16
_MAX_TOKENS = 100_000_000
_MAX_COST_MICROUSD = 1_000_000_000_000
_MAX_TIMEOUT_SECONDS = 3_600.0
_MAX_FILENAME_BYTES = 2_048
_MUTABLE_MODEL_VERSION_ALIASES = frozenset({"default", "latest", "preview", "stable"})


class ModelCandidateProvider(StrEnum):
    """Stable provider identities understood by the self-improvement boundary."""

    LOCAL_GGUF = "local_gguf"
    AZURE_FOUNDRY = "azure_foundry"


class AzureFoundryAPIFamily(StrEnum):
    """Azure data-plane families whose endpoint shapes are not interchangeable."""

    MODEL_INFERENCE = "azure_ai_model_inference"
    AZURE_OPENAI = "azure_openai"


def _stable_digest(value: dict[str, object]) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _strict_label(value: object, field_name: str) -> str:
    if not isinstance(value, str) or _LABEL_RE.fullmatch(value) is None:
        raise ValueError(f"{field_name} must be one bounded canonical label")
    return value


@dataclass(frozen=True, slots=True)
class LocalGGUFCandidateIdentity:
    """Exact acquired GGUF identity without filesystem or credential material."""

    model_id: str
    filename: str
    artifact_sha256: str
    repo_id: str | None = None
    revision: str | None = None

    def __post_init__(self) -> None:
        """Reject mutable revisions, escaping filenames, and partial provenance."""
        _strict_label(self.model_id, "model_id")
        if not isinstance(self.filename, str) or self.filename != self.filename.strip():
            raise ValueError("filename must be one canonical GGUF path")
        try:
            filename_bytes = len(self.filename.encode("utf-8"))
        except UnicodeEncodeError as exc:
            raise ValueError("filename must be one canonical GGUF path") from exc
        filename = PurePosixPath(self.filename)
        if (
            not self.filename
            or filename_bytes > _MAX_FILENAME_BYTES
            or "\\" in self.filename
            or "//" in self.filename
            or self.filename.startswith("./")
            or any(ord(character) < 0x20 or ord(character) == 0x7F for character in self.filename)
            or filename.is_absolute()
            or any(part in {"", ".", ".."} for part in filename.parts)
            or filename.as_posix() != self.filename
            or filename.suffix.lower() != ".gguf"
        ):
            raise ValueError("filename must be one confined GGUF path")
        if not isinstance(self.artifact_sha256, str) or _DIGEST_RE.fullmatch(
            self.artifact_sha256.lower()
        ) is None:
            raise ValueError("artifact_sha256 must be one SHA-256 digest")
        if (self.repo_id is None) != (self.revision is None):
            raise ValueError("repo_id and revision must be supplied together")
        if self.repo_id is not None:
            if _REPOSITORY_RE.fullmatch(self.repo_id) is None:
                raise ValueError("repo_id must be one canonical owner/repository pair")
            if not isinstance(self.revision, str) or _COMMIT_RE.fullmatch(
                self.revision.lower()
            ) is None:
                raise ValueError("revision must be one immutable commit SHA")
            object.__setattr__(self, "revision", self.revision.lower())
        object.__setattr__(self, "artifact_sha256", self.artifact_sha256.lower())

    @property
    def provider(self) -> ModelCandidateProvider:
        """Return the stable provider category."""
        return ModelCandidateProvider.LOCAL_GGUF

    @property
    def identity_digest(self) -> str:
        """Return a canonical path- and secret-free identity digest."""
        return _stable_digest(
            {
                "artifact_sha256": self.artifact_sha256,
                "filename": self.filename,
                "model_id": self.model_id,
                "protocol": "gludd-model-candidate-v1",
                "provider": self.provider.value,
                "repo_id": self.repo_id,
                "revision": self.revision,
            }
        )


_MODEL_INFERENCE_SUFFIXES = (
    ".services.ai.azure.com",
    ".models.ai.azure.com",
    ".services.ai.azure.us",
    ".models.ai.azure.us",
    ".services.ai.azure.cn",
    ".models.ai.azure.cn",
)
_AZURE_OPENAI_SUFFIXES = (
    ".openai.azure.com",
    ".openai.azure.us",
    ".openai.azure.cn",
)


@dataclass(frozen=True, slots=True)
class AzureFoundryCandidateIdentity:
    """Exact Azure routing and deployment identity, excluding authorization."""

    endpoint: str
    api_family: AzureFoundryAPIFamily
    deployment: str
    api_version: str
    model_version: str
    etag: str

    def __post_init__(self) -> None:
        """Require canonical Azure HTTPS routing and immutable deployment evidence."""
        if not isinstance(self.api_family, AzureFoundryAPIFamily):
            raise ValueError("api_family must be a typed AzureFoundryAPIFamily")
        _validate_azure_endpoint(self.endpoint, self.api_family)
        _strict_label(self.deployment, "deployment")
        valid_api_version = (
            self.api_version == "v1"
            if self.api_family is AzureFoundryAPIFamily.AZURE_OPENAI
            else (
                isinstance(self.api_version, str)
                and _API_VERSION_RE.fullmatch(self.api_version) is not None
            )
        )
        if not valid_api_version:
            raise ValueError("api_version does not match the selected Azure API family")
        model_version = _strict_label(self.model_version, "model_version")
        if model_version.casefold() in _MUTABLE_MODEL_VERSION_ALIASES:
            raise ValueError("model_version must identify one immutable deployment")
        if (
            not isinstance(self.etag, str)
            or not self.etag
            or self.etag != self.etag.strip()
            or len(self.etag.encode("utf-8")) > 512
            or any(ord(character) < 0x20 or ord(character) > 0x7E for character in self.etag)
        ):
            raise ValueError("etag must be bounded printable ASCII without whitespace drift")

    @property
    def provider(self) -> ModelCandidateProvider:
        """Return the stable provider category."""
        return ModelCandidateProvider.AZURE_FOUNDRY

    @property
    def identity_digest(self) -> str:
        """Return a stable digest binding every non-secret Azure routing field."""
        return _stable_digest(
            {
                "api_family": self.api_family.value,
                "api_version": self.api_version,
                "deployment": self.deployment,
                "endpoint": self.endpoint,
                "etag": self.etag,
                "model_version": self.model_version,
                "protocol": "gludd-model-candidate-v1",
                "provider": self.provider.value,
            }
        )


def _validate_azure_endpoint(
    endpoint: object,
    api_family: AzureFoundryAPIFamily,
) -> None:
    if (
        not isinstance(endpoint, str)
        or not endpoint
        or endpoint != endpoint.strip()
        or len(endpoint.encode("utf-8")) > 2_048
    ):
        raise ValueError("endpoint must be one bounded canonical HTTPS URL")
    try:
        parsed = urlsplit(endpoint)
        port = parsed.port
    except ValueError as exc:
        raise ValueError("endpoint must be one bounded canonical HTTPS URL") from exc
    hostname = parsed.hostname
    if (
        parsed.scheme != "https"
        or hostname is None
        or parsed.netloc != hostname
        or parsed.username is not None
        or parsed.password is not None
        or port is not None
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("endpoint must not contain credentials, ports, query, or fragment")
    if api_family is AzureFoundryAPIFamily.MODEL_INFERENCE:
        valid_host = hostname.endswith(_MODEL_INFERENCE_SUFFIXES)
        valid_path = parsed.path == "/models"
    else:
        valid_host = hostname.endswith(_AZURE_OPENAI_SUFFIXES)
        valid_path = parsed.path == ""
    if not valid_host or not valid_path:
        raise ValueError("endpoint does not match its Azure API family")


ModelCandidateIdentity = LocalGGUFCandidateIdentity | AzureFoundryCandidateIdentity


class BackendFailure(StrEnum):
    """Secret-safe infrastructure failures a concrete backend may expose."""

    AUTHENTICATION = "authentication"
    AUTHORIZATION = "authorization"
    NOT_FOUND = "not_found"
    RATE_LIMITED = "rate_limited"
    TIMEOUT = "timeout"
    TRANSPORT = "transport"
    UNAVAILABLE = "unavailable"
    INVALID_RESPONSE = "invalid_response"
    INTERNAL = "internal"


class BackendPolicyFailure(StrEnum):
    """Pre-call reasons a bounded session can reject an invocation."""

    AZURE_OPT_IN_REQUIRED = "azure_opt_in_required"
    IDENTITY_DRIFT = "identity_drift"
    CALL_BUDGET_EXHAUSTED = "call_budget_exhausted"
    INPUT_TOKEN_BUDGET_EXCEEDED = "input_token_budget_exceeded"
    OUTPUT_TOKEN_BUDGET_EXCEEDED = "output_token_budget_exceeded"
    TOTAL_TOKEN_BUDGET_EXCEEDED = "total_token_budget_exceeded"
    COST_BUDGET_EXCEEDED = "cost_budget_exceeded"


class BackendInfrastructureError(RuntimeError):
    """Censored backend failure containing no exception, endpoint, or credential text."""

    def __init__(self, failure: BackendFailure) -> None:
        """Retain only one typed failure category."""
        if not isinstance(failure, BackendFailure):
            raise ValueError("failure must be a typed BackendFailure")
        super().__init__(f"model backend failed: {failure.value}")
        self.failure = failure


class BackendPolicyError(RuntimeError):
    """Censored rejection raised before a candidate backend can observe input."""

    def __init__(self, failure: BackendPolicyFailure) -> None:
        """Retain only one typed policy failure category."""
        if not isinstance(failure, BackendPolicyFailure):
            raise ValueError("failure must be a typed BackendPolicyFailure")
        super().__init__(f"model backend blocked: {failure.value}")
        self.failure = failure


def _bounded_integer(value: object, field_name: str, *, minimum: int, maximum: int) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or value < minimum
        or value > maximum
    ):
        raise ValueError(f"{field_name} must be an integer in {minimum}..{maximum}")
    return value


@dataclass(frozen=True, slots=True)
class BackendCallBudget:
    """Conservative per-session limits reserved before every provider call."""

    max_calls: int
    max_input_tokens: int
    max_output_tokens: int
    max_total_tokens: int
    max_cost_microusd: int
    timeout_seconds: float

    def __post_init__(self) -> None:
        """Reject zero, incoherent, or effectively unbounded limits."""
        _bounded_integer(self.max_calls, "max_calls", minimum=1, maximum=_MAX_CALLS)
        _bounded_integer(
            self.max_input_tokens,
            "max_input_tokens",
            minimum=1,
            maximum=_MAX_TOKENS,
        )
        _bounded_integer(
            self.max_output_tokens,
            "max_output_tokens",
            minimum=1,
            maximum=_MAX_TOKENS,
        )
        _bounded_integer(
            self.max_total_tokens,
            "max_total_tokens",
            minimum=max(self.max_input_tokens, self.max_output_tokens),
            maximum=_MAX_TOKENS,
        )
        _bounded_integer(
            self.max_cost_microusd,
            "max_cost_microusd",
            minimum=0,
            maximum=_MAX_COST_MICROUSD,
        )
        if (
            isinstance(self.timeout_seconds, bool)
            or not isinstance(self.timeout_seconds, (int, float))
            or not 0.0 < float(self.timeout_seconds) <= _MAX_TIMEOUT_SECONDS
        ):
            raise ValueError("timeout_seconds must be in 0..3600")
        object.__setattr__(self, "timeout_seconds", float(self.timeout_seconds))


@dataclass(frozen=True, slots=True)
class BackendBudgetSnapshot:
    """Secret-free evidence of conservative resources reserved by a session."""

    calls_started: int
    reserved_tokens: int
    reserved_cost_microusd: int


_RequestT_contra = TypeVar("_RequestT_contra", contravariant=True)
_ResponseT_co = TypeVar("_ResponseT_co", covariant=True)
_RequestT = TypeVar("_RequestT")
_ResponseT = TypeVar("_ResponseT")


@runtime_checkable
class CandidateBackend(Protocol[_RequestT_contra, _ResponseT_co]):
    """One exact-candidate backend; implementations must never route or fallback."""

    @property
    def candidate_identity(self) -> ModelCandidateIdentity:
        """Return the exact immutable candidate served by this backend."""
        ...

    def generate(
        self,
        request: _RequestT_contra,
        *,
        max_output_tokens: int,
        timeout_seconds: float,
    ) -> _ResponseT_co:
        """Generate exactly once for the bound candidate and supplied limits."""
        ...


class BoundedCandidateSession(Generic[_RequestT, _ResponseT]):
    """Fail-closed single-candidate invocation with no automatic fallback path."""

    def __init__(
        self,
        backend: CandidateBackend[_RequestT, _ResponseT],
        budget: BackendCallBudget,
        *,
        azure_enabled: bool,
    ) -> None:
        """Snapshot one backend identity and an immutable execution budget."""
        if not isinstance(backend, CandidateBackend):
            raise ValueError("backend must implement CandidateBackend")
        identity = backend.candidate_identity
        if not isinstance(identity, (LocalGGUFCandidateIdentity, AzureFoundryCandidateIdentity)):
            raise ValueError("backend must expose one typed candidate identity")
        if not isinstance(budget, BackendCallBudget):
            raise ValueError("budget must be a BackendCallBudget")
        if not isinstance(azure_enabled, bool):
            raise ValueError("azure_enabled must be an explicit boolean")
        self._backend = backend
        self._budget = budget
        self._identity = identity
        self._identity_digest = identity.identity_digest
        self._azure_enabled = azure_enabled
        self._calls_started = 0
        self._reserved_tokens = 0
        self._reserved_cost_microusd = 0

    @property
    def candidate_identity(self) -> ModelCandidateIdentity:
        """Return the immutable identity snapshot bound to this session."""
        return self._identity

    @property
    def snapshot(self) -> BackendBudgetSnapshot:
        """Return non-sensitive reservation evidence for events and traces."""
        return BackendBudgetSnapshot(
            calls_started=self._calls_started,
            reserved_tokens=self._reserved_tokens,
            reserved_cost_microusd=self._reserved_cost_microusd,
        )

    def generate(
        self,
        request: _RequestT,
        *,
        input_tokens: int,
        max_output_tokens: int,
        estimated_cost_microusd: int,
    ) -> _ResponseT:
        """Reserve policy budget, invoke once, and censor untyped failures."""
        self._validate_identity_and_provider()
        self._reserve(
            input_tokens=input_tokens,
            max_output_tokens=max_output_tokens,
            estimated_cost_microusd=estimated_cost_microusd,
        )
        try:
            return self._backend.generate(
                request,
                max_output_tokens=max_output_tokens,
                timeout_seconds=self._budget.timeout_seconds,
            )
        except BackendInfrastructureError:
            raise
        except Exception:
            raise BackendInfrastructureError(BackendFailure.INTERNAL) from None

    def _validate_identity_and_provider(self) -> None:
        current = self._backend.candidate_identity
        if (
            not isinstance(current, (LocalGGUFCandidateIdentity, AzureFoundryCandidateIdentity))
            or current.identity_digest != self._identity_digest
        ):
            raise BackendPolicyError(BackendPolicyFailure.IDENTITY_DRIFT)
        if (
            current.provider is ModelCandidateProvider.AZURE_FOUNDRY
            and not self._azure_enabled
        ):
            raise BackendPolicyError(BackendPolicyFailure.AZURE_OPT_IN_REQUIRED)

    def _reserve(
        self,
        *,
        input_tokens: int,
        max_output_tokens: int,
        estimated_cost_microusd: int,
    ) -> None:
        input_tokens = _bounded_integer(
            input_tokens,
            "input_tokens",
            minimum=0,
            maximum=_MAX_TOKENS,
        )
        max_output_tokens = _bounded_integer(
            max_output_tokens,
            "max_output_tokens",
            minimum=1,
            maximum=_MAX_TOKENS,
        )
        estimated_cost_microusd = _bounded_integer(
            estimated_cost_microusd,
            "estimated_cost_microusd",
            minimum=0,
            maximum=_MAX_COST_MICROUSD,
        )
        if self._calls_started >= self._budget.max_calls:
            raise BackendPolicyError(BackendPolicyFailure.CALL_BUDGET_EXHAUSTED)
        if input_tokens > self._budget.max_input_tokens:
            raise BackendPolicyError(
                BackendPolicyFailure.INPUT_TOKEN_BUDGET_EXCEEDED
            )
        if max_output_tokens > self._budget.max_output_tokens:
            raise BackendPolicyError(
                BackendPolicyFailure.OUTPUT_TOKEN_BUDGET_EXCEEDED
            )
        tokens = input_tokens + max_output_tokens
        if self._reserved_tokens + tokens > self._budget.max_total_tokens:
            raise BackendPolicyError(BackendPolicyFailure.TOTAL_TOKEN_BUDGET_EXCEEDED)
        if (
            self._reserved_cost_microusd + estimated_cost_microusd
            > self._budget.max_cost_microusd
        ):
            raise BackendPolicyError(BackendPolicyFailure.COST_BUDGET_EXCEEDED)
        self._calls_started += 1
        self._reserved_tokens += tokens
        self._reserved_cost_microusd += estimated_cost_microusd


__all__ = (
    "AzureFoundryAPIFamily",
    "AzureFoundryCandidateIdentity",
    "BackendBudgetSnapshot",
    "BackendCallBudget",
    "BackendFailure",
    "BackendInfrastructureError",
    "BackendPolicyError",
    "BackendPolicyFailure",
    "BoundedCandidateSession",
    "CandidateBackend",
    "LocalGGUFCandidateIdentity",
    "ModelCandidateIdentity",
    "ModelCandidateProvider",
)
