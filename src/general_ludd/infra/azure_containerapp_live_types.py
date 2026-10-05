"""Validated authority, evidence, and trace values for a Container App proof."""

from __future__ import annotations

import hashlib
import ipaddress
import json
import re
import uuid
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol, runtime_checkable
from urllib.parse import urlsplit

from general_ludd.infra.azure_containerapp_failure_details import (
    LIVE_PROOF_FAILURE_DETAILS,
)
from general_ludd.infra.azure_containerapp_gpu import AzureContainerAppGPUProfile
from general_ludd.self_improve.model_candidates import (
    AzureContainerAppCandidateIdentity,
    BackendCallBudget,
)

LIVE_PROOF_ACKNOWLEDGEMENT = "DEPLOY_ONE_CONTAINER_APP_AND_DESTROY"
_RESOURCE_GROUP_RE = re.compile(r"(?=.{1,90}\Z)[A-Za-z0-9_().-]+(?<!\.)")
_RESOURCE_NAME_RE = re.compile(r"(?=.{1,64}\Z)[A-Za-z0-9_.-]+")
_APP_NAME_RE = re.compile(r"(?=.{2,32}\Z)[a-z][a-z0-9-]*[a-z0-9]")
_LOCATION_RE = re.compile(r"[a-z][a-z0-9]{1,31}")
class AzureContainerAppLiveProofFailure(StrEnum):
    """Fixed failure categories that cannot expose provider or project data."""

    POLICY = "policy"
    ENVIRONMENT = "environment"
    PLAN_SCOPE = "plan_scope"
    PREFLIGHT = "preflight"
    APPLY = "apply"
    DEPLOYMENT_EVIDENCE = "deployment_evidence"
    DISCOVERY = "discovery"
    WORK_REQUEST = "work_request"
    CLEANUP = "cleanup"
    TRACE = "trace"


class AzureContainerAppLiveProofError(RuntimeError):
    """Censored live-proof failure with an exact lifecycle category."""

    def __init__(
        self,
        failure: AzureContainerAppLiveProofFailure,
        *,
        detail: str | None = None,
    ) -> None:
        """Initialize an error without retaining sensitive response content."""
        if not isinstance(failure, AzureContainerAppLiveProofFailure):
            raise ValueError("failure must be an AzureContainerAppLiveProofFailure")
        if detail is not None and detail not in LIVE_PROOF_FAILURE_DETAILS:
            raise ValueError("detail must be a fixed live-proof failure detail")
        super().__init__(f"Azure Container App live proof failed: {failure.value}")
        self.failure = failure
        self.detail = detail


class LiveProofEvent(StrEnum):
    """Observable transitions for the complete proof lifecycle."""

    POLICY_VALIDATED = "azure_containerapp_policy_validated"
    PLAN_STARTED = "azure_containerapp_plan_started"
    PLAN_AUDITED = "azure_containerapp_plan_audited"
    DRY_RUN_COMPLETED = "azure_containerapp_dry_run_completed"
    PREFLIGHT_STARTED = "azure_containerapp_preflight_started"
    PREFLIGHT_SUCCEEDED = "azure_containerapp_preflight_succeeded"
    APPLY_STARTED = "azure_containerapp_apply_started"
    APPLY_SUCCEEDED = "azure_containerapp_apply_succeeded"
    DEPLOYMENT_VERIFIED = "azure_containerapp_deployment_verified"
    DISCOVERY_STARTED = "azure_containerapp_endpoint_discovery_started"
    DISCOVERY_SUCCEEDED = "azure_containerapp_endpoint_discovery_succeeded"
    WORK_REQUEST_STARTED = "azure_containerapp_work_request_started"
    WORK_REQUEST_SUCCEEDED = "azure_containerapp_work_request_succeeded"
    BACKEND_CLOSED = "azure_containerapp_backend_closed"
    DESTROY_STARTED = "azure_containerapp_destroy_started"
    DESTROY_SUCCEEDED = "azure_containerapp_destroy_succeeded"
    ABSENCE_VERIFIED = "azure_containerapp_absence_verified"
    COMPLETED = "azure_containerapp_live_proof_completed"
    FAILED = "azure_containerapp_live_proof_failed"


@dataclass(frozen=True, slots=True)
class LiveProofTrace:
    """Content-free transition evidence safe for logs and durable events."""

    event: LiveProofEvent
    operation_digest: str
    candidate_identity_digest: str | None = None
    failure: AzureContainerAppLiveProofFailure | None = None
    failure_detail: str | None = None
    resource_change_count: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0


@dataclass(frozen=True, slots=True)
class AzureContainerAppLiveProofPolicy:
    """Explicit cost, scope, provenance, and effect authority for one proof."""

    subscription_id: str
    resource_group: str
    environment_name: str
    workload_profile_name: str
    workload_profile_type: str
    location: str
    app_name: str
    allowed_cidr: str
    container_image: str
    model_name: str
    model_revision: str
    max_cost_usd: float
    ttl_minutes: int
    call_budget: BackendCallBudget
    estimated_request_cost_microusd: int
    live: bool
    acknowledgement: str | None
    min_replicas: int = 0
    max_replicas: int = 1
    http_concurrent_requests: int = 1
    gpu_profile: AzureContainerAppGPUProfile | None = None

    def __post_init__(self) -> None:
        """Reject broad, mutable, ambiguous, or unbounded deployment authority."""
        try:
            canonical_subscription = str(uuid.UUID(self.subscription_id))
        except (ValueError, AttributeError):
            canonical_subscription = ""
        if self.subscription_id != canonical_subscription:
            raise ValueError("subscription_id must be one canonical UUID")
        if _RESOURCE_GROUP_RE.fullmatch(self.resource_group) is None:
            raise ValueError("resource_group must be one safe Azure name")
        for value, label in (
            (self.environment_name, "environment_name"),
            (self.workload_profile_name, "workload_profile_name"),
        ):
            if _RESOURCE_NAME_RE.fullmatch(value) is None:
                raise ValueError(f"{label} must be one safe Azure name")
        if _APP_NAME_RE.fullmatch(self.app_name) is None:
            raise ValueError("app_name must be one canonical Container App name")
        try:
            allowed_network = ipaddress.ip_network(self.allowed_cidr, strict=True)
        except (TypeError, ValueError):
            raise ValueError("allowed_cidr must be one canonical IPv4 /32") from None
        if allowed_network.version != 4 or allowed_network.prefixlen != 32:
            raise ValueError("allowed_cidr must be one canonical IPv4 /32")
        if _LOCATION_RE.fullmatch(self.location) is None:
            raise ValueError("location must be one lowercase Azure region")
        if (
            isinstance(self.max_cost_usd, bool)
            or not isinstance(self.max_cost_usd, (int, float))
            or not 0.0 < float(self.max_cost_usd) <= 5.0
        ):
            raise ValueError("max_cost_usd must be in 0..5")
        if (
            isinstance(self.ttl_minutes, bool)
            or not isinstance(self.ttl_minutes, int)
            or not 1 <= self.ttl_minutes <= 60
        ):
            raise ValueError("ttl_minutes must be in 1..60")
        if not isinstance(self.call_budget, BackendCallBudget):
            raise ValueError("call_budget must be a BackendCallBudget")
        if self.call_budget.max_calls != 1:
            raise ValueError("the proof call budget must permit exactly one request")
        if (
            isinstance(self.estimated_request_cost_microusd, bool)
            or not isinstance(self.estimated_request_cost_microusd, int)
            or not 0
            <= self.estimated_request_cost_microusd
            <= self.call_budget.max_cost_microusd
        ):
            raise ValueError("estimated request cost exceeds the call budget")
        if not isinstance(self.live, bool):
            raise ValueError("live must be an explicit boolean")
        if self.live:
            if self.acknowledgement != LIVE_PROOF_ACKNOWLEDGEMENT:
                raise ValueError("live proof requires the exact acknowledgement")
        elif self.acknowledgement is not None:
            raise ValueError("dry-run acknowledgement must be omitted")
        if (
            isinstance(self.min_replicas, bool)
            or not isinstance(self.min_replicas, int)
            or not 0 <= self.min_replicas <= 1
        ):
            raise ValueError("min_replicas must be zero or one")
        if (
            isinstance(self.max_replicas, bool)
            or not isinstance(self.max_replicas, int)
            or not 1 <= self.max_replicas <= 100
        ):
            raise ValueError("max_replicas must be in 1..100")
        if (
            isinstance(self.http_concurrent_requests, bool)
            or not isinstance(self.http_concurrent_requests, int)
            or not 1 <= self.http_concurrent_requests <= 100_000
        ):
            raise ValueError("http_concurrent_requests must be in 1..100000")
        if self.gpu_profile is not None and (
            not isinstance(self.gpu_profile, AzureContainerAppGPUProfile)
            or self.gpu_profile.workload_profile_name != self.workload_profile_name
            or self.gpu_profile.workload_profile_type != self.workload_profile_type
        ):
            raise ValueError("gpu_profile must match the selected workload profile")
        if (
            not isinstance(self.container_image, str)
            or self.container_image.count("@") != 1
        ):
            raise ValueError("container_image must use one immutable digest")
        image_name, image_digest = self.container_image.rsplit("@", 1)
        if not image_name:
            raise ValueError("container_image must use one immutable digest")
        AzureContainerAppCandidateIdentity(
            endpoint="https://validation.azurecontainerapps.io",
            resource_id=self.expected_resource_id,
            revision_name=f"{self.app_name}--validation",
            image_digest=image_digest,
            model_name=self.model_name,
            model_revision=self.model_revision,
            workload_profile_type=self.workload_profile_type,
        )

    @property
    def resource_group_id(self) -> str:
        """Return the canonical resource-group ARM identifier."""
        return (
            f"/subscriptions/{self.subscription_id}/resourceGroups/"
            f"{self.resource_group}"
        )

    @property
    def environment_id(self) -> str:
        """Return the canonical managed-environment ARM identifier."""
        return (
            f"{self.resource_group_id}/providers/Microsoft.App/"
            f"managedEnvironments/{self.environment_name}"
        )

    @property
    def expected_resource_id(self) -> str:
        """Return the exact Container App ARM identifier authorized by policy."""
        return (
            f"{self.resource_group_id}/providers/Microsoft.App/"
            f"containerApps/{self.app_name}"
        )

    @property
    def image_digest(self) -> str:
        """Return the immutable container-image digest from policy."""
        return self.container_image.rsplit("@", 1)[1]

    @property
    def operation_digest(self) -> str:
        """Return the stable digest binding every authorized proof input."""
        payload = {
            "app_name": self.app_name,
            "allowed_cidr": self.allowed_cidr,
            "call_budget": self.call_budget.payload(),
            "container_image": self.container_image,
            "environment_id": self.environment_id,
            "estimated_request_cost_microusd": self.estimated_request_cost_microusd,
            "live": self.live,
            "location": self.location,
            "max_cost_usd": self.max_cost_usd,
            "model_name": self.model_name,
            "model_revision": self.model_revision,
            "min_replicas": self.min_replicas,
            "max_replicas": self.max_replicas,
            "http_concurrent_requests": self.http_concurrent_requests,
            "gpu_profile": (
                None if self.gpu_profile is None else self.gpu_profile.payload()
            ),
            "protocol": "gludd-azure-containerapp-live-proof-v1",
            "ttl_minutes": self.ttl_minutes,
            "workload_profile_name": self.workload_profile_name,
            "workload_profile_type": self.workload_profile_type,
        }
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True, slots=True)
class AzureContainerAppDeploymentEvidence:
    """Terraform outputs needed to bind and later clean one exact app revision."""

    resource_id: str
    cleanup_resource_id: str
    endpoint: str
    revision_name: str
    workload_profile_type: str

    def candidate_identity(
        self,
        policy: AzureContainerAppLiveProofPolicy,
    ) -> AzureContainerAppCandidateIdentity:
        """Validate outputs against policy and return the immutable candidate."""
        if (
            self.resource_id != policy.expected_resource_id
            or self.cleanup_resource_id != policy.expected_resource_id
            or self.workload_profile_type != policy.workload_profile_type
        ):
            raise ValueError("deployment outputs do not match the approved scope")
        try:
            hostname = urlsplit(self.endpoint).hostname
        except ValueError:
            hostname = None
        if hostname is None or not hostname.startswith(f"{policy.app_name}."):
            raise ValueError("deployment endpoint does not match the approved app")
        return AzureContainerAppCandidateIdentity(
            endpoint=self.endpoint,
            resource_id=self.resource_id,
            revision_name=self.revision_name,
            image_digest=policy.image_digest,
            model_name=policy.model_name,
            model_revision=policy.model_revision,
            workload_profile_type=self.workload_profile_type,
        )


@dataclass(frozen=True, slots=True)
class AzureContainerAppLiveProofResult:
    """Content-safe proof outcome; response text is deliberately absent from repr."""

    plan_audited: bool
    deployment_created: bool
    work_completed: bool
    cleanup_verified: bool
    operation_digest: str
    candidate_identity_digest: str | None = None
    response_text: str | None = field(default=None, repr=False)
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0


@runtime_checkable
class AzureContainerAppProofRuntime(Protocol):
    """Make-mediated infrastructure effects used by the proof orchestrator."""

    def plan(self, policy: AzureContainerAppLiveProofPolicy) -> object:
        """Return a bounded Terraform plan for one approved policy."""
        ...

    def preflight(self, policy: AzureContainerAppLiveProofPolicy) -> None:
        """Prove environment availability and sizing before paid effects."""
        ...

    def apply(
        self,
        policy: AzureContainerAppLiveProofPolicy,
    ) -> AzureContainerAppDeploymentEvidence:
        """Apply the saved plan and return deployment identity evidence."""
        ...

    def destroy(self, policy: AzureContainerAppLiveProofPolicy) -> None:
        """Destroy only resources owned by the exact approved policy."""
        ...

    def exists(self, policy: AzureContainerAppLiveProofPolicy) -> bool:
        """Return whether the exact policy-owned app still exists."""
        ...


__all__ = (
    "LIVE_PROOF_ACKNOWLEDGEMENT",
    "AzureContainerAppDeploymentEvidence",
    "AzureContainerAppLiveProofError",
    "AzureContainerAppLiveProofFailure",
    "AzureContainerAppLiveProofPolicy",
    "AzureContainerAppLiveProofResult",
    "AzureContainerAppProofRuntime",
    "LiveProofEvent",
    "LiveProofTrace",
)
