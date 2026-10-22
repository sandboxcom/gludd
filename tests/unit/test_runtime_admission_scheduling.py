"""Native contracts for bounded runtime admission and fresh help isolation."""

from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import sys
from collections import Counter
from copy import deepcopy
from pathlib import Path

import pytest
from scripts import makefile_layout

from tests.unit.test_observed_command import _command, _current_status

# Independent expected inventory: never derive this value from the manifest under test.
EXPECTED_FAMILY_COUNT = 153
ROOT = Path(makefile_layout.__file__).resolve().parents[1]
OWNER = "_pytest-runtime-compatibility-admission"
EXPECTED_MAIN_PYTEST_FLAGS = (
    f"-W error -q -n 2 --maxprocesses 2 --ignore-glob={ROOT / 'tests' / '*'} "
    "-o verbosity_test_cases=-2 --durations=20 --durations-min=0"
)
HELP = "tests/unit/test_makefile_audit_deep.py::TestHelpNoDupTargets::test_no_duplicate_help_entries"
APPROVED_PROMOTION_SELECTORS = (
    "tests/unit/test_env_var_audit_deep.py::TestGLUDDNamingConvention::test_no_trailing_underscore",
    "tests/unit/test_env_var_audit_deep.py::TestGLUDDDocumentation::test_config_reference_documents_live_vars",
    "tests/unit/test_env_var_audit_deep.py::TestGLUDDDefaultConsistency::test_default_values_consistent",
    "tests/unit/test_backlog_audit_target.py::test_backlog_target_is_public_registered_and_documented",
    "tests/unit/test_canonical_release_gate.py::test_actual_local_release_recipe_collects_later_failures_in_one_invocation",
    "tests/unit/test_collection_hook_environment.py::"
    "test_real_pre_push_collection_isolates_only_native_git_local_environment",
)
PLUGIN_ISOLATION_SELECTORS = (
    "tests/unit/test_subagent_output_clean.py::"
    "TestSubagentOutputClean::test_all_tool_execute_before_hooks_have_subagent_guard",
    "tests/unit/test_no_idle_monitoring_plugin.py::test_fallback_subagent_guard_precedes_delegated_handler",
)
PRESENTATION_MANIFEST_SELECTOR = (
    "tests/unit/test_presentation_browser_runner.py::"
    "test_javascript_viewport_child_reads_the_actual_producer_manifest"
)
ROSTER = (
    "tests/unit/test_ci_batch_receipts.py::"
    "test_receipt_validation_refuses_unsafe_artifact_mode[2432]",
    *APPROVED_PROMOTION_SELECTORS,
    "tests/unit/test_content_addressed_unit_lane.py::test_atomic_json_candidate_and_control_evidence",
    "tests/unit/test_owned_checkpoint_shared_worktree_admission.py::"
    "test_main_checkpoint_passes_peer_native_guard_with_all_saved_bytes_intact",
    "tests/unit/test_precommit_quality_guards.py::"
    "test_real_project_make_refuses_engine_expression_before_parse_or_export_execution",
    "tests/unit/test_session_start_crash_recovery.py",
    "tests/unit/test_hook_runtime_split_compat.py",
    "tests/unit/test_gate_async.py",
    "tests/unit/test_session_persistence_integration.py",
    "tests/unit/test_enforce_objective_plugin.py::TestSESSIONmdIntegration::test_primary_objective_mentions_ci",
    "tests/unit/test_start_gate_background.py",
    "tests/unit/test_gate_run_lock.py",
    "tests/unit/test_bulkhead_deep.py",
    "tests/unit/test_bulkhead_admission.py",
    "tests/unit/test_enhancement_ratio_plugin.py",
    "tests/unit/test_pytest_param_fixture_collisions.py",
    "tests/unit/test_ansible_runtime_artifacts.py",
    "tests/unit/test_ansible_ee_full_warm_acceptance.py",
    "tests/unit/test_ansible_ee_searx_acceptance.py",
    "tests/unit/test_beta4_python_runtime_boundary.py",
    "tests/unit/test_ansible_searxng_source_inspection.py",
    "tests/unit/test_integration_admission_target.py",
    "tests/unit/test_ci_failure_ledger.py",
    "tests/unit/test_ci_failure_ledger_concurrency.py",
    "tests/unit/test_proof_observations.py",
    "tests/unit/test_ci_await_exact.py",
    "tests/unit/test_ci_verdict_all_required.py",
    "tests/unit/test_ci_verdict_structural_repair.py",
    "tests/unit/test_secrets_baseline_compaction.py",
    "tests/unit/test_pipeline_status_exact_sha.py",
    "tests/unit/test_pipeline_status_job_progress.py",
    "tests/unit/test_ci_cancel_contract.py",
    "tests/unit/test_release_proof_admission_target.py",
    "tests/unit/test_replace_lines_make_target.py",
    "tests/unit/test_native_line_edit_targets.py",
    "tests/unit/test_trace_summary_make_inputs.py::"
    "test_summary_public_inputs_never_execute_make_or_shell_data[backticks-LOG-False]",
    "tests/unit/test_cache_resource_policy.py::"
    "test_prohibited_home_roots_refuse_before_measurement[library-caches-inventory]",
    "tests/unit/test_run_watched_trace.py::"
    "test_native_trace_option_preserves_default_off_and_exact_on_argv[enabled]",
    "tests/unit/test_observability_guardrails.py::test_disk_and_native_temp_owner_keep_largest_entries[disk]",
    "tests/unit/test_ci_named_shard_files.py::"
    "test_coverage_files_exports_physical_checkout_to_changed_cwd_children[False-command-line]",
    "tests/unit/test_watchdog_secrets_lifecycle.py::"
    "test_native_scan_owns_terminal_receipt_and_descendants[timeout-124-timed_out]",
    "tests/unit/test_watchdog.py::test_watchdog_fixture_confines_command_workspace",
    "tests/unit/test_task_watchdog.py::test_terminal_status_cap_preserves_native_gate_protection[absent-64]",
    "tests/unit/test_clean_ci_shard_scratch.py::test_stale_scratch_nested_symlink_preserves_external_target_modes[file]",
    "tests/unit/test_detect_secrets_full_scan.py::"
    "test_successful_full_scan_rejects_added_identity_or_occurrence[new-digest]",
    "tests/unit/test_audit_coverage_suite_provenance.py::"
    "test_imported_cobertura_never_proves_e2e_execution",
    "tests/unit/test_make_uv_cache_sandbox.py::test_native_prune_preserves_real_child_status[native-failure]",
    "tests/unit/test_make_uv_cache_sandbox.py::test_native_prune_target_policy_refuses_invalid[UV_CACHE_PRUNE_TIMEOUT=0]",
    "tests/unit/test_status_heartbeat_terminal.py",
    "tests/unit/test_task_ledger_validation.py",
    "tests/unit/test_make_readonly_version.py",
    "tests/unit/test_make_help_audit.py",
    "tests/unit/test_clean_stale_resource_namespaces.py",
    "tests/unit/test_native_temp_artifact_target.py",
    "tests/unit/test_ci_run_summary_script.py",
    "tests/unit/test_ci_run_summary_target.py",
    "tests/unit/test_ci_workflow_integrity.py",
    "tests/unit/test_plugin_manifest_deep.py::TestHookValidity::test_no_duplicate_hooks_within_single_plugin",
    "tests/unit/test_plugin_session_start_deadlock.py::TestSessionStartNoDeadlock::"
    "test_no_plugin_denies_reads_at_session_start",
    "tests/unit/test_process_overhead.py::TestProcessTokenOverhead::test_anti_stop_plugins_are_lean",
    "tests/unit/test_enforcement_executable_modes.py::test_enforcement_runtime_surfaces_have_executable_git_mode",
    *PLUGIN_ISOLATION_SELECTORS,
    "tests/unit/test_v011_exact_gate_repair_contract.py::test_all_six_exact_gate_repairs_remain_green_together",
    "tests/unit/test_secrets_scan_resources.py",
    "tests/unit/test_css_lint_target.py::test_lint_css_dependency_config_and_contract_are_exactly_pinned",
    "tests/unit/test_gate_status_attestation.py::"
    "test_repository_identity_batches_only_fresh_metadata_with_equivalent_digests",
    "tests/unit/test_ci_stylelint_workflow.py",
    "tests/security/test_ci_workflow.py",
    "tests/unit/test_workflow_safe_parser.py",
    "tests/unit/test_self_improve_private_policy_ci_contract.py",
    "tests/unit/test_ci_shards_log_context.py",
    "tests/unit/test_ci_shards_log_context_bounds.py",
    "tests/unit/test_makefile_syntax.py",
    "tests/unit/test_makefile_layout.py::test_each_invocation_reads_each_validated_source_once",
    "tests/unit/test_makefile_layout.py::"
    "test_composition_emits_validated_bytes_and_revalidates_next_invocation",
    "tests/unit/test_makefile_targets_deep.py",
    "tests/unit/test_azure_containerapp_coverage_target.py",
    "tests/unit/test_ee_owned_collection_runtime_metadata.py",
    "tests/unit/test_no_sleep_subagent.py",
    "tests/unit/test_ansible_ee_locked_consumer.py",
    "tests/unit/test_native_builder_optional_formats.py",
    "tests/unit/test_native_format_profile_sync_target.py",
    "tests/unit/test_ansible_ee_locked_consumer_admission.py",
    "tests/unit/test_release_asset_matrix.py",
    "tests/unit/test_release_ee_collection_closure.py",
    "tests/unit/test_mcp_topics_manifest.py::test_independent_topic_sizing_has_one_native_dump_per_topic",
    "tests/unit/test_mcp_tool_gen.py",
    "tests/unit/test_observed_command.py",
    "tests/unit/test_observer_retention_concurrency.py",
    "tests/unit/test_ci_collect_all_stress.py",
    "tests/unit/test_admission_cold_start_selection.py::test_real_admission_filters_only_never_selected_main_siblings",
    PRESENTATION_MANIFEST_SELECTOR,
    "tests/unit/test_presentation_make_targets.py::"
    "test_native_browser_preserves_unset_empty_and_explicit_worker_environment"
    "[1-command-line-None-NPROC]",
    "tests/unit/test_presentation_make_targets.py::"
    "test_native_browser_preserves_unset_empty_and_explicit_worker_environment"
    "[1-command-line-None-GLUDD_XDIST_WORKERS]",
    "tests/unit/test_markdown_docs_deep.py::test_all_internal_links_resolve",
    "tests/unit/test_markdown_docs_deep.py::test_relative_links_do_not_escape_repo",
    "tests/unit/test_branch_discipline_checks.py::TestWorktreeCleanupAfterMerge::test_agent_cu_removes_worktree",
    "tests/unit/test_branch_discipline_checks.py::"
    "TestWorktreeCleanupAfterMerge::test_agent_cu_preserves_branch_and_release_evidence",
    "tests/unit/test_execution_adapter_protocols.py::"
    "test_core_protocols_do_not_claim_data_member_validation",
    "tests/unit/test_status_snapshot.py::TestStatusSnapshot::"
    "test_session_md_has_markers",
    "tests/unit/test_status_snapshot.py::TestStatusSnapshot::"
    "test_validation_refuses_invalid_session_structure_without_writes",
    "tests/unit/test_status_snapshot.py::TestStatusSnapshot::"
    "test_repeated_native_rewrite_keeps_one_header_and_surrounding_handoff[absent]",
    "tests/unit/test_active_work_status.py::"
    "test_process_inventory_does_not_promote_resource_directory_names[pytest]",
    "tests/unit/test_agent_watchdog_lifecycle.py::"
    "test_native_sigterm_during_acquisition_releases_the_actual_lease[fsync]",
    "tests/unit/test_agent_watchdog_lifecycle.py::"
    "test_native_start_acknowledges_one_child_and_stop_drains",
    "tests/unit/test_agent_worktree_targets.py::TestAgentWorktreeRecipeShape::"
    "test_agent_cleanup_delegates_to_registered_native_retirement",
    "tests/unit/test_agent_worktree_targets.py::TestAgentWorktreeLifecycle::"
    "test_agent_cleanup_removes_worktree[False]",
    "tests/unit/test_task_watchdog.py::"
    "test_watchdog_absence_refusal_names_the_exact_pid_and_cwd[True]",
    "tests/unit/test_active_work_status.py::"
    "test_process_inventory_preserves_python_module_argument_forms",
    "tests/unit/test_code_intelligence_import_boundary.py::"
    "test_extractor_import_does_not_load_introspection_or_database",
    "tests/unit/test_list_files_target.py::test_listing_missing_input_preserves_native_failure",
    "tests/unit/test_import_time_deep.py::test_measurement_contract_parses_native_numeric_rows",
    "tests/unit/test_import_time_deep.py::test_measurement_contract_rejects_failed_children",
    "tests/unit/test_import_time_deep.py::test_measurement_contract_consumers_fail_for_missing_data",
)


REVIEWED_FINALIZATION = (
    "tests/unit/test_reviewed_head_inputs.py::test_historical_focus_binds_candidate_adapter_blobs_not_inspecting_files",
    "tests/unit/test_reviewed_head_inputs.py::test_finalization_accepts_actual_native_background_producer_command",
    "tests/unit/test_reviewed_head_inputs.py::test_native_focus_commands_preserve_watchdog_ownership",
    "tests/unit/test_task_watchdog.py::test_watchdog_destructive_boundary_requires_registered_ownership",
    "tests/unit/test_task_watchdog.py::test_watchdog_handoff_confirms_exit_after_metadata_changes",
    "tests/unit/test_task_watchdog.py::test_native_watchdog_start_requires_child_acknowledgement",
    "tests/unit/test_task_watchdog.py::test_native_start_serializes_existing_acknowledgement",
    "tests/unit/test_task_watchdog.py::test_unit_watchdog_fault_logs_belong_to_the_test_namespace",
    "tests/unit/test_dependency_profiles.py::test_selected_profile_locks_preserve_controller_jsonpointer_requirement",
)


def _stanza(target: str) -> str:
    composed: str = makefile_layout.compose_makefile(ROOT / "Makefile")
    return composed.split(f"\n{target}:\n", 1)[1].split("\n\n", 1)[0]


def _capture_owner(tmp_path: Path, fail_phase: str) -> tuple[subprocess.CompletedProcess[str], list[dict[str, object]]]:
    """Execute the actual Make stanza with only its child test-files owner replaced."""
    record = tmp_path / "calls.jsonl"
    recorder = tmp_path / "record.py"
    recorder.write_text(
        "import json, os, sys\n"
        "from pathlib import Path\n"
        "files, nproc, workers, flags = sys.argv[1:]\n"
        f"phase = 'help' if files == {HELP!r} else (\n"
        f"    'reviewed' if files.split() == {list(REVIEWED_FINALIZATION)!r} else 'full')\n"
        "row = dict(files=files.split(), nproc=nproc, workers=workers, flags=flags, phase=phase, pid=os.getpid())\n"
        f"with Path({str(record)!r}).open('a') as stream: stream.write(json.dumps(row) + '\\n')\n"
        f"raise SystemExit(1 if phase == {fail_phase!r} else 0)\n",
        encoding="utf-8",
    )
    child = tmp_path / "child.mk"
    child.write_text(
        ".PHONY: test-files\ntest-files:\n"
        f'\t@"{sys.executable}" "{recorder}" "$(TESTFILES)" "$(NPROC)" '
        '"$(GLUDD_XDIST_WORKERS)" "$(PYTEST_ARGS)"\n',
        encoding="utf-8",
    )
    owner = tmp_path / "owner.mk"
    owner.write_text(
        f"MAKE := make --no-print-directory -f {child}\n.PHONY: {OWNER}\n{OWNER}:\n{_stanza(OWNER)}\n",
        encoding="utf-8",
    )
    result = subprocess.run(
        ["make", "--no-print-directory", "-f", str(owner), OWNER],
        cwd=ROOT, text=True, capture_output=True, timeout=10,
    )
    rows = [json.loads(line) for line in record.read_text(encoding="utf-8").splitlines()]
    return result, rows


def test_runtime_admission_retains_entire_roster_and_fresh_help(tmp_path: Path) -> None:
    result, rows = _capture_owner(tmp_path, "")
    assert result.returncode == 0, result.stdout + result.stderr
    assert [row["files"] for row in rows] == [list(ROSTER), [HELP], list(REVIEWED_FINALIZATION)]
    assert [row["phase"] for row in rows] == ["full", "help", "reviewed"]
    assert len({row["pid"] for row in rows}) == 3
    assert [(row["nproc"], row["workers"]) for row in rows] == [("2", "2"), ("1", "1"), ("1", "1")]
    assert "-o verbosity_test_cases=-2" in str(rows[0]["flags"])
    assert "verbosity_test_cases" not in str(rows[1]["flags"])
    for row, workers, limit in zip(rows, (2, 0, 0), (2, 1, 1), strict=True):
        flags = str(row["flags"])
        assert "-W error" in flags
        assert f"-n {workers}" in flags
        assert f"--maxprocesses {limit}" in flags
        assert not any(flag in flags for flag in ("--lf", "--ff", "--collect-only", "-W ignore", "-k "))
    test_files = _stanza("test-files")
    assert '--basetemp="$$BT"' in test_files
    assert 'ANSIBLE_LOCAL_TEMP="$$BT/ansible-local"' in test_files
    assert 'export VIRTUAL_ENV="$$UV_PROJECT_ENVIRONMENT"' in test_files
    assert "verbosity_test_cases" not in test_files
    assert "--dist loadgroup" in makefile_layout.compose_makefile(ROOT / "Makefile")


@pytest.mark.parametrize(
    ("fail_phase", "expected_phases"),
    [("full", ["full"]), ("help", ["full", "help"]), ("reviewed", ["full", "help", "reviewed"])],
)
def test_runtime_admission_propagates_each_owner_failure_without_replay(
    tmp_path: Path, fail_phase: str, expected_phases: list[str],
) -> None:
    result, rows = _capture_owner(tmp_path, fail_phase)
    assert result.returncode != 0
    assert "Error 1" in result.stderr
    assert [row["phase"] for row in rows] == expected_phases


def _native_test_files(
    paths: list[Path], workers: int, *, environment: dict[str, str] | None = None,
    extra_pytest_args: str = "",
) -> subprocess.CompletedProcess[str]:
    flags = f"-W error -q -n {workers} --maxprocesses {workers}"
    if extra_pytest_args:
        flags += " " + extra_pytest_args
    return subprocess.run(
        [
            "make", "--no-print-directory", "test-files",
            "TESTFILES=" + " ".join(str(path) for path in paths),
            f"NPROC={workers}", f"GLUDD_XDIST_WORKERS={workers}",
            "PYTEST_ARGS=" + flags,
            "UV_PROJECT_ENVIRONMENT=" + os.environ.get("VIRTUAL_ENV", str(ROOT / ".venv")),
        ],
        cwd=ROOT, text=True, capture_output=True, timeout=45, env=environment,
    )


def test_existing_group_owner_isolates_shared_state_and_preserves_exact_once_outcomes(tmp_path: Path) -> None:
    """Exercise real xdist with the repository's existing shared-state grouping hook."""
    (tmp_path / "conftest.py").write_text(
        "from tests.conftest import pytest_collection_modifyitems\n", encoding="utf-8",
    )
    outcomes: list[Counter[str]] = []
    for workers in (1, 2):
        record = tmp_path / f"events-{workers}.jsonl"
        shared = tmp_path / "test_shared.py"
        independent = tmp_path / "test_independent.py"
        helper = (
            "import json, os, time\nfrom pathlib import Path\nimport pytest\n"
            "def record(name):\n"
            "    with Path(os.environ['GLUDD_RUNTIME_SCHEDULING_RECORD']).open('a') as stream:\n"
            "        row = dict(name=name, pid=os.getpid(), worker=os.environ['PYTEST_XDIST_WORKER'])\n"
            "        stream.write(json.dumps(row) + '\\n')\n"
        )
        lock = tmp_path / f"shared-{workers}.lock"
        shared.write_text(
            helper + "# Existing hook detects hardcoded shared state: /tmp/gludd-admission-fixture\n"
            "@pytest.mark.parametrize('case', range(4))\ndef test_shared(case):\n"
            f"    lock = Path({str(lock)!r})\n"
            "    with lock.open('x'):\n        time.sleep(0.03)\n        record('shared-' + str(case))\n"
            "    lock.unlink()\n",
            encoding="utf-8",
        )
        independent.write_text(
            helper + "@pytest.mark.parametrize('case', range(4))\ndef test_independent(case):\n"
            "    time.sleep(0.03)\n    record('independent-' + str(case))\n",
            encoding="utf-8",
        )
        result = _native_test_files(
            [shared, independent], workers,
            environment={**os.environ, "GLUDD_RUNTIME_SCHEDULING_RECORD": str(record)},
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert "8 passed" in result.stdout
        rows = [json.loads(line) for line in record.read_text(encoding="utf-8").splitlines()]
        outcomes.append(Counter(row["name"] for row in rows))
        assert set(outcomes[-1].values()) == {1}
        assert len({row["worker"] for row in rows if row["name"].startswith("shared-")}) == 1
        assert len({row["worker"] for row in rows}) == workers
    assert outcomes[0] == outcomes[1]


def test_two_worker_native_admission_keeps_warnings_fatal(tmp_path: Path) -> None:
    probe = tmp_path / "test_warning.py"
    probe.write_text(
        "import warnings\ndef test_original_warning():\n"
        "    warnings.warn('runtime-admission-warning', UserWarning)\n",
        encoding="utf-8",
    )
    result = _native_test_files([probe], 2)
    assert result.returncode != 0
    assert "FAILED" in result.stdout and "test_original_warning" in result.stdout
    assert "UserWarning: runtime-admission-warning" in result.stdout
    assert re.search(r"\b1 failed\b", result.stdout)


@pytest.mark.parametrize("mode", ("success", "assertion", "warning", "setup", "import", "missing"))
def test_compact_native_reporting_preserves_inventory_failures_and_terminal_totals(tmp_path: Path, mode: str) -> None:
    """Change only native per-case verbosity in otherwise identical two-worker children."""
    (tmp_path / "conftest.py").write_text(
        "import os\nfrom pathlib import Path\nimport pytest\n"
        "from tests.conftest import pytest_collection_modifyitems\n"
        "def pytest_configure(config):\n"
        "    assert config.rootpath.resolve() == Path(__file__).resolve().parent\n"
        "    assert Path(config.getoption('confcutdir')).resolve() == Path(__file__).resolve().parent\n"
        "    if not hasattr(config, 'workerinput'):\n"
        "        assert config.get_verbosity() == 1\n"
        "        expected = -2 if os.environ['GLUDD_COMPACT_REPORT_VARIANT'] == 'compact' else 1\n"
        "        assert config.get_verbosity(pytest.Config.VERBOSITY_TEST_CASES) == expected\n"
        "@pytest.fixture(scope='module')\ndef compact_parent():\n"
        "    if os.environ['GLUDD_COMPACT_REPORT_MODE'] == 'setup':\n"
        "        raise RuntimeError('compact-native-setup')\n"
        "    return 'fixture-loaded'\n",
        encoding="utf-8",
    )
    probe = tmp_path / "test_reporting.py"
    probe.write_text(
        "import os\nimport warnings\nimport pytest\n"
        "if os.environ['GLUDD_COMPACT_REPORT_MODE'] == 'import':\n"
        "    raise RuntimeError('compact-native-import')\n"
        "pytestmark = pytest.mark.xdist_group('compact-reporting-controls')\n"
        "@pytest.mark.parametrize('case', range(24))\n"
        "def test_original_success(case, compact_parent, worker_id, base_url):\n"
        "    assert 0 <= case < 24\n    assert compact_parent == 'fixture-loaded'\n"
        "    assert worker_id.startswith('gw')\n"
        "    assert base_url is None or isinstance(base_url, str)\n"
        "def test_original_outcome(compact_parent):\n"
        "    assert compact_parent == 'fixture-loaded'\n"
        "    mode = os.environ['GLUDD_COMPACT_REPORT_MODE']\n"
        "    if mode == 'assertion':\n        assert 1 == 2, 'compact-native-assertion'\n"
        "    if mode == 'warning':\n        warnings.warn('compact-native-warning', UserWarning)\n",
        encoding="utf-8",
    )
    paths = [probe]
    if mode in {"setup", "missing"}:
        node = "test_original_outcome" if mode == "setup" else "test_missing_node"
        paths = [Path(str(probe) + "::" + node)]
    expected_nodes = 0 if mode in {"import", "missing"} else 1 if mode == "setup" else 25
    expected_footer = (
        "no tests ran" if mode == "missing" else "2 errors" if mode == "import" else "1 error" if mode == "setup"
        else "25 passed" if mode == "success" else "1 failed, 24 passed"
    )
    outputs: list[subprocess.CompletedProcess[str]] = []
    snapshots_by_variant: list[list[dict[str, object]]] = []
    node_orders: list[list[str]] = []
    failure_reports: list[Counter[tuple[str, str, str]]] = []
    plugins = ",".join(dict.fromkeys(filter(None, (
        *os.environ.get("PYTEST_PLUGINS", "").split(","),
        "scripts.xdist_trace_plugin", "tests.fixtures.admission_cold_start.snapshot_plugin",
    ))))
    for variant in ("ordinary", "compact"):
        trace = tmp_path / f"{variant}.jsonl"
        snapshots = tmp_path / f"snapshots-{variant}"
        environment = {
            **os.environ, "UV_NO_SYNC": "1", "UV_PYTHON_DOWNLOADS": "never",
            "PYTHONDONTWRITEBYTECODE": "1", "PYTEST_PLUGINS": plugins,
            "GLUDD_COMPACT_REPORT_MODE": mode, "GLUDD_XDIST_TRACE_LOG": str(trace),
            "GLUDD_COMPACT_REPORT_VARIANT": variant,
            "GLUDD_XDIST_TRACE_RUN_ID": variant, "COLD_START_SNAPSHOT_ROOT": str(snapshots),
        }
        options = [
            "-c", str(ROOT / "pyproject.toml"),
            "--rootdir", str(tmp_path.resolve()), "--confcutdir", str(tmp_path.resolve()),
        ]
        if variant == "compact":
            options.extend(("-o", "verbosity_test_cases=-2"))
        result = _native_test_files(
            paths, 2, environment=environment,
            extra_pytest_args=shlex.join(options),
        )
        outputs.append(result)
        assert (result.returncode == 0) == (mode == "success"), result.stdout + result.stderr
        assert expected_footer in result.stdout, result.stdout + result.stderr
        events: list[dict[str, object]] = [json.loads(line) for line in trace.read_text().splitlines()]
        assert {event["run_id"] for event in events} == {variant}
        assert {
            event["worker"] for event in events if event["event"] == "RUN_START" and event["role"] == "worker"
        } == {"gw0", "gw1"}
        started = [str(event["nodeid"]) for event in events if event["event"] == "START"]
        finished = [str(event["nodeid"]) for event in events if event["event"] == "FINISH"]
        assert started == finished and len(started) == expected_nodes
        assert set(Counter(started).values()) <= {1}
        assert len({event["worker"] for event in events if event["event"] == "START"}) == (
            1 if expected_nodes else 0
        )
        node_orders.append(started)
        failure_reports.append(Counter(
            (str(event["nodeid"]), str(event["when"]), str(event["outcome"]))
            for event in events if event["event"] == "REPORT"
        ))
        captured: list[dict[str, object]] = [
            json.loads(path.read_text()) for path in sorted(snapshots.glob("gw*.json"))
        ]
        assert len(captured) == 2
        snapshots_by_variant.append(captured)
    assert outputs[0].returncode == outputs[1].returncode
    assert node_orders[0] == node_orders[1]
    assert failure_reports[0] == failure_reports[1]
    for before, after in zip(*snapshots_by_variant, strict=True):
        for field in ("nodes", "plugins", "conftests", "fixtures"):
            assert before[field] == after[field], field
    if mode == "success":
        assert not failure_reports[0]
        assert "::test_original_success" in outputs[0].stdout
        assert "::test_original_success" not in outputs[1].stdout
        assert len(outputs[1].stdout.encode()) < len(outputs[0].stdout.encode()) / 2
    elif mode in {"assertion", "warning", "setup"}:
        assert failure_reports[0]
        detail = {
            "assertion": "AssertionError: compact-native-assertion",
            "warning": "UserWarning: compact-native-warning",
            "setup": "RuntimeError: compact-native-setup",
        }[mode]
        for result in outputs:
            assert detail in result.stdout and "test_original_outcome" in result.stdout
            if mode == "assertion":
                assert "assert 1 == 2" in result.stdout
    elif mode == "import":
        assert all("RuntimeError: compact-native-import" in result.stdout for result in outputs)
    print(
        f"native-reporting mode={mode} nodes={expected_nodes} "
        f"stdout_bytes={len(outputs[0].stdout.encode())}->{len(outputs[1].stdout.encode())}"
    )


def test_compact_dot_bytes_keep_existing_observer_live_without_newlines(tmp_path: Path) -> None:
    """Flushed partial-line progress must refresh the real observer's quiet clock."""
    completed = subprocess.run(
        _command(
            tmp_path,
            "import sys,time\nfor _ in range(20):\n    sys.stdout.write('.')\n"
            "    sys.stdout.flush()\n    time.sleep(0.06)\n",
            "--heartbeat-secs", "0.05", "--quiet-secs", "0.4", "--max-secs", "4",
            run_id="compact-dot-progress",
        ),
        capture_output=True, check=False, timeout=6,
    )
    status = _current_status(tmp_path)
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert status["state"] == "passed" and status["exit_code"] == 0
    assert status["termination_reason"] is None
    assert status["elapsed_seconds"] > 0.4
    assert status["bytes_written"] == 20 and status["lines_written"] == 0
    assert Path(status["log_path"]).read_bytes() == b"." * 20


def _assert_native_git_metadata_query_count_route(
    payload: dict[str, object], rows: list[dict[str, object]],
) -> None:
    """Require the whole four-case guard as one exact first-cohort selector."""
    family_id = "native-git-metadata-query-count"
    file = "tests/unit/test_gate_status_attestation.py"
    node = f"{file}::test_repository_identity_batches_only_fresh_metadata_with_equivalent_digests"
    families = payload["families"]
    assert isinstance(families, list)
    assert [family for family in families if family["id"] == family_id] == [{
        "id": family_id,
        "owner_target": OWNER,
        "node_kind": "pytest_node",
        "node": node,
        "full_gate_phase": "test",
    }]
    assert len(families) == EXPECTED_FAMILY_COUNT
    assert len(rows) == 3
    assert [row["phase"] for row in rows] == ["full", "help", "reviewed"]
    selectors = rows[0]["files"]
    assert isinstance(selectors, list)
    assert selectors.count(node) == 1
    assert [selector for selector in selectors if selector.split("::", 1)[0] == file] == [node]
    for row in rows[1:]:
        later_selectors = row["files"]
        assert isinstance(later_selectors, list)
        assert not any(selector.split("::", 1)[0] == file for selector in later_selectors)
    assert [(row["nproc"], row["workers"]) for row in rows] == [
        ("2", "2"), ("1", "1"), ("1", "1"),
    ]
    assert [row["flags"] for row in rows] == [
        EXPECTED_MAIN_PYTEST_FLAGS,
        "-W error -q -n 0 --maxprocesses 1",
        f"-W error -q -n 0 --maxprocesses 1 --ignore-glob={ROOT / 'tests' / '*'}",
    ]
    runtime = payload["runtime_contract"]
    assert isinstance(runtime, dict)
    phases = runtime["phases"]
    assert isinstance(phases, list)
    phase = next(item for item in phases if item["target"] == OWNER)
    assert (phase["max_seconds"], phase["quiet_seconds"]) == (90, 60)
    assert len(phases) == 21
    assert sum(item["max_seconds"] for item in phases) == 2850
    assert payload["full_gate_target"] == "gate"
    assert payload["full_gate_required"] is True


def test_native_git_metadata_query_count_executes_once_in_first_cohort(tmp_path: Path) -> None:
    result, rows = _capture_owner(tmp_path, "")
    assert result.returncode == 0, result.stdout + result.stderr
    payload = json.loads((ROOT / "config/gate_failure_promotions.json").read_text(encoding="utf-8"))
    _assert_native_git_metadata_query_count_route(payload, rows)


@pytest.mark.parametrize(
    "mutation",
    (
        "missing", "whole-file", "partial-case", "duplicate", "later-only",
        "later-duplicate", "missing-family", "wrong-owner", "wrong-node", "budget",
    ),
)
def test_native_git_metadata_query_count_routing_rejects_contract_mutations(
    tmp_path: Path, mutation: str,
) -> None:
    result, captured = _capture_owner(tmp_path, "")
    assert result.returncode == 0, result.stdout + result.stderr
    original = json.loads((ROOT / "config/gate_failure_promotions.json").read_text(encoding="utf-8"))
    _assert_native_git_metadata_query_count_route(original, captured)

    payload = deepcopy(original)
    rows = deepcopy(captured)
    node = (
        "tests/unit/test_gate_status_attestation.py::"
        "test_repository_identity_batches_only_fresh_metadata_with_equivalent_digests"
    )
    selectors = rows[0]["files"]
    assert isinstance(selectors, list)
    families = payload["families"]
    family = next(item for item in families if item["id"] == "native-git-metadata-query-count")
    if mutation == "missing":
        selectors.remove(node)
    elif mutation == "whole-file":
        selectors[selectors.index(node)] = node.split("::", 1)[0]
    elif mutation == "partial-case":
        selectors[selectors.index(node)] = f"{node}[sha1-worktree]"
    elif mutation == "duplicate":
        selectors.append(node)
    elif mutation in {"later-only", "later-duplicate"}:
        if mutation == "later-only":
            selectors.remove(node)
        later = rows[2]["files"]
        assert isinstance(later, list)
        later.append(node)
    elif mutation == "missing-family":
        families.remove(family)
    elif mutation == "wrong-owner":
        family["owner_target"] = "_different-admission-owner"
    elif mutation == "wrong-node":
        family["node"] = f"{node}[sha1-worktree]"
    else:
        assert mutation == "budget"
        phase = next(item for item in payload["runtime_contract"]["phases"] if item["target"] == OWNER)
        phase["max_seconds"] = 91
    with pytest.raises(AssertionError):
        _assert_native_git_metadata_query_count_route(payload, rows)


_ADMISSION_SUPPORT_ROUTES = (
    (
        "makefile-validated-source-read-budget",
        "tests/unit/test_makefile_layout.py::test_each_invocation_reads_each_validated_source_once",
        "tests/unit/test_makefile_layout.py::test_each_invocation_reads_each_validated_source_once",
    ),
    (
        "makefile-validated-byte-composition",
        "tests/unit/test_makefile_layout.py::"
        "test_composition_emits_validated_bytes_and_revalidates_next_invocation",
        "tests/unit/test_makefile_layout.py::"
        "test_composition_emits_validated_bytes_and_revalidates_next_invocation",
    ),
    (
        "mcp-independent-topic-native-dump-budget",
        "tests/unit/test_mcp_topics_manifest.py::test_independent_topic_sizing_has_one_native_dump_per_topic",
        "tests/unit/test_mcp_topics_manifest.py::test_independent_topic_sizing_has_one_native_dump_per_topic",
    ),
    (
        "pytest-source-native-gc-restoration",
        "tests/unit/test_pytest_param_fixture_collisions.py::"
        "test_source_audit_restores_exact_gc_state_after_native_work",
        "tests/unit/test_pytest_param_fixture_collisions.py",
    ),
)


def _assert_admission_support_routes(
    payload: dict[str, object], rows: list[dict[str, object]],
) -> None:
    families = payload["families"]
    assert isinstance(families, list)
    selectors = rows[0]["files"]
    assert isinstance(selectors, list)
    for family_id, node, _selector in _ADMISSION_SUPPORT_ROUTES:
        assert [family for family in families if family["id"] == family_id] == [{
            "id": family_id, "owner_target": OWNER, "node_kind": "pytest_node",
            "node": node, "full_gate_phase": "test",
        }]
    for file in {node.split("::", 1)[0] for _, node, _ in _ADMISSION_SUPPORT_ROUTES}:
        expected = [selector for _, _, selector in _ADMISSION_SUPPORT_ROUTES
                    if selector.split("::", 1)[0] == file]
        assert [selector for selector in selectors if selector.split("::", 1)[0] == file] == expected
        for row in rows[1:]:
            later = row["files"]
            assert isinstance(later, list)
            assert not any(selector.split("::", 1)[0] == file for selector in later)
    assert selectors == list(ROSTER)
    # Preserve the prior exact-node guard and all shared process/budget contracts.
    _assert_native_git_metadata_query_count_route(payload, rows)


def test_new_admission_support_guards_execute_without_widening_files(tmp_path: Path) -> None:
    result, rows = _capture_owner(tmp_path, "")
    assert result.returncode == 0, result.stdout + result.stderr
    payload = json.loads((ROOT / "config/gate_failure_promotions.json").read_text(encoding="utf-8"))
    _assert_admission_support_routes(payload, rows)


@pytest.mark.parametrize(
    "mutation",
    (
        "missing", "whole-file", "partial-case", "duplicate", "later-only",
        "later-duplicate", "missing-family", "wrong-owner", "wrong-node", "budget",
        "missing-reader", "duplicate-gc",
    ),
)
def test_admission_support_routing_rejects_contract_mutations(
    tmp_path: Path, mutation: str,
) -> None:
    result, captured = _capture_owner(tmp_path, "")
    assert result.returncode == 0, result.stdout + result.stderr
    original = json.loads((ROOT / "config/gate_failure_promotions.json").read_text(encoding="utf-8"))
    _assert_admission_support_routes(original, captured)
    payload = deepcopy(original)
    rows = deepcopy(captured)
    family_id, node, _ = _ADMISSION_SUPPORT_ROUTES[2]
    selectors = rows[0]["files"]
    assert isinstance(selectors, list)
    families = payload["families"]
    family = next(item for item in families if item["id"] == family_id)
    if mutation == "missing":
        selectors.remove(node)
    elif mutation == "whole-file":
        selectors[selectors.index(node)] = node.split("::", 1)[0]
    elif mutation == "partial-case":
        selectors[selectors.index(node)] = f"{node}[one-case]"
    elif mutation == "duplicate":
        selectors.append(node)
    elif mutation in {"later-only", "later-duplicate"}:
        if mutation == "later-only":
            selectors.remove(node)
        later = rows[2]["files"]
        assert isinstance(later, list)
        later.append(node)
    elif mutation == "missing-family":
        families.remove(family)
    elif mutation == "wrong-owner":
        family["owner_target"] = "_different-admission-owner"
    elif mutation == "wrong-node":
        family["node"] = f"{node}[one-case]"
    elif mutation == "missing-reader":
        selectors.remove(_ADMISSION_SUPPORT_ROUTES[0][2])
    elif mutation == "duplicate-gc":
        selectors.append(_ADMISSION_SUPPORT_ROUTES[3][1])
    else:
        assert mutation == "budget"
        phase = next(item for item in payload["runtime_contract"]["phases"] if item["target"] == OWNER)
        phase["max_seconds"] = 91
    with pytest.raises(AssertionError):
        _assert_admission_support_routes(payload, rows)


def test_watchdog_handoff_exit_control_is_routed_once_in_third_child(tmp_path: Path) -> None:
    result, rows = _capture_owner(tmp_path, "")
    assert result.returncode == 0, result.stdout + result.stderr
    assert [row["files"] for row in rows] == [list(ROSTER), [HELP], list(REVIEWED_FINALIZATION)]
    assert [(row["nproc"], row["workers"]) for row in rows] == [("2", "2"), ("1", "1"), ("1", "1")]
    payload = json.loads((ROOT / "config/gate_failure_promotions.json").read_text(encoding="utf-8"))
    node = "tests/unit/test_task_watchdog.py::test_watchdog_handoff_confirms_exit_after_metadata_changes"
    assert [family for family in payload["families"] if family["id"] == "watchdog-handoff-exit-confirmation"] == [{
        "id": "watchdog-handoff-exit-confirmation",
        "owner_target": OWNER,
        "node_kind": "pytest_node",
        "node": node,
        "full_gate_phase": "test",
    }]
    assert len(payload["families"]) == EXPECTED_FAMILY_COUNT
    phase = next(item for item in payload["runtime_contract"]["phases"] if item["target"] == OWNER)
    assert (phase["max_seconds"], phase["quiet_seconds"]) == (90, 60)
    assert payload["full_gate_required"] is True


def test_native_watchdog_start_control_is_routed_once_in_third_child(tmp_path: Path) -> None:
    result, rows = _capture_owner(tmp_path, "")
    assert result.returncode == 0, result.stdout + result.stderr
    assert [row["files"] for row in rows] == [list(ROSTER), [HELP], list(REVIEWED_FINALIZATION)]
    payload = json.loads((ROOT / "config/gate_failure_promotions.json").read_text(encoding="utf-8"))
    assert [family for family in payload["families"] if family["id"] == "watchdog-native-start-acknowledgement"] == [{
        "id": "watchdog-native-start-acknowledgement", "owner_target": OWNER,
        "node_kind": "pytest_node",
        "node": "tests/unit/test_task_watchdog.py::test_native_watchdog_start_requires_child_acknowledgement",
        "full_gate_phase": "test",
    }]
    assert len(payload["families"]) == EXPECTED_FAMILY_COUNT


def test_native_start_serialization_is_admitted_once_with_unchanged_prior_order(tmp_path: Path) -> None:
    result, rows = _capture_owner(tmp_path, "")
    assert result.returncode == 0, result.stdout + result.stderr
    selected = [row["files"] for row in rows]
    assert selected == [list(ROSTER), [HELP], list(REVIEWED_FINALIZATION)]
    assert [(row["nproc"], row["workers"]) for row in rows] == [("2", "2"), ("1", "1"), ("1", "1")]
    payload = json.loads((ROOT / "config/gate_failure_promotions.json").read_text(encoding="utf-8"))
    expected = {
        "id": "watchdog-native-start-serialized-acknowledgement", "owner_target": OWNER,
        "node_kind": "pytest_node",
        "node": "tests/unit/test_task_watchdog.py::test_native_start_serializes_existing_acknowledgement",
        "full_gate_phase": "test",
    }
    assert payload["families"][108] == expected
    assert len(payload["families"]) == EXPECTED_FAMILY_COUNT
    phase = next(item for item in payload["runtime_contract"]["phases"] if item["target"] == OWNER)
    assert (phase["max_seconds"], phase["quiet_seconds"]) == (90, 60)
    assert payload["full_gate_required"] is True


def test_watchdog_unit_log_namespace_guard_is_the_exact_new_third_child_node(tmp_path: Path) -> None:
    result, rows = _capture_owner(tmp_path, "")
    assert result.returncode == 0, result.stdout + result.stderr
    node = "tests/unit/test_task_watchdog.py::test_unit_watchdog_fault_logs_belong_to_the_test_namespace"
    assert len(rows) == 3
    third_files = rows[2]["files"]
    assert isinstance(third_files, list) and third_files[7] == node
    occurrences = 0
    for row in rows:
        files = row["files"]
        assert isinstance(files, list)
        occurrences += files.count(node)
    assert occurrences == 1
    assert rows[2]["workers"] == "1"
    manifest = json.loads((ROOT / "config/gate_failure_promotions.json").read_text(encoding="utf-8"))
    assert manifest["families"][109] == {
        "id": "watchdog-unit-log-namespace", "owner_target": OWNER,
        "node_kind": "pytest_node", "node": node, "full_gate_phase": "test",
    }
    assert len(manifest["families"]) == EXPECTED_FAMILY_COUNT


def test_profile_composition_guard_is_once_in_third_child_with_original_bounds(tmp_path: Path) -> None:
    result, rows = _capture_owner(tmp_path, "")
    assert result.returncode == 0, result.stdout + result.stderr
    node = (
        "tests/unit/test_dependency_profiles.py::"
        "test_selected_profile_locks_preserve_controller_jsonpointer_requirement"
    )
    assert len(rows) == 3
    selections = [row["files"] for row in rows]
    assert all(isinstance(files, list) for files in selections)
    assert sum(files.count(node) for files in selections if isinstance(files, list)) == 1
    assert sum(
        selection == node or node.startswith(selection + "::")
        for files in selections if isinstance(files, list) for selection in files
    ) == 1, "composition node executes once, including whole-owner selections"
    assert selections[0] == list(ROSTER) and selections[1] == [HELP]
    assert isinstance(selections[2], list) and selections[2][-1] == node
    assert [(row["nproc"], row["workers"]) for row in rows] == [("2", "2"), ("1", "1"), ("1", "1")]
    manifest = json.loads((ROOT / "config/gate_failure_promotions.json").read_text(encoding="utf-8"))
    phase = next(item for item in manifest["runtime_contract"]["phases"] if item["target"] == OWNER)
    assert (phase["max_seconds"], phase["quiet_seconds"]) == (90, 60)
    assert manifest["full_gate_required"] is True


def test_descendant_symlink_cleanup_is_selected_once_by_original_whole_owner(tmp_path: Path) -> None:
    result, rows = _capture_owner(tmp_path, "")
    assert result.returncode == 0, result.stdout + result.stderr
    assert [row["files"] for row in rows] == [list(ROSTER), [HELP], list(REVIEWED_FINALIZATION)]
    assert [(row["nproc"], row["workers"]) for row in rows] == [("2", "2"), ("1", "1"), ("1", "1")]
    node = (
        "tests/unit/test_clean_stale_resource_namespaces.py::"
        "test_ordinary_descendant_symlink_unlinks_without_following_target"
    )
    selections = [row["files"] for row in rows]
    assert sum(
        selection == node or node.startswith(selection + "::")
        for files in selections if isinstance(files, list) for selection in files
    ) == 1
    payload = json.loads((ROOT / "config/gate_failure_promotions.json").read_text(encoding="utf-8"))
    assert payload["families"][111] == {
        "id": "ordinary-descendant-symlink-unlink", "owner_target": OWNER,
        "node_kind": "pytest_node", "node": node, "full_gate_phase": "test",
    }
    assert len(payload["families"]) == EXPECTED_FAMILY_COUNT
    phase = next(item for item in payload["runtime_contract"]["phases"] if item["target"] == OWNER)
    assert (phase["max_seconds"], phase["quiet_seconds"]) == (90, 60)
    assert payload["full_gate_required"] is True


def _assert_native_setuid_input_first_route(
    payload: dict[str, object], rows: list[dict[str, object]],
) -> None:
    node = (
        "tests/unit/test_ci_batch_receipts.py::"
        "test_receipt_validation_refuses_unsafe_artifact_mode[2432]"
    )
    file = node.split("::", 1)[0]
    families = payload["families"]
    assert isinstance(families, list)
    expected = {
        "id": "native-setuid-input-realization", "owner_target": OWNER,
        "node_kind": "pytest_node", "node": node, "full_gate_phase": "test",
    }
    assert len(families) == EXPECTED_FAMILY_COUNT and families[112] == expected
    assert [family for family in families if family["id"] == expected["id"]] == [expected]
    assert [row["files"] for row in rows] == [list(ROSTER), [HELP], list(REVIEWED_FINALIZATION)]
    first_files = rows[0]["files"]
    assert isinstance(first_files, list) and first_files[0] == node
    selected: list[str] = []
    for row in rows:
        files = row["files"]
        assert isinstance(files, list)
        selected.extend(selector for selector in files if selector.split("::", 1)[0] == file)
    assert selected == [node], "only the exact setuid case may be selected, once in MAIN"
    assert [(row["nproc"], row["workers"]) for row in rows] == [
        ("2", "2"), ("1", "1"), ("1", "1"),
    ]
    assert [row["flags"] for row in rows] == [
        EXPECTED_MAIN_PYTEST_FLAGS,
        "-W error -q -n 0 --maxprocesses 1",
        f"-W error -q -n 0 --maxprocesses 1 --ignore-glob={ROOT / 'tests' / '*'}",
    ]
    runtime = payload["runtime_contract"]
    assert isinstance(runtime, dict)
    phases = runtime["phases"]
    assert isinstance(phases, list)
    phase = next(item for item in phases if item["target"] == OWNER)
    assert (phase["max_seconds"], phase["quiet_seconds"]) == (90, 60)
    assert len(phases) == 21 and sum(item["max_seconds"] for item in phases) == 2850
    assert payload["full_gate_required"] is True


def test_native_setuid_input_is_first_once_in_main_child(tmp_path: Path) -> None:
    result, rows = _capture_owner(tmp_path, "")
    assert result.returncode == 0, result.stdout + result.stderr
    payload = json.loads((ROOT / "config/gate_failure_promotions.json").read_text(encoding="utf-8"))
    _assert_native_setuid_input_first_route(payload, rows)


@pytest.mark.parametrize(
    "mutation",
    ("missing", "whole-file", "wrong-case", "duplicate", "later-only", "not-first", "wrong-family-node"),
)
def test_native_setuid_input_routing_rejects_contract_mutations(tmp_path: Path, mutation: str) -> None:
    result, captured = _capture_owner(tmp_path, "")
    assert result.returncode == 0, result.stdout + result.stderr
    original = json.loads((ROOT / "config/gate_failure_promotions.json").read_text(encoding="utf-8"))
    _assert_native_setuid_input_first_route(original, captured)
    payload, rows = deepcopy(original), deepcopy(captured)
    node = (
        "tests/unit/test_ci_batch_receipts.py::"
        "test_receipt_validation_refuses_unsafe_artifact_mode[2432]"
    )
    selectors = rows[0]["files"]
    assert isinstance(selectors, list)
    if mutation == "missing":
        selectors.remove(node)
    elif mutation == "whole-file":
        selectors[0] = node.split("::", 1)[0]
    elif mutation == "wrong-case":
        selectors[0] = node.replace("[2432]", "[420]")
    elif mutation == "duplicate":
        selectors.append(node)
    elif mutation == "later-only":
        selectors.remove(node)
        later = rows[2]["files"]
        assert isinstance(later, list)
        later.append(node)
    elif mutation == "not-first":
        selectors.append(selectors.pop(0))
    else:
        assert mutation == "wrong-family-node"
        payload["families"][112]["node"] = node.replace("[2432]", "[420]")
    with pytest.raises(AssertionError):
        _assert_native_setuid_input_first_route(payload, rows)


def test_scratch_symlink_permission_failure_has_one_exact_native_route(
    tmp_path: Path,
) -> None:
    """Retain the real permission witness once before the existing fanout."""
    payload = json.loads((ROOT / "config/gate_failure_promotions.json").read_text())
    node = (
        "tests/unit/test_clean_ci_shard_scratch.py::"
        "test_stale_scratch_nested_symlink_preserves_external_target_modes[file]"
    )
    expected = {
        "id": "scratch-nested-symlink-permissions",
        "owner_target": OWNER,
        "node_kind": "pytest_node",
        "node": node,
        "full_gate_phase": "test",
    }
    assert [family for family in payload["families"] if family["id"] == expected["id"]] == [expected]
    assert payload["families"][148] == expected
    assert payload["families"][147]["id"] == "watchdog-empty-observer-projection"
    assert len(payload["families"]) == EXPECTED_FAMILY_COUNT
    selectors = re.findall(r"TESTFILES=\"([^\"]*)\"", _stanza(OWNER))
    assert len(selectors) == 3
    arguments = [selector.split() for selector in selectors]
    assert arguments[0].count(node) == 1
    assert node not in arguments[1] + arguments[2]
    assert all(node.split("::", 1)[0] not in selection for selection in arguments)
    result, rows = _capture_owner(tmp_path, "")
    assert result.returncode == 0, result.stdout + result.stderr
    assert [row["files"] for row in rows] == [list(ROSTER), [HELP], list(REVIEWED_FINALIZATION)]
    assert [(row["nproc"], row["workers"]) for row in rows] == [("2", "2"), ("1", "1"), ("1", "1")]
    phase = next(
        item for item in payload["runtime_contract"]["phases"]
        if item["name"] == "pytest-runtime-compatibility"
    )
    assert (phase["max_seconds"], phase["quiet_seconds"]) == (90, 60)
    assert len(payload["runtime_contract"]["phases"]) == 21
    assert sum(item["max_seconds"] for item in payload["runtime_contract"]["phases"]) == 2850
    assert payload["full_gate_target"] == "gate" and payload["full_gate_required"] is True


def test_coverage_suite_provenance_failure_has_one_exact_native_route(tmp_path: Path) -> None:
    """Route one imported-report witness without changing native proof bounds."""
    result, rows = _capture_owner(tmp_path, "")
    assert result.returncode == 0, result.stdout + result.stderr
    node = (
        "tests/unit/test_audit_coverage_suite_provenance.py::"
        "test_imported_cobertura_never_proves_e2e_execution"
    )
    assert [row["files"] for row in rows] == [list(ROSTER), [HELP], list(REVIEWED_FINALIZATION)]
    first = rows[0]["files"]
    assert isinstance(first, list) and first.count(node) == 1
    for index, row in enumerate(rows):
        selection = row["files"]
        assert isinstance(selection, list)
        assert node.split("::", 1)[0] not in selection
        if index:
            assert node not in selection
    payload = json.loads((ROOT / "config/gate_failure_promotions.json").read_text())
    assert len(payload["families"]) == EXPECTED_FAMILY_COUNT == 153
    assert payload["families"][149]["id"] == "native-full-scan-success-inventory"
    assert payload["families"][150] == {
        "id": "coverage-native-suite-provenance",
        "owner_target": OWNER, "node_kind": "pytest_node",
        "node": node, "full_gate_phase": "test",
    }
    assert [(row["nproc"], row["workers"]) for row in rows] == [("2", "2"), ("1", "1"), ("1", "1")]
    phase = next(
        item for item in payload["runtime_contract"]["phases"]
        if item["name"] == "pytest-runtime-compatibility"
    )
    assert (phase["max_seconds"], phase["quiet_seconds"]) == (90, 60)
    assert len(payload["runtime_contract"]["phases"]) == 21
    assert sum(item["max_seconds"] for item in payload["runtime_contract"]["phases"]) == 2850
    assert payload["full_gate_target"] == "gate" and payload["full_gate_required"] is True


def test_full_scan_success_inventory_failure_has_one_exact_native_route(
    tmp_path: Path,
) -> None:
    """Route the real successful-scan finding witness once before fanout."""
    payload = json.loads((ROOT / "config/gate_failure_promotions.json").read_text())
    node = (
        "tests/unit/test_detect_secrets_full_scan.py::"
        "test_successful_full_scan_rejects_added_identity_or_occurrence[new-digest]"
    )
    expected = {
        "id": "native-full-scan-success-inventory",
        "owner_target": OWNER, "node_kind": "pytest_node",
        "node": node, "full_gate_phase": "test",
    }
    assert payload["families"][149] == expected
    assert payload["families"][148]["id"] == "scratch-nested-symlink-permissions"
    assert len(payload["families"]) == EXPECTED_FAMILY_COUNT
    selectors = re.findall(r"TESTFILES=\"([^\"]*)\"", _stanza(OWNER))
    assert len(selectors) == 3
    arguments = [selector.split() for selector in selectors]
    assert [selection.count(node) for selection in arguments] == [1, 0, 0]
    assert all(node.split("::", 1)[0] not in selection for selection in arguments)
    result, rows = _capture_owner(tmp_path, "")
    assert result.returncode == 0, result.stdout + result.stderr
    assert [row["files"] for row in rows] == [list(ROSTER), [HELP], list(REVIEWED_FINALIZATION)]
    assert [(row["nproc"], row["workers"]) for row in rows] == [("2", "2"), ("1", "1"), ("1", "1")]



def test_uv_prune_native_failure_has_one_exact_native_route(tmp_path: Path) -> None:
    """Route the actual hidden-failure witness once with every prior bound."""
    result, rows = _capture_owner(tmp_path, "")
    assert result.returncode == 0, result.stdout + result.stderr
    node = (
        "tests/unit/test_make_uv_cache_sandbox.py::"
        "test_native_prune_preserves_real_child_status[native-failure]"
    )
    assert [row["files"] for row in rows] == [list(ROSTER), [HELP], list(REVIEWED_FINALIZATION)]
    counts = []
    for row in rows:
        selection = row["files"]
        assert isinstance(selection, list)
        counts.append(selection.count(node))
        assert node.split("::", 1)[0] not in selection
    assert counts == [1, 0, 0]
    payload = json.loads((ROOT / "config/gate_failure_promotions.json").read_text())
    assert len(payload["families"]) == EXPECTED_FAMILY_COUNT == 153
    assert payload["families"][150]["id"] == "coverage-native-suite-provenance"
    assert payload["families"][151] == {
        "id": "uv-prune-native-failure-status",
        "owner_target": OWNER, "node_kind": "pytest_node",
        "node": node, "full_gate_phase": "test",
    }
    assert [(row["nproc"], row["workers"]) for row in rows] == [("2", "2"), ("1", "1"), ("1", "1")]
    phase = next(
        item for item in payload["runtime_contract"]["phases"]
        if item["name"] == "pytest-runtime-compatibility"
    )
    assert (phase["max_seconds"], phase["quiet_seconds"]) == (90, 60)
    assert len(payload["runtime_contract"]["phases"]) == 21
    assert sum(item["max_seconds"] for item in payload["runtime_contract"]["phases"]) == 2850
    assert payload["full_gate_target"] == "gate" and payload["full_gate_required"] is True



def test_uv_prune_inherited_policy_has_one_exact_native_route(tmp_path: Path) -> None:
    """Promote the real inherited zero-timeout refusal before fanout."""
    result, rows = _capture_owner(tmp_path, "")
    assert result.returncode == 0, result.stdout + result.stderr
    node = (
        "tests/unit/test_make_uv_cache_sandbox.py::"
        "test_native_prune_target_policy_refuses_invalid[UV_CACHE_PRUNE_TIMEOUT=0]"
    )
    counts = []
    for row in rows:
        selection = row["files"]
        assert isinstance(selection, list)
        counts.append(selection.count(node))
        assert node.split("::", 1)[0] not in selection
    assert counts == [1, 0, 0]
    assert [row["files"] for row in rows] == [list(ROSTER), [HELP], list(REVIEWED_FINALIZATION)]
    payload = json.loads((ROOT / "config/gate_failure_promotions.json").read_text())
    assert len(payload["families"]) == EXPECTED_FAMILY_COUNT == 153
    assert payload["families"][151]["id"] == "uv-prune-native-failure-status"
    assert payload["families"][152] == {
        "id": "uv-prune-inherited-policy-context", "owner_target": OWNER,
        "node_kind": "pytest_node", "node": node, "full_gate_phase": "test",
    }
    assert [(row["nproc"], row["workers"]) for row in rows] == [("2", "2"), ("1", "1"), ("1", "1")]
    phase = next(
        item for item in payload["runtime_contract"]["phases"]
        if item["name"] == "pytest-runtime-compatibility"
    )
    assert (phase["max_seconds"], phase["quiet_seconds"]) == (90, 60)
    assert len(payload["runtime_contract"]["phases"]) == 21
    assert sum(item["max_seconds"] for item in payload["runtime_contract"]["phases"]) == 2850
    assert payload["full_gate_target"] == "gate" and payload["full_gate_required"] is True
