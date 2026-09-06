"""One-command operator bootstrap for the shared Azure GPU environment."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from scripts import render_azure_accelerator_auth_args as subject

ROOT = Path(__file__).resolve().parents[2]
SUBSCRIPTION_ID = "11111111-2222-3333-4444-555555555555"
RESOURCE_GROUP = "gludd-models-eastus"
ENVIRONMENT = "gludd-gpu-environment"
PROFILE_NAME = "gpu-t4"
PROFILE_TYPE = "Consumption-GPU-NC8as-T4"
TEMPLATE = ROOT / "config/infra/azure-containerapp-environment.json"


def _decode(payload: bytes) -> tuple[str, ...]:
    return tuple(part.decode() for part in payload.removesuffix(b"\0").split(b"\0"))


def test_bootstrap_template_owns_only_one_shared_managed_environment() -> None:
    template = json.loads(TEMPLATE.read_text(encoding="utf-8"))

    assert template["parameters"]["workloadProfileType"]["allowedValues"] == [
        "Consumption-GPU-NC8as-T4",
        "Consumption-GPU-NC24-A100",
    ]
    assert len(template["resources"]) == 1
    environment = template["resources"][0]
    assert environment["type"] == "Microsoft.App/managedEnvironments"
    assert environment["apiVersion"] == "2025-01-01"
    assert environment["properties"]["workloadProfiles"] == [
        {
            "name": "[parameters('workloadProfileName')]",
            "workloadProfileType": "[parameters('workloadProfileType')]",
        }
    ]
    rendered = json.dumps(template)
    for forbidden in (
        "Microsoft.Authorization",
        "containerApps",
        "Microsoft.Network",
        "OperationalInsights",
        "clientSecret",
        "minimumCount",
        "maximumCount",
    ):
        assert forbidden not in rendered


def test_bootstrap_arguments_are_one_incremental_group_deployment() -> None:
    arguments = subject.build_environment_bootstrap_arguments(
        subscription_id=SUBSCRIPTION_ID,
        resource_group=RESOURCE_GROUP,
        environment_name=ENVIRONMENT,
        workload_profile_name=PROFILE_NAME,
        workload_profile_type=PROFILE_TYPE,
        location="eastus",
    )

    assert arguments == (
        "deployment",
        "group",
        "create",
        "--subscription",
        SUBSCRIPTION_ID,
        "--resource-group",
        RESOURCE_GROUP,
        "--name",
        "gludd-containerapp-environment-bootstrap",
        "--mode",
        "Incremental",
        "--template-file",
        "config/infra/azure-containerapp-environment.json",
        "--parameters",
        "location=eastus",
        f"environmentName={ENVIRONMENT}",
        f"workloadProfileName={PROFILE_NAME}",
        f"workloadProfileType={PROFILE_TYPE}",
        "--output",
        "json",
    )
    assert not any("role" in argument.casefold() for argument in arguments)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("resource_group", "../other"),
        ("environment_name", "name/other"),
        ("workload_profile_name", "--query"),
        ("workload_profile_type", "D4"),
        ("location", "East US"),
    ],
)
def test_bootstrap_arguments_reject_unsafe_or_non_gpu_values(
    field: str,
    value: str,
) -> None:
    kwargs = {
        "subscription_id": SUBSCRIPTION_ID,
        "resource_group": RESOURCE_GROUP,
        "environment_name": ENVIRONMENT,
        "workload_profile_name": PROFILE_NAME,
        "workload_profile_type": PROFILE_TYPE,
        "location": "eastus",
    }
    kwargs[field] = value

    with pytest.raises(ValueError, match="invalid"):
        subject.build_environment_bootstrap_arguments(**kwargs)


def test_make_target_emits_exact_nul_arguments_without_stderr() -> None:
    result = subprocess.run(
        [
            "make",
            "--no-print-directory",
            "azure-containerapp-environment-bootstrap-args",
            f"AZURE_ACCELERATOR_SUBSCRIPTION_ID={SUBSCRIPTION_ID}",
            f"AZURE_ACCELERATOR_RESOURCE_GROUP={RESOURCE_GROUP}",
            f"AZURE_CONTAINERAPP_ENVIRONMENT={ENVIRONMENT}",
            f"AZURE_CONTAINERAPP_WORKLOAD_PROFILE_NAME={PROFILE_NAME}",
            f"AZURE_CONTAINERAPP_WORKLOAD_PROFILE_TYPE={PROFILE_TYPE}",
            "AZURE_CONTAINERAPP_LOCATION=eastus",
        ],
        cwd=ROOT,
        capture_output=True,
        timeout=10,
    )

    assert result.returncode == 0, result.stderr.decode(errors="replace")
    assert _decode(result.stdout) == subject.build_environment_bootstrap_arguments(
        subscription_id=SUBSCRIPTION_ID,
        resource_group=RESOURCE_GROUP,
        environment_name=ENVIRONMENT,
        workload_profile_name=PROFILE_NAME,
        workload_profile_type=PROFILE_TYPE,
        location="eastus",
    )
    assert result.stderr == b""
