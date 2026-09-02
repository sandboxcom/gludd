"""Executable contract tests for the ten-shape self-improvement matrix."""

from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from scripts.run_self_improve_e2e import TaskSpec

from general_ludd.routing_roles.small_model_policy import DEFAULT_TASK_CONTRACTS
from general_ludd.schemas.benchmark import TaskType
from general_ludd.self_improve.task_diversity import infer_task_type
from general_ludd.small_models.recommender import map_task_to_capabilities
from tests.unit import self_improve_acceptance_matrix_runner as matrix_runner
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
    elif case == "extra-top-level-field":
        payload["unexpected"] = True
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
    elif case == "empty-paths":
        payload["cases"][0]["allowed_changed_paths"] = []
    elif case == "non-string-path":
        payload["cases"][0]["allowed_changed_paths"] = [1]
    elif case == "parent-path":
        payload["cases"][0]["allowed_changed_paths"] = ["../outside.py"]
    elif case == "duplicate-path":
        path = payload["cases"][0]["allowed_changed_paths"][0]
        payload["cases"][0]["allowed_changed_paths"] = [path, path]
    elif case == "invalid-task":
        payload["cases"][0]["task"]["objective"] = ""
    elif case in {
        "invalid-eligibility",
        "invalid-fixture-digest",
        "non-string-fixture-path",
        "fixture-outside-config",
        "fixture-missing",
        "fixture-byte-drift",
        "fixture-invalid-task",
        "fixture-task-mismatch",
    }:
        row = payload["cases"][0]
        row.update(
            {
                "baseline_ref": "1" * 40,
                "eligibility": "eligible",
                "fixture_digest": "0" * 64,
                "fixture_path": "config/self-improve/catalog-truth.json",
                "ineligible_reason": None,
                "reference_ref": "2" * 40,
            }
        )
        if case == "invalid-eligibility":
            row["eligibility"] = "unknown"
        elif case == "invalid-fixture-digest":
            row["fixture_digest"] = "not-a-digest"
        elif case == "non-string-fixture-path":
            row["fixture_path"] = 1
        elif case == "fixture-outside-config":
            row["fixture_path"] = "tasks/reference.json"
        elif case == "fixture-missing":
            row["fixture_path"] = "config/self-improve/missing-reference.json"
        elif case == "fixture-invalid-task":
            fixture = ROOT / "config/self-improve/failure-corpus.json"
            row["fixture_path"] = "config/self-improve/failure-corpus.json"
            row["fixture_digest"] = hashlib.sha256(fixture.read_bytes()).hexdigest()
        elif case == "fixture-task-mismatch":
            fixture = ROOT / "config/self-improve/catalog-truth.json"
            row["fixture_digest"] = hashlib.sha256(fixture.read_bytes()).hexdigest()
    elif case == "case-id-drift":
        payload["cases"][0]["case_id"] = "AM-WRONG-01"
    elif case == "task-type-drift":
        payload["cases"][0]["task_type"] = "feature"
    elif case == "invalid-role":
        payload["cases"][0]["role"] = "not-a-role"
    elif case == "unknown-contract":
        payload["cases"][0]["task_kind"] = "not-a-contract"
    elif case == "malformed-checks":
        payload["cases"][0]["required_acceptance_checks"] = "syntax_valid"
    elif case == "too-many-attempts":
        payload["cases"][0]["max_attempts"] = 3
    elif case == "schema-version":
        payload["schema_version"] = 2
    elif case == "suite-id":
        payload["suite_id"] = "untracked-suite"
    elif case == "timeout":
        payload["case_timeout_seconds"] = 3_601
    elif case == "duplicate-task-id":
        payload["cases"][1]["task"]["task_id"] = payload["cases"][0]["task"][
            "task_id"
        ]
    elif case == "declared-budget-too-small":
        payload["max_total_attempts"] = 19
    path = tmp_path / f"{case}.json"
    encoded = (
        json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
        + "\n"
    )
    if case == "noncanonical-json":
        encoded = json.dumps(payload, indent=2) + "\n"
    path.write_text(encoded, encoding="utf-8")
    return path


def _write_fully_eligible_manifest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Path:
    repository = tmp_path / "repository"
    fixture_root = repository / "config/self-improve"
    fixture_root.mkdir(parents=True)
    payload = json.loads(MANIFEST.read_text(encoding="utf-8"))
    for ordinal, case in enumerate(payload["cases"], start=1):
        relative_fixture = (
            f"config/self-improve/{case['case_id'].lower()}-reference.json"
        )
        fixture = repository / relative_fixture
        fixture_text = (
            json.dumps(
                case["task"],
                ensure_ascii=True,
                separators=(",", ":"),
                sort_keys=True,
            )
            + "\n"
        )
        fixture.write_text(fixture_text, encoding="utf-8")
        case.update(
            {
                "baseline_ref": f"{ordinal:040x}",
                "eligibility": "eligible",
                "fixture_digest": hashlib.sha256(
                    fixture_text.encode("utf-8")
                ).hexdigest(),
                "fixture_path": relative_fixture,
                "ineligible_reason": None,
                "reference_ref": f"{ordinal + 100:040x}",
            }
        )
    path = tmp_path / "eligible-matrix.json"
    path.write_text(
        json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(matrix_runner, "_ROOT", repository)
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
        "extra-top-level-field",
        "empty-paths",
        "non-string-path",
        "parent-path",
        "duplicate-path",
        "invalid-task",
        "invalid-eligibility",
        "invalid-fixture-digest",
        "non-string-fixture-path",
        "fixture-outside-config",
        "fixture-missing",
        "fixture-byte-drift",
        "fixture-invalid-task",
        "fixture-task-mismatch",
        "case-id-drift",
        "task-type-drift",
        "invalid-role",
        "unknown-contract",
        "malformed-checks",
        "too-many-attempts",
        "noncanonical-json",
        "schema-version",
        "suite-id",
        "timeout",
        "duplicate-task-id",
        "declared-budget-too-small",
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


def test_fully_admitted_matrix_replays_every_step_through_the_bounded_make_seam(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest = load_manifest(_write_fully_eligible_manifest(tmp_path, monkeypatch))

    class Runner:
        def __init__(self) -> None:
            self.calls: list[tuple[str, dict[str, str], int]] = []

        def run_observable(
            self,
            target: str,
            variables: dict[str, str],
            *,
            timeout: int,
        ) -> SimpleNamespace:
            self.calls.append((target, variables, timeout))
            return SimpleNamespace(returncode=0)

    runner = Runner()
    summary = execute_matrix(
        manifest,
        live=True,
        model_path="/tmp/gludd-acceptance-model.gguf",
        runner=runner,
    )

    assert summary.status == "passed"
    assert len(runner.calls) == 11
    assert all(target == "test-self-improve" for target, _, _ in runner.calls)
    assert all(timeout == 3_600 for _, _, timeout in runner.calls)
    assert [call[1]["SELF_IMPROVE_VALIDATE_ONLY"] for call in runner.calls] == [
        "0"
    ] * 11
    assert all(
        variables["SELF_IMPROVE_MODEL_PATH"]
        == "/tmp/gludd-acceptance-model.gguf"
        for _, variables, _ in runner.calls
    )
    assert runner.calls[0][1]["TARGET"] == "acceptance-am-bug-01-cold"
    assert runner.calls[1][1]["TARGET"] == "acceptance-am-bug-01-warm"
    assert runner.calls[-1][1]["TARGET"] == "acceptance-am-integration-01-standard"

    class FailingRunner:
        def run_observable(
            self,
            target: str,
            variables: dict[str, str],
            *,
            timeout: int,
        ) -> SimpleNamespace:
            raise RuntimeError("bounded runner failure")

    failed = execute_matrix(
        manifest,
        live=False,
        model_path="",
        runner=FailingRunner(),
    )
    assert failed.status == "failed"
    assert failed.passed_steps == 0


def test_fully_admitted_manifest_rejects_reused_reference_pair(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = _write_fully_eligible_manifest(tmp_path, monkeypatch)
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["cases"][1]["baseline_ref"] = payload["cases"][0]["baseline_ref"]
    payload["cases"][1]["reference_ref"] = payload["cases"][0]["reference_ref"]
    path.write_text(
        json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
        + "\n",
        encoding="utf-8",
    )

    with pytest.raises(
        MatrixContractError,
        match="independent reference evidence cannot be reused",
    ):
        load_manifest(path)


def test_runner_cli_returns_bounded_status_for_incomplete_and_invalid_matrix(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        sys,
        "argv",
        ["self-improve-acceptance-matrix", "--manifest", str(MANIFEST)],
    )
    assert matrix_runner.main() == 1

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "self-improve-acceptance-matrix",
            "--manifest",
            str(tmp_path / "missing.json"),
        ],
    )
    assert matrix_runner.main() == 2


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


def test_document_has_traceable_fixture_authoring_and_replay_guide() -> None:
    document = DOCUMENT.read_text(encoding="utf-8")
    runner_path = ROOT / "tests/unit/self_improve_acceptance_matrix_runner.py"
    runner = runner_path.read_text(encoding="utf-8")
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")

    for fact in (
        "## Author an independent reference fixture",
        "Reference independence invariant",
        "`MatrixCase`",
        "`_validate_evidence`",
        "`load_manifest`",
        "`execute_matrix`",
        "`EXPECTED_MATRIX_SHA256`",
        "`fixture_digest`",
        "`baseline_ref`",
        "`reference_ref`",
        "`eligibility`",
        "`ineligible_reason`",
        "json.dumps",
        "ensure_ascii=True",
        "sort_keys=True",
        "hashlib.sha256(fixture_bytes).hexdigest()",
        "make agent-worktree-base BRANCH=acceptance-am-doc-reference ",
        "BASE=<40-character-baseline-sha>",
        "make agent-cleanup BRANCH=acceptance-am-doc-reference",
        "SELF_IMPROVE_ACCEPTANCE_MATRIX_LIVE=0",
        "SELF_IMPROVE_ACCEPTANCE_MATRIX_LIVE=1",
        "does not claim a live pass",
    ):
        assert fact in document

    for relative_link in (
        "../config/self-improve/acceptance-matrix.json",
        "../tests/unit/self_improve_acceptance_matrix_runner.py",
        "../Makefile",
    ):
        assert f"]({relative_link})" in document
        assert (DOCUMENT.parent / relative_link).resolve().is_file()

    for declaration in (
        "class MatrixCase:",
        "def _validate_evidence(",
        "def load_manifest(",
        "def execute_matrix(",
    ):
        assert declaration in runner
    assert "EXPECTED_MATRIX_SHA256" in makefile
