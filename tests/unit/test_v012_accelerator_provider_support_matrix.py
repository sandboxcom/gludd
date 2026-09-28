"""Structural contract for the v0.1.2 accelerator/provider support matrix."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

ROOT = Path(__file__).parent.parent.parent
MATRIX = ROOT / "docs" / "releases" / "v0.1.2-accelerator-provider-support-matrix.md"
MATRIX_PATTERN = re.compile(
    r"<!-- v012-accelerator-provider-matrix:start -->\s*"
    r"```json\s*(.*?)\s*```\s*"
    r"<!-- v012-accelerator-provider-matrix:end -->",
    re.DOTALL,
)

EXPECTED_TRACKS = {
    "azure_gpu_vm": (
        "azure",
        "gpu",
        "subscription_quota_capacity_conditional",
        "DESIGN_ONLY",
    ),
    "azure_container_apps_gpu": (
        "azure",
        "gpu",
        "region_quota_capacity_conditional",
        "OUT_OF_SCOPE",
    ),
    "azure_npu_vm": (
        "azure",
        "npu",
        "no_public_vm_sku_evidenced",
        "UNADMITTED",
    ),
    "azure_intel_amx_vm": (
        "azure",
        "cpu_matrix_extension",
        "vm_cpu_feature_conditional",
        "UNADMITTED",
    ),
    "azure_fpga_np_vm": (
        "azure",
        "fpga",
        "retiring_conditional",
        "UNADMITTED",
    ),
    "azure_maia_managed": (
        "azure",
        "ai_asic",
        "managed_service_or_sdk_preview_only",
        "UNADMITTED",
    ),
    "google_cloud_tpu_vm": (
        "gcp",
        "tpu",
        "yes_quota_zone_capacity_conditional",
        "UNADMITTED",
    ),
    "aws_ec2_accelerated": (
        "aws",
        "accelerated_compute",
        "yes_quota_region_capacity_conditional",
        "SEPARATE_PROVIDER_TRACK",
    ),
}

IMPLEMENTATION_CONTRACTS = {
    "device_discovery",
    "multi_device_assignment",
    "model_right_sizing",
    "vllm_usage",
    "ollama_usage",
    "remote_workload_consumption",
    "zdd_rollback",
    "evidence_status",
}


def _text() -> str:
    return MATRIX.read_text(encoding="utf-8")


def _matrix() -> dict[str, Any]:
    match = MATRIX_PATTERN.search(_text())
    assert match is not None, "matrix must contain its parseable JSON authority"
    value = json.loads(match.group(1))
    assert isinstance(value, dict)
    return value


def test_matrix_pins_base_release_and_fail_closed_evidence_status() -> None:
    matrix = _matrix()
    assert matrix["schema"] == "gludd.v012.accelerator_provider_support_matrix.v1"
    assert matrix["base_commit"] == "5566d6dcff58c4f6391c9e3b8e3320ed55c73df9"
    assert matrix["reviewed_at"] == "2026-09-28"
    assert matrix["release"] == "v0.1.2"
    assert matrix["artifact_status"] == "IMPLEMENTATION_READY_DESIGN"
    assert matrix["default_track_status"] == "UNADMITTED"
    assert matrix["structural_test"] == (
        "tests/unit/test_v012_accelerator_provider_support_matrix.py"
    )
    assert matrix["evidence_status"] == {
        "official_source_review": "DOCUMENTED",
        "live_hardware": "NOT_ESTABLISHED",
        "provider_allocation": "NOT_ESTABLISHED",
        "multi_device_runner": "NOT_ESTABLISHED",
        "remote_gateway": "NOT_ESTABLISHED",
        "zdd_rollback": "NOT_ESTABLISHED",
        "release_admission": "UNADMITTED",
    }


def test_provider_tracks_have_exact_ownership_access_and_release_status() -> None:
    tracks = _matrix()["tracks"]
    assert set(tracks) == set(EXPECTED_TRACKS)
    for name, expected in EXPECTED_TRACKS.items():
        track = tracks[name]
        actual = (
            track["provider"],
            track["accelerator_kind"],
            track["customer_access"],
            track["gludd_status"],
        )
        assert actual == expected, name
        assert track["surface"]
        assert track["discovery_authority"]
        assert track["admission_blocker"]


def test_matrix_answers_npu_maia_tpu_and_azure_versus_ec2_questions() -> None:
    text = " ".join(_text().split())
    for required in (
        "Azure Virtual Machines are Microsoft Azure resources; Amazon EC2",
        "There is no product named Azure EC2",
        "no customer-provisionable NPU VM family",
        "catalog inference, not a claim that Microsoft owns no NPU hardware",
        "Maia 200 exists",
        "no tenant-selectable Maia VM SKU",
        "Yes—Google Cloud TPU is actually provisionable",
        "Google Cloud project, supported zone, quota, and available capacity",
        "Cloud TPU is not an Azure offering",
    ):
        assert required in text


def test_each_implementation_contract_is_actionable_and_fail_closed() -> None:
    contracts = _matrix()["implementation_contracts"]
    assert set(contracts) == IMPLEMENTATION_CONTRACTS
    for name, contract in contracts.items():
        assert set(contract) == {
            "required_observations",
            "fail_closed_on",
            "minimum_tests",
        }, name
        for field in contract.values():
            assert isinstance(field, list), name
            assert len(field) >= 2, name
            assert all(isinstance(value, str) and value for value in field), name

    text = _text()
    for heading in (
        "## Device discovery contract",
        "## Multi-device assignment contract",
        "## Model right-sizing contract",
        "## vLLM and Ollama execution contract",
        "## Remote workload consumption contract",
        "## ZDD and rollback contract",
        "## Evidence and admission status",
    ):
        assert heading in text


def test_official_sources_and_long_lived_practitioner_reports_are_pinned() -> None:
    text = _text()
    for source in (
        "https://learn.microsoft.com/en-us/azure/virtual-machines/sizes/overview",
        "https://learn.microsoft.com/en-us/azure/container-apps/gpu-serverless-overview",
        "https://learn.microsoft.com/en-us/azure/virtual-machines/sizes/lifecycle/retirements-and-capacity-restrictions",
        "https://learn.microsoft.com/en-us/azure/virtual-machines/accelerator-technologies/intel-amx-overview",
        "https://blogs.microsoft.com/blog/2026/01/26/maia-200-the-ai-accelerator-built-for-inference/",
        "https://docs.cloud.google.com/tpu/docs/create-tpu-vm",
        "https://docs.cloud.google.com/tpu/docs/quota",
        "https://docs.cloud.google.com/tpu/docs/regions-zones",
        "https://aws.amazon.com/ec2/instance-types/accelerated-computing/",
        "https://docs.vllm.ai/en/latest/serving/parallelism_scaling/",
        "https://docs.ollama.com/gpu",
        "https://docs.ollama.com/faq",
    ):
        assert source in text

    reports = {
        "https://github.com/Azure/WALinuxAgent/issues/1938": "2020-07-08",
        "https://github.com/vllm-project/vllm/issues/1437": "2023-10-20",
        "https://github.com/ollama/ollama/issues/1288": "2023-11-27",
        "https://learn.microsoft.com/en-us/answers/questions/811075/": "2022-04-13",
    }
    for source, opened in reports.items():
        assert source in text
        assert opened in text


def test_matrix_does_not_promote_research_or_inherit_cross_provider_proof() -> None:
    lowered = _text().lower()
    for unsupported in (
        "azure ec2 instance",
        "azure tpu vm",
        "maia vm is generally available",
        "npu vm is generally available",
        "google tpu is not provisionable",
        "documentation proves live hardware",
        "quota proves capacity",
        "single gpu proves multi gpu",
        "local request proves remote consumption",
        '"live_hardware": "established"',
        '"release_admission": "admitted"',
    ):
        assert unsupported not in lowered
