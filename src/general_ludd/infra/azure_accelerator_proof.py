"""Tamper-evident receipts for Azure accelerator release proofs.

The live harness owns cloud mutation.  This module owns the smaller, pure-data
contract that prevents a successful workload from being confused with a
successful release proof: ordered lifecycle phases, accelerator attribution,
and verified cleanup must all be present in one canonical receipt.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import math
import re
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum

_SAFE_RUN_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
_SAFE_NAME = re.compile(r"[a-z0-9][a-z0-9-]{0,127}\Z")
_SAFE_CLAIM = re.compile(r"[a-z0-9][a-z0-9._-]{0,127}\Z")
_SAFE_SKU = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
_SAFE_REVISION = re.compile(r"[a-z0-9][a-z0-9-]{0,252}\Z")
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_GIT_SHA = re.compile(r"[0-9a-f]{40}\Z")
_COMPUTE_CAPABILITY = re.compile(r"([0-9]+)\.([0-9]+)\Z")


class AzureAcceleratorBackend(StrEnum):
    """Supported Azure accelerator execution surfaces."""

    VM = "vm"
    VMSS = "vmss"
    CONTAINER_APP = "container_app"


class AzureAcceleratorTelemetrySource(StrEnum):
    """Independent accelerator telemetry contracts."""

    CUDA_DCGM = "cuda_dcgm"
    AZURE_MONITOR = "azure_monitor"


class AzureAcceleratorProofPhase(StrEnum):
    """Monotonic Azure proof lifecycle phases."""

    NEW = "new"
    PREFLIGHTED = "preflighted"
    LEASED = "leased"
    PROVISIONED = "provisioned"
    READY = "ready"
    PROFILE_REGISTERED = "profile_registered"
    WORKLOAD_PROVED = "workload_proved"
    PROFILE_REMOVED = "profile_removed"
    DEALLOCATED = "deallocated"
    DESTROYED = "destroyed"
    PASS = "pass"
    CLEANUP_REQUIRED = "cleanup_required"
    FAILED = "failed"


_SUCCESS_PHASES = (
    AzureAcceleratorProofPhase.NEW,
    AzureAcceleratorProofPhase.PREFLIGHTED,
    AzureAcceleratorProofPhase.LEASED,
    AzureAcceleratorProofPhase.PROVISIONED,
    AzureAcceleratorProofPhase.READY,
    AzureAcceleratorProofPhase.PROFILE_REGISTERED,
    AzureAcceleratorProofPhase.WORKLOAD_PROVED,
    AzureAcceleratorProofPhase.PROFILE_REMOVED,
    AzureAcceleratorProofPhase.DEALLOCATED,
    AzureAcceleratorProofPhase.DESTROYED,
    AzureAcceleratorProofPhase.PASS,
)


def _require_match(value: object, pattern: re.Pattern[str], label: str) -> str:
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        raise ValueError(f"{label} is invalid")
    return value


def _require_int(value: object, label: str, *, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ValueError(f"{label} must be an integer >= {minimum}")
    return value


def _require_bool(value: object, label: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{label} must be boolean")
    return value


def _require_float(value: object, label: str) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
    ):
        raise ValueError(f"{label} must be a finite number")
    return float(value)


def _require_sequence(value: object, label: str) -> tuple[object, ...]:
    if not isinstance(value, (list, tuple)):
        raise ValueError(f"{label} must be an array")
    return tuple(value)


def _required(payload: Mapping[str, object], key: str) -> object:
    try:
        return payload[key]
    except KeyError as error:
        raise ValueError(f"missing required receipt field: {key}") from error


def _canonical_json(payload: Mapping[str, object]) -> str:
    return json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True)


def _sha256(payload: Mapping[str, object]) -> str:
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def _compute_capability(value: str, label: str) -> tuple[int, int]:
    match = _COMPUTE_CAPABILITY.fullmatch(value)
    if match is None:
        raise ValueError(f"{label} must be a CUDA compute capability")
    return int(match.group(1)), int(match.group(2))


@dataclass(frozen=True, slots=True)
class AzureAcceleratorProofState:
    """Immutable state that rejects skipped, repeated, or premature phases."""

    run_id: str
    phase: AzureAcceleratorProofPhase
    history: tuple[AzureAcceleratorProofPhase, ...]

    def __post_init__(self) -> None:
        """Validate the initial state and history binding."""
        _require_match(self.run_id, _SAFE_RUN_ID, "run_id")
        if not self.history or self.history[0] is not AzureAcceleratorProofPhase.NEW:
            raise ValueError("Azure accelerator proof history must start at new")
        if self.history[-1] is not self.phase:
            raise ValueError("Azure accelerator proof phase must match its history")

    @classmethod
    def start(cls, run_id: str) -> AzureAcceleratorProofState:
        """Start a new proof with no implied cloud action."""
        return cls(run_id, AzureAcceleratorProofPhase.NEW, (AzureAcceleratorProofPhase.NEW,))

    def advance(self, next_phase: AzureAcceleratorProofPhase) -> AzureAcceleratorProofState:
        """Advance by exactly one success or cleanup transition."""
        cleanup = AzureAcceleratorProofPhase.CLEANUP_REQUIRED in self.history
        allowed: AzureAcceleratorProofPhase | None = None
        if cleanup:
            cleanup_successors = {
                AzureAcceleratorProofPhase.CLEANUP_REQUIRED: AzureAcceleratorProofPhase.DESTROYED,
                AzureAcceleratorProofPhase.DESTROYED: AzureAcceleratorProofPhase.FAILED,
            }
            allowed = cleanup_successors.get(self.phase)
        elif self.phase in _SUCCESS_PHASES[:-1]:
            allowed = _SUCCESS_PHASES[_SUCCESS_PHASES.index(self.phase) + 1]
        if next_phase is not allowed:
            raise ValueError(
                "invalid Azure accelerator proof transition: "
                f"{self.phase.value} -> {next_phase.value}"
            )
        return AzureAcceleratorProofState(
            self.run_id,
            next_phase,
            (*self.history, next_phase),
        )

    def require_cleanup(self) -> AzureAcceleratorProofState:
        """Divert a non-terminal proof into mandatory cleanup."""
        if self.phase in {
            AzureAcceleratorProofPhase.PASS,
            AzureAcceleratorProofPhase.FAILED,
            AzureAcceleratorProofPhase.CLEANUP_REQUIRED,
        }:
            raise ValueError(
                "invalid Azure accelerator proof transition: "
                f"{self.phase.value} -> cleanup_required"
            )
        next_phase = AzureAcceleratorProofPhase.CLEANUP_REQUIRED
        return AzureAcceleratorProofState(
            self.run_id,
            next_phase,
            (*self.history, next_phase),
        )


@dataclass(frozen=True, slots=True)
class AzureAcceleratorTelemetry:
    """Accelerator evidence bound to the exact candidate identity."""

    backend: AzureAcceleratorBackend
    source: AzureAcceleratorTelemetrySource
    candidate_identity_digest: str
    device_name: str | None = None
    gpu_count: int = 0
    vram_mib: int = 0
    cuda_compute_capability: str | None = None
    cuda_kernel_count: int = 0
    cuda_kernel_duration_ns: int = 0
    cuda_memory_activity_bytes: int = 0
    dcgm_sm_active_samples: tuple[float, ...] = ()
    revision_name: str | None = None
    azure_gpu_maximum_percent: float = 0.0
    azure_gpu_positive_sample_count: int = 0

    def __post_init__(self) -> None:
        """Validate telemetry against its backend-specific evidence contract."""
        _require_match(
            self.candidate_identity_digest,
            _SHA256,
            "candidate_identity_digest",
        )
        if self.source is AzureAcceleratorTelemetrySource.CUDA_DCGM:
            self._validate_cuda_dcgm()
        elif self.source is AzureAcceleratorTelemetrySource.AZURE_MONITOR:
            self._validate_azure_monitor()
        else:
            raise ValueError("unsupported Azure accelerator telemetry source")

    def _validate_cuda_dcgm(self) -> None:
        if self.backend not in {AzureAcceleratorBackend.VM, AzureAcceleratorBackend.VMSS}:
            raise ValueError("CUDA/DCGM telemetry requires an Azure VM or VMSS backend")
        if not isinstance(self.device_name, str) or not self.device_name.strip():
            raise ValueError("CUDA/DCGM telemetry requires a device name")
        _require_int(self.gpu_count, "gpu_count", minimum=1)
        _require_int(self.vram_mib, "vram_mib", minimum=1)
        if self.cuda_compute_capability is None:
            raise ValueError("CUDA/DCGM telemetry requires a CUDA compute capability")
        _compute_capability(self.cuda_compute_capability, "cuda_compute_capability")
        if (
            _require_int(self.cuda_kernel_count, "cuda_kernel_count") == 0
            or _require_int(self.cuda_kernel_duration_ns, "cuda_kernel_duration_ns") == 0
        ):
            raise ValueError("accelerator evidence requires a positive CUDA kernel")
        _require_int(
            self.cuda_memory_activity_bytes,
            "cuda_memory_activity_bytes",
            minimum=1,
        )
        if not self.dcgm_sm_active_samples or len(self.dcgm_sm_active_samples) > 10_000:
            raise ValueError("accelerator evidence requires a fresh positive DCGM window")
        samples = self.dcgm_sm_active_samples
        if any(
            isinstance(sample, bool)
            or not isinstance(sample, (int, float))
            or not math.isfinite(sample)
            or not 0 <= sample <= 1
            for sample in samples
        ) or not any(sample > 0 for sample in samples):
            raise ValueError("accelerator evidence requires a fresh positive DCGM window")

    def _validate_azure_monitor(self) -> None:
        if self.backend is not AzureAcceleratorBackend.CONTAINER_APP:
            raise ValueError("Azure Monitor telemetry requires a Container App backend")
        _require_match(self.revision_name, _SAFE_REVISION, "revision_name")
        maximum = self.azure_gpu_maximum_percent
        positive_samples = self.azure_gpu_positive_sample_count
        if (
            isinstance(maximum, bool)
            or not isinstance(maximum, (int, float))
            or not math.isfinite(maximum)
            or not 0 < maximum <= 100
            or _require_int(positive_samples, "azure_gpu_positive_sample_count") == 0
        ):
            raise ValueError("accelerator evidence requires a positive Azure GPU metric")

    def payload(self) -> dict[str, object]:
        """Return the canonical, secret-free telemetry representation."""
        return {
            "azure_gpu_maximum_percent": self.azure_gpu_maximum_percent,
            "azure_gpu_positive_sample_count": self.azure_gpu_positive_sample_count,
            "backend": self.backend.value,
            "candidate_identity_digest": self.candidate_identity_digest,
            "cuda_compute_capability": self.cuda_compute_capability,
            "cuda_kernel_count": self.cuda_kernel_count,
            "cuda_kernel_duration_ns": self.cuda_kernel_duration_ns,
            "cuda_memory_activity_bytes": self.cuda_memory_activity_bytes,
            "dcgm_sm_active_samples": list(self.dcgm_sm_active_samples),
            "device_name": self.device_name,
            "gpu_count": self.gpu_count,
            "revision_name": self.revision_name,
            "source": self.source.value,
            "vram_mib": self.vram_mib,
        }

    @classmethod
    def from_payload(cls, payload: Mapping[str, object]) -> AzureAcceleratorTelemetry:
        """Parse and validate untrusted telemetry JSON."""
        samples = _require_sequence(_required(payload, "dcgm_sm_active_samples"), "dcgm_sm_active_samples")
        numeric_samples: list[float] = []
        for sample in samples:
            if isinstance(sample, bool) or not isinstance(sample, (int, float)):
                raise ValueError("dcgm_sm_active_samples must contain numbers")
            numeric_samples.append(float(sample))
        device_name = _required(payload, "device_name")
        capability = _required(payload, "cuda_compute_capability")
        revision_name = _required(payload, "revision_name")
        return cls(
            backend=AzureAcceleratorBackend(str(_required(payload, "backend"))),
            source=AzureAcceleratorTelemetrySource(str(_required(payload, "source"))),
            candidate_identity_digest=str(_required(payload, "candidate_identity_digest")),
            device_name=None if device_name is None else str(device_name),
            gpu_count=_require_int(_required(payload, "gpu_count"), "gpu_count"),
            vram_mib=_require_int(_required(payload, "vram_mib"), "vram_mib"),
            cuda_compute_capability=None if capability is None else str(capability),
            cuda_kernel_count=_require_int(_required(payload, "cuda_kernel_count"), "cuda_kernel_count"),
            cuda_kernel_duration_ns=_require_int(
                _required(payload, "cuda_kernel_duration_ns"),
                "cuda_kernel_duration_ns",
            ),
            cuda_memory_activity_bytes=_require_int(
                _required(payload, "cuda_memory_activity_bytes"),
                "cuda_memory_activity_bytes",
            ),
            dcgm_sm_active_samples=tuple(numeric_samples),
            revision_name=None if revision_name is None else str(revision_name),
            azure_gpu_maximum_percent=_require_float(
                _required(payload, "azure_gpu_maximum_percent"),
                "azure_gpu_maximum_percent",
            ),
            azure_gpu_positive_sample_count=_require_int(
                _required(payload, "azure_gpu_positive_sample_count"),
                "azure_gpu_positive_sample_count",
            ),
        )


@dataclass(frozen=True, slots=True)
class AzureAcceleratorClaimPolicy:
    """Policy that binds a release claim to an exact SKU and evidence floor."""

    claim: str
    sku: str
    allowed_backends: tuple[AzureAcceleratorBackend, ...]
    expected_device_name: str | None
    minimum_gpu_count: int
    minimum_vram_mib: int
    minimum_cuda_compute_capability: str | None
    require_device_identity: bool

    def __post_init__(self) -> None:
        """Validate the immutable release-claim policy."""
        _require_match(self.claim, _SAFE_CLAIM, "claim")
        _require_match(self.sku, _SAFE_SKU, "sku")
        if not self.allowed_backends or len(set(self.allowed_backends)) != len(self.allowed_backends):
            raise ValueError("allowed_backends must contain distinct Azure backends")
        if any(not isinstance(backend, AzureAcceleratorBackend) for backend in self.allowed_backends):
            raise ValueError("allowed_backends contains an unsupported Azure backend")
        _require_int(self.minimum_gpu_count, "minimum_gpu_count")
        _require_int(self.minimum_vram_mib, "minimum_vram_mib")
        if self.expected_device_name is not None:
            try:
                re.compile(self.expected_device_name)
            except re.error as error:
                raise ValueError("expected_device_name must be a valid regular expression") from error
        if self.minimum_cuda_compute_capability is not None:
            _compute_capability(
                self.minimum_cuda_compute_capability,
                "minimum_cuda_compute_capability",
            )
        _require_bool(self.require_device_identity, "require_device_identity")


@dataclass(frozen=True, slots=True)
class AzureAcceleratorProofReceipt:
    """Canonical success receipt for one Azure accelerator proof."""

    run_id: str
    git_sha: str
    backend: AzureAcceleratorBackend
    claim: str
    requested_sku: str
    observed_sku: str
    request_id_digest: str
    profile_id_digest: str
    input_tokens: int
    output_tokens: int
    telemetry: AzureAcceleratorTelemetry
    phases: tuple[AzureAcceleratorProofPhase, ...]
    admission_closed: bool
    profile_removed: bool
    compute_released: bool
    resources_destroyed: bool
    absence_verified: bool
    lease_closed: bool

    def __post_init__(self) -> None:
        """Validate the structural and cleanup receipt invariants."""
        _require_match(self.run_id, _SAFE_RUN_ID, "run_id")
        _require_match(self.git_sha, _GIT_SHA, "git_sha")
        _require_match(self.claim, _SAFE_CLAIM, "claim")
        _require_match(self.requested_sku, _SAFE_SKU, "requested_sku")
        _require_match(self.observed_sku, _SAFE_SKU, "observed_sku")
        _require_match(self.request_id_digest, _SHA256, "request_id_digest")
        _require_match(self.profile_id_digest, _SHA256, "profile_id_digest")
        _require_int(self.input_tokens, "input_tokens")
        _require_int(self.output_tokens, "output_tokens")
        if self.phases != _SUCCESS_PHASES:
            raise ValueError("Azure accelerator receipt does not contain the exact successful phase history")
        cleanup = (
            self.admission_closed,
            self.profile_removed,
            self.compute_released,
            self.resources_destroyed,
            self.absence_verified,
            self.lease_closed,
        )
        if any(not isinstance(value, bool) for value in cleanup) or not all(cleanup):
            raise ValueError("Azure accelerator cleanup evidence is incomplete")

    def validate_against(self, policy: AzureAcceleratorClaimPolicy) -> None:
        """Validate this receipt against one release claim policy."""
        if self.claim != policy.claim:
            raise ValueError("Azure accelerator receipt claim does not match policy")
        if self.requested_sku != policy.sku:
            raise ValueError("Azure accelerator requested SKU does not match policy")
        if self.observed_sku != self.requested_sku:
            raise ValueError("Azure accelerator observed SKU does not match the requested SKU")
        if self.backend not in policy.allowed_backends:
            raise ValueError("Azure accelerator backend is not allowed for this claim")
        if self.telemetry.backend is not self.backend:
            raise ValueError("Azure accelerator telemetry backend does not match the receipt")
        if self.input_tokens < 1 or self.output_tokens != 1:
            raise ValueError("Azure accelerator proof requires exactly one output token")
        if not policy.require_device_identity:
            return
        telemetry = self.telemetry
        if telemetry.device_name is None or policy.expected_device_name is None:
            raise ValueError("Azure accelerator device identity evidence is missing")
        if re.search(policy.expected_device_name, telemetry.device_name) is None:
            raise ValueError("Azure accelerator device name does not satisfy policy")
        if telemetry.gpu_count < policy.minimum_gpu_count:
            raise ValueError("Azure accelerator GPU count does not satisfy policy")
        if telemetry.vram_mib < policy.minimum_vram_mib:
            raise ValueError("Azure accelerator VRAM does not satisfy policy")
        minimum_capability = policy.minimum_cuda_compute_capability
        if minimum_capability is not None and (
            telemetry.cuda_compute_capability is None
            or _compute_capability(
                telemetry.cuda_compute_capability,
                "cuda_compute_capability",
            )
            < _compute_capability(
                minimum_capability,
                "minimum_cuda_compute_capability",
            )
        ):
            raise ValueError("Azure accelerator CUDA compute capability does not satisfy policy")

    def _payload_without_digest(self) -> dict[str, object]:
        return {
            "absence_verified": self.absence_verified,
            "admission_closed": self.admission_closed,
            "backend": self.backend.value,
            "claim": self.claim,
            "compute_released": self.compute_released,
            "git_sha": self.git_sha,
            "input_tokens": self.input_tokens,
            "lease_closed": self.lease_closed,
            "observed_sku": self.observed_sku,
            "output_tokens": self.output_tokens,
            "phases": [phase.value for phase in self.phases],
            "profile_id_digest": self.profile_id_digest,
            "profile_removed": self.profile_removed,
            "request_id_digest": self.request_id_digest,
            "requested_sku": self.requested_sku,
            "resources_destroyed": self.resources_destroyed,
            "run_id": self.run_id,
            "telemetry": self.telemetry.payload(),
        }

    @property
    def evidence_sha256(self) -> str:
        """Return the digest of the canonical receipt body."""
        return _sha256(self._payload_without_digest())

    def payload(self) -> dict[str, object]:
        """Return the canonical receipt with its self-verifying digest."""
        payload = self._payload_without_digest()
        payload["evidence_sha256"] = self.evidence_sha256
        return payload

    def to_json(self) -> str:
        """Serialize the receipt deterministically."""
        return _canonical_json(self.payload())

    @classmethod
    def from_payload(cls, payload: Mapping[str, object]) -> AzureAcceleratorProofReceipt:
        """Parse untrusted receipt JSON and reject checksum drift."""
        telemetry_value = _required(payload, "telemetry")
        if not isinstance(telemetry_value, Mapping):
            raise ValueError("telemetry must be an object")
        phase_values = _require_sequence(_required(payload, "phases"), "phases")
        receipt = cls(
            run_id=str(_required(payload, "run_id")),
            git_sha=str(_required(payload, "git_sha")),
            backend=AzureAcceleratorBackend(str(_required(payload, "backend"))),
            claim=str(_required(payload, "claim")),
            requested_sku=str(_required(payload, "requested_sku")),
            observed_sku=str(_required(payload, "observed_sku")),
            request_id_digest=str(_required(payload, "request_id_digest")),
            profile_id_digest=str(_required(payload, "profile_id_digest")),
            input_tokens=_require_int(_required(payload, "input_tokens"), "input_tokens"),
            output_tokens=_require_int(_required(payload, "output_tokens"), "output_tokens"),
            telemetry=AzureAcceleratorTelemetry.from_payload(telemetry_value),
            phases=tuple(AzureAcceleratorProofPhase(str(value)) for value in phase_values),
            admission_closed=_require_bool(_required(payload, "admission_closed"), "admission_closed"),
            profile_removed=_require_bool(_required(payload, "profile_removed"), "profile_removed"),
            compute_released=_require_bool(_required(payload, "compute_released"), "compute_released"),
            resources_destroyed=_require_bool(
                _required(payload, "resources_destroyed"),
                "resources_destroyed",
            ),
            absence_verified=_require_bool(_required(payload, "absence_verified"), "absence_verified"),
            lease_closed=_require_bool(_required(payload, "lease_closed"), "lease_closed"),
        )
        supplied_digest = payload.get("evidence_sha256")
        if supplied_digest is not None:
            digest = _require_match(supplied_digest, _SHA256, "evidence_sha256")
            if not hmac.compare_digest(digest, receipt.evidence_sha256):
                raise ValueError("Azure accelerator receipt checksum does not match its evidence")
        return receipt


@dataclass(frozen=True, slots=True)
class ProviderProofReference:
    """Content-addressed evidence from one provider."""

    provider: str
    evidence_sha256: str

    def __post_init__(self) -> None:
        """Validate the provider name and content address."""
        _require_match(self.provider, _SAFE_NAME, "provider")
        _require_match(self.evidence_sha256, _SHA256, "evidence_sha256")

    def payload(self) -> dict[str, object]:
        """Return the canonical provider reference."""
        return {
            "evidence_sha256": self.evidence_sha256,
            "provider": self.provider,
        }


@dataclass(frozen=True, slots=True)
class MixedProviderProofReceipt:
    """Bind Azure evidence to at least one independent provider proof."""

    run_id: str
    git_sha: str
    azure_evidence_sha256: str
    provider_receipts: tuple[ProviderProofReference, ...]
    selected_provider: str
    cleanup_verified: bool

    def __post_init__(self) -> None:
        """Validate cross-provider uniqueness and Azure evidence binding."""
        _require_match(self.run_id, _SAFE_RUN_ID, "run_id")
        _require_match(self.git_sha, _GIT_SHA, "git_sha")
        _require_match(self.azure_evidence_sha256, _SHA256, "azure_evidence_sha256")
        _require_match(self.selected_provider, _SAFE_NAME, "selected_provider")
        providers = self.provider_names
        azure_references = tuple(
            reference
            for reference in self.provider_receipts
            if reference.provider == "azure"
        )
        if (
            len(self.provider_receipts) < 2
            or len(set(providers)) != len(providers)
            or len(azure_references) != 1
            or azure_references[0].evidence_sha256 != self.azure_evidence_sha256
            or self.selected_provider not in providers
            or self.cleanup_verified is not True
        ):
            raise ValueError(
                "mixed-provider proof requires distinct providers, exact Azure binding, "
                "a selected provider, and verified cleanup"
            )

    @property
    def provider_names(self) -> tuple[str, ...]:
        """Return providers in canonical receipt order."""
        return tuple(reference.provider for reference in self.provider_receipts)

    def _payload_without_digest(self) -> dict[str, object]:
        return {
            "azure_evidence_sha256": self.azure_evidence_sha256,
            "cleanup_verified": self.cleanup_verified,
            "git_sha": self.git_sha,
            "provider_receipts": [reference.payload() for reference in self.provider_receipts],
            "run_id": self.run_id,
            "selected_provider": self.selected_provider,
        }

    @property
    def evidence_sha256(self) -> str:
        """Return the digest of the mixed-provider receipt body."""
        return _sha256(self._payload_without_digest())

    def payload(self) -> dict[str, object]:
        """Return the canonical mixed-provider receipt and digest."""
        payload = self._payload_without_digest()
        payload["evidence_sha256"] = self.evidence_sha256
        return payload

    def to_json(self) -> str:
        """Serialize the mixed-provider receipt deterministically."""
        return _canonical_json(self.payload())
