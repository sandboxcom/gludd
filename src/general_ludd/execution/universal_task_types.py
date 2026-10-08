"""Provider-neutral contracts shared by universal task executors and adapters."""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable

from general_ludd.hardware.model_service_rightsizing import InferenceWorkloadDemand
from general_ludd.scheduling.scheduler import WorkItem

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class TaskStatus(StrEnum):
    """Terminal state of a universal task attempt."""

    SUCCEEDED = "succeeded"
    REFUSED = "refused"
    FAILED = "failed"


@dataclass(frozen=True)
class ModelProfileOrigin:
    """Content-addressed provenance bound to one exact routing scope."""

    source: str
    protocol: str
    evidence_sha256: str
    profile_id: str
    provider: str
    accelerator_sku: str
    capabilities: frozenset[str]
    allowed_data_classifications: frozenset[str]
    offline: bool
    model_runner_id: str | None
    receipt_sha256: str

    @classmethod
    def bind(
        cls,
        *,
        source: str,
        protocol: str,
        evidence_sha256: str,
        profile_id: str,
        provider: str,
        accelerator_sku: str,
        capabilities: frozenset[str],
        allowed_data_classifications: frozenset[str],
        offline: bool,
        model_runner_id: str | None,
    ) -> ModelProfileOrigin:
        """Create a receipt whose digest binds every security-relevant field."""
        receipt_sha256 = cls._receipt_digest(
            source=source,
            protocol=protocol,
            evidence_sha256=evidence_sha256,
            profile_id=profile_id,
            provider=provider,
            accelerator_sku=accelerator_sku,
            capabilities=capabilities,
            allowed_data_classifications=allowed_data_classifications,
            offline=offline,
            model_runner_id=model_runner_id,
        )
        return cls(
            source=source,
            protocol=protocol,
            evidence_sha256=evidence_sha256,
            profile_id=profile_id,
            provider=provider,
            accelerator_sku=accelerator_sku,
            capabilities=capabilities,
            allowed_data_classifications=allowed_data_classifications,
            offline=offline,
            model_runner_id=model_runner_id,
            receipt_sha256=receipt_sha256,
        )

    def __post_init__(self) -> None:
        """Reject mutable, ambiguous, or digest-inconsistent origin receipts."""
        self.validate()

    def validate(self) -> None:
        """Revalidate canonical fields and the receipt digest at a trust boundary."""
        for name in ("source", "protocol", "profile_id", "provider", "accelerator_sku"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value or value != value.strip():
                raise ValueError(f"{name} must be canonical non-empty text")
        if _SHA256_RE.fullmatch(self.evidence_sha256) is None:
            raise ValueError("evidence_sha256 must be a lowercase SHA-256 digest")
        if not isinstance(self.capabilities, frozenset) or not self.capabilities:
            raise ValueError("capabilities must be a non-empty frozenset")
        if (
            not isinstance(self.allowed_data_classifications, frozenset)
            or not self.allowed_data_classifications
        ):
            raise ValueError(
                "allowed_data_classifications must be a non-empty frozenset"
            )
        for values, name in (
            (self.capabilities, "capabilities"),
            (self.allowed_data_classifications, "allowed_data_classifications"),
        ):
            if any(
                not isinstance(value, str)
                or not value
                or value != value.strip()
                for value in values
            ):
                raise ValueError(f"{name} must contain canonical non-empty text")
        if not isinstance(self.offline, bool):
            raise ValueError("offline must be boolean")
        if self.model_runner_id is not None and (
            not isinstance(self.model_runner_id, str)
            or not self.model_runner_id.strip()
            or self.model_runner_id != self.model_runner_id.strip()
        ):
            raise ValueError("model_runner_id must be canonical text when provided")
        expected = self._receipt_digest(
            source=self.source,
            protocol=self.protocol,
            evidence_sha256=self.evidence_sha256,
            profile_id=self.profile_id,
            provider=self.provider,
            accelerator_sku=self.accelerator_sku,
            capabilities=self.capabilities,
            allowed_data_classifications=self.allowed_data_classifications,
            offline=self.offline,
            model_runner_id=self.model_runner_id,
        )
        if self.receipt_sha256 != expected:
            raise ValueError("receipt_sha256 does not match the bound profile origin")

    def matches_target(
        self,
        *,
        profile_id: str,
        provider: str,
        accelerator_sku: str,
        capabilities: frozenset[str],
        allowed_data_classifications: frozenset[str],
        offline: bool,
        model_runner_id: str | None,
    ) -> bool:
        """Return whether this receipt belongs to the exact supplied target."""
        return (
            self.profile_id == profile_id
            and self.provider == provider
            and self.accelerator_sku == accelerator_sku
            and self.capabilities == capabilities
            and self.allowed_data_classifications == allowed_data_classifications
            and self.offline is offline
            and self.model_runner_id == model_runner_id
        )

    @staticmethod
    def _receipt_digest(
        *,
        source: str,
        protocol: str,
        evidence_sha256: str,
        profile_id: str,
        provider: str,
        accelerator_sku: str,
        capabilities: frozenset[str],
        allowed_data_classifications: frozenset[str],
        offline: bool,
        model_runner_id: str | None,
    ) -> str:
        payload = {
            "schema_version": "gludd-profile-origin-receipt-v1",
            "source": source,
            "protocol": protocol,
            "evidence_sha256": evidence_sha256,
            "profile_id": profile_id,
            "provider": provider,
            "accelerator_sku": accelerator_sku,
            "capabilities": sorted(capabilities),
            "allowed_data_classifications": sorted(
                allowed_data_classifications
            ),
            "offline": offline,
            "model_runner_id": model_runner_id,
        }
        encoded = json.dumps(
            payload,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("ascii")
        return hashlib.sha256(encoded).hexdigest()


@runtime_checkable
class ProfileOriginVerifierProtocol(Protocol):
    """Trust-root boundary for externally discovered profile receipts."""

    def verify(self, origin: ModelProfileOrigin) -> bool:
        """Return whether the exact content-addressed receipt is trusted."""
        ...


class PinnedProfileOriginVerifier:
    """Verify discovered origins against an immutable receipt allowlist."""

    def __init__(self, trusted_receipt_sha256: frozenset[str]) -> None:
        """Bind the exact receipt digests trusted for this executor."""
        if not isinstance(trusted_receipt_sha256, frozenset):
            raise TypeError("trusted receipt digests must be a frozenset")
        if any(_SHA256_RE.fullmatch(value) is None for value in trusted_receipt_sha256):
            raise ValueError("trusted receipt digests must be lowercase SHA-256")
        self._trusted_receipt_sha256 = trusted_receipt_sha256

    @classmethod
    def from_origins(
        cls,
        origins: Sequence[ModelProfileOrigin],
    ) -> PinnedProfileOriginVerifier:
        """Pin a validated immutable snapshot of origin receipts."""
        if isinstance(origins, (str, bytes)) or not all(
            isinstance(origin, ModelProfileOrigin) for origin in origins
        ):
            raise TypeError("origins must contain ModelProfileOrigin receipts")
        return cls(frozenset(origin.receipt_sha256 for origin in origins))

    def verify(self, origin: ModelProfileOrigin) -> bool:
        """Accept only the complete receipt previously pinned by the operator."""
        try:
            origin.validate()
        except Exception:
            return False
        return origin.receipt_sha256 in self._trusted_receipt_sha256


@runtime_checkable
class ModelResponseProtocol(Protocol):
    """The normalized response surface supplied by ``ModelGateway``."""

    content: str
    cost_estimate: float


@runtime_checkable
class ModelGatewayProtocol(Protocol):
    """Structural subset of ``ModelGateway`` needed by the executor."""

    def call_model(
        self,
        profile_id: str,
        messages: list[dict[str, str]],
        **kwargs: Any,
    ) -> ModelResponseProtocol:
        """Invoke one selected model profile and return a normalized response."""
        ...


@runtime_checkable
class SchedulerProtocol(Protocol):
    """Structural subset of ``Scheduler`` used for work admission."""

    def plan(self, items: list[WorkItem]) -> list[list[str]]:
        """Group admitted work items into conflict-free execution batches."""
        ...


@runtime_checkable
class AcceleratorPlannerProtocol(Protocol):
    """Read-only accelerator discovery; execution never provisions hardware."""

    def discover_hardware(self) -> Sequence[object]:
        """Return the currently observed provider-neutral accelerator inventory."""
        ...


@runtime_checkable
class ToolRunnerProtocol(Protocol):
    """Injected bounded tool surface available to capability adapters."""

    def run(
        self,
        tool_name: str,
        payload: dict[str, object],
    ) -> dict[str, object]:
        """Run one allowlisted tool with structured input and output."""
        ...


@dataclass(frozen=True)
class UniversalTaskRequest:
    """Provider-neutral task input and execution constraints."""

    task_id: str
    capability: str
    instruction: str
    budget_usd: float
    data_classification: str = "public"
    allowed_tools: frozenset[str] = field(default_factory=frozenset)
    resources: frozenset[str] = field(default_factory=frozenset)
    metadata: Mapping[str, object] = field(default_factory=dict)
    model_workload: InferenceWorkloadDemand | None = None

    def __post_init__(self) -> None:
        """Reject incomplete identities and unusable budget constraints."""
        for name in ("task_id", "capability", "instruction", "data_classification"):
            if not str(getattr(self, name)).strip():
                raise ValueError(f"{name} must be non-empty")
        if not math.isfinite(self.budget_usd) or self.budget_usd < 0:
            raise ValueError("budget_usd must be finite and non-negative")
        if self.model_workload is not None and not isinstance(
            self.model_workload,
            InferenceWorkloadDemand,
        ):
            raise ValueError("model_workload must be InferenceWorkloadDemand")


@dataclass(frozen=True)
class ExecutionTarget:
    """One model target plus the evidence required to route to it."""

    profile_id: str
    provider: str
    accelerator_sku: str
    capabilities: frozenset[str]
    allowed_data_classifications: frozenset[str]
    estimated_cost_usd: float
    healthy: bool
    health_evidence: str
    capability_evidence: str
    cost_evidence: str
    privacy_evidence: str
    offline: bool
    model_runner_id: str | None = None
    profile_origin: ModelProfileOrigin | None = None

    @classmethod
    def bind_origin(
        cls,
        *,
        profile_id: str,
        provider: str,
        accelerator_sku: str,
        capabilities: frozenset[str],
        allowed_data_classifications: frozenset[str],
        estimated_cost_usd: float,
        healthy: bool,
        health_evidence: str,
        capability_evidence: str,
        cost_evidence: str,
        privacy_evidence: str,
        offline: bool,
        origin_source: str,
        origin_protocol: str,
        origin_evidence_sha256: str,
        model_runner_id: str | None = None,
    ) -> ExecutionTarget:
        """Create a target and its inseparable content-addressed origin receipt."""
        origin = ModelProfileOrigin.bind(
            source=origin_source,
            protocol=origin_protocol,
            evidence_sha256=origin_evidence_sha256,
            profile_id=profile_id,
            provider=provider,
            accelerator_sku=accelerator_sku,
            capabilities=capabilities,
            allowed_data_classifications=allowed_data_classifications,
            offline=offline,
            model_runner_id=model_runner_id,
        )
        return cls(
            profile_id=profile_id,
            provider=provider,
            accelerator_sku=accelerator_sku,
            capabilities=capabilities,
            allowed_data_classifications=allowed_data_classifications,
            estimated_cost_usd=estimated_cost_usd,
            healthy=healthy,
            health_evidence=health_evidence,
            capability_evidence=capability_evidence,
            cost_evidence=cost_evidence,
            privacy_evidence=privacy_evidence,
            offline=offline,
            model_runner_id=model_runner_id,
            profile_origin=origin,
        )

    def __post_init__(self) -> None:
        """Require complete routing evidence and a finite cost claim."""
        for name in (
            "profile_id",
            "provider",
            "accelerator_sku",
            "health_evidence",
            "capability_evidence",
            "cost_evidence",
            "privacy_evidence",
        ):
            if not str(getattr(self, name)).strip():
                raise ValueError(f"{name} must be non-empty")
        if not self.capabilities:
            raise ValueError("capabilities must be non-empty")
        if not self.allowed_data_classifications:
            raise ValueError("allowed_data_classifications must be non-empty")
        if not math.isfinite(self.estimated_cost_usd) or self.estimated_cost_usd < 0:
            raise ValueError("estimated_cost_usd must be finite and non-negative")
        if self.model_runner_id is not None and (
            not isinstance(self.model_runner_id, str)
            or not self.model_runner_id.strip()
        ):
            raise ValueError("model_runner_id must be non-empty text when provided")
        if self.profile_origin is None:
            raise ValueError("profile_origin_required")
        if not isinstance(self.profile_origin, ModelProfileOrigin):
            raise ValueError("profile_origin must be ModelProfileOrigin")
        if not self.profile_origin.matches_target(
            profile_id=self.profile_id,
            provider=self.provider,
            accelerator_sku=self.accelerator_sku,
            capabilities=self.capabilities,
            allowed_data_classifications=self.allowed_data_classifications,
            offline=self.offline,
            model_runner_id=self.model_runner_id,
        ):
            raise ValueError("profile_origin_target_mismatch")


@runtime_checkable
class ModelServicePlanProtocol(Protocol):
    """Structural desired-state surface consumed by the task graph."""

    @property
    def resource_key(self) -> str:
        """Return the selected accelerator resource identity."""
        ...

    @property
    def runner_id(self) -> str:
        """Return the selected model runner identity."""
        ...

    @property
    def replica_count(self) -> int:
        """Return the required replica count."""
        ...

    @property
    def devices_per_replica(self) -> int:
        """Return the required device count for each replica."""
        ...

    def to_dict(self) -> Mapping[str, object]:
        """Return credential-free model-service desired state."""
        ...


@runtime_checkable
class ModelServicePlannerProtocol(Protocol):
    """Task-to-model-service planning boundary for any capability."""

    def plan(
        self,
        request: UniversalTaskRequest,
        target: ExecutionTarget,
    ) -> ModelServicePlanProtocol:
        """Produce one immutable desired service or raise a typed refusal."""
        ...


@dataclass(frozen=True)
class TargetEvaluation:
    """Auditable eligibility decision for one execution target."""

    profile_id: str
    provider: str
    eligible: bool
    reasons: tuple[str, ...]
    evidence: Mapping[str, object] = field(default_factory=dict)
    profile_origin: ModelProfileOrigin | None = None


@dataclass(frozen=True)
class RouteDecision:
    """Selected target and all evaluated alternatives."""

    selected_profile_id: str | None
    selected_provider: str | None
    evaluations: tuple[TargetEvaluation, ...]
    selected_profile_origin: ModelProfileOrigin | None = None


@dataclass(frozen=True)
class AdapterDecision:
    """Preflight verdict returned by a capability adapter."""

    accepted: bool
    reasons: tuple[str, ...] = ()
    evidence: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class CandidateAssessment:
    """Safety, validation, provenance, and tool verdict for a candidate."""

    accepted: bool
    reasons: tuple[str, ...] = ()
    evidence: Mapping[str, object] = field(default_factory=dict)


@runtime_checkable
class TaskAdapterProtocol(Protocol):
    """Capability-owned translation and validation boundary."""

    capability: str

    def build_messages(
        self,
        request: UniversalTaskRequest,
        target: ExecutionTarget,
    ) -> list[dict[str, str]]:
        """Translate a universal request into target-ready model messages."""
        ...

    def preflight(
        self,
        request: UniversalTaskRequest,
        target: ExecutionTarget,
    ) -> AdapterDecision:
        """Decide whether the request and target satisfy capability policy."""
        ...

    def parse_candidate(self, content: str) -> object:
        """Parse normalized model content into a capability-owned candidate."""
        ...

    def assess_candidate(
        self,
        request: UniversalTaskRequest,
        candidate: object,
        tool_runner: ToolRunnerProtocol | None,
    ) -> CandidateAssessment:
        """Validate one candidate with the adapter's bounded tool surface."""
        ...


@dataclass(frozen=True)
class UniversalTaskResult:
    """Terminal executor result; success always carries a gated candidate."""

    task_id: str
    status: TaskStatus
    route: RouteDecision | None
    candidate: object | None = None
    reasons: tuple[str, ...] = ()
    evidence: Mapping[str, object] = field(default_factory=dict)


__all__ = [
    "AcceleratorPlannerProtocol",
    "AdapterDecision",
    "CandidateAssessment",
    "ExecutionTarget",
    "ModelGatewayProtocol",
    "ModelProfileOrigin",
    "ModelResponseProtocol",
    "ModelServicePlanProtocol",
    "ModelServicePlannerProtocol",
    "PinnedProfileOriginVerifier",
    "ProfileOriginVerifierProtocol",
    "RouteDecision",
    "SchedulerProtocol",
    "TargetEvaluation",
    "TaskAdapterProtocol",
    "TaskStatus",
    "ToolRunnerProtocol",
    "UniversalTaskRequest",
    "UniversalTaskResult",
]
