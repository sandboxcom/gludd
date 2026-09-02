"""Executable contract tests for the ten-shape self-improvement matrix."""

from __future__ import annotations

import hashlib
import json
import re
import sys
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from general_ludd.routing_roles.small_model_policy import DEFAULT_TASK_CONTRACTS
from general_ludd.schemas.benchmark import TaskType
from general_ludd.self_improve.codex_comparison import CodexReference
from general_ludd.self_improve.runtime import MakeResult, MakeRunner, TaskSpec
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
    validate_reference_boundaries,
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

EXPECTED_REFERENCE_PAIRS = {
    "AM-BUG-01": (
        "0af495b60a60c05b2ea6a011fd5b4beb6272b846",
        "e4c9b68aea0ff59cab77a06747471e248e9e0601",
    ),
    "AM-FEATURE-01": (
        "e4c9b68aea0ff59cab77a06747471e248e9e0601",
        "21a4759880a549eae1ba14f3332c967937690843",
    ),
    "AM-REFACTOR-01": (
        "58a36457ab6333e885b7203011b15ffc1c6af48c",
        "2bb39446d770c7e00be1e0eb18da384221f47236",
    ),
    "AM-TEST-01": (
        "459899671737a199628ac317a3626fab5ef64935",
        "0af495b60a60c05b2ea6a011fd5b4beb6272b846",
    ),
    "AM-REVIEW-01": (
        "ad09bd07d64d834a6e4d46c7d4e493d1c3229910",
        "a55a96a619135aeb21e63ab5ef2d863cbc8ce1db",
    ),
    "AM-DOC-01": (
        "2bb39446d770c7e00be1e0eb18da384221f47236",
        "ab45aae30f989a02b1c2cb98da49d8bbcfd208d1",
    ),
    "AM-DEBUG-01": (
        "ab45aae30f989a02b1c2cb98da49d8bbcfd208d1",
        "85c1b6a69b1b224ce5004bf995ad32ce3549b799",
    ),
    "AM-OPT-01": (
        "21a4759880a549eae1ba14f3332c967937690843",
        "ad09bd07d64d834a6e4d46c7d4e493d1c3229910",
    ),
    "AM-SEC-01": (
        "85c1b6a69b1b224ce5004bf995ad32ce3549b799",
        "5d46410bf204f285b5c7b5e90823d2fc0598e2ee",
    ),
    "AM-INTEGRATION-01": (
        "5d46410bf204f285b5c7b5e90823d2fc0598e2ee",
        "8c10bb7fb59975a5037d99eec85cf34d7c19173e",
    ),
}

EXPECTED_FIXTURE_PATHS = {
    case_id: f"config/self-improve/acceptance-{case_id.lower()}.json"
    for case_id in EXPECTED_CASE_IDS
}

EXPECTED_REFERENCE_ELAPSED_SECONDS = {
    "AM-BUG-01": 278.0,
    "AM-FEATURE-01": 578.0,
    "AM-REFACTOR-01": 157.0,
    "AM-TEST-01": 312.0,
    "AM-REVIEW-01": 1345.0,
    "AM-DOC-01": 603.0,
    "AM-DEBUG-01": 655.0,
    "AM-OPT-01": 385.0,
    "AM-SEC-01": 1245.0,
    "AM-INTEGRATION-01": 909.0,
}

EXPECTED_REFERENCE_CHANGED_LINES = {
    "AM-BUG-01": 29,
    "AM-FEATURE-01": 93,
    "AM-REFACTOR-01": 189,
    "AM-TEST-01": 117,
    "AM-REVIEW-01": 55,
    "AM-DOC-01": 437,
    "AM-DEBUG-01": 263,
    "AM-OPT-01": 89,
    "AM-SEC-01": 59,
    "AM-INTEGRATION-01": 739,
}


def test_canonical_matrix_materializes_every_independent_reference_pair() -> None:
    payload = json.loads(MANIFEST.read_text(encoding="utf-8"))

    actual = {
        case["case_id"]: (case["baseline_ref"], case["reference_ref"])
        for case in payload["cases"]
    }
    assert actual == EXPECTED_REFERENCE_PAIRS
    assert all(case["eligibility"] == "eligible" for case in payload["cases"])
    assert all(case["ineligible_reason"] is None for case in payload["cases"])


def test_each_matrix_fixture_is_canonical_and_digest_bound_to_its_exact_task() -> None:
    payload = json.loads(MANIFEST.read_text(encoding="utf-8"))
    cases = {case["case_id"]: case for case in payload["cases"]}
    actual_digests: dict[str, str] = {}

    for case_id, relative_path in EXPECTED_FIXTURE_PATHS.items():
        fixture = ROOT / relative_path
        raw = fixture.read_text(encoding="utf-8")
        task = json.loads(raw)
        assert raw == (
            json.dumps(task, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
            + "\n"
        )
        expected_task = dict(cases[case_id]["task"])
        expected_task["reference_elapsed_seconds"] = (
            EXPECTED_REFERENCE_ELAPSED_SECONDS[case_id]
        )
        assert task == expected_task
        assert TaskSpec.from_path(fixture).reference_elapsed_seconds == (
            EXPECTED_REFERENCE_ELAPSED_SECONDS[case_id]
        )
        actual_digests[case_id] = hashlib.sha256(raw.encode("utf-8")).hexdigest()

    assert {
        case_id: cases[case_id]["fixture_digest"] for case_id in EXPECTED_CASE_IDS
    } == actual_digests
    assert {
        case_id: cases[case_id]["fixture_path"] for case_id in EXPECTED_CASE_IDS
    } == EXPECTED_FIXTURE_PATHS


def test_reference_commits_have_exact_parent_scope_tests_and_line_facts() -> None:
    manifest = load_manifest(MANIFEST)
    runner = MakeRunner(ROOT)
    references = validate_reference_boundaries(manifest, runner=runner)

    assert len(references) == len(manifest.cases)
    for case, reference in zip(manifest.cases, references, strict=True):
        assert reference.changed_files == frozenset(case.allowed_changed_paths)
        assert reference.test_files == frozenset(case.required_test_paths)
        assert reference.changed_lines == EXPECTED_REFERENCE_CHANGED_LINES[case.case_id]
        assert reference.elapsed_seconds == (
            EXPECTED_REFERENCE_ELAPSED_SECONDS[case.case_id]
        )


@pytest.mark.parametrize(
    ("drift", "message"),
    (
        ("unreachable", "not reachable"),
        ("parent", "direct child"),
        ("missing-metadata", "metadata is incomplete"),
        ("elapsed", "elapsed bound drifted"),
        ("non-positive", "delta must be positive"),
        ("changed-files", "changed-file scope"),
        ("test-files", "test-file scope"),
        ("empty-patch", "non-empty patch"),
        ("reference-error", "facts are unavailable"),
    ),
)
def test_reference_boundary_preflight_fails_closed_on_git_fact_drift(
    monkeypatch: pytest.MonkeyPatch,
    drift: str,
    message: str,
) -> None:
    complete = load_manifest(MANIFEST)
    case = complete.cases[0]
    manifest = replace(complete, cases=(case,))

    class Runner:
        def run(
            self,
            target: str,
            variables: dict[str, str] | None = None,
            *,
            timeout: int = 120,
            read_only: bool = False,
        ) -> MakeResult:
            del target, timeout, read_only
            assert variables is not None
            commit = variables["C"]
            is_reference = commit == case.reference_ref
            parent = (
                "f" * 40
                if drift == "parent" and is_reference
                else case.baseline_ref
            )
            baseline_time = 1_000
            reference_time = baseline_time + int(
                case.task.reference_elapsed_seconds
            )
            if drift == "elapsed":
                reference_time += 1
            elif drift == "non-positive":
                reference_time = baseline_time
            committed_at = reference_time if is_reference else baseline_time
            timestamp_line = (
                "" if drift == "missing-metadata" else f"committer_unix: {committed_at}\n"
            )
            return MakeResult(
                argv=("make", "git-show-commit"),
                returncode=int(drift == "unreachable"),
                stdout=f"parent: {parent}\n{timestamp_line}",
                stderr="",
                elapsed_seconds=0.0,
            )

    def reference_builder(*_args: object) -> CodexReference:
        if drift == "reference-error":
            raise RuntimeError("reference unavailable")
        changed_files = frozenset(case.allowed_changed_paths)
        test_files = frozenset(case.required_test_paths)
        if drift == "changed-files":
            changed_files = frozenset({"src/general_ludd/self_improve/other.py"})
        if drift == "test-files":
            test_files = frozenset({"tests/unit/test_other.py"})
        assert case.baseline_ref is not None
        assert case.reference_ref is not None
        return CodexReference(
            baseline_sha=case.baseline_ref,
            reference_sha=case.reference_ref,
            changed_files=changed_files,
            test_files=test_files,
            changed_lines=0 if drift == "empty-patch" else 1,
            elapsed_seconds=case.task.reference_elapsed_seconds,
        )

    monkeypatch.setattr(matrix_runner, "build_reference", reference_builder)

    with pytest.raises(MatrixContractError, match=message):
        validate_reference_boundaries(manifest, runner=Runner())


def test_standalone_reference_fixtures_remain_independent_from_matrix_evidence() -> None:
    catalog = TaskSpec.from_path(ROOT / "config/self-improve/catalog-truth.json")
    multifile = TaskSpec.from_path(
        ROOT / "config/self-improve/context-budget-lifecycle.json"
    )

    assert infer_task_type(catalog.objective) is TaskType.SECURITY_FIX
    assert infer_task_type(multifile.objective) is TaskType.SECURITY_FIX
    manifest = load_manifest(MANIFEST)
    standalone_pairs = {
        (
            "eac05dc88c03f14fbd7dd5f4c6d72943609d9e26",
            "80b381bd87f32487d784964ce93566e3b016b191",
        ),
        (
            "80b381bd87f32487d784964ce93566e3b016b191",
            "6463324cfcf6db9b9a2f9ec203e0bd3862a1e80e",
        ),
    }
    assert all(case.eligible for case in manifest.cases)
    assert not standalone_pairs.intersection(
        (case.baseline_ref, case.reference_ref) for case in manifest.cases
    )


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
        payload["cases"][1]["fixture_digest"] = None
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
    elif case == "non-positive-reference-elapsed":
        payload["cases"][0]["task"]["reference_elapsed_seconds"] = 0
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
    assert [case.case_id for case in manifest.cases if case.eligible] == list(
        EXPECTED_CASE_IDS
    )

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
        assert case.ineligible_reason is None
        assert SHA_RE.fullmatch(str(case.baseline_ref))
        assert SHA_RE.fullmatch(str(case.reference_ref))
        assert case.fixture_path == EXPECTED_FIXTURE_PATHS[case.case_id]
        assert isinstance(case.fixture_digest, str)
        assert len(case.fixture_digest) == 64

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
        "non-positive-reference-elapsed",
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
    assert summary.status == "passed"
    assert summary.passed_steps == len(steps)
    assert summary.failed_step_ids == ()

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
    assert safe_summary.status == "passed"
    assert len(safe_runner.calls) == 11
    assert all(
        variables["SELF_IMPROVE_VALIDATE_ONLY"] == "1"
        for _, variables, _ in safe_runner.calls
    )

    live_runner = Runner(failing_ordinal=3)
    live_summary = execute_matrix(
        manifest,
        live=True,
        model_path="/tmp/gludd-acceptance-model.gguf",
        runner=live_runner,
    )
    assert live_summary.status == "failed"
    assert len(live_runner.calls) == 11


def test_incomplete_future_matrix_skips_blocked_validate_steps_and_blocks_live(
    tmp_path: Path,
) -> None:
    payload = json.loads(MANIFEST.read_text(encoding="utf-8"))
    blocked = payload["cases"][0]
    blocked.update(
        {
            "baseline_ref": None,
            "eligibility": "ineligible",
            "fixture_digest": None,
            "fixture_path": None,
            "ineligible_reason": "no_semantically_matching_independent_reference",
            "reference_ref": None,
        }
    )
    path = tmp_path / "incomplete-matrix.json"
    path.write_text(
        json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
        + "\n",
        encoding="utf-8",
    )
    manifest = load_manifest(path)

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

    validate_runner = Runner()
    validate_summary = execute_matrix(
        manifest,
        live=False,
        model_path="",
        runner=validate_runner,
    )
    assert validate_summary.status == "failed"
    assert validate_summary.passed_steps == 9
    assert len(validate_runner.calls) == 9

    live_runner = Runner()
    live_summary = execute_matrix(
        manifest,
        live=True,
        model_path="/tmp/gludd-acceptance-model.gguf",
        runner=live_runner,
    )
    assert live_summary.status == "failed"
    assert live_summary.passed_steps == 0
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


def test_runner_cli_returns_bounded_status_for_failed_and_invalid_matrix(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        matrix_runner,
        "execute_matrix",
        lambda *_args, **_kwargs: SimpleNamespace(status="failed"),
    )
    monkeypatch.setattr(
        matrix_runner,
        "validate_reference_boundaries",
        lambda *_args, **_kwargs: (),
    )
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


def test_git_show_commit_exposes_stable_boundary_metadata_additively() -> None:
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    target = _target_block(makefile, "git-show-commit")

    assert "git log -1 --format='%H%nparent: %P%ncommitter_unix: %ct%n%s' $(C)" in target
    assert 'echo "--- $(C) summary ---"' in target
    assert 'echo "--- files touched ---"' in target
    assert "git show --stat --oneline $(C) | tail -n +2" in target

    contract = json.loads(
        (ROOT / "config/make_target_contract.json").read_text(encoding="utf-8")
    )
    entry = next(item for item in contract["targets"] if item["name"] == "git-show-commit")
    assert entry == {
        "name": "git-show-commit",
        "make_variables": ["C"],
        "behavior": (
            "make git-show-commit "
            "C=e4c9b68aea0ff59cab77a06747471e248e9e0601"
        ),
    }


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
    manifest_digest = hashlib.sha256(MANIFEST.read_bytes()).hexdigest()
    assert f'EXPECTED_MATRIX_SHA256="{manifest_digest}"' in target
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
        "committer Unix timestamp",
        "conservative end-to-end upper bound",
        "not an inference-only benchmark",
    ):
        assert fact in document

    for case_id, (baseline_ref, reference_ref) in EXPECTED_REFERENCE_PAIRS.items():
        assert case_id in document
        assert baseline_ref in document
        assert reference_ref in document


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
        "`validate_reference_boundaries`",
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
        "make git-show-commit C=<40-character-sha>",
        "make agent-cleanup BRANCH=acceptance-am-doc-reference",
        "SELF_IMPROVE_ACCEPTANCE_MATRIX_LIVE=0",
        "SELF_IMPROVE_ACCEPTANCE_MATRIX_LIVE=1",
        "does not claim a live pass",
        "zero or negative result is missing evidence",
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
        "def validate_reference_boundaries(",
        "def execute_matrix(",
    ):
        assert declaration in runner
    assert "EXPECTED_MATRIX_SHA256" in makefile
