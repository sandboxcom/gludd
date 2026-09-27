"""Behavioral contract for Azure accelerator live-proof receipts."""

from __future__ import annotations

import json

import pytest

from general_ludd.infra.azure_accelerator_proof import (
    AzureAcceleratorBackend,
    AzureAcceleratorClaimPolicy,
    AzureAcceleratorProofPhase,
    AzureAcceleratorProofReceipt,
    AzureAcceleratorProofState,
    AzureAcceleratorTelemetry,
    AzureAcceleratorTelemetrySource,
    MixedProviderProofReceipt,
    ProviderProofReference,
)

_RUN_ID = "v011-azure-proof-001"
_GIT_SHA = "a" * 40
_DIGEST = "b" * 64


def _success_phases() -> tuple[AzureAcceleratorProofPhase, ...]:
    return (
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


def _vm_telemetry(
    backend: AzureAcceleratorBackend = AzureAcceleratorBackend.VM,
) -> AzureAcceleratorTelemetry:
    return AzureAcceleratorTelemetry(
        backend=backend,
        source=AzureAcceleratorTelemetrySource.CUDA_DCGM,
        candidate_identity_digest=_DIGEST,
        device_name="NVIDIA A100 80GB PCIe",
        gpu_count=1,
        vram_mib=81_920,
        cuda_compute_capability="8.0",
        cuda_kernel_count=3,
        cuda_kernel_duration_ns=42_000,
        cuda_memory_activity_bytes=4_096,
        dcgm_sm_active_samples=(0.25, 0.75),
    )


def _containerapp_telemetry() -> AzureAcceleratorTelemetry:
    return AzureAcceleratorTelemetry(
        backend=AzureAcceleratorBackend.CONTAINER_APP,
        source=AzureAcceleratorTelemetrySource.AZURE_MONITOR,
        candidate_identity_digest=_DIGEST,
        revision_name="gludd-vllm-proof--0000001",
        azure_gpu_maximum_percent=42.0,
        azure_gpu_positive_sample_count=2,
    )


def _receipt(
    telemetry: AzureAcceleratorTelemetry | None = None,
    *,
    output_tokens: int = 1,
    observed_sku: str = "Standard_NC24ads_A100_v4",
) -> AzureAcceleratorProofReceipt:
    selected = telemetry or _vm_telemetry()
    return AzureAcceleratorProofReceipt(
        run_id=_RUN_ID,
        git_sha=_GIT_SHA,
        backend=selected.backend,
        claim="azure-a100-80gb",
        requested_sku="Standard_NC24ads_A100_v4",
        observed_sku=observed_sku,
        request_id_digest="c" * 64,
        profile_id_digest="d" * 64,
        input_tokens=128,
        output_tokens=output_tokens,
        telemetry=selected,
        phases=_success_phases(),
        admission_closed=True,
        profile_removed=True,
        compute_released=True,
        resources_destroyed=True,
        absence_verified=True,
        lease_closed=True,
    )


def _a100_policy(
    *backends: AzureAcceleratorBackend,
) -> AzureAcceleratorClaimPolicy:
    return AzureAcceleratorClaimPolicy(
        claim="azure-a100-80gb",
        sku="Standard_NC24ads_A100_v4",
        allowed_backends=backends or (AzureAcceleratorBackend.VM,),
        expected_device_name=r"^NVIDIA A100.*80GB",
        minimum_gpu_count=1,
        minimum_vram_mib=79_000,
        minimum_cuda_compute_capability="8.0",
        require_device_identity=True,
    )


def test_success_state_is_monotonic_and_reaches_pass_only_after_destroy() -> None:
    state = AzureAcceleratorProofState.start(_RUN_ID)
    for phase in _success_phases()[1:]:
        state = state.advance(phase)

    assert state.phase is AzureAcceleratorProofPhase.PASS
    assert state.history == _success_phases()


@pytest.mark.parametrize(
    "phase",
    [
        AzureAcceleratorProofPhase.READY,
        AzureAcceleratorProofPhase.PASS,
        AzureAcceleratorProofPhase.NEW,
    ],
)
def test_state_rejects_skips_early_pass_and_repeats(
    phase: AzureAcceleratorProofPhase,
) -> None:
    with pytest.raises(ValueError, match="invalid Azure accelerator proof transition"):
        AzureAcceleratorProofState.start(_RUN_ID).advance(phase)


def test_failure_requires_cleanup_and_cannot_turn_into_pass() -> None:
    state = AzureAcceleratorProofState.start(_RUN_ID).advance(
        AzureAcceleratorProofPhase.PREFLIGHTED
    )
    state = state.require_cleanup()
    state = state.advance(AzureAcceleratorProofPhase.DESTROYED)

    with pytest.raises(ValueError, match="invalid Azure accelerator proof transition"):
        state.advance(AzureAcceleratorProofPhase.PASS)

    assert state.advance(AzureAcceleratorProofPhase.FAILED).phase is (
        AzureAcceleratorProofPhase.FAILED
    )


def test_vm_receipt_validates_three_plane_accelerator_evidence() -> None:
    receipt = _receipt()

    receipt.validate_against(_a100_policy(AzureAcceleratorBackend.VM))

    assert len(receipt.evidence_sha256) == 64
    assert json.loads(receipt.to_json())["telemetry"]["cuda_kernel_count"] == 3


def test_vmss_uses_the_same_device_attribution_contract() -> None:
    receipt = _receipt(_vm_telemetry(AzureAcceleratorBackend.VMSS))

    receipt.validate_against(_a100_policy(AzureAcceleratorBackend.VMSS))


def test_containerapp_receipt_accepts_exact_revision_azure_monitor_evidence() -> None:
    receipt = _receipt(_containerapp_telemetry())
    policy = AzureAcceleratorClaimPolicy(
        claim="azure-a100-80gb",
        sku="Standard_NC24ads_A100_v4",
        allowed_backends=(AzureAcceleratorBackend.CONTAINER_APP,),
        expected_device_name=None,
        minimum_gpu_count=0,
        minimum_vram_mib=0,
        minimum_cuda_compute_capability=None,
        require_device_identity=False,
    )

    receipt.validate_against(policy)


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"output_tokens": 2}, "exactly one output token"),
        ({"observed_sku": "Standard_NC24ads_A100_v5"}, "observed SKU"),
    ],
)
def test_receipt_rejects_wrong_token_count_or_sku(
    changes: dict[str, object],
    message: str,
) -> None:
    receipt = _receipt(**changes)

    with pytest.raises(ValueError, match=message):
        receipt.validate_against(_a100_policy(AzureAcceleratorBackend.VM))


def test_vm_telemetry_rejects_zero_kernel_or_stale_dcgm_window() -> None:
    with pytest.raises(ValueError, match="positive CUDA kernel"):
        AzureAcceleratorTelemetry(
            backend=AzureAcceleratorBackend.VM,
            source=AzureAcceleratorTelemetrySource.CUDA_DCGM,
            candidate_identity_digest=_DIGEST,
            device_name="NVIDIA A100 80GB PCIe",
            gpu_count=1,
            vram_mib=81_920,
            cuda_compute_capability="8.0",
            cuda_kernel_count=0,
            cuda_kernel_duration_ns=0,
            cuda_memory_activity_bytes=4_096,
            dcgm_sm_active_samples=(0.0,),
        )


def test_containerapp_telemetry_rejects_zero_gpu_metric() -> None:
    with pytest.raises(ValueError, match="positive Azure GPU metric"):
        AzureAcceleratorTelemetry(
            backend=AzureAcceleratorBackend.CONTAINER_APP,
            source=AzureAcceleratorTelemetrySource.AZURE_MONITOR,
            candidate_identity_digest=_DIGEST,
            revision_name="gludd-vllm-proof--0000001",
            azure_gpu_maximum_percent=0.0,
            azure_gpu_positive_sample_count=0,
        )


def test_receipt_requires_every_cleanup_proof() -> None:
    values = _receipt().payload()
    values.pop("evidence_sha256")
    values["absence_verified"] = False

    with pytest.raises(ValueError, match="cleanup evidence"):
        AzureAcceleratorProofReceipt.from_payload(values)


def test_receipt_digest_is_canonical_and_tamper_evident() -> None:
    receipt = _receipt()
    round_trip = AzureAcceleratorProofReceipt.from_payload(
        json.loads(receipt.to_json())
    )

    assert round_trip == receipt
    assert round_trip.evidence_sha256 == receipt.evidence_sha256
    assert "NVIDIA A100" in round_trip.to_json()


def test_receipt_rejects_digest_preserving_payload_tampering() -> None:
    values = _receipt().payload()
    values["input_tokens"] = 129

    with pytest.raises(ValueError, match="checksum"):
        AzureAcceleratorProofReceipt.from_payload(values)


def test_receipt_rejects_non_object_telemetry() -> None:
    values = _receipt().payload()
    values["telemetry"] = "redacted"

    with pytest.raises(ValueError, match="telemetry must be an object"):
        AzureAcceleratorProofReceipt.from_payload(values)


def test_receipt_rejects_non_numeric_azure_gpu_metric() -> None:
    values = _receipt(_containerapp_telemetry()).payload()
    telemetry = values["telemetry"]
    assert isinstance(telemetry, dict)
    telemetry["azure_gpu_maximum_percent"] = "unknown"

    with pytest.raises(ValueError, match="finite number"):
        AzureAcceleratorProofReceipt.from_payload(values)


def test_terminal_state_cannot_reenter_cleanup() -> None:
    state = AzureAcceleratorProofState.start(_RUN_ID)
    for phase in _success_phases()[1:]:
        state = state.advance(phase)

    with pytest.raises(ValueError, match="invalid Azure accelerator proof transition"):
        state.require_cleanup()


def test_mixed_provider_receipt_binds_azure_and_distinct_provider_evidence() -> None:
    azure = _receipt()
    mixed = MixedProviderProofReceipt(
        run_id=_RUN_ID,
        git_sha=_GIT_SHA,
        azure_evidence_sha256=azure.evidence_sha256,
        provider_receipts=(
            ProviderProofReference("azure", azure.evidence_sha256),
            ProviderProofReference("runpod", "e" * 64),
        ),
        selected_provider="azure",
        cleanup_verified=True,
    )

    assert mixed.provider_names == ("azure", "runpod")
    assert json.loads(mixed.to_json())["azure_evidence_sha256"] == (
        azure.evidence_sha256
    )


@pytest.mark.parametrize(
    "references",
    [
        (ProviderProofReference("azure", "a" * 64),),
        (
            ProviderProofReference("azure", "a" * 64),
            ProviderProofReference("azure", "b" * 64),
        ),
        (
            ProviderProofReference("runpod", "a" * 64),
            ProviderProofReference("local", "b" * 64),
        ),
    ],
)
def test_mixed_provider_receipt_requires_two_distinct_providers_and_azure_binding(
    references: tuple[ProviderProofReference, ...],
) -> None:
    with pytest.raises(ValueError, match="mixed-provider proof"):
        MixedProviderProofReceipt(
            run_id=_RUN_ID,
            git_sha=_GIT_SHA,
            azure_evidence_sha256="a" * 64,
            provider_receipts=references,
            selected_provider=references[0].provider,
            cleanup_verified=True,
        )
