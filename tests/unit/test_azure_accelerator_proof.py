"""Behavioral contract for Azure accelerator live-proof receipts."""

from __future__ import annotations

import json
from dataclasses import replace

import pytest

from general_ludd.infra.azure_accelerator_proof import (
    AzureAcceleratorBackend,
    AzureAcceleratorClaimPolicy,
    AzureAcceleratorProofPhase,
    AzureAcceleratorProofReceipt,
    AzureAcceleratorProofState,
    AzureAcceleratorTelemetry,
    AzureAcceleratorTelemetrySource,
    AzureResourceAbsenceEvidence,
    MixedProviderProofReceipt,
    ProviderProofReference,
)

_RUN_ID = "v011-azure-proof-001"
_GIT_SHA = "a" * 40
_DIGEST = "b" * 64
_SUBSCRIPTION_DIGEST = "1" * 64
_RESOURCE_DIGEST = "2" * 64
_DEVICE_DIGEST = "3" * 64
_STARTED_AT = 1_800_000_000
_TELEMETRY_AT = _STARTED_AT + 10
_ABSENCE_AT = _STARTED_AT + 20
_COMPLETED_AT = _STARTED_AT + 30
_EXPIRES_AT = _STARTED_AT + 600


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
        run_id=_RUN_ID,
        git_sha=_GIT_SHA,
        candidate_identity_digest=_DIGEST,
        subscription_id_digest=_SUBSCRIPTION_DIGEST,
        resource_id_digest=_RESOURCE_DIGEST,
        observed_at_epoch_s=_TELEMETRY_AT,
        device_name="NVIDIA A100 80GB PCIe",
        gpu_count=1,
        vram_mib=81_920,
        cuda_compute_capability="8.0",
        cuda_kernel_count=3,
        cuda_kernel_duration_ns=42_000,
        cuda_memory_activity_bytes=4_096,
        dcgm_sm_active_samples=(0.25, 0.75),
        device_identity_digests=(_DEVICE_DIGEST,),
        cuda_device_identity_digests=(_DEVICE_DIGEST,),
        dcgm_device_identity_digests=(_DEVICE_DIGEST,),
    )


def _containerapp_telemetry() -> AzureAcceleratorTelemetry:
    return AzureAcceleratorTelemetry(
        backend=AzureAcceleratorBackend.CONTAINER_APP,
        source=AzureAcceleratorTelemetrySource.AZURE_MONITOR,
        run_id=_RUN_ID,
        git_sha=_GIT_SHA,
        candidate_identity_digest=_DIGEST,
        subscription_id_digest=_SUBSCRIPTION_DIGEST,
        resource_id_digest=_RESOURCE_DIGEST,
        observed_at_epoch_s=_TELEMETRY_AT,
        revision_name="gludd-vllm-proof--0000001",
        azure_gpu_maximum_percent=42.0,
        azure_gpu_positive_sample_count=2,
    )


def _absence(
    *,
    resource_id_digest: str = _RESOURCE_DIGEST,
    checked_at_epoch_s: int = _ABSENCE_AT,
    status_code: int = 404,
) -> AzureResourceAbsenceEvidence:
    return AzureResourceAbsenceEvidence(
        run_id=_RUN_ID,
        git_sha=_GIT_SHA,
        subscription_id_digest=_SUBSCRIPTION_DIGEST,
        resource_id_digest=resource_id_digest,
        checked_at_epoch_s=checked_at_epoch_s,
        status_code=status_code,
        lookup_attempts=2,
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
        absence_evidence=_absence(),
        started_at_epoch_s=_STARTED_AT,
        completed_at_epoch_s=_COMPLETED_AT,
        lease_expires_at_epoch_s=_EXPIRES_AT,
        spend_expires_at_epoch_s=_EXPIRES_AT,
        phases=_success_phases(),
        admission_closed=True,
        profile_removed=True,
        compute_released=True,
        resources_destroyed=True,
        absence_verified=True,
        lease_closed=True,
        spend_closed=True,
        secret_scan_passed=True,
        secret_scan_sha256="4" * 64,
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
        expected_candidate_identity_digest=_DIGEST,
        expected_subscription_id_digest=_SUBSCRIPTION_DIGEST,
        expected_resource_id_digest=_RESOURCE_DIGEST,
        expected_gpu_count=1,
        expected_revision_name=None,
        maximum_proof_age_s=86_400,
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

    receipt.validate_against(
        _a100_policy(AzureAcceleratorBackend.VM),
        verification_time_epoch_s=_COMPLETED_AT + 60,
    )

    assert len(receipt.evidence_sha256) == 64
    assert json.loads(receipt.to_json())["telemetry"]["cuda_kernel_count"] == 3


def test_vmss_uses_the_same_device_attribution_contract() -> None:
    receipt = _receipt(_vm_telemetry(AzureAcceleratorBackend.VMSS))

    receipt.validate_against(
        _a100_policy(AzureAcceleratorBackend.VMSS),
        verification_time_epoch_s=_COMPLETED_AT + 60,
    )


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
        expected_candidate_identity_digest=_DIGEST,
        expected_subscription_id_digest=_SUBSCRIPTION_DIGEST,
        expected_resource_id_digest=_RESOURCE_DIGEST,
        expected_gpu_count=0,
        expected_revision_name="gludd-vllm-proof--0000001",
        maximum_proof_age_s=86_400,
    )

    receipt.validate_against(
        policy,
        verification_time_epoch_s=_COMPLETED_AT + 60,
    )


@pytest.mark.parametrize(
    ("output_tokens", "observed_sku", "message"),
    [
        (2, "Standard_NC24ads_A100_v4", "exactly one output token"),
        (1, "Standard_NC24ads_A100_v5", "observed SKU"),
    ],
)
def test_receipt_rejects_wrong_token_count_or_sku(
    output_tokens: int,
    observed_sku: str,
    message: str,
) -> None:
    receipt = _receipt(output_tokens=output_tokens, observed_sku=observed_sku)

    with pytest.raises(ValueError, match=message):
        receipt.validate_against(
            _a100_policy(AzureAcceleratorBackend.VM),
            verification_time_epoch_s=_COMPLETED_AT + 60,
        )


def test_vm_telemetry_rejects_zero_kernel_or_stale_dcgm_window() -> None:
    with pytest.raises(ValueError, match="positive CUDA kernel"):
        AzureAcceleratorTelemetry(
            backend=AzureAcceleratorBackend.VM,
            source=AzureAcceleratorTelemetrySource.CUDA_DCGM,
            run_id=_RUN_ID,
            git_sha=_GIT_SHA,
            candidate_identity_digest=_DIGEST,
            subscription_id_digest=_SUBSCRIPTION_DIGEST,
            resource_id_digest=_RESOURCE_DIGEST,
            observed_at_epoch_s=_TELEMETRY_AT,
            device_name="NVIDIA A100 80GB PCIe",
            gpu_count=1,
            vram_mib=81_920,
            cuda_compute_capability="8.0",
            cuda_kernel_count=0,
            cuda_kernel_duration_ns=0,
            cuda_memory_activity_bytes=4_096,
            dcgm_sm_active_samples=(0.0,),
            device_identity_digests=(_DEVICE_DIGEST,),
            cuda_device_identity_digests=(_DEVICE_DIGEST,),
            dcgm_device_identity_digests=(_DEVICE_DIGEST,),
        )


def test_containerapp_telemetry_rejects_zero_gpu_metric() -> None:
    with pytest.raises(ValueError, match="positive Azure GPU metric"):
        AzureAcceleratorTelemetry(
            backend=AzureAcceleratorBackend.CONTAINER_APP,
            source=AzureAcceleratorTelemetrySource.AZURE_MONITOR,
            run_id=_RUN_ID,
            git_sha=_GIT_SHA,
            candidate_identity_digest=_DIGEST,
            subscription_id_digest=_SUBSCRIPTION_DIGEST,
            resource_id_digest=_RESOURCE_DIGEST,
            observed_at_epoch_s=_TELEMETRY_AT,
            revision_name="gludd-vllm-proof--0000001",
            azure_gpu_maximum_percent=0.0,
            azure_gpu_positive_sample_count=0,
        )


def test_receipt_requires_every_cleanup_proof() -> None:
    with pytest.raises(ValueError, match="cleanup evidence"):
        replace(_receipt(), absence_verified=False)


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
            ProviderProofReference("azure", azure.evidence_sha256, _RUN_ID, _GIT_SHA),
            ProviderProofReference("runpod", "e" * 64, _RUN_ID, _GIT_SHA),
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
        (ProviderProofReference("azure", "a" * 64, _RUN_ID, _GIT_SHA),),
        (
            ProviderProofReference("azure", "a" * 64, _RUN_ID, _GIT_SHA),
            ProviderProofReference("azure", "b" * 64, _RUN_ID, _GIT_SHA),
        ),
        (
            ProviderProofReference("runpod", "a" * 64, _RUN_ID, _GIT_SHA),
            ProviderProofReference("local", "b" * 64, _RUN_ID, _GIT_SHA),
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


@pytest.mark.parametrize("backend", [AzureAcceleratorBackend.VM, AzureAcceleratorBackend.VMSS])
def test_receipt_rejects_swapped_vm_or_vmss_telemetry(
    backend: AzureAcceleratorBackend,
) -> None:
    swapped = replace(_vm_telemetry(backend), run_id="different-proof")

    with pytest.raises(ValueError, match="telemetry run"):
        _receipt(swapped)


def test_receipt_rejects_forged_candidate_identity() -> None:
    receipt = _receipt(replace(_vm_telemetry(), candidate_identity_digest="f" * 64))

    with pytest.raises(ValueError, match="candidate identity"):
        receipt.validate_against(
            _a100_policy(AzureAcceleratorBackend.VM),
            verification_time_epoch_s=_COMPLETED_AT + 60,
        )


def test_containerapp_receipt_rejects_swapped_revision_telemetry() -> None:
    receipt = _receipt(replace(_containerapp_telemetry(), revision_name="other--0000001"))
    policy = replace(
        _a100_policy(AzureAcceleratorBackend.CONTAINER_APP),
        require_device_identity=False,
        expected_device_name=None,
        minimum_gpu_count=0,
        minimum_vram_mib=0,
        minimum_cuda_compute_capability=None,
        expected_gpu_count=0,
        expected_revision_name="gludd-vllm-proof--0000001",
    )

    with pytest.raises(ValueError, match="revision"):
        receipt.validate_against(
            policy,
            verification_time_epoch_s=_COMPLETED_AT + 60,
        )


@pytest.mark.parametrize(
    ("gpu_count", "cuda_identities", "dcgm_identities"),
    [
        (2, (_DEVICE_DIGEST,), (_DEVICE_DIGEST,)),
        (1, ("5" * 64,), (_DEVICE_DIGEST,)),
        (1, (_DEVICE_DIGEST,), ("6" * 64,)),
    ],
)
def test_vm_telemetry_rejects_multi_device_topology_mismatch(
    gpu_count: int,
    cuda_identities: tuple[str, ...],
    dcgm_identities: tuple[str, ...],
) -> None:
    with pytest.raises(ValueError, match="device topology"):
        replace(
            _vm_telemetry(),
            gpu_count=gpu_count,
            cuda_device_identity_digests=cuda_identities,
            dcgm_device_identity_digests=dcgm_identities,
        )


def test_receipt_rejects_stale_cleanup_state() -> None:
    receipt = _receipt()

    with pytest.raises(ValueError, match="stale"):
        receipt.validate_against(
            _a100_policy(AzureAcceleratorBackend.VM),
            verification_time_epoch_s=_COMPLETED_AT + 86_401,
        )

    with pytest.raises(ValueError, match="evidence ordering"):
        replace(
            receipt,
            absence_evidence=_absence(checked_at_epoch_s=_TELEMETRY_AT - 1),
        )


def test_receipt_requires_exact_absent_resource_verification() -> None:
    with pytest.raises(ValueError, match="404"):
        _absence(status_code=200)

    with pytest.raises(ValueError, match="absence resource"):
        replace(
            _receipt(),
            absence_evidence=_absence(resource_id_digest="7" * 64),
        )


def test_receipt_rejects_expired_lease_or_spend_authority() -> None:
    with pytest.raises(ValueError, match="expired before proof completion"):
        replace(_receipt(), lease_expires_at_epoch_s=_COMPLETED_AT - 1)
    with pytest.raises(ValueError, match="expired before proof completion"):
        replace(_receipt(), spend_expires_at_epoch_s=_COMPLETED_AT - 1)


def test_mixed_provider_receipt_rejects_cross_run_substitution() -> None:
    azure = _receipt()
    with pytest.raises(ValueError, match="same run and Git SHA"):
        MixedProviderProofReceipt(
            run_id=_RUN_ID,
            git_sha=_GIT_SHA,
            azure_evidence_sha256=azure.evidence_sha256,
            provider_receipts=(
                ProviderProofReference("azure", azure.evidence_sha256, _RUN_ID, _GIT_SHA),
                ProviderProofReference("runpod", "e" * 64, "other-run", _GIT_SHA),
            ),
            selected_provider="azure",
            cleanup_verified=True,
        )


def test_mixed_provider_parser_rejects_tamper_and_missing_digest() -> None:
    azure = _receipt()
    mixed = MixedProviderProofReceipt(
        run_id=_RUN_ID,
        git_sha=_GIT_SHA,
        azure_evidence_sha256=azure.evidence_sha256,
        provider_receipts=(
            ProviderProofReference("azure", azure.evidence_sha256, _RUN_ID, _GIT_SHA),
            ProviderProofReference("runpod", "e" * 64, _RUN_ID, _GIT_SHA),
        ),
        selected_provider="azure",
        cleanup_verified=True,
    )
    payload = mixed.payload()
    payload["selected_provider"] = "runpod"
    with pytest.raises(ValueError, match="checksum"):
        MixedProviderProofReceipt.from_payload(payload)

    payload = mixed.payload()
    payload.pop("evidence_sha256")
    with pytest.raises(ValueError, match="missing required receipt field"):
        MixedProviderProofReceipt.from_payload(payload)


def test_receipt_parser_rejects_secret_material_and_missing_digest() -> None:
    payload = _receipt().payload()
    payload["client_secret"] = "do-not-copy-this-secret"
    with pytest.raises(ValueError, match="secret"):
        AzureAcceleratorProofReceipt.from_payload(payload)

    payload = _receipt().payload()
    payload.pop("evidence_sha256")
    with pytest.raises(ValueError, match="missing required receipt field"):
        AzureAcceleratorProofReceipt.from_payload(payload)


def test_telemetry_rejects_secret_bearing_device_name() -> None:
    with pytest.raises(ValueError, match="secret"):
        replace(_vm_telemetry(), device_name="Bearer eyJhbGciOiJub25lIn0.payload.sig")


def test_vm_telemetry_rejects_incomplete_or_cross_surface_evidence() -> None:
    telemetry = _vm_telemetry()
    with pytest.raises(ValueError, match="VM or VMSS"):
        replace(telemetry, backend=AzureAcceleratorBackend.CONTAINER_APP)
    with pytest.raises(ValueError, match="device name"):
        replace(telemetry, device_name="")
    with pytest.raises(ValueError, match="compute capability"):
        replace(telemetry, cuda_compute_capability=None)
    with pytest.raises(ValueError, match="DCGM window"):
        replace(telemetry, dcgm_sm_active_samples=())
    with pytest.raises(ValueError, match="DCGM window"):
        replace(telemetry, dcgm_sm_active_samples=(float("nan"),))
    with pytest.raises(ValueError, match="Container App evidence"):
        replace(telemetry, revision_name="other--0000001")


def test_containerapp_telemetry_rejects_vm_or_wrong_backend_evidence() -> None:
    telemetry = _containerapp_telemetry()
    with pytest.raises(ValueError, match="Container App backend"):
        replace(telemetry, backend=AzureAcceleratorBackend.VM)
    with pytest.raises(ValueError, match="VM device evidence"):
        replace(telemetry, device_name="NVIDIA A100")


def test_policy_rejects_ambiguous_or_impossible_topology() -> None:
    policy = _a100_policy(AzureAcceleratorBackend.VM)
    with pytest.raises(ValueError, match="distinct"):
        replace(
            policy,
            allowed_backends=(
                AzureAcceleratorBackend.VM,
                AzureAcceleratorBackend.VM,
            ),
        )
    with pytest.raises(ValueError, match="valid regular expression"):
        replace(policy, expected_device_name="[")
    with pytest.raises(ValueError, match="below minimum"):
        replace(policy, minimum_gpu_count=2, expected_gpu_count=1)


def test_receipt_rejects_swapped_cleanup_identity_planes() -> None:
    receipt = _receipt()
    with pytest.raises(ValueError, match="telemetry Git SHA"):
        replace(receipt, telemetry=replace(receipt.telemetry, git_sha="e" * 40))
    with pytest.raises(ValueError, match="absence run"):
        replace(
            receipt,
            absence_evidence=replace(receipt.absence_evidence, run_id="other-run"),
        )
    with pytest.raises(ValueError, match="absence Git SHA"):
        replace(
            receipt,
            absence_evidence=replace(receipt.absence_evidence, git_sha="e" * 40),
        )
    with pytest.raises(ValueError, match="absence subscription"):
        replace(
            receipt,
            absence_evidence=replace(
                receipt.absence_evidence,
                subscription_id_digest="8" * 64,
            ),
        )
    with pytest.raises(ValueError, match="successful phase history"):
        replace(receipt, phases=_success_phases()[:-1])


def test_policy_validation_rejects_trusted_identity_and_time_substitution() -> None:
    receipt = _receipt()
    policy = _a100_policy(AzureAcceleratorBackend.VM)
    with pytest.raises(ValueError, match="in the future"):
        receipt.validate_against(
            policy,
            verification_time_epoch_s=_COMPLETED_AT - 1,
        )
    with pytest.raises(ValueError, match="receipt claim"):
        receipt.validate_against(
            replace(policy, claim="azure-other-claim"),
            verification_time_epoch_s=_COMPLETED_AT + 1,
        )
    with pytest.raises(ValueError, match="requested SKU"):
        receipt.validate_against(
            replace(policy, sku="Standard_Other_GPU"),
            verification_time_epoch_s=_COMPLETED_AT + 1,
        )
    with pytest.raises(ValueError, match="not allowed"):
        receipt.validate_against(
            _a100_policy(AzureAcceleratorBackend.VMSS),
            verification_time_epoch_s=_COMPLETED_AT + 1,
        )
    with pytest.raises(ValueError, match="subscription identity"):
        receipt.validate_against(
            replace(policy, expected_subscription_id_digest="8" * 64),
            verification_time_epoch_s=_COMPLETED_AT + 1,
        )
    with pytest.raises(ValueError, match="resource identity"):
        receipt.validate_against(
            replace(policy, expected_resource_id_digest="9" * 64),
            verification_time_epoch_s=_COMPLETED_AT + 1,
        )
    with pytest.raises(ValueError, match="device topology"):
        receipt.validate_against(
            replace(policy, expected_gpu_count=2),
            verification_time_epoch_s=_COMPLETED_AT + 1,
        )


def test_device_policy_rejects_wrong_name_vram_and_capability() -> None:
    receipt = _receipt()
    policy = _a100_policy(AzureAcceleratorBackend.VM)
    with pytest.raises(ValueError, match="identity evidence is missing"):
        receipt.validate_against(
            replace(policy, expected_device_name=None),
            verification_time_epoch_s=_COMPLETED_AT + 1,
        )
    with pytest.raises(ValueError, match="device name"):
        receipt.validate_against(
            replace(policy, expected_device_name=r"^NVIDIA H100"),
            verification_time_epoch_s=_COMPLETED_AT + 1,
        )
    with pytest.raises(ValueError, match="VRAM"):
        receipt.validate_against(
            replace(policy, minimum_vram_mib=90_000),
            verification_time_epoch_s=_COMPLETED_AT + 1,
        )
    with pytest.raises(ValueError, match="compute capability"):
        receipt.validate_against(
            replace(policy, minimum_cuda_compute_capability="9.0"),
            verification_time_epoch_s=_COMPLETED_AT + 1,
        )


def test_nested_receipt_parsers_fail_closed_and_round_trip() -> None:
    payload = _receipt().payload()
    payload["absence_evidence"] = "missing"
    with pytest.raises(ValueError, match="absence_evidence must be an object"):
        AzureAcceleratorProofReceipt.from_payload(payload)

    azure = _receipt()
    mixed = MixedProviderProofReceipt(
        run_id=_RUN_ID,
        git_sha=_GIT_SHA,
        azure_evidence_sha256=azure.evidence_sha256,
        provider_receipts=(
            ProviderProofReference("azure", azure.evidence_sha256, _RUN_ID, _GIT_SHA),
            ProviderProofReference("runpod", "e" * 64, _RUN_ID, _GIT_SHA),
        ),
        selected_provider="azure",
        cleanup_verified=True,
    )
    assert MixedProviderProofReceipt.from_payload(mixed.payload()) == mixed

    malformed = mixed.payload()
    malformed["provider_receipts"] = ["not-an-object", "also-not-an-object"]
    with pytest.raises(ValueError, match="must contain objects"):
        MixedProviderProofReceipt.from_payload(malformed)


def test_untrusted_parsers_do_not_coerce_numeric_identities_to_strings() -> None:
    telemetry_payload = _vm_telemetry().payload()
    telemetry_payload["candidate_identity_digest"] = int("1" * 64)
    with pytest.raises(ValueError, match="candidate_identity_digest"):
        AzureAcceleratorTelemetry.from_payload(telemetry_payload)

    provider_payload = ProviderProofReference(
        "runpod",
        "e" * 64,
        _RUN_ID,
        _GIT_SHA,
    ).payload()
    provider_payload["git_sha"] = int("1" * 40)
    with pytest.raises(ValueError, match="git_sha"):
        ProviderProofReference.from_payload(provider_payload)
