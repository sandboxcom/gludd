"""Executable contract tests for the ten-shape self-improvement matrix."""

from __future__ import annotations

import json
import re
from pathlib import Path
from types import SimpleNamespace

import pytest
from scripts.run_self_improve_e2e import TaskSpec

from general_ludd.routing_roles.small_model_policy import DEFAULT_TASK_CONTRACTS
from general_ludd.schemas.benchmark import TaskType
from general_ludd.self_improve.task_diversity import infer_task_type
from general_ludd.small_models.recommender import map_task_to_capabilities
from tests.unit.self_improve_acceptance_matrix_runner import (
    MatrixContractError,
    MatrixOutcome,
    aggregate_outcomes,
    build_execution_plan,
    execute_matrix,
    load_manifest,
)

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / "config/self-improve/acceptance-matrix.json"
DOCUMENT = ROOT / "docs/SELF_IMPROVEMENT_ACCEPTANCE_MATRIX.md"
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
EXPECTED_CASE_IDS = (
    "AM-BUG-01",
    "AM-FEATURE-01",
    "AM-REFACTOR-01",
    "AM-TEST-01",
    "AM-REVIEW-01",
    "AM-DOC-01",
    "AM-DEBUG-01",
    "AM-OPT-01",
    "AM-SEC-01",
    "AM-INTEGRATION-01",
)


def test_tracked_reference_fixtures_are_not_relabelled_as_matrix_evidence() -> None:
    catalog = TaskSpec.from_path(ROOT / "config/self-improve/catalog-truth.json")
    multifile = TaskSpec.from_path(
        ROOT / "config/self-improve/context-budget-lifecycle.json"
    )

    assert infer_task_type(catalog.objective) is TaskType.SECURITY_FIX
    assert infer_task_type(multifile.objective) is TaskType.SECURITY_FIX
    manifest = load_manifest(MANIFEST)
    assert all(not case.eligible for case in manifest.cases)


def _target_block(makefile: str, name: str) -> str:
    marker = f"\n{name}:"
    return makefile.split(marker, 1)[1].split("\n\n", 1)[0]


def _write_mutated_manifest(tmp_path: Path, case: str) -> Path:
    payload = json.loads(MANIFEST.read_text(encoding="utf-8"))
    if case == "missing-shape":
        payload["cases"].pop()
    elif case == "mutable-ref":
        payload["cases"][0]["baseline_ref"] = "development"
    elif case == "over-budget":
        payload["max_total_attempts"] = 21
    elif case == "parallel":
        payload["serial"] = False
    elif case == "sentinel-order":
        payload["sentinel"]["phases"] = ["warm", "cold"]
    elif case == "contract-drift":
        payload["cases"][0]["required_acceptance_checks"] = ["schema_valid"]
    elif case == "fixture-digest":
        payload["cases"][0]["fixture_digest"] = "0" * 64
    elif case == "false-evidence":
        payload["cases"][1]["eligibility"] = "eligible"
        payload["cases"][1]["ineligible_reason"] = None
    elif case == "reference-reuse":
        source = payload["cases"][0]
        target = payload["cases"][1]
        target.update(
            {
                "baseline_ref": source["baseline_ref"],
                "eligibility": "eligible",
                "fixture_digest": source["fixture_digest"],
                "fixture_path": source["fixture_path"],
                "ineligible_reason": None,
                "reference_ref": source["reference_ref"],
            }
        )
    path = tmp_path / f"{case}.json"
    path.write_text(
        json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
        + "\n",
        encoding="utf-8",
    )
    return path


def test_manifest_is_canonical_and_covers_every_existing_task_shape_once() -> None:
    raw = MANIFEST.read_text(encoding="utf-8")
    payload = json.loads(raw)
    assert raw == (
        json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
        + "\n"
    )

    manifest = load_manifest(MANIFEST)
    assert manifest.schema_version == 1
    assert manifest.suite_id == "self-improvement-acceptance-matrix-v1"
    assert tuple(case.case_id for case in manifest.cases) == EXPECTED_CASE_IDS
    assert tuple(case.task_type for case in manifest.cases) == tuple(TaskType)
    assert [case.case_id for case in manifest.cases if case.eligible] == []

    for case in manifest.cases:
        assert infer_task_type(case.task.objective) is case.task_type
        mapped_kind, mapped_role = map_task_to_capabilities(case.task.objective)[0]
        assert (mapped_kind, mapped_role) == (case.task_kind, case.role)
        contract = DEFAULT_TASK_CONTRACTS[case.task_kind]
        assert case.role in contract.allowed_roles
        assert case.required_acceptance_checks == tuple(
            sorted(contract.required_acceptance_checks)
        )
        assert case.allowed_changed_paths
        assert case.required_test_paths
        assert set(case.required_test_paths) <= set(case.allowed_changed_paths)
        assert all(command.startswith("make ") for command in case.task.canonical_make_commands)
        assert case.ineligible_reason == (
            "no_semantically_matching_independent_reference"
        )
        assert case.baseline_ref is None
        assert case.reference_ref is None
        assert case.fixture_path is None
        assert case.fixture_digest is None

    with pytest.raises(AttributeError):
        manifest.cases[0].__setattr__("baseline_ref", "development")


def test_execution_plan_is_serial_bounded_and_has_exact_cold_warm_sentinel() -> None:
    manifest = load_manifest(MANIFEST)
    steps = build_execution_plan(manifest)

    assert manifest.serial is True
    assert manifest.max_total_attempts == 20
    assert tuple((step.case.case_id, step.phase) for step in steps[:2]) == (
        ("AM-BUG-01", "cold"),
        ("AM-BUG-01", "warm"),
    )
    assert steps[0].case is steps[1].case
    assert steps[0].max_attempts == steps[1].max_attempts == 1
    assert tuple(step.case.case_id for step in steps[2:]) == EXPECTED_CASE_IDS[1:]
    assert sum(step.max_attempts for step in steps) == 20
    assert all(step.ordinal == index for index, step in enumerate(steps, start=1))


@pytest.mark.parametrize(
    "case",
    (
        "missing-shape",
        "mutable-ref",
        "over-budget",
        "parallel",
        "sentinel-order",
        "contract-drift",
        "fixture-digest",
        "false-evidence",
        "reference-reuse",
    ),
)
def test_manifest_drift_fails_closed(tmp_path: Path, case: str) -> None:
    with pytest.raises(MatrixContractError):
        load_manifest(_write_mutated_manifest(tmp_path, case))


def test_result_aggregation_requires_every_exact_step_to_pass() -> None:
    manifest = load_manifest(MANIFEST)
    steps = build_execution_plan(manifest)
    passing = tuple(
        MatrixOutcome(step_id=step.step_id, returncode=0) for step in steps
    )

    summary = aggregate_outcomes(steps, passing)
    assert summary.status == "failed"
    assert summary.passed_steps == 0
    assert summary.failed_step_ids == tuple(
        step.step_id for step in steps if not step.case.eligible
    )

    one_failure = (*passing[:-1], MatrixOutcome(steps[-1].step_id, 1))
    assert aggregate_outcomes(steps, one_failure).status == "failed"
    assert aggregate_outcomes(steps, passing[:-1]).status == "failed"
    duplicated = (*passing[:-1], passing[0])
    assert aggregate_outcomes(steps, duplicated).status == "failed"


def test_execution_is_serial_observable_and_validate_only_unless_explicitly_live() -> None:
    manifest = load_manifest(MANIFEST)

    class Runner:
        def __init__(self, failing_ordinal: int | None = None) -> None:
            self.failing_ordinal = failing_ordinal
            self.calls: list[tuple[str, dict[str, str], int]] = []

        def run_observable(
            self,
            target: str,
            variables: dict[str, str],
            *,
            timeout: int,
        ) -> SimpleNamespace:
            self.calls.append((target, variables, timeout))
            return SimpleNamespace(
                returncode=int(len(self.calls) == self.failing_ordinal)
            )

    safe_runner = Runner()
    safe_summary = execute_matrix(
        manifest,
        live=False,
        model_path="",
        runner=safe_runner,
    )
    assert safe_summary.status == "failed"
    assert safe_runner.calls == []

    live_runner = Runner(failing_ordinal=3)
    live_summary = execute_matrix(
        manifest,
        live=True,
        model_path="/tmp/gludd-acceptance-model.gguf",
        runner=live_runner,
    )
    assert live_summary.status == "failed"
    assert live_runner.calls == []


def test_make_target_is_pinned_safe_by_default_and_explicitly_live() -> None:
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    target = _target_block(makefile, "test-self-improve-acceptance-matrix")

    assert "SELF_IMPROVE_ACCEPTANCE_MATRIX_LIVE ?= 0" in makefile
    assert (
        "SELF_IMPROVE_ACCEPTANCE_MATRIX_FILE ?= "
        "config/self-improve/acceptance-matrix.json"
    ) in makefile
    assert "SELF_IMPROVE_ACCEPTANCE_MATRIX_MODEL_PATH ?=" in makefile
    assert 'case "$(SELF_IMPROVE_ACCEPTANCE_MATRIX_LIVE)" in 0|1)' in target
    assert "tests.unit.self_improve_acceptance_matrix_runner" in target
    assert '$(if $(filter 1,$(SELF_IMPROVE_ACCEPTANCE_MATRIX_LIVE)),--live,)' in target
    assert "--manifest \"$(SELF_IMPROVE_ACCEPTANCE_MATRIX_FILE)\"" in target
    assert "--model-path \"$(SELF_IMPROVE_ACCEPTANCE_MATRIX_MODEL_PATH)\"" in target
    assert "EXPECTED_MATRIX_SHA256" in target
    assert "sha256" in target.lower()
    assert "&" not in target
    assert " -j" not in target
    assert "release-" not in target
    assert "git tag" not in target

    contract = json.loads(
        (ROOT / "config/make_target_contract.json").read_text(encoding="utf-8")
    )
    entry = next(
        item
        for item in contract["targets"]
        if item["name"] == "test-self-improve-acceptance-matrix"
    )
    assert entry == {
        "name": "test-self-improve-acceptance-matrix",
        "make_variables": [
            "SELF_IMPROVE_ACCEPTANCE_MATRIX_FILE",
            "SELF_IMPROVE_ACCEPTANCE_MATRIX_MODEL_PATH",
            "SELF_IMPROVE_ACCEPTANCE_MATRIX_LIVE",
        ],
        "behavior": (
            "make test-self-improve-acceptance-matrix "
            "SELF_IMPROVE_ACCEPTANCE_MATRIX_FILE="
            "config/self-improve/acceptance-matrix.json "
            "SELF_IMPROVE_ACCEPTANCE_MATRIX_MODEL_PATH= "
            "SELF_IMPROVE_ACCEPTANCE_MATRIX_LIVE=0"
        ),
    }


def test_document_routes_execution_to_manifest_and_existing_practitioner_evidence() -> None:
    document = DOCUMENT.read_text(encoding="utf-8")
    for fact in (
        "config/self-improve/acceptance-matrix.json",
        "test-self-improve-acceptance-matrix",
        "SELF_IMPROVE_ACCEPTANCE_MATRIX_LIVE=0",
        "SELF_IMPROVE_ACCEPTANCE_MATRIX_LIVE=1",
        "twenty",
        "cold",
        "warm",
        "fail-closed",
        "ineligible",
        "SELF_IMPROVEMENT_CATALOG_TRUTH_FIXTURE.md",
        "SELF_IMPROVEMENT_MULTIFILE_FIXTURE.md",
        "Evaluation practice and practitioner evidence",
    ):
        assert fact in document
