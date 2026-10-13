"""Contracts for deterministic full-gate failure promotion into admission."""

from __future__ import annotations

import importlib
import json
import subprocess
from copy import deepcopy
from pathlib import Path
from textwrap import dedent
from types import ModuleType

import pytest
import yaml
from scripts.makefile_layout import compose_makefile

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / "config" / "gate_failure_promotions.json"
CONTRACT = ROOT / "config" / "make_target_contract.json"
FEATURE_DOC = ROOT / "docs" / "features" / "INTEGRATION_ADMISSION.md"

EXPECTED_RUNTIME_PHASES = [
    ("worktree-guard", "worktree-guard", "fast", 90, 60),
    (
        "check-gate-failure-promotions",
        "check-gate-failure-promotions",
        "fast",
        90,
        60,
    ),
    (
        "pytest-runtime-compatibility",
        "_pytest-runtime-compatibility-admission",
        "fast",
        90,
        60,
    ),
    (
        "ansible-role-variable-prefix",
        "_ansible-role-variable-prefix",
        "fast",
        90,
        60,
    ),
    (
        "cloud-iam-generation-parity",
        "_cloud-iam-generation-parity",
        "fast",
        90,
        60,
    ),
    (
        "_dead-code-baseline-refresh",
        "_dead-code-baseline-refresh",
        "fast",
        90,
        60,
    ),
    ("check-coverage-gaps", "check-coverage-gaps", "fast", 90, 60),
    (
        "check-resource-ownership",
        "check-resource-ownership",
        "fast",
        90,
        60,
    ),
    ("validate-task-ledger", "validate-task-ledger", "fast", 90, 60),
    ("check-task-registration", "check-task-registration", "fast", 90, 60),
    ("check-task-integrity", "check-task-integrity", "fast", 90, 60),
    (
        "check-generated-artifact-hygiene",
        "check-generated-artifact-hygiene",
        "fast",
        90,
        60,
    ),
    ("lint-markdown", "lint-markdown", "fast", 90, 60),
    (
        "check-make-target-contract",
        "check-make-target-contract",
        "fast",
        90,
        60,
    ),
    ("check-duplicate-code", "check-duplicate-code", "standard", 180, 120),
    ("yaml-lint", "yaml-lint", "standard", 180, 120),
    (
        "project-dispatch-integration",
        "_project-dispatch-integration",
        "standard",
        180,
        120,
    ),
    (
        "mcp-workspace-jail-integration",
        "_mcp-workspace-jail-integration",
        "standard",
        180,
        120,
    ),
    (
        "module-graph-classification",
        "_module-graph-classification",
        "fast",
        90,
        60,
    ),
    (
        "presentation-browser-test",
        "presentation-browser-test",
        "standard",
        180,
        120,
    ),
    ("pre-commit-check", "pre-commit-check", "slow", 600, 300),
]


def _checker() -> ModuleType:
    return importlib.import_module("scripts.check_gate_failure_promotions")


def test_async_canonical_profile_contract_fails_in_compatibility_admission_first() -> None:
    """A cheap exact-node failure must not recur deep in hosted unit-2."""
    node = (
        "tests/unit/test_gate_async.py::"
        "test_default_gate_command_invokes_whole_gate_without_recursion"
    )
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    family = next((family for family in manifest["families"] if family["node"] == node), None)
    assert family is not None
    assert family["owner_target"] == "_pytest-runtime-compatibility-admission"
    assert family["full_gate_phase"] == "test"
    owner = _target_stanza(compose_makefile(ROOT / "Makefile"), family["owner_target"])
    assert "tests/unit/test_gate_async.py" in owner
    assert 'NPROC=1 GLUDD_XDIST_WORKERS=1 PYTEST_ARGS="-W error -q -n 1"' in owner
    assert manifest["full_gate_required"] is True


@pytest.mark.parametrize(
    ("family_id", "node", "executable_input"),
    [
        (
            "session-objective-ci-evidence",
            "tests/unit/test_enforce_objective_plugin.py::"
            "TestSESSIONmdIntegration::test_primary_objective_mentions_ci",
            "tests/unit/test_enforce_objective_plugin.py::"
            "TestSESSIONmdIntegration::test_primary_objective_mentions_ci",
        ),
        (
            "session-handoff-git-identity",
            "tests/unit/test_session_persistence_integration.py::"
            "TestHeadHashMatchesGitLog::test_session_head_in_git_log",
            "tests/unit/test_session_persistence_integration.py",
        ),
    ],
)
def test_session_metadata_failures_are_executed_before_hosted_shards(
    family_id: str, node: str, executable_input: str,
) -> None:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    family = next(
        (family for family in manifest["families"] if family["id"] == family_id),
        None,
    )
    assert family is not None
    assert family == {
        "id": family_id,
        "owner_target": "_pytest-runtime-compatibility-admission",
        "node_kind": "pytest_node",
        "node": node,
        "full_gate_phase": "test",
    }
    owner = _target_stanza(compose_makefile(ROOT / "Makefile"), family["owner_target"])
    executable = "\n".join(line for line in owner.splitlines() if not line.startswith("#"))
    assert executable_input in executable
    assert 'NPROC=1 GLUDD_XDIST_WORKERS=1 PYTEST_ARGS="-W error -q -n 1"' in executable
    assert manifest["full_gate_required"] is True
    assert ("pytest-runtime-compatibility", family["owner_target"], "fast", 90, 60) in (
        EXPECTED_RUNTIME_PHASES
    )


@pytest.mark.parametrize(
    "test_name",
    [
        "test_timeout_terminates_exact_session_and_removes_owned_pid",
        "test_timeout_escalates_to_sigkill_for_term_ignoring_gate",
        "test_timeout_never_removes_a_replaced_pid_receipt",
    ],
)
def test_timeout_teardown_failures_execute_in_early_admission(test_name: str) -> None:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    node = f"tests/unit/test_start_gate_background.py::{test_name}"
    family = next((family for family in manifest["families"] if family["node"] == node), None)
    assert family is not None
    assert family["owner_target"] == "_pytest-runtime-compatibility-admission"
    assert family["full_gate_phase"] == "test"
    owner = _target_stanza(compose_makefile(ROOT / "Makefile"), family["owner_target"])
    executable = "\n".join(line for line in owner.splitlines() if not line.startswith("#"))
    assert "tests/unit/test_start_gate_background.py" in executable
    assert 'NPROC=1 GLUDD_XDIST_WORKERS=1 PYTEST_ARGS="-W error -q -n 1"' in executable
    assert manifest["full_gate_required"] is True


def _target_stanza(makefile: str, target: str) -> str:
    return makefile.split(f"\n{target}:", 1)[1].split("\n\n", 1)[0]


def test_gate_lock_prerequisite_failure_runs_in_whole_file_early_admission() -> None:
    """A read-only admission check must not be confused with a stateful gate phase."""
    payload = json.loads(MANIFEST.read_text(encoding="utf-8"))
    family = next(
        family for family in payload["families"]
        if family["id"] == "gate-lock-prerequisite-order"
    )
    assert family == {
        "id": "gate-lock-prerequisite-order",
        "owner_target": "_pytest-runtime-compatibility-admission",
        "node_kind": "pytest_node",
        "node": "tests/unit/test_gate_run_lock.py::test_gate_and_refresh_acquire_before_running_phases",
        "full_gate_phase": "test",
    }
    owner = _target_stanza(compose_makefile(ROOT / "Makefile"), family["owner_target"])
    assert "tests/unit/test_gate_run_lock.py" in owner
    assert "test_gate_and_refresh_acquire_before_running_phases" in owner
    phase = next(
        phase for phase in payload["runtime_contract"]["phases"]
        if phase["target"] == family["owner_target"]
    )
    assert (phase["max_seconds"], phase["quiet_seconds"]) == (90, 60)
    assert payload["full_gate_required"] is True


@pytest.mark.parametrize(
    ("family_id", "node", "test_file"),
    [
        (
            "threadpool-bulkhead-nonblocking-admission",
            "tests/unit/test_bulkhead_deep.py::TestThreadPoolSubmit::test_submit_rejects_when_queue_full_nonblocking",
            "tests/unit/test_bulkhead_deep.py",
        ),
        (
            "threadpool-bulkhead-worker-reservations",
            "tests/unit/test_bulkhead_admission.py::test_admission_reserves_workers_before_backlog",
            "tests/unit/test_bulkhead_admission.py",
        ),
    ],
)
def test_bulkhead_scheduling_failures_execute_in_complete_early_cohorts(
    family_id: str, node: str, test_file: str,
) -> None:
    payload = json.loads(MANIFEST.read_text(encoding="utf-8"))
    family = next(family for family in payload["families"] if family["id"] == family_id)
    assert family == {
        "id": family_id,
        "owner_target": "_pytest-runtime-compatibility-admission",
        "node_kind": "pytest_node",
        "node": node,
        "full_gate_phase": "test",
    }
    owner = _target_stanza(compose_makefile(ROOT / "Makefile"), family["owner_target"])
    assert node in owner
    assert test_file in owner
    phase = next(
        phase for phase in payload["runtime_contract"]["phases"]
        if phase["target"] == family["owner_target"]
    )
    assert (phase["max_seconds"], phase["quiet_seconds"]) == (90, 60)
    assert "-W error" in owner and "-n 1" in owner
    assert payload["full_gate_required"] is True


def test_hosted_fanout_requires_executed_runtime_admission() -> None:
    workflow = yaml.safe_load((ROOT / ".github/workflows/build.yml").read_text())
    jobs = workflow["jobs"]
    steps = jobs["gate"]["steps"]
    owners = [
        step for step in steps
        if "make _pytest-runtime-compatibility-admission" in step.get("run", "")
    ]
    assert len(owners) == 1
    owner = owners[0]
    assert "if" not in owner and not owner.get("continue-on-error", False)
    assert "make run-watched" in owner["run"]
    assert "STALL_SECS=60 MAX_SECS=90" in owner["run"]
    assert "OBSERVED_HEARTBEAT_SECS=10" in owner["run"]
    dependency_step = next(step for step in steps if step.get("name") == "Install deps")
    assert steps.index(dependency_step) < steps.index(owner)
    assert "--set ci --environment .venv" in dependency_step["run"]
    assert jobs["gate"]["strategy"]["matrix"]["python-version"] == ["3.11", "3.12"]
    for job_name in (
        "test-shard", "linux", "macos", "windows", "termux", "container",
        "ansible-ee", "game-building",
    ):
        assert "gate" in jobs[job_name]["needs"]


@pytest.mark.parametrize(
    ("family_id", "node", "test_file"),
    [
        (
            "enhancement-ratio-class-fixture-binding",
            "tests/unit/test_enhancement_ratio_plugin.py::TestKeywordClassification::test_classifies_as_enhancement",
            "tests/unit/test_enhancement_ratio_plugin.py",
        ),
        (
            "pytest-class-fixture-binding-invariant",
            "tests/unit/test_pytest_param_fixture_collisions.py::test_repository_class_scoped_fixtures_use_supported_binding",
            "tests/unit/test_pytest_param_fixture_collisions.py",
        ),
    ],
)
def test_class_fixture_binding_failures_execute_in_complete_early_cohorts(
    family_id: str, node: str, test_file: str,
) -> None:
    """Unsupported fixture declarations must fail before late hosted setup."""
    payload = json.loads(MANIFEST.read_text(encoding="utf-8"))
    family = next(family for family in payload["families"] if family["id"] == family_id)
    assert family == {
        "id": family_id,
        "owner_target": "_pytest-runtime-compatibility-admission",
        "node_kind": "pytest_node",
        "node": node,
        "full_gate_phase": "test",
    }
    owner = _target_stanza(compose_makefile(ROOT / "Makefile"), family["owner_target"])
    executable = "\n".join(line for line in owner.splitlines() if not line.startswith("#"))
    assert node in owner
    assert test_file in executable
    phase = next(
        phase for phase in payload["runtime_contract"]["phases"]
        if phase["target"] == family["owner_target"]
    )
    assert (phase["max_seconds"], phase["quiet_seconds"]) == (90, 60)
    assert 'NPROC=1 GLUDD_XDIST_WORKERS=1 PYTEST_ARGS="-W error -q -n 1"' in executable
    assert payload["full_gate_required"] is True


@pytest.mark.parametrize(
    ("family_id", "test_name"),
    [
        (
            "ee-isolated-metadata-prerequisite",
            "test_controller_augments_only_searxng_isolated_metadata_dependencies",
        ),
        (
            "ee-builder-installer-boundary",
            "test_generated_controller_build_preserves_installer_and_interpreter_boundaries",
        ),
        (
            "ee-owned-azure-sdk-pin-parity",
            "test_controller_azure_identity_has_one_explicit_owner",
        ),
        (
            "ee-owned-linux-transitive-pin-parity",
            "test_controller_cli_and_identity_share_supported_linux_transitives",
        ),
        (
            "ee-complete-requirement-resolution-contract",
            "test_resolve_requirements_uses_real_builder_assembly_and_bounded_uv_compile",
        ),
    ],
)
def test_ee_metadata_contracts_execute_before_hosted_image_construction(
    family_id: str, test_name: str,
) -> None:
    """Reuse the installed Builder with complete frozen-input acceptance files."""
    payload = json.loads(MANIFEST.read_text(encoding="utf-8"))
    node = f"tests/unit/test_ansible_runtime_artifacts.py::{test_name}"
    family = next(family for family in payload["families"] if family["id"] == family_id)
    assert family == {
        "id": family_id,
        "owner_target": "_pytest-runtime-compatibility-admission",
        "node_kind": "pytest_node",
        "node": node,
        "full_gate_phase": "test",
    }
    owner = _target_stanza(compose_makefile(ROOT / "Makefile"), family["owner_target"])
    executable = "\n".join(line for line in owner.splitlines() if not line.startswith("#"))
    assert node in owner
    for test_file in (
        "tests/unit/test_ansible_runtime_artifacts.py",
        "tests/unit/test_beta4_python_runtime_boundary.py",
        "tests/unit/test_ansible_searxng_source_inspection.py",
    ):
        assert test_file in executable
    phase = next(
        phase for phase in payload["runtime_contract"]["phases"]
        if phase["target"] == family["owner_target"]
    )
    assert (phase["max_seconds"], phase["quiet_seconds"]) == (90, 60)
    assert 'NPROC=1 GLUDD_XDIST_WORKERS=1 PYTEST_ARGS="-W error -q -n 1"' in executable
    assert payload["full_gate_required"] is True


@pytest.mark.parametrize(
    ("family_id", "test_name"),
    [
        (
            "integration-admission-plan-contract",
            "test_integration_admission_validate_only_prints_complete_plan",
        ),
        (
            "integration-admission-document-contract",
            "test_integration_admission_document_records_queue_evidence_and_boundaries",
        ),
    ],
)
def test_admission_plan_contracts_execute_before_hosted_shards(
    family_id: str, test_name: str,
) -> None:
    """Plan and documented bounds belong to the complete executable early cohort."""
    payload = json.loads(MANIFEST.read_text(encoding="utf-8"))
    node = f"tests/unit/test_integration_admission_target.py::{test_name}"
    family = next(family for family in payload["families"] if family["id"] == family_id)
    assert family == {
        "id": family_id,
        "owner_target": "_pytest-runtime-compatibility-admission",
        "node_kind": "pytest_node",
        "node": node,
        "full_gate_phase": "test",
    }
    owner = _target_stanza(compose_makefile(ROOT / "Makefile"), family["owner_target"])
    executable = "\n".join(line for line in owner.splitlines() if not line.startswith("#"))
    assert node in owner
    assert "tests/unit/test_integration_admission_target.py" in executable
    assert 'NPROC=1 GLUDD_XDIST_WORKERS=1 PYTEST_ARGS="-W error -q -n 1"' in executable
    phase = next(
        phase for phase in payload["runtime_contract"]["phases"]
        if phase["target"] == family["owner_target"]
    )
    assert (phase["max_seconds"], phase["quiet_seconds"]) == (90, 60)
    assert payload["full_gate_required"] is True


def test_ci_repair_evidence_forwarding_executes_in_complete_early_cohort() -> None:
    """The real Make argument transport must be proved before hosted fan-out."""
    payload = json.loads(MANIFEST.read_text(encoding="utf-8"))
    family = next(
        family for family in payload["families"]
        if family["id"] == "ci-repair-evidence-variable-forwarding"
    )
    node = (
        "tests/unit/test_ci_failure_ledger.py::"
        "test_make_ci_failure_repair_exports_quoted_values_to_cli_probe"
    )
    assert family == {
        "id": "ci-repair-evidence-variable-forwarding",
        "owner_target": "_pytest-runtime-compatibility-admission",
        "node_kind": "pytest_node",
        "node": node,
        "full_gate_phase": "test",
    }
    owner = _target_stanza(compose_makefile(ROOT / "Makefile"), family["owner_target"])
    executable = "\n".join(line for line in owner.splitlines() if not line.startswith("#"))
    assert node in owner
    assert "tests/unit/test_ci_failure_ledger.py" in executable
    assert 'NPROC=1 GLUDD_XDIST_WORKERS=1 PYTEST_ARGS="-W error -q -n 1"' in executable
    phase = next(
        phase for phase in payload["runtime_contract"]["phases"]
        if phase["target"] == family["owner_target"]
    )
    assert (phase["max_seconds"], phase["quiet_seconds"]) == (90, 60)
    assert payload["full_gate_required"] is True


@pytest.mark.parametrize(
    ("family_id", "node"),
    [
        ("automatic-proof-observation-ownership",
         "tests/unit/test_proof_observations.py::test_writer_derives_timestamp_and_never_admits_release"),
        ("automatic-proof-handoff-native-query-shape",
         "tests/unit/test_pipeline_status_exact_sha.py::test_pipeline_snapshot_accepts_the_existing_ledger_detail_shape"),
        ("make-search-literal-transport",
         "tests/unit/test_make_readonly_version.py::test_actual_search_never_evaluates_exported_make_function_data"),
        ("producer-current-capacity-proof-admission",
         "tests/unit/test_pipeline_status_exact_sha.py::test_pipeline_snapshot_current_capacity_selects_safe_next_action"),
    ],
)
def test_automatic_observation_contracts_execute_in_complete_early_cohorts(
    family_id: str, node: str,
) -> None:
    payload = json.loads(MANIFEST.read_text(encoding="utf-8"))
    family = next(family for family in payload["families"] if family["id"] == family_id)
    assert family == {
        "id": family_id, "owner_target": "_pytest-runtime-compatibility-admission",
        "node_kind": "pytest_node", "node": node, "full_gate_phase": "test",
    }
    owner = _target_stanza(compose_makefile(ROOT / "Makefile"), family["owner_target"])
    executable = "\n".join(line for line in owner.splitlines() if not line.startswith("#"))
    assert node in owner
    for test_file in (
        "tests/unit/test_proof_observations.py", "tests/unit/test_ci_await_exact.py",
        "tests/unit/test_pipeline_status_exact_sha.py", "tests/unit/test_task_ledger_validation.py",
        "tests/unit/test_make_readonly_version.py",
    ):
        assert test_file in executable
    phase = next(phase for phase in payload["runtime_contract"]["phases"]
                 if phase["target"] == family["owner_target"])
    assert (phase["max_seconds"], phase["quiet_seconds"]) == (90, 60)
    assert 'NPROC=1 GLUDD_XDIST_WORKERS=1 PYTEST_ARGS="-W error -q -n 1"' in executable
    assert payload["full_gate_required"] is True


@pytest.mark.parametrize(
    ("family_id", "node"),
    [
        (
            "hosted-shard-timing-diagnostics",
            "tests/unit/test_ci_run_summary_script.py::test_shard_timings_bind_all_eight_rows_to_one_exact_run_fetch",
        ),
        (
            "hosted-shard-creation-order",
            "tests/unit/test_ci_workflow_integrity.py::TestTestShardStructure::test_hosted_matrix_prioritizes_long_lanes_without_extra_runners",
        ),
    ],
)
def test_hosted_timing_contracts_execute_in_complete_early_cohorts(
    family_id: str, node: str,
) -> None:
    payload = json.loads(MANIFEST.read_text(encoding="utf-8"))
    family = next(family for family in payload["families"] if family["id"] == family_id)
    assert family == {
        "id": family_id, "owner_target": "_pytest-runtime-compatibility-admission",
        "node_kind": "pytest_node", "node": node, "full_gate_phase": "test",
    }
    owner = _target_stanza(compose_makefile(ROOT / "Makefile"), family["owner_target"])
    assert node in owner
    executable = "\n".join(line for line in owner.splitlines() if not line.startswith("#"))
    for test_file in (
        "tests/unit/test_ci_run_summary_script.py",
        "tests/unit/test_ci_run_summary_target.py",
        "tests/unit/test_ci_workflow_integrity.py",
    ):
        assert test_file in executable
    phase = next(phase for phase in payload["runtime_contract"]["phases"]
                 if phase["target"] == family["owner_target"])
    assert (phase["max_seconds"], phase["quiet_seconds"]) == (90, 60)
    assert 'NPROC=1 GLUDD_XDIST_WORKERS=1 PYTEST_ARGS="-W error -q -n 1"' in executable
    assert payload["full_gate_required"] is True


def test_private_help_regression_runs_before_hosted_unit_2() -> None:
    payload = json.loads(MANIFEST.read_text(encoding="utf-8"))
    node = (
        "tests/unit/test_makefile_targets_deep.py::TestInternalTargetConventions::"
        "test_internal_targets_not_in_help"
    )
    family = next(family for family in payload["families"]
                  if family["id"] == "make-private-target-help-visibility")
    assert family == {
        "id": "make-private-target-help-visibility",
        "owner_target": "_pytest-runtime-compatibility-admission",
        "node_kind": "pytest_node", "node": node, "full_gate_phase": "test",
    }
    owner = _target_stanza(compose_makefile(ROOT / "Makefile"), family["owner_target"])
    executable = "\n".join(line for line in owner.splitlines() if not line.startswith("#"))
    assert node in executable
    phase = next(phase for phase in payload["runtime_contract"]["phases"]
                 if phase["target"] == family["owner_target"])
    assert (phase["max_seconds"], phase["quiet_seconds"]) == (90, 60)
    assert 'NPROC=1 GLUDD_XDIST_WORKERS=1 PYTEST_ARGS="-W error -q -n 1"' in executable
    assert payload["full_gate_required"] is True


@pytest.mark.parametrize(
    ("family_id", "file", "node_name"),
    [
        (
            "stale-resource-packaged-artifact-identity", "tests/unit/test_clean_stale_resource_namespaces.py",
            "test_recorded_installed_module_is_not_a_runtime_lease_marker",
        ),
        (
            "native-artifact-literal-argument-transport", "tests/unit/test_native_temp_artifact_target.py",
            "test_native_temp_artifact_inspection_preserves_literal_path_data",
        ),
    ],
)
def test_artifact_identity_and_literal_transport_execute_before_fanout(
    family_id: str, file: str, node_name: str,
) -> None:
    payload = json.loads(MANIFEST.read_text(encoding="utf-8"))
    family = next(item for item in payload["families"] if item["id"] == family_id)
    assert family == {
        "id": family_id,
        "owner_target": "_pytest-runtime-compatibility-admission",
        "node_kind": "pytest_node", "node": f"{file}::{node_name}", "full_gate_phase": "test",
    }
    owner = _target_stanza(compose_makefile(ROOT / "Makefile"), family["owner_target"])
    executable = "\n".join(line for line in owner.splitlines() if not line.startswith("#"))
    assert file in executable
    assert 'NPROC=1 GLUDD_XDIST_WORKERS=1 PYTEST_ARGS="-W error -q -n 1"' in executable
    phase = next(item for item in payload["runtime_contract"]["phases"]
                 if item["target"] == family["owner_target"])
    assert (phase["max_seconds"], phase["quiet_seconds"]) == (90, 60)
    assert payload["full_gate_required"] is True


def _synthetic_repository(tmp_path: Path) -> tuple[Path, dict[str, object]]:
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    (tests_dir / "test_promoted.py").write_text(
        dedent(
            """
            class TestPromoted:
                def test_claim_fence(self):
                    pass
            """
        ),
        encoding="utf-8",
    )
    (tmp_path / "Makefile").write_text(
        dedent(
            """
            GATE_PREFLIGHT_TARGETS := _dead-code-baseline-refresh

            integration-admission:
            \t@set -eu; printf '{"kind":"integration_admission_phase","evidence_label":"%s%s"}'
            \t@$(UV) run python scripts/stream_command.py \
            \t\t--root ".gate-logs/observed" \
            \t\t--label "integration-admission-$$phase" --run-id "$$run_id" \
            \t\t--heartbeat-secs "10" --quiet-secs "$$quiet_seconds" \
            \t\t--max-secs "$$max_seconds" --retain-runs "20"
            \t@run_phase "check-gate-failure-promotions" \
            \t\t"check-gate-failure-promotions" "fast" "90" "60" \
            \t\t$(MAKE) --no-print-directory check-gate-failure-promotions
            \t@run_phase "_dead-code-baseline-refresh" \
            \t\t"_dead-code-baseline-refresh" "fast" "90" "60" \
            \t\t$(MAKE) --no-print-directory _dead-code-baseline-refresh
            \t@run_phase "project-dispatch-integration" \
            \t\t"_project-dispatch-integration" "slow" "600" "300" \
            \t\t$(MAKE) --no-print-directory _project-dispatch-integration

            check-gate-failure-promotions:
            \t@true

            _dead-code-baseline-refresh:
            \t@true

            _project-dispatch-integration:
            \t@echo tests/test_promoted.py::TestPromoted::test_claim_fence

            gate:
            \t@echo "=== GATE PHASE: preflights ==="
            \t@echo "=== GATE PHASE: test ==="
            \t@bash scripts/run_gate.sh
            """
        ).lstrip(),
        encoding="utf-8",
    )
    payload: dict[str, object] = {
        "schema_version": 1,
        "admission_target": "integration-admission",
        "full_gate_target": "gate",
        "full_gate_required": True,
        "runtime_contract": {
            "observer": "scripts/stream_command.py",
            "evidence_root": ".gate-logs/observed",
            "label_prefix": "integration-admission-",
            "heartbeat_seconds": 10,
            "max_retained_runs": 20,
            "phases": [
                {
                    "name": "check-gate-failure-promotions",
                    "target": "check-gate-failure-promotions",
                    "budget_class": "fast",
                    "max_seconds": 90,
                    "quiet_seconds": 60,
                },
                {
                    "name": "_dead-code-baseline-refresh",
                    "target": "_dead-code-baseline-refresh",
                    "budget_class": "fast",
                    "max_seconds": 90,
                    "quiet_seconds": 60,
                },
                {
                    "name": "project-dispatch-integration",
                    "target": "_project-dispatch-integration",
                    "budget_class": "slow",
                    "max_seconds": 600,
                    "quiet_seconds": 300,
                },
            ],
        },
        "families": [
            {
                "id": "dead-code-baseline-drift",
                "owner_target": "_dead-code-baseline-refresh",
                "node_kind": "make_target",
                "node": "_dead-code-baseline-refresh",
                "full_gate_phase": "preflights",
            },
            {
                "id": "claim-fence",
                "owner_target": "_project-dispatch-integration",
                "node_kind": "pytest_node",
                "node": "tests/test_promoted.py::TestPromoted::test_claim_fence",
                "full_gate_phase": "test",
            },
        ],
    }
    return tmp_path, payload


def test_validator_accepts_owned_make_and_pytest_nodes(tmp_path: Path) -> None:
    repository, payload = _synthetic_repository(tmp_path)

    assert _checker().validate_manifest(payload, repository_root=repository) == []


def test_validator_rejects_duplicate_missing_and_unwired_nodes(tmp_path: Path) -> None:
    repository, payload = _synthetic_repository(tmp_path)
    families = payload["families"]
    assert isinstance(families, list)
    broken = families[1]
    assert isinstance(broken, dict)
    broken["id"] = "dead-code-baseline-drift"
    broken["owner_target"] = "_missing-owner"
    broken["node"] = "tests/test_promoted.py::TestPromoted::test_missing"

    errors = _checker().validate_manifest(payload, repository_root=repository)

    assert any("duplicate family id" in error for error in errors)
    assert any("owner target is not defined: _missing-owner" in error for error in errors)
    assert any("pytest node does not exist" in error for error in errors)
    assert any("owner target is not wired into integration-admission" in error for error in errors)


def test_validator_rejects_schema_and_makefile_load_failures(tmp_path: Path) -> None:
    checker = _checker()

    schema_errors = checker.validate_manifest({}, repository_root=tmp_path)
    assert any("schema_version" in error for error in schema_errors)
    assert checker._pytest_node_exists("src/not-a-test.py::test_nope", tmp_path) is False

    valid_root = tmp_path / "valid"
    valid_root.mkdir()
    _, payload = _synthetic_repository(valid_root)
    missing_root = tmp_path / "missing"
    missing_root.mkdir()
    makefile_errors = checker.validate_manifest(payload, repository_root=missing_root)
    assert len(makefile_errors) == 1
    assert "Makefile composition failed" in makefile_errors[0]
    assert str(missing_root / "Makefile") in makefile_errors[0]


def test_validator_rejects_gate_and_owner_topology_drift(tmp_path: Path) -> None:
    repository, payload = _synthetic_repository(tmp_path)
    (repository / "Makefile").write_text(
        dedent(
            """
            integration-admission:
            \t@$(MAKE) --no-print-directory gate

            _dead-code-baseline-refresh:
            \t@true

            _project-dispatch-integration:
            \t@echo unrelated

            gate:
            \t@echo no-declared-phases
            """
        ).lstrip(),
        encoding="utf-8",
    )
    families = payload["families"]
    assert isinstance(families, list)
    make_family = families[0]
    pytest_family = families[1]
    assert isinstance(make_family, dict)
    assert isinstance(pytest_family, dict)
    make_family["node"] = "other-target"
    pytest_family["node"] = "tests/missing.py::test_missing"
    duplicate = deepcopy(pytest_family)
    duplicate["id"] = "second-pytest-family"
    families.append(duplicate)

    errors = _checker().validate_manifest(payload, repository_root=repository)

    for phrase in (
        "does not invoke the promotion checker",
        "must not invoke the full gate",
        "owner target is not wired into integration-admission",
        "full gate phase is not defined",
        "make node must equal its owner target",
        "make owner is absent from gate preflights",
        "pytest node is absent from its owner target",
        "pytest node does not exist",
        "full gate test runner is not wired",
        "duplicate promoted node",
    ):
        assert any(phrase in error for error in errors)

    missing_targets = deepcopy(payload)
    missing_targets["admission_target"] = "absent"
    missing_targets["full_gate_target"] = "absent"
    missing_errors = _checker().validate_manifest(
        missing_targets, repository_root=repository
    )
    assert any("admission target is not defined" in error for error in missing_errors)
    assert any("full gate target is not defined" in error for error in missing_errors)
    assert any("admission target must remain separate" in error for error in missing_errors)


def test_validator_rejects_runtime_budget_and_observer_drift(tmp_path: Path) -> None:
    repository, payload = _synthetic_repository(tmp_path)
    runtime = payload["runtime_contract"]
    assert isinstance(runtime, dict)
    phases = runtime["phases"]
    assert isinstance(phases, list)
    phase = phases[0]
    assert isinstance(phase, dict)
    phase["max_seconds"] = 91
    phase["quiet_seconds"] = 92
    runtime["label_prefix"] = "unattributed-"

    errors = _checker().validate_manifest(payload, repository_root=repository)

    assert any("quiet_seconds must not exceed max_seconds" in error for error in errors)
    assert any("runtime phase is not wired with its exact budget" in error for error in errors)
    assert any("observer contract is absent from integration admission" in error for error in errors)


def test_cli_rejects_missing_invalid_and_oversized_manifests(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    checker = _checker()
    missing = tmp_path / "missing.json"
    assert (
        checker.main(
            ["--manifest", str(missing), "--repository-root", str(ROOT)]
        )
        == 1
    )
    assert "cannot load manifest" in capsys.readouterr().err

    invalid = tmp_path / "invalid.json"
    invalid.write_text("{}", encoding="utf-8")
    assert (
        checker.main(
            ["--manifest", str(invalid), "--repository-root", str(ROOT)]
        )
        == 1
    )
    assert "schema_version" in capsys.readouterr().err

    oversized = tmp_path / "oversized.json"
    oversized.write_text(" " * 256_001, encoding="utf-8")
    with pytest.raises(ValueError, match="exceeds"):
        checker._load_manifest(oversized)


@pytest.mark.parametrize(
    ("family_id", "test_name"),
    [
        ("ee-complete-offline-build-acceptance",
         "test_acceptance_warms_all_controller_imports_with_owned_offline_containers"),
        ("ee-immutable-built-image-acceptance",
         "test_build_warms_captured_image_after_candidate_tag_can_be_retargeted"),
    ],
)
def test_full_ee_acceptance_executes_before_hosted_fanout(
    family_id: str, test_name: str,
) -> None:
    payload = json.loads(MANIFEST.read_text(encoding="utf-8"))
    family = next(family for family in payload["families"] if family["id"] == family_id)
    node = f"tests/unit/test_ansible_ee_full_warm_acceptance.py::{test_name}"
    assert family == {
        "id": family_id, "owner_target": "_pytest-runtime-compatibility-admission",
        "node_kind": "pytest_node", "node": node, "full_gate_phase": "test",
    }
    owner = _target_stanza(compose_makefile(ROOT / "Makefile"), family["owner_target"])
    executable = "\n".join(line for line in owner.splitlines() if not line.startswith("#"))
    assert node in owner
    assert node.split("::", 1)[0] in executable
    assert 'NPROC=1 GLUDD_XDIST_WORKERS=1 PYTEST_ARGS="-W error -q -n 1"' in executable
    phase = next(phase for phase in payload["runtime_contract"]["phases"]
                 if phase["target"] == family["owner_target"])
    assert (phase["max_seconds"], phase["quiet_seconds"]) == (90, 60)
    assert payload["full_gate_required"] is True


def test_byte_bounded_ci_context_executes_in_complete_early_cohort() -> None:
    payload = json.loads(MANIFEST.read_text(encoding="utf-8"))
    family = next(
        family for family in payload["families"]
        if family["id"] == "ci-failure-context-output-byte-bound"
    )
    node = (
        "tests/unit/test_ci_shards_log_context_bounds.py::"
        "test_cumulative_bound_preserves_source_order_and_declares_omitted_context"
    )
    assert family == {
        "id": "ci-failure-context-output-byte-bound",
        "owner_target": "_pytest-runtime-compatibility-admission",
        "node_kind": "pytest_node", "node": node, "full_gate_phase": "test",
    }
    owner = _target_stanza(compose_makefile(ROOT / "Makefile"), family["owner_target"])
    executable = "\n".join(line for line in owner.splitlines() if not line.startswith("#"))
    assert node in owner
    for path in ("tests/unit/test_ci_shards_log_context.py", node.split("::", 1)[0]):
        assert path in executable
    assert 'NPROC=1 GLUDD_XDIST_WORKERS=1 PYTEST_ARGS="-W error -q -n 1"' in executable
    phase = next(phase for phase in payload["runtime_contract"]["phases"]
                 if phase["target"] == family["owner_target"])
    assert (phase["max_seconds"], phase["quiet_seconds"]) == (90, 60)
    assert payload["full_gate_required"] is True


def test_private_policy_contract_runs_before_hosted_shard_fanout() -> None:
    payload = json.loads(MANIFEST.read_text(encoding="utf-8"))
    family = next(
        family for family in payload["families"]
        if family["id"] == "private-policy-hosted-matrix-membership"
    )
    node = (
        "tests/unit/test_self_improve_private_policy_ci_contract.py::"
        "test_private_policy_e2e_has_one_local_and_hosted_release_contract"
    )
    assert family == {
        "id": "private-policy-hosted-matrix-membership",
        "owner_target": "_pytest-runtime-compatibility-admission",
        "node_kind": "pytest_node", "node": node, "full_gate_phase": "test",
    }
    owner = _target_stanza(compose_makefile(ROOT / "Makefile"), family["owner_target"])
    executable = "\n".join(line for line in owner.splitlines() if not line.startswith("#"))
    assert node in owner
    assert node.split("::", 1)[0] in executable
    assert 'NPROC=1 GLUDD_XDIST_WORKERS=1 PYTEST_ARGS="-W error -q -n 1"' in executable
    phase = next(phase for phase in payload["runtime_contract"]["phases"]
                 if phase["target"] == family["owner_target"])
    assert (phase["max_seconds"], phase["quiet_seconds"]) == (90, 60)
    assert payload["full_gate_required"] is True


@pytest.mark.parametrize("kind", ["automatic-terminal-push-verdict", "all-required-ci-verdict"])
def test_verdict_history_contract_executes_before_hosted_fanout(kind: str) -> None:
    payload = json.loads(MANIFEST.read_text(encoding="utf-8"))
    nodes = {
        "automatic-terminal-push-verdict": (
            "tests/unit/test_pipeline_status_exact_sha.py::"
            "test_pipeline_terminal_snapshot_records_owned_push_verdict"
        ),
        "all-required-ci-verdict": (
            "tests/unit/test_ci_verdict_all_required.py::"
            "test_ci_verdict_safe_records_failure_without_recursive_make_exit_aliasing"
        ),
    }
    family = next(f for f in payload["families"] if f["id"] == kind)
    assert family == {
        "id": kind, "owner_target": "_pytest-runtime-compatibility-admission",
        "node_kind": "pytest_node", "node": nodes[kind], "full_gate_phase": "test",
    }
    owner = _target_stanza(compose_makefile(ROOT / "Makefile"), family["owner_target"])
    assert nodes[kind] in owner
    combined = owner.split("\t@$(MAKE) --no-print-directory test-files")[1]
    assert nodes[kind].split("::", 1)[0] in combined
    phase = next(p for p in payload["runtime_contract"]["phases"]
                 if p["target"] == family["owner_target"])
    assert (phase["max_seconds"], phase["quiet_seconds"]) == (90, 60)
    assert payload["full_gate_required"] is True


@pytest.mark.parametrize("kind", ["transient-error", "fallback-runtime"])
def test_search_scan_contract_executes_in_complete_early_cohort(kind: str) -> None:
    payload = json.loads(MANIFEST.read_text(encoding="utf-8"))
    family_id = f"make-search-{kind}-contract"
    names = {
        "transient-error": "test_search_target_succeeds_when_ripgrep_finds_match_before_transient_error",
        "fallback-runtime": "test_search_fallback_succeeds_when_match_precedes_scan_error",
    }
    family = next(f for f in payload["families"] if f["id"] == family_id)
    node = f"tests/unit/test_make_help_audit.py::{names[kind]}"
    assert family == {
        "id": family_id,
        "owner_target": "_pytest-runtime-compatibility-admission",
        "node_kind": "pytest_node", "node": node, "full_gate_phase": "test",
    }
    owner = _target_stanza(compose_makefile(ROOT / "Makefile"), family["owner_target"])
    assert node in owner
    combined = owner.split("\t@$(MAKE) --no-print-directory test-files")[1]
    assert "tests/unit/test_make_help_audit.py" in combined
    phase = next(phase for phase in payload["runtime_contract"]["phases"]
                 if phase["target"] == family["owner_target"])
    assert (phase["max_seconds"], phase["quiet_seconds"]) == (90, 60)
    assert payload["full_gate_required"] is True


def test_security_workflow_membership_executes_in_complete_early_cohort() -> None:
    payload = json.loads(MANIFEST.read_text(encoding="utf-8"))
    family = next(
        family for family in payload["families"]
        if family["id"] == "security-workflow-shard-membership"
    )
    node = (
        "tests/security/test_ci_workflow.py::"
        "TestGateMatrixStructure::test_test_shard_matrix_dimensions"
    )
    assert family == {
        "id": "security-workflow-shard-membership",
        "owner_target": "_pytest-runtime-compatibility-admission",
        "node_kind": "pytest_node", "node": node, "full_gate_phase": "test",
    }
    owner = _target_stanza(compose_makefile(ROOT / "Makefile"), family["owner_target"])
    assert node in owner
    combined = owner.split("\t@$(MAKE) --no-print-directory test-files")[1]
    assert "tests/security/test_ci_workflow.py" in combined
    phase = next(phase for phase in payload["runtime_contract"]["phases"]
                 if phase["target"] == family["owner_target"])
    assert (phase["max_seconds"], phase["quiet_seconds"]) == (90, 60)
    assert payload["full_gate_required"] is True


def test_duplicate_help_failure_runs_in_a_fresh_early_process() -> None:
    payload = json.loads(MANIFEST.read_text(encoding="utf-8"))
    family = next(
        family for family in payload["families"]
        if family["id"] == "make-help-duplicate-admission"
    )
    node = (
        "tests/unit/test_makefile_audit_deep.py::"
        "TestHelpNoDupTargets::test_no_duplicate_help_entries"
    )
    assert family == {
        "id": "make-help-duplicate-admission",
        "owner_target": "_pytest-runtime-compatibility-admission",
        "node_kind": "pytest_node", "node": node, "full_gate_phase": "test",
    }
    owner = _target_stanza(compose_makefile(ROOT / "Makefile"), family["owner_target"])
    recipes = owner.split("\t@$(MAKE) --no-print-directory test-files")
    assert len(recipes) == 3
    assert "tests/unit/test_makefile_audit_deep.py" not in recipes[1]
    assert f'TESTFILES="{node}"' in recipes[2]
    assert 'NPROC=1 GLUDD_XDIST_WORKERS=1 PYTEST_ARGS="-W error -q -n 1"' in recipes[2]
    phase = next(phase for phase in payload["runtime_contract"]["phases"]
                 if phase["target"] == family["owner_target"])
    assert (phase["max_seconds"], phase["quiet_seconds"]) == (90, 60)
    assert payload["full_gate_required"] is True


def test_repository_manifest_seeds_each_promoted_failure_family() -> None:
    payload = json.loads(MANIFEST.read_text(encoding="utf-8"))

    assert payload["full_gate_required"] is True
    assert [family["id"] for family in payload["families"]] == [
        "dead-code-baseline-drift",
        "coverage-gap-drift",
        "resource-ownership-drift",
        "claim-fence",
        "project-isolation",
        "concurrent-tick",
        "mcp-workspace-containment",
        "mcp-workspace-dispatch-jail",
        "module-graph-classification-drift",
        "ansible-role-variable-prefix-drift",
        "cloud-iam-resource-pruning-trigger",
        "cloud-iam-generation-parity",
        "pytest-class-fixture-scope",
        "hook-runtime-case-ast-portability",
        "hook-runtime-helper-ast-portability",
        "async-gate-canonical-profile-contract",
        "session-objective-ci-evidence",
        "session-handoff-git-identity",
        "gate-timeout-owned-zombie",
        "gate-timeout-term-escalation-zombie",
        "gate-timeout-replaced-receipt-zombie",
        "gate-lock-prerequisite-order",
        "threadpool-bulkhead-nonblocking-admission",
        "threadpool-bulkhead-worker-reservations",
        "enhancement-ratio-class-fixture-binding",
        "pytest-class-fixture-binding-invariant",
        "ee-isolated-metadata-prerequisite",
        "ee-builder-installer-boundary",
        "ee-owned-azure-sdk-pin-parity",
        "integration-admission-plan-contract",
        "integration-admission-document-contract",
        "ci-repair-evidence-variable-forwarding",
        "automatic-proof-observation-ownership",
        "automatic-proof-handoff-native-query-shape",
        "ee-owned-linux-transitive-pin-parity",
        "ee-complete-requirement-resolution-contract",
        "make-search-literal-transport",
        "hosted-shard-timing-diagnostics",
        "hosted-shard-creation-order",
        "make-private-target-help-visibility",
        "stale-resource-packaged-artifact-identity",
        "native-artifact-literal-argument-transport",
        "producer-current-capacity-proof-admission",
        "private-policy-hosted-matrix-membership",
        "ee-complete-offline-build-acceptance",
        "ee-immutable-built-image-acceptance",
        "ci-failure-context-output-byte-bound",
        "make-help-duplicate-admission",
        "security-workflow-shard-membership",
        "make-search-transient-error-contract",
        "make-search-fallback-runtime-contract",
        "automatic-terminal-push-verdict",
        "all-required-ci-verdict",
    ]
    families = {family["id"]: family for family in payload["families"]}
    assert families["mcp-workspace-containment"]["node"] == (
        "tests/unit/test_mcp_builtins_structural.py::TestBuiltinToolHandler::"
        "test_contain_workspace_escape_returns_none"
    )
    assert families["mcp-workspace-dispatch-jail"]["node"] == (
        "tests/unit/test_project_runner_tool.py::TestRunProjectCheckDispatch::"
        "test_workspace_escaping_jail_is_refused"
    )
    assert families["module-graph-classification-drift"]["node"] == (
        "tests/unit/test_module_graph_deep.py::test_all_subpackages_classified"
    )
    assert families["ansible-role-variable-prefix-drift"]["node"] == (
        "tests/unit/test_ansible_lint_deep.py::test_role_variables_use_namespaced_prefix"
    )
    assert families["cloud-iam-resource-pruning-trigger"]["node"] == (
        "tests/unit/test_cloud_role_generator.py::"
        "TestGenerateRoleFromTemplateWithResourceTypes::"
        "test_aws_prune_produces_warning_when_actions_removed"
    )
    assert families["cloud-iam-generation-parity"]["node"] == (
        "tests/unit/test_cloud_iam_expert.py::TestGenerateCloudRole::"
        "test_generate_aws_terraform_deploy"
    )
    assert families["coverage-gap-drift"]["node"] == "check-coverage-gaps"
    assert families["resource-ownership-drift"]["node"] == (
        "check-resource-ownership"
    )
    for family_id, node in (
        (
            "pytest-class-fixture-scope",
            "tests/unit/test_session_start_crash_recovery.py::"
            "TestCrashRecoveryStatic::test_session_state_has_pid_field",
        ),
        (
            "hook-runtime-case-ast-portability",
            "tests/unit/test_hook_runtime_split_compat.py::"
            "test_case_inventory_order_signatures_and_marks_match_pre_split_snapshot",
        ),
        (
            "hook-runtime-helper-ast-portability",
            "tests/unit/test_hook_runtime_split_compat.py::"
            "test_helper_signatures_match_pre_split_snapshot",
        ),
    ):
        assert families[family_id] == {
            "id": family_id,
            "owner_target": "_pytest-runtime-compatibility-admission",
            "node_kind": "pytest_node",
            "node": node,
            "full_gate_phase": "test",
        }
    runtime = payload["runtime_contract"]
    assert runtime["observer"] == "scripts/stream_command.py"
    assert runtime["max_retained_runs"] == 20
    assert [
        (
            phase["name"],
            phase["target"],
            phase["budget_class"],
            phase["max_seconds"],
            phase["quiet_seconds"],
        )
        for phase in runtime["phases"]
    ] == EXPECTED_RUNTIME_PHASES
    assert _checker().validate_manifest(payload, repository_root=ROOT) == []


def test_make_wiring_and_target_contract_are_explicit() -> None:
    makefile = compose_makefile(ROOT / "Makefile")
    admission = _target_stanza(makefile, "integration-admission")
    owner = _target_stanza(makefile, "_project-dispatch-integration")
    mcp_owner = _target_stanza(makefile, "_mcp-workspace-jail-integration")
    module_graph_owner = _target_stanza(makefile, "_module-graph-classification")
    variable_prefix_owner = _target_stanza(makefile, "_ansible-role-variable-prefix")
    cloud_iam_owner = _target_stanza(makefile, "_cloud-iam-generation-parity")
    runtime_compatibility_owner = _target_stanza(
        makefile, "_pytest-runtime-compatibility-admission"
    )

    assert "check-gate-failure-promotions" in makefile.split("help:", 1)[0]
    assert (
        "check-gate-failure-promotions  Validate owned fast-admission failure nodes"
        in makefile
    )
    assert (
        admission.index('run_phase "worktree-guard"')
        < admission.index('run_phase "check-gate-failure-promotions"')
        < admission.index('run_phase "pytest-runtime-compatibility"')
        < admission.index('run_phase "ansible-role-variable-prefix"')
        < admission.index('run_phase "cloud-iam-generation-parity"')
        < admission.index('run_phase "_dead-code-baseline-refresh"')
        < admission.index('run_phase "check-coverage-gaps"')
        < admission.index('run_phase "check-resource-ownership"')
    )
    assert "$(MAKE) --no-print-directory _project-dispatch-integration;" in admission
    assert "$(MAKE) --no-print-directory _mcp-workspace-jail-integration;" in admission
    assert "$(MAKE) --no-print-directory _module-graph-classification;" in admission
    assert "$(MAKE) --no-print-directory _ansible-role-variable-prefix;" in admission
    assert "$(MAKE) --no-print-directory _cloud-iam-generation-parity;" in admission
    assert (
        "$(MAKE) --no-print-directory _pytest-runtime-compatibility-admission;"
        in admission
    )
    assert (
        'GATE_FAILURE_PROMOTION_MANIFEST="$(GATE_FAILURE_PROMOTION_MANIFEST)"'
        in admission
    )
    for fragment in (
        "scripts/stream_command.py",
        '--root ".gate-logs/observed"',
        '--label "integration-admission-$$phase"',
        '--run-id "$$run_id"',
        '--heartbeat-secs "10"',
        '--quiet-secs "$$quiet_seconds"',
        '--max-secs "$$max_seconds"',
        '--retain-runs "20"',
    ):
        assert fragment in admission
    for node in (
        "test_event_loop_dispatch_includes_project_id",
        "test_dispatch_job_contains_only_project_data",
        "test_event_loop_serializes_concurrent_ticks",
    ):
        assert node in owner
    for node in (
        "test_contain_workspace_escape_returns_none",
        "test_workspace_escaping_jail_is_refused",
    ):
        assert node in mcp_owner
    assert "test_all_subpackages_classified" in module_graph_owner
    assert "test_role_variables_use_namespaced_prefix" in variable_prefix_owner
    prune_node = "test_aws_prune_produces_warning_when_actions_removed"
    full_role_node = "test_generate_aws_terraform_deploy"
    assert prune_node in cloud_iam_owner
    assert full_role_node in cloud_iam_owner
    assert cloud_iam_owner.index(prune_node) < cloud_iam_owner.index(full_role_node)
    assert 'PYTEST_ARGS="-W error -q -n 0"' in cloud_iam_owner
    assert "$(MAKE) --no-print-directory test-files" in runtime_compatibility_owner
    assert (
        'TESTFILES="tests/unit/test_session_start_crash_recovery.py '
        'tests/unit/test_hook_runtime_split_compat.py tests/unit/test_gate_async.py '
        'tests/unit/test_session_persistence_integration.py '
        'tests/unit/test_enforce_objective_plugin.py::TestSESSIONmdIntegration::'
        'test_primary_objective_mentions_ci tests/unit/test_start_gate_background.py '
        'tests/unit/test_gate_run_lock.py tests/unit/test_bulkhead_deep.py '
        'tests/unit/test_bulkhead_admission.py '
        'tests/unit/test_enhancement_ratio_plugin.py '
        'tests/unit/test_pytest_param_fixture_collisions.py '
        'tests/unit/test_ansible_runtime_artifacts.py '
        'tests/unit/test_ansible_ee_full_warm_acceptance.py '
        'tests/unit/test_beta4_python_runtime_boundary.py '
        'tests/unit/test_ansible_searxng_source_inspection.py '
        'tests/unit/test_integration_admission_target.py '
        'tests/unit/test_ci_failure_ledger.py '
        'tests/unit/test_proof_observations.py tests/unit/test_ci_await_exact.py '
        'tests/unit/test_ci_verdict_all_required.py '
        'tests/unit/test_pipeline_status_exact_sha.py tests/unit/test_task_ledger_validation.py '
        'tests/unit/test_make_readonly_version.py '
        'tests/unit/test_make_help_audit.py '
        'tests/unit/test_clean_stale_resource_namespaces.py '
        'tests/unit/test_native_temp_artifact_target.py '
        'tests/unit/test_ci_run_summary_script.py '
        'tests/unit/test_ci_run_summary_target.py '
        'tests/unit/test_ci_workflow_integrity.py '
        'tests/security/test_ci_workflow.py '
        'tests/unit/test_self_improve_private_policy_ci_contract.py '
        'tests/unit/test_ci_shards_log_context.py '
        'tests/unit/test_ci_shards_log_context_bounds.py '
        'tests/unit/test_makefile_targets_deep.py::TestInternalTargetConventions::'
        'test_internal_targets_not_in_help"'
        in runtime_compatibility_owner
    )
    assert "-W error" in runtime_compatibility_owner
    assert "-n 1" in runtime_compatibility_owner

    contracts = json.loads(CONTRACT.read_text(encoding="utf-8"))["targets"]
    check_contract = next(
        item for item in contracts if item["name"] == "check-gate-failure-promotions"
    )
    assert check_contract == {
        "name": "check-gate-failure-promotions",
        "make_variables": ["GATE_FAILURE_PROMOTION_MANIFEST"],
        "behavior": (
            "make check-gate-failure-promotions "
            "GATE_FAILURE_PROMOTION_MANIFEST=config/gate_failure_promotions.json"
        ),
    }
    admission_contract = next(
        item for item in contracts if item["name"] == "integration-admission"
    )
    assert "GATE_FAILURE_PROMOTION_MANIFEST" in admission_contract["make_variables"]
    assert (
        "GATE_FAILURE_PROMOTION_MANIFEST=config/gate_failure_promotions.json"
        in admission_contract["behavior"]
    )


def test_target_and_feature_document_preserve_full_gate_boundary() -> None:
    result = subprocess.run(
        [
            "make",
            "check-gate-failure-promotions",
            "GATE_FAILURE_PROMOTION_MANIFEST=config/gate_failure_promotions.json",
        ],
        cwd=ROOT,
        capture_output=True,
        check=False,
        text=True,
        timeout=30,
    )
    content = FEATURE_DOC.read_text(encoding="utf-8")

    assert result.returncode == 0, result.stdout + result.stderr
    # The exact family roster is pinned above; the CLI must report that roster,
    # including newly mandated early regressions, without hard-coding an old size.
    family_count = len(json.loads(MANIFEST.read_text(encoding="utf-8"))["families"])
    assert f"gate-failure-promotions: PASS families={family_count} " in result.stdout
    assert "runtime_phases=21" in result.stdout
    assert "runtime_ceiling_seconds=2850" in result.stdout
    for phrase in (
        "dead-code-baseline-drift",
        "coverage-gap-drift",
        "resource-ownership-drift",
        "claim-fence",
        "project-isolation",
        "concurrent-tick",
        "mcp-workspace-containment",
        "mcp-workspace-dispatch-jail",
        "module-graph-classification-drift",
        "ansible-role-variable-prefix-drift",
        "cloud-iam-resource-pruning-trigger",
        "cloud-iam-generation-parity",
        "pytest-class-fixture-scope",
        "hook-runtime-case-ast-portability",
        "hook-runtime-helper-ast-portability",
        "exact full gate remains mandatory",
        "https://github.com/orgs/community/discussions/41726",
        "https://github.com/modelcontextprotocol/servers/issues/1838",
        "https://github.com/seddonym/import-linter/issues/93",
    ):
        assert phrase in content
