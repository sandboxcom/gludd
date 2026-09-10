"""Secure renderer contracts for a live mixed-model runtime configuration."""

from __future__ import annotations

import json
import stat
from pathlib import Path

import pytest
from scripts import render_azure_self_improve_runtime_config as subject

from general_ludd.self_improve.azure_model_selection import (
    azure_model_deployment_identity_digest,
)

SUBSCRIPTION = "11111111-2222-3333-4444-555555555555"
TENANT = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
CLIENT = "99999999-8888-7777-6666-555555555555"
MODEL_REVISION = "b" * 40
IMAGE = "registry.example/vllm@sha256:" + "c" * 64


def _selection(path: Path) -> Path:
    identity = azure_model_deployment_identity_digest(
        model_id="vendor/task-selected-coder",
        model_revision=MODEL_REVISION,
        weight_bits=16,
        container_image=IMAGE,
        workload_profile_type="Consumption-GPU-NC24-A100",
    )
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "model_id": "vendor/task-selected-coder",
                "revision": MODEL_REVISION,
                "parameter_count": 7_000_000_000,
                "weight_bits": 16,
                "kv_cache_mib": 2048,
                "runtime_overhead_mib": 3072,
                "context_tokens": 32768,
                "container_image": IMAGE,
                "workload_profile_type": "Consumption-GPU-NC24-A100",
                "selection_identity_digest": identity,
                "selection_reason": "least_tested_challenger",
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
                        "workload_profile_type": "Consumption-GPU-NC24-A100",
                        "gpu_vram_mib": 81_920,
                        "usable_vram_mib": 73_728,
                        "cpu_cores": 24,
                        "memory_gib": 220,
                        "max_replicas": 1,
                        "hourly_cost_microusd_per_replica": 3_500_000,
                    },
                ],
                "max_hourly_cost_microusd": 4_000_000,
            }
        ),
        encoding="utf-8",
    )
    return path


def _arguments(
    output: Path,
    *authentication: str,
    selection: Path | None = None,
) -> list[str]:
    selected = selection or _selection(output.parent / "selection.json")
    return [
        *authentication,
        "--model-selection-file",
        str(selected),
        "--subscription-id",
        SUBSCRIPTION,
        "--resource-group",
        "gludd-models-eastus",
        "--environment",
        "gludd-gpu-environment",
        "--location",
        "eastus",
        "--allowed-cidr",
        "203.0.113.7/32",
        "--max-cost-usd",
        "5",
        "--ttl-minutes",
        "60",
        "--acknowledgement",
        "DEPLOY_ONE_CONTAINER_APP_AND_DESTROY",
        "--idle-retention-preset",
        "always_destroy",
        "--idle-retention-seconds",
        "21600",
        "--output",
        str(output),
    ]


def test_file_auth_renderer_writes_exact_private_code_canary_config(
    tmp_path: Path,
) -> None:
    output = tmp_path / "runtime.json"

    assert subject.main(_arguments(output, "--auth-file", "/private/auth.json")) == 0

    payload = json.loads(output.read_text(encoding="utf-8"))
    azure = payload["azure_containerapp"]
    assert set(payload) == {"azure_containerapp"}
    assert azure["auth_file"] == "/private/auth.json"
    assert "federated_token_file" not in azure
    assert azure["model_name"] == "vendor/task-selected-coder"
    assert azure["model_revision"] == MODEL_REVISION
    assert azure["parameter_count"] == 7_000_000_000
    assert azure["container_image"] == IMAGE
    assert azure["max_input_tokens"] == 24_576
    assert azure["max_output_tokens"] == 4_096
    assert azure["max_total_tokens"] == 28_672
    assert azure["peak_concurrency"] == 1
    assert len(azure["profile_capacities"]) == 2
    assert azure["profile_capacities"][1]["workload_profile_type"] == (
        "Consumption-GPU-NC24-A100"
    )
    assert "t4_max_replicas" not in azure
    assert "a100_max_replicas" not in azure
    assert azure["idle_retention"]["preset"] == "always_destroy"
    assert stat.S_IMODE(output.stat().st_mode) == 0o600


def test_workload_identity_renderer_resolves_auto_cidr_without_storing_assertion(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "runtime.json"
    calls: list[None] = []

    def resolve() -> str:
        calls.append(None)
        return "192.0.2.44/32"

    monkeypatch.setattr(subject, "resolve_public_ipv4_cidr", resolve)
    arguments = _arguments(
        output,
        "--federated-token-file",
        "/private/oidc-token.jwt",
        "--azure-client-id",
        CLIENT,
        "--azure-tenant-id",
        TENANT,
    )
    arguments[arguments.index("203.0.113.7/32")] = "auto"

    assert subject.main(arguments) == 0

    azure = json.loads(output.read_text(encoding="utf-8"))["azure_containerapp"]
    assert calls == [None]
    assert azure["allowed_cidr"] == "192.0.2.44/32"
    assert azure["client_id"] == CLIENT
    assert azure["tenant_id"] == TENANT
    assert azure["federated_token_file"] == "/private/oidc-token.jwt"
    assert "auth_file" not in azure
    assert "eyjhb" not in output.read_text(encoding="utf-8").casefold()


def test_renderer_refuses_existing_or_symlink_output(tmp_path: Path) -> None:
    existing = tmp_path / "existing.json"
    existing.write_text("owned", encoding="utf-8")
    symlink = tmp_path / "link.json"
    symlink.symlink_to(existing)

    for output in (existing, symlink):
        with pytest.raises(ValueError, match="new private output"):
            subject.main(_arguments(output, "--auth-file", "/private/auth.json"))
    assert existing.read_text(encoding="utf-8") == "owned"


def test_renderer_rejects_missing_mutable_or_unfitted_model_selection(
    tmp_path: Path,
) -> None:
    missing = tmp_path / "missing.json"
    mutable = _selection(tmp_path / "mutable.json")
    payload = json.loads(mutable.read_text(encoding="utf-8"))
    payload["revision"] = "main"
    mutable.write_text(json.dumps(payload), encoding="utf-8")
    wrong_profile = _selection(tmp_path / "wrong-profile.json")
    payload = json.loads(wrong_profile.read_text(encoding="utf-8"))
    payload["workload_profile_type"] = "Consumption-GPU-NC8as-T4"
    wrong_profile.write_text(json.dumps(payload), encoding="utf-8")
    forged = _selection(tmp_path / "forged.json")
    payload = json.loads(forged.read_text(encoding="utf-8"))
    payload["selection_identity_digest"] = "0" * 64
    forged.write_text(json.dumps(payload), encoding="utf-8")

    for selection in (missing, mutable, wrong_profile, forged):
        with pytest.raises(ValueError, match="model selection"):
            subject.main(
                _arguments(
                    tmp_path / f"{selection.name}.runtime.json",
                    "--auth-file",
                    "/private/auth.json",
                    selection=selection,
                )
            )


@pytest.mark.parametrize(
    "arguments",
    (
        ("--federated-token-file", "/private/assertion.jwt"),
        (
            "--auth-file",
            "/private/auth.json",
            "--federated-token-file",
            "/private/assertion.jwt",
            "--azure-client-id",
            CLIENT,
            "--azure-tenant-id",
            TENANT,
        ),
    ),
)
def test_renderer_rejects_incomplete_or_ambiguous_authentication(
    tmp_path: Path,
    arguments: tuple[str, ...],
) -> None:
    with pytest.raises(SystemExit):
        subject.main(_arguments(tmp_path / "runtime.json", *arguments))


def test_make_target_runs_real_benchmark_with_one_temporary_config() -> None:
    makefile = Path("Makefile").read_text(encoding="utf-8")
    recipe = makefile.split("\nazure-self-improve-live-proof:", 1)[1].split(
        "\n\n", 1
    )[0]

    assert "render_azure_self_improve_runtime_config.py" in recipe
    assert "select_azure_self_improve_model.py" in recipe
    assert "mktemp -d" in recipe
    assert "trap" in recipe
    assert "test-self-improve" in recipe
    assert "SELF_IMPROVE_CONFIG_FILE=" in recipe
    assert "SELF_IMPROVE_MAX_ATTEMPTS=1" in recipe
    assert "SELF_IMPROVE_VALIDATE_ONLY=" in recipe
    assert "$(AZURE_SELF_IMPROVE_TASK_FILE)" in recipe
    assert (
        "AZURE_SELF_IMPROVE_TASK_FILE ?= config/self-improve/catalog-truth.json"
        in makefile
    )
    assert "azure-containerapp-live-proof" not in recipe
    assert " az " not in recipe
    assert " terraform " not in recipe


def test_operational_renderer_contains_no_named_model_default() -> None:
    source = Path("scripts/render_azure_self_improve_runtime_config.py").read_text(
        encoding="utf-8"
    )

    assert "Qwen/" not in source
    assert "DeepSeek" not in source
    assert "CODE_IMPROVEMENT_CANARY" not in source
    assert "t4_max_replicas" not in source
    assert "a100_max_replicas" not in source


def test_live_config_requests_the_locked_azure_dependency_extra() -> None:
    makefile = Path("Makefile").read_text(encoding="utf-8")
    recipe = makefile.split("\ntest-self-improve:", 1)[1].split("\n\n", 1)[0]

    assert "$(if $(strip $(SELF_IMPROVE_CONFIG_FILE)),--extra azure,)" in recipe
