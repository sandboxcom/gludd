"""Execute the Azure coverage owner through native recursive Make transport."""

from __future__ import annotations

import json
import shlex
import subprocess
import sys
from pathlib import Path
from typing import cast

import pytest

ROOT = Path(__file__).resolve().parents[2]
ORIGINAL_TESTFILES = (
    "tests/unit/test_ansible_runtime_artifacts.py",
    "tests/unit/test_azure_accelerator_credentials.py",
    "tests/unit/test_azure_accelerator_openbao.py",
    "tests/unit/test_azure_accelerator_role.py",
    "tests/unit/test_azure_resource_group_bootstrap.py",
    "tests/unit/test_azure_containerapp_ansible_orchestration.py",
    "tests/unit/test_azure_containerapp_retention_module_utils.py",
    "tests/unit/test_azure_containerapp_arm.py",
    "tests/unit/test_azure_containerapp_bootstrap_planning.py",
    "tests/unit/test_azure_containerapp_environment_document.py",
    "tests/unit/test_azure_containerapp_environment_guard.py",
    "tests/unit/test_azure_containerapp_environment_lifecycle.py",
    "tests/unit/test_azure_containerapp_environment_operations.py",
    "tests/unit/test_azure_containerapp_environment_retention.py",
    "tests/unit/test_azure_containerapp_environment_make_runtime.py",
    "tests/unit/test_azure_containerapp_environment_runtime_types.py",
    "tests/unit/test_azure_containerapp_environment_state.py",
    "tests/unit/test_azure_containerapp_environment_preflight.py",
    "tests/unit/test_azure_containerapp_environment_terraform.py",
    "tests/unit/test_azure_containerapp_gpu.py",
    "tests/unit/test_azure_containerapp_gpu_backend.py",
    "tests/unit/test_azure_idle_retention.py",
    "tests/unit/test_azure_containerapp_live_proof.py",
    "tests/unit/test_azure_containerapp_make_runtime.py",
    "tests/unit/test_azure_containerapp_owned_lifecycle.py",
    "tests/unit/test_azure_containerapp_preflight.py",
    "tests/unit/test_azure_containerapp_preflight_cli.py",
    "tests/unit/test_azure_containerapp_runtime_factories.py",
    "tests/unit/test_azure_containerapp_runtime_readers.py",
    "tests/unit/test_azure_containerapp_resource_owner.py",
    "tests/unit/test_azure_containerapp_runtime_resources.py",
    "tests/unit/test_azure_containerapp_runtime_state.py",
    "tests/unit/test_azure_containerapp_sdk.py",
    "tests/unit/test_azure_containerapp_terraform_executor.py",
    "tests/unit/test_azure_containerapp_terraform_phase.py",
    "tests/unit/test_azure_containerapp_topology.py",
    "tests/unit/test_azure_containerapp_tfvars.py",
    "tests/unit/test_azure_infrastructure_evidence.py",
    "tests/unit/test_azure_self_improve_model_selection.py",
    "tests/unit/test_deployment_telemetry.py",
    "tests/unit/test_provider_auth.py",
    "tests/unit/test_select_azure_self_improve_model.py",
    "tests/unit/test_self_improve_azure_containerapp_backend.py",
    "tests/unit/test_self_improve_azure_containerapp_bootstrap.py",
    "tests/unit/test_self_improve_runtime_config.py",
    "tests/e2e/test_azure_containerapp_live_proof_cli.py",
    "tests/e2e/test_azure_containerapp_gha_oidc.py",
)
RUNTIME_CLOSURE_TESTFILES = (
    "tests/unit/test_ansible_collection_staging.py",
    "tests/unit/test_ansible_collection_stage_target.py",
    "tests/unit/test_ansible_canonical_collection_binding.py",
    "tests/unit/test_azure_containerapp_coverage_target.py",
)
OWNER_POLICY = {
    "COVERAGE_CONFIG": "config/coverage_azure_containerapp.ini",
    "COVERAGE_REPORT": ".gate-logs/coverage-azure-containerapp.json",
    "COVERAGE_AGGREGATE_MIN": "85",
    "COVERAGE_PER_FILE_MIN": "75",
    "OBSERVED_ROOT": ".gate-logs/observed",
    "OBSERVED_HEARTBEAT_SECS": "1",
    "OBSERVED_QUIET_SECS": "60",
    "OBSERVED_MAX_SECS": "300",
    "OBSERVED_RETAIN_RUNS": "20",
}


@pytest.mark.parametrize("caller_overrides", [False, True])
@pytest.mark.parametrize("child_status", [0, 17])
def test_native_azure_coverage_selects_complete_runtime_closure_and_keeps_policy(
    tmp_path: Path, caller_overrides: bool, child_status: int,
) -> None:
    """Capture the real child argv without running coverage or external tools."""
    capture = tmp_path / "capture.py"
    capture.write_text(
        "import json, sys\n"
        "print(json.dumps(sys.argv[1:]))\n"
        f"raise SystemExit({child_status})\n",
        encoding="utf-8",
    )
    arguments = " ".join(f"'{name}=$({name})'" for name in OWNER_POLICY)
    (tmp_path / "Makefile").write_text(
        f"include {ROOT / 'make/40-cross-version-and-worktrees.mk'}\n"
        ".PHONY: coverage-files\n"
        "coverage-files:\n"
        f'\t@"{sys.executable}" "{capture}" $(COVERAGE_TESTFILES) {arguments}\n',
        encoding="utf-8",
    )
    overrides = [
        "COVERAGE_TESTFILES=tests/unit/never-select-this.py",
        *(f"{name}=caller-cannot-weaken-owner" for name in OWNER_POLICY),
    ] if caller_overrides else []
    result = subprocess.run(
        ["make", "--no-print-directory", "test-azure-containerapp-coverage", *overrides],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    argv = cast(list[str], json.loads(result.stdout))
    selected = tuple(argument for argument in argv if "=" not in argument)
    policy = dict(argument.split("=", 1) for argument in argv if "=" in argument)
    assert len(ORIGINAL_TESTFILES) == 47
    assert selected[:len(ORIGINAL_TESTFILES)] == ORIGINAL_TESTFILES
    assert selected[len(ORIGINAL_TESTFILES):] == RUNTIME_CLOSURE_TESTFILES
    assert len(selected) == len(set(selected))
    assert policy == OWNER_POLICY
    if child_status:
        assert result.returncode != 0
        assert f"Error {child_status}" in result.stderr
    else:
        assert result.returncode == 0, result.stdout + result.stderr


def test_runtime_closure_roster_is_checked_before_publication() -> None:
    """Require the existing fast owner to execute the native roster regressions."""
    result = subprocess.run(
        ["make", "--no-print-directory", "-n", "_pytest-runtime-compatibility-admission"],
        cwd=ROOT, capture_output=True, text=True, timeout=20, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assignments = [
        argument.removeprefix("TESTFILES=")
        for argument in shlex.split(result.stdout) if argument.startswith("TESTFILES=")
    ]
    assert assignments
    assert "tests/unit/test_azure_containerapp_coverage_target.py" in assignments[0].split()
    manifest = json.loads((ROOT / "config/gate_failure_promotions.json").read_text(encoding="utf-8"))
    family = next(
        entry for entry in manifest["families"]
        if entry["id"] == "azure-runtime-artifact-coverage-roster"
    )
    assert family["owner_target"] == "_pytest-runtime-compatibility-admission"
    assert family["node_kind"] == "pytest_node"
    assert family["node"] == (
        "tests/unit/test_azure_containerapp_coverage_target.py::"
        "test_native_azure_coverage_selects_complete_runtime_closure_and_keeps_policy"
    )
    assert family["full_gate_phase"] == "test"
