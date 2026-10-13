"""Native contracts for bounded runtime admission and fresh help isolation."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

import pytest
from scripts import makefile_layout

ROOT = Path(makefile_layout.__file__).resolve().parents[1]
OWNER = "_pytest-runtime-compatibility-admission"
HELP = "tests/unit/test_makefile_audit_deep.py::TestHelpNoDupTargets::test_no_duplicate_help_entries"
ROSTER = (
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
    "tests/unit/test_status_heartbeat_terminal.py",
    "tests/unit/test_task_ledger_validation.py",
    "tests/unit/test_make_readonly_version.py",
    "tests/unit/test_make_help_audit.py",
    "tests/unit/test_clean_stale_resource_namespaces.py",
    "tests/unit/test_native_temp_artifact_target.py",
    "tests/unit/test_ci_run_summary_script.py",
    "tests/unit/test_ci_run_summary_target.py",
    "tests/unit/test_ci_workflow_integrity.py",
    "tests/security/test_ci_workflow.py",
    "tests/unit/test_workflow_safe_parser.py",
    "tests/unit/test_self_improve_private_policy_ci_contract.py",
    "tests/unit/test_ci_shards_log_context.py",
    "tests/unit/test_ci_shards_log_context_bounds.py",
    "tests/unit/test_makefile_targets_deep.py::TestInternalTargetConventions::test_internal_targets_not_in_help",
    "tests/unit/test_azure_containerapp_coverage_target.py",
    "tests/unit/test_ee_owned_collection_runtime_metadata.py",
    "tests/unit/test_no_sleep_subagent.py",
    "tests/unit/test_ansible_ee_locked_consumer.py",
    "tests/unit/test_ansible_ee_locked_consumer_admission.py",
    "tests/unit/test_release_asset_matrix.py",
    "tests/unit/test_release_ee_collection_closure.py",
    "tests/unit/test_mcp_tool_gen.py",
)


def _stanza(target: str) -> str:
    composed = makefile_layout.compose_makefile(ROOT / "Makefile")
    return composed.split(f"\n{target}:\n", 1)[1].split("\n\n", 1)[0]


def _capture_owner(tmp_path: Path, fail_phase: str) -> tuple[subprocess.CompletedProcess[str], list[dict[str, object]]]:
    """Execute the actual Make stanza with only its child test-files owner replaced."""
    record = tmp_path / "calls.jsonl"
    recorder = tmp_path / "record.py"
    recorder.write_text(
        "import json, os, sys\n"
        "from pathlib import Path\n"
        "files, nproc, workers, flags = sys.argv[1:]\n"
        f"phase = 'help' if files == {HELP!r} else 'full'\n"
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
    assert [row["files"] for row in rows] == [list(ROSTER), [HELP]]
    assert [row["phase"] for row in rows] == ["full", "help"]
    assert len({row["pid"] for row in rows}) == 2
    assert [(row["nproc"], row["workers"]) for row in rows] == [("2", "2"), ("1", "1")]
    for row, workers, limit in zip(rows, (2, 0), (2, 1), strict=True):
        flags = str(row["flags"])
        assert "-W error" in flags
        assert f"-n {workers}" in flags
        assert f"--maxprocesses {limit}" in flags
        assert not any(flag in flags for flag in ("--lf", "--ff", "--collect-only", "-W ignore", "-k "))
    test_files = _stanza("test-files")
    assert '--basetemp="$$BT"' in test_files
    assert 'ANSIBLE_LOCAL_TEMP="$$BT/ansible-local"' in test_files
    assert 'export VIRTUAL_ENV="$$UV_PROJECT_ENVIRONMENT"' in test_files
    assert "--dist loadgroup" in makefile_layout.compose_makefile(ROOT / "Makefile")


@pytest.mark.parametrize(("fail_phase", "expected_phases"), [("full", ["full"]), ("help", ["full", "help"])])
def test_runtime_admission_propagates_each_owner_failure_without_replay(
    tmp_path: Path, fail_phase: str, expected_phases: list[str],
) -> None:
    result, rows = _capture_owner(tmp_path, fail_phase)
    assert result.returncode != 0
    assert "Error 1" in result.stderr
    assert [row["phase"] for row in rows] == expected_phases


def _native_test_files(
    paths: list[Path], workers: int, *, environment: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            "make", "--no-print-directory", "test-files",
            "TESTFILES=" + " ".join(str(path) for path in paths),
            f"NPROC={workers}", f"GLUDD_XDIST_WORKERS={workers}",
            f"PYTEST_ARGS=-W error -q -n {workers} --maxprocesses {workers}",
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
