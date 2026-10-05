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
_SECRET_VALUE_PATTERNS = (
    re.compile(r"\bbearer\s+\S+", re.IGNORECASE),
    re.compile(r"[?&](?:sig|se|sp|sv)=", re.IGNORECASE),
    re.compile(r"\b(?:accountkey|client_secret|password)\s*[:=]", re.IGNORECASE),
    re.compile(r"-----BEGIN (?:EC |OPENSSH |RSA )?PRIVATE KEY-----"),
)
_SECRET_KEYS = {
    "access_token",
    "account_key",
    "authorization",
    "client_secret",
    "credential",
    "credentials",
    "password",
    "refresh_token",
    "sas_token",
}


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


def _require_string(value: object, label: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{label} must be a string")
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


def _require_epoch(value: object, label: str) -> int:
    return _require_int(value, label, minimum=1)


def _require_exact_keys(
    payload: Mapping[str, object],
    expected: set[str],
    label: str,
) -> None:
    keys = set(payload)
    if any(not isinstance(key, str) for key in keys):
        raise ValueError(f"{label} field names must be strings")
    missing = expected - keys
    if missing:
        raise ValueError(f"missing required receipt field: {sorted(missing)[0]}")
    unexpected = keys - expected
    if unexpected:
        raise ValueError(f"{label} contains unsupported fields: {sorted(unexpected)!r}")


def _assert_no_secret_material(value: object, path: str = "receipt") -> None:
    """Reject secret-bearing keys and recognizable credential values."""
    if isinstance(value, Mapping):
        for key, child in value.items():
            if not isinstance(key, str):
                raise ValueError(f"{path} field names must be strings")
            normalized = key.lower().replace("-", "_")
            if normalized in _SECRET_KEYS or normalized.endswith(("_password", "_secret")):
                raise ValueError(f"secret-bearing field is forbidden: {path}.{key}")
            _assert_no_secret_material(child, f"{path}.{key}")
        return
    if isinstance(value, (list, tuple)):
        for index, child in enumerate(value):
            _assert_no_secret_material(child, f"{path}[{index}]")
        return
    if isinstance(value, str) and any(
        pattern.search(value) for pattern in _SECRET_VALUE_PATTERNS
    ):
        raise ValueError(f"secret-like material is forbidden: {path}")


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
    run_id: str
    git_sha: str
    candidate_identity_digest: str
    subscription_id_digest: str
    resource_id_digest: str
    observed_at_epoch_s: int
    device_name: str | None = None
    gpu_count: int = 0
    vram_mib: int = 0
    cuda_compute_capability: str | None = None
    cuda_kernel_count: int = 0
    cuda_kernel_duration_ns: int = 0
    cuda_memory_activity_bytes: int = 0
    dcgm_sm_active_samples: tuple[float, ...] = ()
    device_identity_digests: tuple[str, ...] = ()
    cuda_device_identity_digests: tuple[str, ...] = ()
    dcgm_device_identity_digests: tuple[str, ...] = ()
    revision_name: str | None = None
    azure_gpu_maximum_percent: float = 0.0
    azure_gpu_positive_sample_count: int = 0

    def __post_init__(self) -> None:
        """Validate telemetry against its backend-specific evidence contract."""
        _require_match(self.run_id, _SAFE_RUN_ID, "run_id")
        _require_match(self.git_sha, _GIT_SHA, "git_sha")
        _require_match(
            self.candidate_identity_digest,
            _SHA256,
            "candidate_identity_digest",
        )
        _require_match(
            self.subscription_id_digest,
            _SHA256,
            "subscription_id_digest",
        )
        _require_match(self.resource_id_digest, _SHA256, "resource_id_digest")
        _require_epoch(self.observed_at_epoch_s, "observed_at_epoch_s")
        if self.source is AzureAcceleratorTelemetrySource.CUDA_DCGM:
            self._validate_cuda_dcgm()
        elif self.source is AzureAcceleratorTelemetrySource.AZURE_MONITOR:
            self._validate_azure_monitor()
        else:
            raise ValueError("unsupported Azure accelerator telemetry source")
        _assert_no_secret_material(self.payload(), "telemetry")

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
        topology = (
            self.device_identity_digests,
            self.cuda_device_identity_digests,
            self.dcgm_device_identity_digests,
        )
        if any(
            len(identities) != self.gpu_count
            or len(set(identities)) != len(identities)
            or any(_SHA256.fullmatch(identity) is None for identity in identities)
            for identities in topology
        ) or not (
            set(self.device_identity_digests)
            == set(self.cuda_device_identity_digests)
            == set(self.dcgm_device_identity_digests)
        ):
            raise ValueError(
                "accelerator device topology must match CUDA and DCGM attribution"
            )
        if (
            self.revision_name is not None
            or self.azure_gpu_maximum_percent != 0
            or self.azure_gpu_positive_sample_count != 0
        ):
            raise ValueError("CUDA/DCGM telemetry cannot contain Container App evidence")

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
        if (
            self.device_name is not None
            or self.gpu_count != 0
            or self.vram_mib != 0
            or self.cuda_compute_capability is not None
            or self.cuda_kernel_count != 0
            or self.cuda_kernel_duration_ns != 0
            or self.cuda_memory_activity_bytes != 0
            or self.dcgm_sm_active_samples
            or self.device_identity_digests
            or self.cuda_device_identity_digests
            or self.dcgm_device_identity_digests
        ):
            raise ValueError("Azure Monitor telemetry cannot contain VM device evidence")

    def payload(self) -> dict[str, object]:
        """Return the canonical, secret-free telemetry representation."""
        return {
            "azure_gpu_maximum_percent": self.azure_gpu_maximum_percent,
            "azure_gpu_positive_sample_count": self.azure_gpu_positive_sample_count,
            "backend": self.backend.value,
            "candidate_identity_digest": self.candidate_identity_digest,
            "cuda_device_identity_digests": list(
                self.cuda_device_identity_digests
            ),
            "cuda_compute_capability": self.cuda_compute_capability,
            "cuda_kernel_count": self.cuda_kernel_count,
            "cuda_kernel_duration_ns": self.cuda_kernel_duration_ns,
            "cuda_memory_activity_bytes": self.cuda_memory_activity_bytes,
            "dcgm_sm_active_samples": list(self.dcgm_sm_active_samples),
            "dcgm_device_identity_digests": list(
                self.dcgm_device_identity_digests
            ),
            "device_identity_digests": list(self.device_identity_digests),
            "device_name": self.device_name,
            "git_sha": self.git_sha,
            "gpu_count": self.gpu_count,
            "observed_at_epoch_s": self.observed_at_epoch_s,
            "resource_id_digest": self.resource_id_digest,
            "revision_name": self.revision_name,
            "run_id": self.run_id,
            "source": self.source.value,
            "subscription_id_digest": self.subscription_id_digest,
            "vram_mib": self.vram_mib,
        }

    @classmethod
    def from_payload(cls, payload: Mapping[str, object]) -> AzureAcceleratorTelemetry:
        """Parse and validate untrusted telemetry JSON."""
        _assert_no_secret_material(payload, "telemetry")
        _require_exact_keys(
            payload,
            {
                "azure_gpu_maximum_percent",
                "azure_gpu_positive_sample_count",
                "backend",
                "candidate_identity_digest",
                "cuda_compute_capability",
                "cuda_device_identity_digests",
                "cuda_kernel_count",
                "cuda_kernel_duration_ns",
                "cuda_memory_activity_bytes",
                "dcgm_device_identity_digests",
                "dcgm_sm_active_samples",
                "device_identity_digests",
                "device_name",
                "git_sha",
                "gpu_count",
                "observed_at_epoch_s",
                "resource_id_digest",
                "revision_name",
                "run_id",
                "source",
                "subscription_id_digest",
                "vram_mib",
            },
            "telemetry",
        )
        samples = _require_sequence(_required(payload, "dcgm_sm_active_samples"), "dcgm_sm_active_samples")
        numeric_samples: list[float] = []
        for sample in samples:
            if isinstance(sample, bool) or not isinstance(sample, (int, float)):
                raise ValueError("dcgm_sm_active_samples must contain numbers")
            numeric_samples.append(float(sample))
        device_name = _required(payload, "device_name")
        capability = _required(payload, "cuda_compute_capability")
        revision_name = _required(payload, "revision_name")
        device_identities = _require_sequence(
            _required(payload, "device_identity_digests"),
            "device_identity_digests",
        )
        cuda_identities = _require_sequence(
            _required(payload, "cuda_device_identity_digests"),
            "cuda_device_identity_digests",
        )
        dcgm_identities = _require_sequence(
            _required(payload, "dcgm_device_identity_digests"),
            "dcgm_device_identity_digests",
        )
        return cls(
            backend=AzureAcceleratorBackend(
                _require_string(_required(payload, "backend"), "backend")
            ),
            source=AzureAcceleratorTelemetrySource(
                _require_string(_required(payload, "source"), "source")
            ),
            run_id=_require_string(_required(payload, "run_id"), "run_id"),
            git_sha=_require_string(_required(payload, "git_sha"), "git_sha"),
            candidate_identity_digest=_require_string(
                _required(payload, "candidate_identity_digest"),
                "candidate_identity_digest",
            ),
            subscription_id_digest=_require_string(
                _required(payload, "subscription_id_digest"),
                "subscription_id_digest",
            ),
            resource_id_digest=_require_string(
                _required(payload, "resource_id_digest"),
                "resource_id_digest",
            ),
            observed_at_epoch_s=_require_epoch(
                _required(payload, "observed_at_epoch_s"),
                "observed_at_epoch_s",
            ),
            device_name=(
                None
                if device_name is None
                else _require_string(device_name, "device_name")
            ),
            gpu_count=_require_int(_required(payload, "gpu_count"), "gpu_count"),
            vram_mib=_require_int(_required(payload, "vram_mib"), "vram_mib"),
            cuda_compute_capability=(
                None
                if capability is None
                else _require_string(capability, "cuda_compute_capability")
            ),
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
            device_identity_digests=tuple(
                _require_string(value, "device_identity_digests")
                for value in device_identities
            ),
            cuda_device_identity_digests=tuple(
                _require_string(value, "cuda_device_identity_digests")
                for value in cuda_identities
            ),
            dcgm_device_identity_digests=tuple(
                _require_string(value, "dcgm_device_identity_digests")
                for value in dcgm_identities
            ),
            revision_name=(
                None
                if revision_name is None
                else _require_string(revision_name, "revision_name")
            ),
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
    expected_candidate_identity_digest: str
    expected_subscription_id_digest: str
    expected_resource_id_digest: str
    expected_gpu_count: int
    expected_revision_name: str | None
    maximum_proof_age_s: int

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
        _require_match(
            self.expected_candidate_identity_digest,
            _SHA256,
            "expected_candidate_identity_digest",
        )
        _require_match(
            self.expected_subscription_id_digest,
            _SHA256,
            "expected_subscription_id_digest",
        )
        _require_match(
            self.expected_resource_id_digest,
            _SHA256,
            "expected_resource_id_digest",
        )
        _require_int(self.expected_gpu_count, "expected_gpu_count")
        if self.expected_gpu_count < self.minimum_gpu_count:
            raise ValueError("expected_gpu_count cannot be below minimum_gpu_count")
        if self.expected_revision_name is not None:
            _require_match(
                self.expected_revision_name,
                _SAFE_REVISION,
                "expected_revision_name",
            )
        _require_int(self.maximum_proof_age_s, "maximum_proof_age_s", minimum=1)


@dataclass(frozen=True, slots=True)
class AzureResourceAbsenceEvidence:
    """Exact Azure resource lookup proving that the owned resource is absent."""

    run_id: str
    git_sha: str
    subscription_id_digest: str
    resource_id_digest: str
    checked_at_epoch_s: int
    status_code: int
    lookup_attempts: int

    def __post_init__(self) -> None:
        """Validate identity binding and the terminal Azure 404."""
        _require_match(self.run_id, _SAFE_RUN_ID, "run_id")
        _require_match(self.git_sha, _GIT_SHA, "git_sha")
        _require_match(
            self.subscription_id_digest,
            _SHA256,
            "subscription_id_digest",
        )
        _require_match(self.resource_id_digest, _SHA256, "resource_id_digest")
        _require_epoch(self.checked_at_epoch_s, "checked_at_epoch_s")
        if _require_int(self.status_code, "status_code") != 404:
            raise ValueError("Azure resource absence evidence requires an exact 404")
        _require_int(self.lookup_attempts, "lookup_attempts", minimum=1)

    def payload(self) -> dict[str, object]:
        """Return the canonical non-secret absence evidence."""
        return {
            "checked_at_epoch_s": self.checked_at_epoch_s,
            "git_sha": self.git_sha,
            "lookup_attempts": self.lookup_attempts,
            "resource_id_digest": self.resource_id_digest,
            "run_id": self.run_id,
            "status_code": self.status_code,
            "subscription_id_digest": self.subscription_id_digest,
        }

    @classmethod
    def from_payload(
        cls,
        payload: Mapping[str, object],
    ) -> AzureResourceAbsenceEvidence:
        """Parse one untrusted exact-resource absence observation."""
        _assert_no_secret_material(payload, "absence_evidence")
        _require_exact_keys(
            payload,
            {
                "checked_at_epoch_s",
                "git_sha",
                "lookup_attempts",
                "resource_id_digest",
                "run_id",
                "status_code",
                "subscription_id_digest",
            },
            "absence_evidence",
        )
        return cls(
            run_id=_require_string(_required(payload, "run_id"), "run_id"),
            git_sha=_require_string(_required(payload, "git_sha"), "git_sha"),
            subscription_id_digest=_require_string(
                _required(payload, "subscription_id_digest"),
                "subscription_id_digest",
            ),
            resource_id_digest=_require_string(
                _required(payload, "resource_id_digest"),
                "resource_id_digest",
            ),
            checked_at_epoch_s=_require_epoch(
                _required(payload, "checked_at_epoch_s"),
                "checked_at_epoch_s",
            ),
            status_code=_require_int(_required(payload, "status_code"), "status_code"),
            lookup_attempts=_require_int(
                _required(payload, "lookup_attempts"),
                "lookup_attempts",
                minimum=1,
            ),
        )


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
    absence_evidence: AzureResourceAbsenceEvidence
    started_at_epoch_s: int
    completed_at_epoch_s: int
    lease_expires_at_epoch_s: int
    spend_expires_at_epoch_s: int
    phases: tuple[AzureAcceleratorProofPhase, ...]
    admission_closed: bool
    profile_removed: bool
    compute_released: bool
    resources_destroyed: bool
    absence_verified: bool
    lease_closed: bool
    spend_closed: bool
    secret_scan_passed: bool
    secret_scan_sha256: str

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
        _require_epoch(self.started_at_epoch_s, "started_at_epoch_s")
        _require_epoch(self.completed_at_epoch_s, "completed_at_epoch_s")
        _require_epoch(self.lease_expires_at_epoch_s, "lease_expires_at_epoch_s")
        _require_epoch(self.spend_expires_at_epoch_s, "spend_expires_at_epoch_s")
        _require_match(self.secret_scan_sha256, _SHA256, "secret_scan_sha256")
        if self.phases != _SUCCESS_PHASES:
            raise ValueError("Azure accelerator receipt does not contain the exact successful phase history")
        cleanup = (
            self.admission_closed,
            self.profile_removed,
            self.compute_released,
            self.resources_destroyed,
            self.absence_verified,
            self.lease_closed,
            self.spend_closed,
            self.secret_scan_passed,
        )
        if any(not isinstance(value, bool) for value in cleanup) or not all(cleanup):
            raise ValueError("Azure accelerator cleanup evidence is incomplete")
        if self.telemetry.run_id != self.run_id:
            raise ValueError("Azure accelerator telemetry run does not match the receipt")
        if self.telemetry.git_sha != self.git_sha:
            raise ValueError("Azure accelerator telemetry Git SHA does not match the receipt")
        if self.absence_evidence.run_id != self.run_id:
            raise ValueError("Azure absence run does not match the receipt")
        if self.absence_evidence.git_sha != self.git_sha:
            raise ValueError("Azure absence Git SHA does not match the receipt")
        if (
            self.absence_evidence.subscription_id_digest
            != self.telemetry.subscription_id_digest
        ):
            raise ValueError("Azure absence subscription does not match telemetry")
        if self.absence_evidence.resource_id_digest != self.telemetry.resource_id_digest:
            raise ValueError("Azure absence resource does not match telemetry")
        if not (
            self.started_at_epoch_s
            <= self.telemetry.observed_at_epoch_s
            <= self.absence_evidence.checked_at_epoch_s
            <= self.completed_at_epoch_s
        ):
            raise ValueError("Azure proof evidence ordering is invalid or stale")
        if (
            self.lease_expires_at_epoch_s < self.completed_at_epoch_s
            or self.spend_expires_at_epoch_s < self.completed_at_epoch_s
        ):
            raise ValueError("Azure lease or spend authority expired before proof completion")
        _assert_no_secret_material(self._payload_without_digest())

    def validate_against(
        self,
        policy: AzureAcceleratorClaimPolicy,
        *,
        verification_time_epoch_s: int,
    ) -> None:
        """Validate this receipt against one release claim policy."""
        verified_at = _require_epoch(
            verification_time_epoch_s,
            "verification_time_epoch_s",
        )
        if verified_at < self.completed_at_epoch_s:
            raise ValueError("Azure accelerator receipt completion is in the future")
        if verified_at - self.completed_at_epoch_s > policy.maximum_proof_age_s:
            raise ValueError("Azure accelerator cleanup evidence is stale")
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
        telemetry = self.telemetry
        if (
            telemetry.candidate_identity_digest
            != policy.expected_candidate_identity_digest
        ):
            raise ValueError("Azure accelerator candidate identity does not match policy")
        if telemetry.subscription_id_digest != policy.expected_subscription_id_digest:
            raise ValueError("Azure accelerator subscription identity does not match policy")
        if telemetry.resource_id_digest != policy.expected_resource_id_digest:
            raise ValueError("Azure accelerator resource identity does not match policy")
        if telemetry.gpu_count != policy.expected_gpu_count:
            raise ValueError("Azure accelerator GPU device topology does not match policy")
        if telemetry.revision_name != policy.expected_revision_name:
            raise ValueError("Azure accelerator revision does not match policy")
        if self.input_tokens < 1 or self.output_tokens != 1:
            raise ValueError("Azure accelerator proof requires exactly one output token")
        if not policy.require_device_identity:
            return
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
            "absence_evidence": self.absence_evidence.payload(),
            "absence_verified": self.absence_verified,
            "admission_closed": self.admission_closed,
            "backend": self.backend.value,
            "claim": self.claim,
            "compute_released": self.compute_released,
            "completed_at_epoch_s": self.completed_at_epoch_s,
            "git_sha": self.git_sha,
            "input_tokens": self.input_tokens,
            "lease_closed": self.lease_closed,
            "lease_expires_at_epoch_s": self.lease_expires_at_epoch_s,
            "observed_sku": self.observed_sku,
            "output_tokens": self.output_tokens,
            "phases": [phase.value for phase in self.phases],
            "profile_id_digest": self.profile_id_digest,
            "profile_removed": self.profile_removed,
            "request_id_digest": self.request_id_digest,
            "requested_sku": self.requested_sku,
            "resources_destroyed": self.resources_destroyed,
            "run_id": self.run_id,
            "secret_scan_passed": self.secret_scan_passed,
            "secret_scan_sha256": self.secret_scan_sha256,
            "spend_closed": self.spend_closed,
            "spend_expires_at_epoch_s": self.spend_expires_at_epoch_s,
            "started_at_epoch_s": self.started_at_epoch_s,
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
        _assert_no_secret_material(payload)
        _require_exact_keys(
            payload,
            {
                "absence_evidence",
                "absence_verified",
                "admission_closed",
                "backend",
                "claim",
                "completed_at_epoch_s",
                "compute_released",
                "evidence_sha256",
                "git_sha",
                "input_tokens",
                "lease_closed",
                "lease_expires_at_epoch_s",
                "observed_sku",
                "output_tokens",
                "phases",
                "profile_id_digest",
                "profile_removed",
                "request_id_digest",
                "requested_sku",
                "resources_destroyed",
                "run_id",
                "secret_scan_passed",
                "secret_scan_sha256",
                "spend_closed",
                "spend_expires_at_epoch_s",
                "started_at_epoch_s",
                "telemetry",
            },
            "Azure accelerator receipt",
        )
        telemetry_value = _required(payload, "telemetry")
        if not isinstance(telemetry_value, Mapping):
            raise ValueError("telemetry must be an object")
        absence_value = _required(payload, "absence_evidence")
        if not isinstance(absence_value, Mapping):
            raise ValueError("absence_evidence must be an object")
        phase_values = _require_sequence(_required(payload, "phases"), "phases")
        receipt = cls(
            run_id=_require_string(_required(payload, "run_id"), "run_id"),
            git_sha=_require_string(_required(payload, "git_sha"), "git_sha"),
            backend=AzureAcceleratorBackend(
                _require_string(_required(payload, "backend"), "backend")
            ),
            claim=_require_string(_required(payload, "claim"), "claim"),
            requested_sku=_require_string(
                _required(payload, "requested_sku"),
                "requested_sku",
            ),
            observed_sku=_require_string(
                _required(payload, "observed_sku"),
                "observed_sku",
            ),
            request_id_digest=_require_string(
                _required(payload, "request_id_digest"),
                "request_id_digest",
            ),
            profile_id_digest=_require_string(
                _required(payload, "profile_id_digest"),
                "profile_id_digest",
            ),
            input_tokens=_require_int(_required(payload, "input_tokens"), "input_tokens"),
            output_tokens=_require_int(_required(payload, "output_tokens"), "output_tokens"),
            telemetry=AzureAcceleratorTelemetry.from_payload(telemetry_value),
            absence_evidence=AzureResourceAbsenceEvidence.from_payload(absence_value),
            started_at_epoch_s=_require_epoch(
                _required(payload, "started_at_epoch_s"),
                "started_at_epoch_s",
            ),
            completed_at_epoch_s=_require_epoch(
                _required(payload, "completed_at_epoch_s"),
                "completed_at_epoch_s",
            ),
            lease_expires_at_epoch_s=_require_epoch(
                _required(payload, "lease_expires_at_epoch_s"),
                "lease_expires_at_epoch_s",
            ),
            spend_expires_at_epoch_s=_require_epoch(
                _required(payload, "spend_expires_at_epoch_s"),
                "spend_expires_at_epoch_s",
            ),
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
            spend_closed=_require_bool(_required(payload, "spend_closed"), "spend_closed"),
            secret_scan_passed=_require_bool(
                _required(payload, "secret_scan_passed"),
                "secret_scan_passed",
            ),
            secret_scan_sha256=_require_string(
                _required(payload, "secret_scan_sha256"),
                "secret_scan_sha256",
            ),
        )
        supplied_digest = _required(payload, "evidence_sha256")
        digest = _require_match(supplied_digest, _SHA256, "evidence_sha256")
        if not hmac.compare_digest(digest, receipt.evidence_sha256):
            raise ValueError("Azure accelerator receipt checksum does not match its evidence")
        return receipt


@dataclass(frozen=True, slots=True)
class ProviderProofReference:
    """Content-addressed evidence from one provider."""

    provider: str
    evidence_sha256: str
    run_id: str
    git_sha: str

    def __post_init__(self) -> None:
        """Validate the provider name and content address."""
        _require_match(self.provider, _SAFE_NAME, "provider")
        _require_match(self.evidence_sha256, _SHA256, "evidence_sha256")
        _require_match(self.run_id, _SAFE_RUN_ID, "run_id")
        _require_match(self.git_sha, _GIT_SHA, "git_sha")

    def payload(self) -> dict[str, object]:
        """Return the canonical provider reference."""
        return {
            "evidence_sha256": self.evidence_sha256,
            "git_sha": self.git_sha,
            "provider": self.provider,
            "run_id": self.run_id,
        }

    @classmethod
    def from_payload(cls, payload: Mapping[str, object]) -> ProviderProofReference:
        """Parse one exact provider evidence reference."""
        _assert_no_secret_material(payload, "provider_reference")
        _require_exact_keys(
            payload,
            {"evidence_sha256", "git_sha", "provider", "run_id"},
            "provider_reference",
        )
        return cls(
            provider=_require_string(_required(payload, "provider"), "provider"),
            evidence_sha256=_require_string(
                _required(payload, "evidence_sha256"),
                "evidence_sha256",
            ),
            run_id=_require_string(_required(payload, "run_id"), "run_id"),
            git_sha=_require_string(_required(payload, "git_sha"), "git_sha"),
        )


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
        identities_match = all(
            reference.run_id == self.run_id and reference.git_sha == self.git_sha
            for reference in self.provider_receipts
        )
        if (
            len(self.provider_receipts) < 2
            or len(set(providers)) != len(providers)
            or len(azure_references) != 1
            or azure_references[0].evidence_sha256 != self.azure_evidence_sha256
            or self.selected_provider not in providers
            or self.cleanup_verified is not True
            or not identities_match
        ):
            raise ValueError(
                "mixed-provider proof requires distinct providers, exact Azure binding, "
                "the same run and Git SHA, a selected provider, and verified cleanup"
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

    @classmethod
    def from_payload(cls, payload: Mapping[str, object]) -> MixedProviderProofReceipt:
        """Parse and checksum-verify an untrusted mixed-provider receipt."""
        _assert_no_secret_material(payload, "mixed_provider_receipt")
        _require_exact_keys(
            payload,
            {
                "azure_evidence_sha256",
                "cleanup_verified",
                "evidence_sha256",
                "git_sha",
                "provider_receipts",
                "run_id",
                "selected_provider",
            },
            "mixed_provider_receipt",
        )
        references_value = _require_sequence(
            _required(payload, "provider_receipts"),
            "provider_receipts",
        )
        references: list[ProviderProofReference] = []
        for reference in references_value:
            if not isinstance(reference, Mapping):
                raise ValueError("provider_receipts must contain objects")
            references.append(ProviderProofReference.from_payload(reference))
        receipt = cls(
            run_id=_require_string(_required(payload, "run_id"), "run_id"),
            git_sha=_require_string(_required(payload, "git_sha"), "git_sha"),
            azure_evidence_sha256=_require_string(
                _required(payload, "azure_evidence_sha256"),
                "azure_evidence_sha256",
            ),
            provider_receipts=tuple(references),
            selected_provider=_require_string(
                _required(payload, "selected_provider"),
                "selected_provider",
            ),
            cleanup_verified=_require_bool(
                _required(payload, "cleanup_verified"),
                "cleanup_verified",
            ),
        )
        supplied_digest = _require_match(
            _required(payload, "evidence_sha256"),
            _SHA256,
            "evidence_sha256",
        )
        if not hmac.compare_digest(supplied_digest, receipt.evidence_sha256):
            raise ValueError("mixed-provider receipt checksum does not match its evidence")
        return receipt
