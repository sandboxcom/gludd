"""Tracked acceptance fixture for multi-file local-model self-improvement."""

from __future__ import annotations

import hashlib
import json
import re
from configparser import ConfigParser
from pathlib import Path

from general_ludd.self_improve.managed_runner import TaskSpec

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "config/self-improve/context-budget-lifecycle.json"
DOCUMENT = ROOT / "docs/features/SELF_IMPROVEMENT_MULTIFILE_FIXTURE.md"
COVERAGE_CONFIG = ROOT / "config/coverage_self_improve.ini"
FIXTURE_SHA256 = "cb1ae6a252cedc2b337d84fd02e7ca36935709c30bb0e8be9060401cba8d1f04"
COVERAGE_TEST_SELECTOR = (
    "tests/unit/test_project*.py",
    "tests/unit/test_daemon*.py",
    "tests/unit/test_event_loop*.py",
    "tests/unit/test_self_improve*.py",
    "tests/unit/test_managed_self_improve*.py",
    "tests/unit/test_worker*.py",
    "tests/unit/test_job*.py",
    "tests/unit/test_approval*.py",
    "tests/unit/test_runtime*.py",
    "tests/unit/test_router*.py",
    "tests/unit/test_managed_promotion*.py",
    "tests/unit/test_beta4_hosted_self_improve_coverage.py",
    "tests/unit/test_c13_self_improve_gate.py",
    "tests/unit/test_cli_self_improve.py",
    "tests/unit/test_ornith_self_improve_role.py",
    "tests/unit/test_reload_self_improve.py",
    "tests/unit/test_small_models_recommender_branch_coverage.py",
    "tests/unit/test_gap_fixes.py",
    "tests/unit/test_c21_alpha4_open.py",
    "tests/unit/test_c21_alpha4_leftovers.py",
    "tests/unit/test_generation_tool_dispatch.py",
    "tests/unit/test_toolcallloop_work_types.py",
    "tests/unit/test_adaptive_routing.py",
    "tests/unit/test_scheduler_self_update_branch.py",
    "tests/unit/test_completion_audit_wiring.py",
    "tests/unit/test_ab_test_dispatch.py",
    "tests/unit/test_task48_debt_eval_seam.py",
    "tests/unit/test_floor_controller_wiring.py",
    "tests/unit/test_run_recorder_daemon_wiring.py",
    "tests/unit/test_clean_hf_cache_target.py",
)
COVERAGE_SOURCES = (
    "*/scripts/clean_hf_cache.py",
    "*/scripts/run_self_improve_e2e.py",
    "*/scripts/self_improve_local_proposal.py",
    "*/src/general_ludd/self_improve/_candidate_attempt.py",
    "*/src/general_ludd/self_improve/_candidate_calibration.py",
    "*/src/general_ludd/self_improve/_candidate_execution_runtime.py",
    "*/src/general_ludd/self_improve/_candidate_execution_types.py",
    "*/src/general_ludd/self_improve/_candidate_execution_validation.py",
    "*/src/general_ludd/self_improve/_candidate_prediction.py",
    "*/src/general_ludd/self_improve/_candidate_trials.py",
    "*/src/general_ludd/self_improve/azure_backend.py",
    "*/src/general_ludd/self_improve/candidate_execution.py",
    "*/src/general_ludd/self_improve/codex_comparison.py",
    "*/src/general_ludd/self_improve/candidate_routing.py",
    "*/src/general_ludd/self_improve/model_candidate_planner.py",
    "*/src/general_ludd/self_improve/model_candidates.py",
    "*/src/general_ludd/self_improve/model_lifecycle.py",
    "*/src/general_ludd/small_models/recommender.py",
    "*/src/general_ludd/self_improve/managed_runner.py",
    "*/src/general_ludd/self_improve/private_policy.py",
    "*/src/general_ludd/self_improve/harness.py",
    "*/src/general_ludd/self_improve/runtime.py",
    "*/src/general_ludd/self_improve/evaluator.py",
    "*/src/general_ludd/self_improve/result_artifact.py",
    "*/src/general_ludd/self_improve/staging.py",
    "*/src/general_ludd/self_improve/apply.py",
    "*/src/general_ludd/self_improve/approval.py",
    "*/src/general_ludd/self_improve/promotion.py",
    "*/src/general_ludd/db/promotion_repository.py",
    "*/src/general_ludd/projects/repository_binding.py",
    "*/src/general_ludd/projects/manager.py",
    "*/src/general_ludd/routers/self_improve.py",
    "*/src/general_ludd/schemas/job.py",
    "*/src/general_ludd/schemas/self_improve_artifact.py",
    "*/src/general_ludd/event_loop/loop.py",
    "*/src/general_ludd/worker/app.py",
    "*/src/general_ludd/daemon.py",
)
OBJECTIVE = (
    "Fix the local-model self-improvement runner so it rejects model candidates "
    "whose native context cannot hold the full rendered prompt and required proposal, "
    "uses the GGUF native context instead of an oversized forced context, bounds each "
    "proposal worker to five minutes, publishes lease release and persistent outcome "
    "evidence, and exits with a bounded terminal diagnostic instead of a traceback."
)
COMMANDS = (
    'make test-files TESTFILES="tests/unit/test_self_improve_codex_comparison.py '
    'tests/unit/test_self_improve_local_worker.py '
    'tests/unit/test_self_improve_model_candidate_planner.py '
    'tests/unit/test_self_improve_runner_model_lifecycle.py '
    'tests/unit/test_self_improve_codex_runner.py" '
    'PYTEST_ARGS="-q -W error --tb=short"',
    (
        'make coverage-files COVERAGE_TESTFILES="'
        + " ".join(COVERAGE_TEST_SELECTOR)
        + '" COVERAGE_CONFIG=config/coverage_self_improve.ini '
        'COVERAGE_REPORT=.gate-logs/coverage-self-improve.json '
        'COVERAGE_AGGREGATE_MIN=85 COVERAGE_PER_FILE_MIN=75 '
        'OBSERVED_ROOT=.gate-logs/observed OBSERVED_HEARTBEAT_SECS=30 '
        'OBSERVED_QUIET_SECS=900 OBSERVED_MAX_SECS=3600 OBSERVED_RETAIN_RUNS=20'
    ),
    'make lint-files FILES="scripts/run_self_improve_e2e.py '
    'src/general_ludd/self_improve/codex_comparison.py '
    'src/general_ludd/self_improve/model_candidate_planner.py '
    'tests/unit/test_self_improve_codex_comparison.py '
    'tests/unit/test_self_improve_codex_runner.py '
    'tests/unit/test_self_improve_local_worker.py '
    'tests/unit/test_self_improve_model_candidate_planner.py '
    'tests/unit/test_self_improve_runner_model_lifecycle.py"',
    "make typecheck-scope FILES=scripts/run_self_improve_e2e.py",
    'make typecheck-scope FILES="'
    'src/general_ludd/self_improve/codex_comparison.py '
    'src/general_ludd/self_improve/model_candidate_planner.py '
    'tests/unit/test_self_improve_codex_comparison.py '
    'tests/unit/test_self_improve_codex_runner.py '
    'tests/unit/test_self_improve_local_worker.py '
    'tests/unit/test_self_improve_model_candidate_planner.py '
    'tests/unit/test_self_improve_runner_model_lifecycle.py"',
    'make lint-docstrings DOCSTRING_FILES="scripts/run_self_improve_e2e.py '
    'src/general_ludd/self_improve/codex_comparison.py '
    'src/general_ludd/self_improve/model_candidate_planner.py"',
)


def _target_block(makefile: str, name: str) -> str:
    marker = f"\n{name}:"
    return makefile.split(marker, 1)[1].split("\n\n", 1)[0]


def test_multifile_fixture_reuses_strict_task_spec() -> None:
    spec = TaskSpec.from_path(FIXTURE)

    assert spec == TaskSpec(
        task_id="S83.133",
        objective=OBJECTIVE,
        canonical_make_commands=COMMANDS,
        reference_elapsed_seconds=600.0,
    )
    payload_text = FIXTURE.read_text(encoding="utf-8")
    payload = json.loads(payload_text)
    expected_payload = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    assert payload_text == f"{expected_payload}\n"
    assert set(payload) == {
        "task_id",
        "objective",
        "canonical_make_commands",
        "reference_elapsed_seconds",
    }
    assert len(spec.canonical_make_commands) == 6


def test_multifile_fixture_bytes_are_immutable() -> None:
    assert hashlib.sha256(FIXTURE.read_bytes()).hexdigest() == FIXTURE_SHA256


def test_canonical_coverage_runs_every_self_improvement_contract() -> None:
    spec = TaskSpec.from_path(FIXTURE)
    coverage_command = next(
        command for command in spec.canonical_make_commands if command.startswith("make coverage-files ")
    )
    match = re.search(r'COVERAGE_TESTFILES="([^"]+)"', coverage_command)
    assert match is not None
    canonical_selector = tuple(match.group(1).split())
    selected_tests: set[str] = set()
    for selector in COVERAGE_TEST_SELECTOR:
        matches = {
            path.relative_to(ROOT).as_posix()
            for path in ROOT.glob(selector)
        }
        assert matches, selector
        selected_tests.update(matches)
    required_self_improve_tests = {
        path.relative_to(ROOT).as_posix()
        for path in (ROOT / "tests/unit").glob("test_*self_improve*.py")
    }

    coverage_config = ConfigParser()
    coverage_config.read(COVERAGE_CONFIG, encoding="utf-8")
    configured_sources = tuple(
        line for line in coverage_config["run"]["include"].splitlines() if line
    )

    assert canonical_selector == COVERAGE_TEST_SELECTOR
    assert configured_sources == COVERAGE_SOURCES
    assert required_self_improve_tests <= selected_tests, sorted(
        required_self_improve_tests - selected_tests
    )
    assert "COVERAGE_AGGREGATE_MIN=85" in coverage_command
    assert "COVERAGE_PER_FILE_MIN=75" in coverage_command


def test_multifile_target_is_pinned_and_safe_by_default() -> None:
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    target = _target_block(makefile, "test-self-improve-multifile")

    assert "SELF_IMPROVE_MULTIFILE_LIVE ?= 0" in makefile
    assert 'case "$(SELF_IMPROVE_MULTIFILE_LIVE)" in 0|1)' in target
    assert re.search(r"SELF_IMPROVE_BASELINE_REF=[0-9a-f]{40}(?: |$)", target)
    assert re.search(r"SELF_IMPROVE_REFERENCE_REF=[0-9a-f]{40}(?: |$)", target)
    assert (
        "SELF_IMPROVE_TASK_FILE=config/self-improve/context-budget-lifecycle.json"
        in target
    )
    assert "SELF_IMPROVE_MAX_ATTEMPTS=2" in target
    assert "SELF_IMPROVE_MODEL_PATH=" in target
    assert (
        'SELF_IMPROVE_VALIDATE_ONLY="$(if $(filter '
        '1,$(SELF_IMPROVE_MULTIFILE_LIVE)),0,1)"'
    ) in target
    assert FIXTURE_SHA256 in target
    assert target.index(FIXTURE_SHA256) < target.index(
        "$(MAKE) --no-print-directory test-self-improve"
    )
    assert "release-" not in target
    assert "git tag" not in target


def test_multifile_target_has_complete_make_contract() -> None:
    contract = json.loads(
        (ROOT / "config/make_target_contract.json").read_text(encoding="utf-8")
    )
    entry = next(
        item
        for item in contract["targets"]
        if item["name"] == "test-self-improve-multifile"
    )
    assert entry == {
        "name": "test-self-improve-multifile",
        "make_variables": ["SELF_IMPROVE_MULTIFILE_LIVE"],
        "behavior": (
            "make test-self-improve-multifile SELF_IMPROVE_MULTIFILE_LIVE=0"
        ),
    }
    help_text = (ROOT / "Makefile").read_text(encoding="utf-8")
    assert "test-self-improve-multifile" in help_text.split("# --- Git ---", 1)[0]


def test_multifile_fixture_documents_evidence_zdd_and_cleanup() -> None:
    document = DOCUMENT.read_text(encoding="utf-8")

    for fact in (
        "context-budget-lifecycle.json",
        "SELF_IMPROVE_MULTIFILE_LIVE=0",
        "SELF_IMPROVE_MULTIFILE_LIVE=1",
        "isolated worktree",
        "zero-downtime",
        "rollback",
        "85%",
        "75%",
        "8 GiB",
        "2 GiB",
        "one retained model instance",
        "llama.cpp discussion 4020",
        "lm-evaluation-harness issue 1098",
        "same exact protocol",
        "no external server",
    ):
        assert fact in document
