"""Durable Azure terminal evidence to model-placement integration coverage."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from scripts import select_azure_self_improve_model as selector

from general_ludd.infra.azure_containerapp_gpu import ModelServingRequirement
from general_ludd.self_improve.azure_infrastructure_evidence import (
    AzureInfrastructurePhase,
)
from general_ludd.self_improve.azure_operational_availability import (
    AzureAvailabilityTerminal,
    build_azure_availability_scope,
    record_azure_availability_terminal,
)
from general_ludd.small_models.evidence_store import CapabilityEvidenceStore

IMAGE = "registry.example/vllm@sha256:" + ("a" * 64)


def _capacity(name: str, usable_vram_mib: int, hourly: int) -> dict[str, object]:
    return {
        "name": name,
        "workload_profile_name": f"gpu-{name}",
        "workload_profile_type": f"provider/{name}",
        "gpu_vram_mib": usable_vram_mib + 1_000,
        "usable_vram_mib": usable_vram_mib,
        "cpu_cores": 8 if name == "small" else 24,
        "memory_gib": 56 if name == "small" else 220,
        "max_replicas": 1,
        "hourly_cost_microusd_per_replica": hourly,
    }


def test_terminal_placement_evidence_routes_one_bounded_selection(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    task = tmp_path / "task.txt"
    task.write_text("Implement a bounded Python feature", encoding="utf-8")
    policy = tmp_path / "policy.json"
    policy.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "task_queries": {"coding": "code"},
                "search_limit": 4,
                "allowed_publishers": ["vendor"],
                "allowed_licenses": ["apache-2.0"],
                "required_tags": ["safetensors", "text-generation"],
                "blocked_tags": ["custom_code"],
                "minimum_context_tokens": 8_192,
                "container_image": IMAGE,
                "profile_capacities": [
                    _capacity("small", 14_745, 900_000),
                    _capacity("large", 73_728, 3_500_000),
                ],
                "max_hourly_cost_microusd": 4_000_000,
                "kv_cache_mib": 2_048,
                "runtime_overhead_mib": 3_072,
                "infrastructure_failure_threshold": 2,
                "infrastructure_failure_ttl_seconds": 300,
            }
        ),
        encoding="utf-8",
    )
    catalog = tmp_path / "catalog.json"
    catalog.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "models": [
                    {
                        "model_id": "vendor/private-coder",
                        "revision": "b" * 40,
                        "parameter_count": 3_000_000_000,
                        "context_tokens": 32_768,
                        "storage_bytes": 6_000_000_000,
                        "weight_bits": 16,
                        "license_id": "apache-2.0",
                        "tags": ["safetensors", "text-generation"],
                        "pipeline_tag": "text-generation",
                        "library_name": "transformers",
                        "downloads": 300,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    evidence_path = tmp_path / "evidence.json"
    evidence = CapabilityEvidenceStore(str(evidence_path))
    requirement = ModelServingRequirement(
        model_id="vendor/private-coder",
        revision="b" * 40,
        parameter_count=3_000_000_000,
        weight_bits=16,
        kv_cache_mib=2_048,
        runtime_overhead_mib=3_072,
    )
    for observed_at in (1_000.0, 1_001.0):
        record_azure_availability_terminal(
            evidence,
            scope=build_azure_availability_scope(
                location="westus3",
                resource_sku="provider/small",
                container_image=IMAGE,
                requirement=requirement,
            ),
            deployment_identity_digest="d" * 64,
            phase=AzureInfrastructurePhase.CANDIDATE_STARTUP,
            outcome=AzureAvailabilityTerminal.UNAVAILABLE,
            clock=lambda observed_at=observed_at: observed_at,
        )
    record_azure_availability_terminal(
        evidence,
        scope=build_azure_availability_scope(
            location="westus3",
            resource_sku="provider/large",
            container_image=IMAGE,
            requirement=requirement,
        ),
        deployment_identity_digest="e" * 64,
        phase=AzureInfrastructurePhase.CANDIDATE_STARTUP,
        outcome=AzureAvailabilityTerminal.AVAILABLE,
        clock=lambda: 1_002.0,
    )
    output = tmp_path / "selection.json"

    assert selector.main(
        [
            "--task-file",
            str(task),
            "--policy-file",
            str(policy),
            "--evidence-file",
            str(evidence_path),
            "--location",
            "westus3",
            "--catalog-file",
            str(catalog),
            "--registry-cache-dir",
            str(tmp_path / "cache"),
            "--output",
            str(output),
        ],
        now_epoch=1_003.0,
    ) == 0

    selected = json.loads(output.read_text(encoding="utf-8"))
    assert selected["workload_profile_type"] == "provider/large"
    assert selected["selection_reason"] == "operational_failover"
    trace = capsys.readouterr().out
    assert "vendor/private-coder" not in trace
    assert "registry.example" not in trace
    assert "SELF_IMPROVE_AZURE_AVAILABILITY_EVIDENCE_LOADED" in trace
