"""Structural contract for the Azure GPU deployment diagnostics role."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
ROLE = (
    ROOT
    / "collections"
    / "ansible_collections"
    / "general_ludd"
    / "azure"
    / "roles"
    / "gpu_deployment_diagnostics"
)
SCENARIO = ROOT / "molecule" / "playbooks" / "azure_gpu_deployment_diagnostics"


def _yaml(path: Path) -> Any:
    """Load one trusted repository YAML document."""
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def test_role_has_complete_ansible_layout() -> None:
    """The role ships defaults, tasks, metadata, and operator documentation."""
    required = {
        ROLE / "defaults" / "main.yml",
        ROLE / "tasks" / "main.yml",
        ROLE / "meta" / "main.yml",
        ROLE / "README.md",
    }
    assert all(path.is_file() for path in required)


def test_role_is_opt_in_and_report_only_by_default() -> None:
    """Diagnostics cannot acquire cloud resources or write artifacts by default."""
    defaults = _yaml(ROLE / "defaults" / "main.yml")
    assert defaults["azure_gpu_diagnostics_enabled"] is False
    assert defaults["azure_gpu_diagnostics_write_artifact"] is False
    assert defaults["azure_gpu_diagnostics_observation"] == {}
    assert defaults["azure_gpu_diagnostics_artifact_dir"].startswith(
        "/tmp/gludd-azure-gpu-diagnostics"
    )


def test_tasks_use_only_builtin_fact_validation_reporting_modules() -> None:
    """The role does not duplicate Terraform, SDK, CLI, or lifecycle behavior."""
    tasks_text = (ROLE / "tasks" / "main.yml").read_text(encoding="utf-8")
    forbidden = (
        "ansible.builtin.command:",
        "ansible.builtin.shell:",
        "azure.azcollection",
        " az ",
        "terraform",
        "openstack",
    )
    assert not any(token in tasks_text.lower() for token in forbidden)
    tasks = _yaml(ROLE / "tasks" / "main.yml")
    allowed_modules = {
        "ansible.builtin.assert",
        "ansible.builtin.copy",
        "ansible.builtin.debug",
        "ansible.builtin.file",
        "ansible.builtin.set_fact",
    }
    used_modules = {
        key
        for task in tasks
        for key in task
        if key.startswith("ansible.builtin.")
    }
    assert used_modules <= allowed_modules


def test_tasks_pin_sanitized_schema_and_exact_replica_inventory() -> None:
    """The executable contract rejects raw logs and distrusts summary counts."""
    tasks_text = (ROLE / "tasks" / "main.yml").read_text(encoding="utf-8")
    for token in (
        "_azure_gpu_allowed_observation_keys",
        "_azure_gpu_allowed_event_keys",
        "summary_replica_count",
        "exact_replicas",
        "replica_summary_mismatch",
        "reason_class",
    ):
        assert token in tasks_text
    for forbidden_field in (
        "provider_message",
        "raw_message",
        "credential",
        "access_token",
    ):
        assert forbidden_field in tasks_text


def test_tasks_expose_phase_markers_and_an_ansible_fact() -> None:
    """Long-lived diagnostics stay observable and machine-consumable."""
    tasks_text = (ROLE / "tasks" / "main.yml").read_text(encoding="utf-8")
    assert "azure_gpu_diagnostics_phase" in tasks_text
    assert "azure_gpu_diagnostics:" in tasks_text
    assert "duration_seconds" in tasks_text


def test_prevalidation_tasks_never_echo_rejected_values() -> None:
    """Unknown fields fail closed without Ansible printing their values."""
    tasks = _yaml(ROLE / "tasks" / "main.yml")
    protected_names = {
        "Extract normalized diagnostic sections",
        "Validate every sanitized phase marker",
        "Validate every exact replica observation",
        "Validate every normalized system event aggregate",
        "Validate every policy-approved fallback profile",
    }
    protected = {task["name"]: task for task in tasks if task.get("name") in protected_names}
    assert set(protected) == protected_names
    assert all(task.get("no_log") is True for task in protected.values())


def test_role_metadata_is_portable_and_dependency_free() -> None:
    """The diagnostic policy can run in the existing controller EE."""
    metadata = _yaml(ROLE / "meta" / "main.yml")
    galaxy = metadata["galaxy_info"]
    assert galaxy["role_name"] == "gpu_deployment_diagnostics"
    assert galaxy["min_ansible_version"] == "2.14"
    assert metadata["dependencies"] == []


def test_documentation_defines_sdk_boundary_and_no_secret_policy() -> None:
    """Operators are directed to SDK/module facts, not raw logs or CLI output."""
    readme = (ROLE / "README.md").read_text(encoding="utf-8")
    assert "Microsoft SDK" in readme
    assert "azure.azcollection" in readme
    assert "does not provision" in readme.lower()
    assert "raw provider" in readme.lower()
    assert "credentials" in readme.lower()


def test_molecule_matrix_covers_operational_decisions_and_redaction() -> None:
    """The functional scenario pins the learned Azure failure modes."""
    converge = (SCENARIO / "default" / "converge.yml").read_text(encoding="utf-8")
    verify = (SCENARIO / "default" / "verify.yml").read_text(encoding="utf-8")
    for case in (
        "healthy",
        "quota_blocked",
        "replica_summary_mismatch",
        "retry_same_profile",
        "failover_profile",
        "repair_deployment",
        "cleanup_required",
        "raw_input_rejected",
    ):
        assert case in converge + verify
    assert _yaml(SCENARIO / "molecule.yml")["scenario"]["test_sequence"] == [
        "syntax",
        "converge",
        "idempotence",
        "verify",
    ]
