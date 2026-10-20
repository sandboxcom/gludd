"""Structural contract for the v0.1.2 accelerator portability gap ledger."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

ROOT = Path(__file__).parent.parent.parent
LEDGER = ROOT / "docs" / "releases" / "v0.1.2-accelerator-portability-gap-ledger.md"
LEDGER_PATTERN = re.compile(
    r"<!-- v012-accelerator-portability-ledger:start -->\s*"
    r"```json\s*(.*?)\s*```\s*"
    r"<!-- v012-accelerator-portability-ledger:end -->",
    re.DOTALL,
)

TOPICS = {
    "vllm_multi_gpu": "### 1. vLLM multi-GPU execution",
    "ollama_gpu_selection": "### 2. Ollama GPU selection",
    "azure_container_apps_gpu": "### 3. Azure Container Apps GPU limits",
    "azure_gpu_vm_topology": "### 4. Azure GPU VM attachment and topology",
    "tpu_runtime_portability": "### 5. TPU and runtime portability",
}


def _text() -> str:
    return LEDGER.read_text(encoding="utf-8")


def _inventory() -> dict[str, Any]:
    match = LEDGER_PATTERN.search(_text())
    assert match is not None, "ledger must contain its parseable JSON inventory"
    value = json.loads(match.group(1))
    assert isinstance(value, dict)
    return value


def test_ledger_pins_all_five_open_gaps_and_global_proof_boundary() -> None:
    inventory = _inventory()
    assert inventory == {
        "schema_version": 1,
        "reviewed_at": "2026-09-28",
        "base_commit": "19ec541d1d94b69a5acdc1e718e1caa4a2b05f9e",
        "ledger_status": "gap_inventory_only",
        "full_gate": "pending",
        "live_accelerator_proof": "not_established",
        "multi_device_runner_proof": "not_established",
        "tpu_runtime_proof": "not_established",
        "topics": inventory["topics"],
    }
    assert set(inventory["topics"]) == set(TOPICS)
    for topic in inventory["topics"].values():
        assert set(topic) == {
            "status",
            "default_state",
            "v011_capability",
            "missing_v012_requirement",
            "proof_required",
            "residual_boundary",
            "proof_status",
        }
        assert topic["status"] == "gap_open"
        assert topic["default_state"] == "unadmitted"
        assert topic["proof_status"] == "not_established"
        for field in (
            "v011_capability",
            "missing_v012_requirement",
            "proof_required",
            "residual_boundary",
        ):
            assert isinstance(topic[field], str)
            assert topic[field]


def test_each_topic_separates_facts_inference_mapping_and_proof() -> None:
    text = _text()
    positions = [text.index(heading) for heading in TOPICS.values()]
    for index, (topic, heading) in enumerate(TOPICS.items()):
        start = positions[index]
        end = positions[index + 1] if index + 1 < len(positions) else len(text)
        section = text[start:end]
        assert heading in section, topic
        for label in (
            "**Dated upstream facts.**",
            "**Gludd inference.**",
            "**v0.1.1 mapping.**",
            "**Missing v0.1.2 requirement.**",
            "**Test/proof required.**",
            "**Residual/provider boundary.**",
        ):
            assert label in section, f"{topic} is missing {label}"
        assert "https://" in section, f"{topic} needs a direct upstream source"


def test_authoritative_sources_and_practitioner_reports_are_directly_linked() -> None:
    text = _text()
    for source in (
        "https://docs.vllm.ai/en/latest/serving/parallelism_scaling/",
        "https://docs.vllm.ai/en/latest/cli/serve/",
        "https://github.com/vllm-project/vllm/issues/33041",
        "https://docs.ollama.com/gpu",
        "https://github.com/ollama/ollama/issues/9722",
        "https://learn.microsoft.com/en-us/azure/container-apps/gpu-serverless-overview",
        "https://learn.microsoft.com/en-us/azure/container-apps/quotas",
        "https://github.com/microsoft/azure-container-apps/issues/1511",
        "https://learn.microsoft.com/en-us/azure/virtual-machines/sizes/gpu-accelerated/nd-family",
        "https://learn.microsoft.com/en-us/azure/virtual-machines/sizes/resize-vm",
        "https://github.com/Azure/WALinuxAgent/issues/1938",
        "https://docs.vllm.ai/projects/tpu/en/latest/features/",
        "https://docs.vllm.ai/projects/tpu/en/latest/getting_started/installation/",
        "https://docs.pytorch.org/xla/master/runtime.html",
        "https://docs.cloud.google.com/tpu/docs/runtimes",
        "https://docs.cloud.google.com/tpu/docs/system-architecture-tpu-vm",
        "https://github.com/vllm-project/vllm/issues/20869",
    ):
        assert source in text

    for local_source in (
        "(v0.1.1.md)",
        "(../features/ACCELERATOR_DISCOVERY.md)",
        "(../features/ACCELERATOR_TOPOLOGY_CONTRACT.md)",
        "(../features/AZURE_GPU_VM_STRATEGY.md)",
        "(../design/AZURE_ACCELERATOR_LIVE_PROOF.md)",
    ):
        assert local_source in text


def test_ledger_retains_security_zdd_rollback_and_provider_boundaries() -> None:
    text = _text()
    for heading in (
        "## Scope and evidence rules",
        "## v0.1.1 capability baseline",
        "## Gap ledger",
        "## Cross-provider requirements",
        "## Proof matrix",
        "## Security and privacy",
        "## ZDD and rollback",
        "## Residual provider boundaries",
        "## Sources and dated practitioner reports",
        "## Exit criteria",
    ):
        assert heading in text

    lowered = text.lower()
    for boundary in (
        "no live accelerator proof is established",
        "no multi-device runner proof is established",
        "no tpu runtime proof is established",
        "planning and hermetic fixtures are not live execution evidence",
        "disabled by default",
        "fail closed",
        "replacement generation",
        "operator-owned",
        "provider-owned",
        "full gate remains pending",
        "85% aggregate",
        "75% per file",
    ):
        assert boundary in lowered


def test_ledger_does_not_make_unsupported_live_or_multi_device_claims() -> None:
    lowered = _text().lower()
    for unsupported_claim in (
        "live accelerator support is established",
        "multi-gpu support is established",
        "multi-device support is established",
        "tpu support is established",
        "production-ready accelerator",
        "generally available through gludd",
        "all accelerators are supported",
        "gpu attachment is supported",
        "container apps supports multi-gpu replicas",
        "container apps supports fractional gpu replicas",
        "ollama device selection is deterministic",
        "vllm multi-gpu is proven",
        "tpu runtime is portable",
        '"status": "complete"',
        '"proof_status": "complete"',
    ):
        assert unsupported_claim not in lowered
