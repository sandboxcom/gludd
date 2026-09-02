"""Tracked acceptance fixture for multi-file local-model self-improvement."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from scripts.run_self_improve_e2e import TaskSpec

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "config/self-improve/context-budget-lifecycle.json"
DOCUMENT = ROOT / "docs/features/SELF_IMPROVEMENT_MULTIFILE_FIXTURE.md"
FIXTURE_SHA256 = "33a58eab1407d174cda2f98a3a3a7594622c61b7d84696c0f5c568fec9187462"
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
    'make coverage-files COVERAGE_TESTFILES="'
    'tests/unit/test_self_improve_model_candidate_planner.py '
    'tests/unit/test_self_improve_model_lifecycle.py '
    'tests/unit/test_self_improve_model_lifecycle_deep.py '
    'tests/unit/test_self_improve_persistent_model_evidence.py '
    'tests/unit/test_self_improve_runner_model_lifecycle.py '
    'tests/unit/test_self_improve_codex_runner.py '
    'tests/unit/test_self_improve_codex_comparison.py '
    'tests/unit/test_self_improve_local_worker.py '
    'tests/unit/test_small_models_recommender.py '
    'tests/unit/test_recommender_deep.py '
    'tests/unit/test_model_recommender_deep.py" '
    'COVERAGE_CONFIG=config/coverage_self_improve.ini '
    'COVERAGE_AGGREGATE_MIN=85 COVERAGE_PER_FILE_MIN=75',
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
    assert payload_text == json.dumps(payload, sort_keys=True, separators=(",", ":"))
    assert set(payload) == {
        "task_id",
        "objective",
        "canonical_make_commands",
        "reference_elapsed_seconds",
    }
    assert len(spec.canonical_make_commands) == 6


def test_multifile_fixture_bytes_are_immutable() -> None:
    assert hashlib.sha256(FIXTURE.read_bytes()).hexdigest() == FIXTURE_SHA256


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
