"""End-to-end CLI tests for task-driven Azure model selection."""

from __future__ import annotations

import json
import stat
from pathlib import Path

import pytest
from scripts import select_azure_self_improve_model as subject

from general_ludd.self_improve.azure_infrastructure_evidence import (
    AzureInfrastructurePhase,
    record_azure_infrastructure_failure,
)
from general_ludd.self_improve.model_candidates import BackendFailure
from general_ludd.small_models.evidence_store import CapabilityEvidenceStore

IMAGE = "registry.example/vllm@sha256:" + "a" * 64


def _policy(path: Path) -> Path:
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "task_queries": {"coding": "code"},
                "search_limit": 20,
                "allowed_publishers": ["vendor"],
                "allowed_licenses": ["apache-2.0"],
                "required_tags": ["safetensors", "text-generation"],
                "blocked_tags": ["custom_code"],
                "minimum_context_tokens": 8192,
                "container_image": IMAGE,
                "profile_capacities": [
                    {
                        "name": "inventory-small",
                        "workload_profile_name": "gpu-inventory-small",
                        "workload_profile_type": "provider/accelerator-small",
                        "gpu_vram_mib": 16_384,
                        "usable_vram_mib": 14_745,
                        "cpu_cores": 8,
                        "memory_gib": 56,
                        "max_replicas": 1,
                        "hourly_cost_microusd_per_replica": 900_000,
                    },
                    {
                        "name": "inventory-large",
                        "workload_profile_name": "gpu-inventory-large",
                        "workload_profile_type": "provider/accelerator-large",
                        "gpu_vram_mib": 81_920,
                        "usable_vram_mib": 73_728,
                        "cpu_cores": 24,
                        "memory_gib": 220,
                        "max_replicas": 1,
                        "hourly_cost_microusd_per_replica": 3_500_000,
                    },
                ],
                "max_hourly_cost_microusd": 4_000_000,
                "kv_cache_mib": 2048,
                "runtime_overhead_mib": 3072,
                "infrastructure_failure_threshold": 2,
                "infrastructure_failure_ttl_seconds": 3600,
            }
        ),
        encoding="utf-8",
    )
    return path


def _catalog(path: Path) -> Path:
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "models": [
                    {
                        "model_id": "vendor/task-coder-small",
                        "revision": "b" * 40,
                        "parameter_count": 3_000_000_000,
                        "context_tokens": 32768,
                        "storage_bytes": 6_000_000_000,
                        "weight_bits": 16,
                        "license_id": "apache-2.0",
                        "tags": ["safetensors", "text-generation"],
                        "pipeline_tag": "text-generation",
                        "library_name": "transformers",
                        "downloads": 300,
                    },
                    {
                        "model_id": "vendor/task-coder-large",
                        "revision": "c" * 40,
                        "parameter_count": 20_000_000_000,
                        "context_tokens": 65536,
                        "storage_bytes": 40_000_000_000,
                        "weight_bits": 16,
                        "license_id": "apache-2.0",
                        "tags": ["safetensors", "text-generation"],
                        "pipeline_tag": "text-generation",
                        "library_name": "transformers",
                        "downloads": 500,
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    return path


def _arguments(tmp_path: Path, output: Path) -> list[str]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    task = tmp_path / "task.txt"
    task.write_text("Implement a bounded Python feature", encoding="utf-8")
    return [
        "--task-file",
        str(task),
        "--policy-file",
        str(_policy(tmp_path / "policy.json")),
        "--evidence-file",
        str(tmp_path / "evidence.json"),
        "--location",
        "westus3",
        "--catalog-file",
        str(_catalog(tmp_path / "catalog.json")),
        "--registry-cache-dir",
        str(tmp_path / "cache"),
        "--output",
        str(output),
    ]


def test_offline_cli_selects_task_fit_without_named_model_default(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    output = tmp_path / "selection.json"

    assert subject.main(_arguments(tmp_path, output)) == 0

    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["model_id"] == "vendor/task-coder-small"
    assert payload["selection_reason"] == "least_tested_challenger"
    assert payload["workload_profile_type"] == "provider/accelerator-small"
    assert stat.S_IMODE(output.stat().st_mode) == 0o600
    trace = capsys.readouterr().out
    assert "Implement a bounded Python feature" not in trace
    assert "vendor/task-coder-small" not in trace
    assert "SELF_IMPROVE_AZURE_MODEL_SELECTED" in trace


def test_cli_routes_around_repeated_exact_scope_infrastructure_failure(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    output = tmp_path / "selection.json"
    arguments = _arguments(tmp_path, output)
    evidence_path = Path(arguments[arguments.index("--evidence-file") + 1])
    store = CapabilityEvidenceStore(str(evidence_path))
    for _ in range(2):
        record_azure_infrastructure_failure(
            store,
            location="westus3",
            workload_profile_type="provider/accelerator-small",
            container_image=IMAGE,
            deployment_identity_digest="a" * 64,
            phase=AzureInfrastructurePhase.CANDIDATE_STARTUP,
            failure=BackendFailure.UNAVAILABLE,
        )

    assert subject.main(arguments) == 0

    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["model_id"] == "vendor/task-coder-small"
    assert payload["selection_reason"] == "operational_failover"
    assert payload["workload_profile_type"] == "provider/accelerator-large"
    trace = capsys.readouterr().out
    assert "registry.example" not in trace
    assert "vendor/task-coder-small" not in trace


def test_cli_classifies_only_the_validated_task_objective(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Control fields and test commands must not distort model-work classification."""
    output = tmp_path / "selection.json"
    arguments = _arguments(tmp_path, output)
    task = Path(arguments[arguments.index("--task-file") + 1])
    task.write_text(
        json.dumps(
            {
                "task_id": "S83.157",
                "objective": "Implement a bounded Python feature",
                "canonical_make_commands": [
                    "make test-files TESTFILES=tests/unit/test_example.py"
                ],
                "reference_elapsed_seconds": 60,
            }
        ),
        encoding="utf-8",
    )

    assert subject.main(arguments) == 0

    events = [
        json.loads(line)
        for line in capsys.readouterr().out.splitlines()
        if line.startswith("{")
    ]
    classified = next(
        event for event in events if event.get("event") == "self_improve_candidate_classified"
    )
    assert classified["task_kind"] == "coding"


def test_cli_rejects_unknown_policy_or_catalog_fields(tmp_path: Path) -> None:
    for target_name in ("policy.json", "catalog.json"):
        output = tmp_path / f"{target_name}.selection.json"
        arguments = _arguments(tmp_path / target_name.replace(".", "-"), output)
        source_index = arguments.index(f"--{target_name.split('.')[0]}-file") + 1
        source = Path(arguments[source_index])
        payload = json.loads(source.read_text(encoding="utf-8"))
        payload["unexpected"] = True
        source.write_text(json.dumps(payload), encoding="utf-8")

        with pytest.raises(ValueError, match=target_name.split(".")[0]):
            subject.main(arguments)


@pytest.mark.parametrize(
    ("field", "value", "match"),
    (
        ("task_queries", [], "task_queries"),
        ("task_queries", {"documentation": "docs"}, "no query"),
        ("allowed_publishers", "vendor", "array of strings"),
        ("profile_capacities", [], "non-empty array"),
    ),
)
def test_cli_rejects_ambiguous_policy_values(
    tmp_path: Path,
    field: str,
    value: object,
    match: str,
) -> None:
    policy = _policy(tmp_path / "policy.json")
    payload = json.loads(policy.read_text(encoding="utf-8"))
    payload[field] = value
    policy.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match=match):
        subject._load_policy(
            policy,
            subject.classify_candidate_task("Implement a bounded Python feature"),
        )


def test_catalog_registry_filters_and_rejects_ambiguous_lookup(
    tmp_path: Path,
) -> None:
    catalog = _catalog(tmp_path / "catalog.json")
    registry = subject.CatalogModelRegistry(catalog)

    assert [
        result.model_id
        for result in registry.search(
            query="coder",
            tags=["safetensors"],
            author="vendor",
            limit=1,
        )
    ] == ["vendor/task-coder-large"]
    with pytest.raises(ValueError, match="downloads sorting"):
        registry.search(sort="likes")
    with pytest.raises(ValueError, match="absent"):
        registry.get_deployment_metadata("vendor/absent")

    payload = json.loads(catalog.read_text(encoding="utf-8"))
    payload["models"].append(dict(payload["models"][0]))
    catalog.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate"):
        subject.CatalogModelRegistry(catalog)


@pytest.mark.parametrize("payload", ("[]", "{invalid"))
def test_configuration_loader_rejects_non_object_or_malformed_json(
    tmp_path: Path,
    payload: str,
) -> None:
    path = tmp_path / "config.json"
    path.write_text(payload, encoding="utf-8")

    with pytest.raises(ValueError, match=r"JSON object|readable JSON"):
        subject._load_object(path, "configuration")


def test_cli_uses_live_registry_seam_when_no_snapshot_is_requested(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "selection.json"
    arguments = _arguments(tmp_path, output)
    catalog_index = arguments.index("--catalog-file")
    catalog = Path(arguments[catalog_index + 1])
    del arguments[catalog_index : catalog_index + 2]
    registry = subject.CatalogModelRegistry(catalog)
    cache_roots: list[str] = []

    def model_registry(*, cache_dir: str) -> subject.CatalogModelRegistry:
        cache_roots.append(cache_dir)
        return registry

    monkeypatch.setattr(subject, "ModelRegistry", model_registry)

    assert subject.main(arguments) == 0
    assert cache_roots == [str(tmp_path / "cache")]


def test_cli_refuses_to_replace_selection_artifact(tmp_path: Path) -> None:
    output = tmp_path / "selection.json"
    output.write_text("owned", encoding="utf-8")

    with pytest.raises(ValueError, match="new private output"):
        subject.main(_arguments(tmp_path, output))

    assert output.read_text(encoding="utf-8") == "owned"


def test_selector_script_contains_no_named_operational_model() -> None:
    source = Path("scripts/select_azure_self_improve_model.py").read_text(
        encoding="utf-8"
    )

    assert "Qwen/" not in source
    assert "DeepSeek" not in source
    assert "Mistral" not in source


def test_repository_policy_routes_bounded_code_enumeration_to_code_models() -> None:
    """A catalog-editing task must discover code models, not models named test."""
    policy = json.loads(
        Path("config/self-improve/azure-model-selection-policy.json").read_text(
            encoding="utf-8"
        )
    )

    assert policy["task_queries"]["bounded_enumeration"] == "code"


def test_repository_policy_pins_previously_attested_aca_runner_by_digest() -> None:
    """The live runner is the immutable amd64 image proven by Azure inference."""
    policy = json.loads(
        Path("config/self-improve/azure-model-selection-policy.json").read_text(
            encoding="utf-8"
        )
    )

    assert policy["container_image"] == (
        "vllm/vllm-openai@sha256:"
        "df2607b26bdda2875de4832f4d08da0055b4b6e3570347f3a849bcc652771dd6"
    )
