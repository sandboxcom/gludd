"""Validate and execute the tracked self-improvement acceptance matrix."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Protocol, cast

from general_ludd.routing_roles.small_model_policy import DEFAULT_TASK_CONTRACTS
from general_ludd.schemas.benchmark import TaskRole, TaskType
from general_ludd.self_improve.codex_comparison import CodexReference
from general_ludd.self_improve.runtime import (
    MakeRunner,
    TaskSpec,
    _TargetRunner,
    build_reference,
)
from general_ludd.self_improve.task_diversity import infer_task_type
from general_ludd.small_models.recommender import map_task_to_capabilities

_ROOT = Path(__file__).resolve().parents[2]
_MAX_MANIFEST_BYTES = 262_144
_MAX_TOTAL_ATTEMPTS = 20
_MAX_CASE_TIMEOUT_SECONDS = 3_600
_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
_INELIGIBLE_REASON = "no_semantically_matching_independent_reference"
_CASE_ID_BY_TASK_TYPE = {
    TaskType.BUG_FIX: "AM-BUG-01",
    TaskType.FEATURE: "AM-FEATURE-01",
    TaskType.REFACTOR: "AM-REFACTOR-01",
    TaskType.TEST_WRITE: "AM-TEST-01",
    TaskType.CODE_REVIEW: "AM-REVIEW-01",
    TaskType.DOCUMENTATION: "AM-DOC-01",
    TaskType.DEBUGGING: "AM-DEBUG-01",
    TaskType.OPTIMIZATION: "AM-OPT-01",
    TaskType.SECURITY_FIX: "AM-SEC-01",
    TaskType.INTEGRATION: "AM-INTEGRATION-01",
}
_TOP_LEVEL_FIELDS = frozenset(
    {
        "case_timeout_seconds",
        "cases",
        "max_total_attempts",
        "schema_version",
        "sentinel",
        "serial",
        "suite_id",
    }
)
_CASE_FIELDS = frozenset(
    {
        "allowed_changed_paths",
        "baseline_ref",
        "case_id",
        "eligibility",
        "fixture_digest",
        "fixture_path",
        "ineligible_reason",
        "max_attempts",
        "reference_ref",
        "required_acceptance_checks",
        "required_test_paths",
        "role",
        "task",
        "task_kind",
        "task_type",
    }
)


class MatrixContractError(ValueError):
    """Raised when the tracked matrix has ambiguous or unsafe semantics."""


class _CommandResult(Protocol):
    returncode: int


class _ObservableRunner(Protocol):
    def run_observable(
        self,
        target: str,
        variables: dict[str, str],
        *,
        timeout: int,
    ) -> _CommandResult:
        """Run one observable, bounded Make target."""


@dataclass(frozen=True)
class MatrixCase:
    """One immutable TaskType fixture and its independent reference boundary."""

    case_id: str
    task_type: TaskType
    task_kind: str
    role: TaskRole
    required_acceptance_checks: tuple[str, ...]
    eligible: bool
    ineligible_reason: str | None
    fixture_path: str | None
    fixture_digest: str | None
    baseline_ref: str | None
    reference_ref: str | None
    max_attempts: int
    allowed_changed_paths: tuple[str, ...]
    required_test_paths: tuple[str, ...]
    task: TaskSpec


@dataclass(frozen=True)
class MatrixManifest:
    """Complete immutable identity and resource policy for one matrix run."""

    schema_version: int
    suite_id: str
    serial: bool
    max_total_attempts: int
    case_timeout_seconds: int
    sentinel_case_id: str
    sentinel_phases: tuple[str, ...]
    cases: tuple[MatrixCase, ...]


@dataclass(frozen=True)
class MatrixStep:
    """One serial matrix invocation, including a sentinel cache phase."""

    ordinal: int
    case: MatrixCase
    phase: str
    max_attempts: int

    @property
    def step_id(self) -> str:
        """Return the stable identity used for aggregation and diagnostics."""
        return f"{self.case.case_id}:{self.phase}"


@dataclass(frozen=True)
class MatrixOutcome:
    """Bounded terminal result for one expected execution step."""

    step_id: str
    returncode: int


@dataclass(frozen=True)
class MatrixSummary:
    """Fail-closed conjunction of every expected matrix outcome."""

    status: str
    total_steps: int
    passed_steps: int
    failed_step_ids: tuple[str, ...]


def _require_exact_fields(
    value: object,
    expected: frozenset[str],
    label: str,
) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) != expected:
        raise MatrixContractError(f"{label} must contain exactly {sorted(expected)}")
    return cast(dict[str, object], value)


def _require_path_tuple(
    value: object,
    *,
    label: str,
    maximum: int,
) -> tuple[str, ...]:
    if not isinstance(value, list) or not 1 <= len(value) <= maximum:
        raise MatrixContractError(f"{label} must contain 1..{maximum} paths")
    paths: list[str] = []
    for raw in value:
        if not isinstance(raw, str) or not raw or raw != raw.strip():
            raise MatrixContractError(f"{label} contains an invalid path")
        path = PurePosixPath(raw)
        if path.is_absolute() or ".." in path.parts or str(path) != raw:
            raise MatrixContractError(f"{label} paths must be canonical and relative")
        paths.append(raw)
    if len(paths) != len(set(paths)):
        raise MatrixContractError(f"{label} paths must be unique")
    return tuple(paths)


def _task_spec(value: object, case_id: str) -> TaskSpec:
    task = _require_exact_fields(
        value,
        frozenset(
            {
                "canonical_make_commands",
                "objective",
                "reference_elapsed_seconds",
                "task_id",
            }
        ),
        f"{case_id} task",
    )
    encoded = json.dumps(
        task,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )
    with tempfile.TemporaryDirectory(prefix="gludd-matrix-task-validate-") as raw:
        task_path = Path(raw) / "task.json"
        task_path.write_text(encoded, encoding="utf-8")
        try:
            return TaskSpec.from_path(task_path)
        except (OSError, ValueError) as exc:
            raise MatrixContractError(f"{case_id} task is invalid: {exc}") from exc


def _task_identity(task: TaskSpec) -> tuple[object, ...]:
    return (
        task.task_id,
        task.objective,
        task.canonical_make_commands,
        task.reference_elapsed_seconds,
    )


def _validate_evidence(
    raw: dict[str, object],
    *,
    case_id: str,
    task: TaskSpec,
) -> tuple[bool, str | None, str | None, str | None, str | None, str | None]:
    status = raw["eligibility"]
    reason = raw["ineligible_reason"]
    fixture_path = raw["fixture_path"]
    fixture_digest = raw["fixture_digest"]
    baseline_ref = raw["baseline_ref"]
    reference_ref = raw["reference_ref"]
    if status == "ineligible":
        if reason != _INELIGIBLE_REASON or any(
            value is not None
            for value in (
                fixture_path,
                fixture_digest,
                baseline_ref,
                reference_ref,
            )
        ):
            raise MatrixContractError(
                f"{case_id} ineligible evidence must be explicitly empty"
            )
        return False, reason, None, None, None, None
    if status != "eligible" or reason is not None:
        raise MatrixContractError(f"{case_id} eligibility is invalid")
    if task.reference_elapsed_seconds <= 0:
        raise MatrixContractError(
            f"{case_id} eligible reference elapsed time must be positive"
        )
    if (
        not isinstance(baseline_ref, str)
        or _SHA_RE.fullmatch(baseline_ref) is None
        or not isinstance(reference_ref, str)
        or _SHA_RE.fullmatch(reference_ref) is None
        or baseline_ref == reference_ref
    ):
        raise MatrixContractError(
            f"{case_id} requires distinct immutable Git identities"
        )
    if not isinstance(fixture_digest, str) or _DIGEST_RE.fullmatch(
        fixture_digest
    ) is None:
        raise MatrixContractError(f"{case_id} fixture digest is invalid")
    if not isinstance(fixture_path, str):
        raise MatrixContractError(f"{case_id} fixture path is invalid")
    path_tuple = _require_path_tuple(
        [fixture_path],
        label=f"{case_id} fixture_path",
        maximum=1,
    )
    relative_path = path_tuple[0]
    if not relative_path.startswith("config/self-improve/"):
        raise MatrixContractError(f"{case_id} fixture must be repository tracked")
    resolved = _ROOT / relative_path
    try:
        if resolved.is_symlink() or not resolved.is_file():
            raise MatrixContractError(f"{case_id} fixture must be one regular file")
        fixture_bytes = resolved.read_bytes()
    except OSError as exc:
        raise MatrixContractError(f"{case_id} fixture is unreadable") from exc
    if hashlib.sha256(fixture_bytes).hexdigest() != fixture_digest:
        raise MatrixContractError(f"{case_id} fixture digest drifted")
    try:
        fixture_task = TaskSpec.from_path(resolved)
    except (OSError, ValueError) as exc:
        raise MatrixContractError(f"{case_id} tracked fixture is invalid") from exc
    if _task_identity(fixture_task) != _task_identity(task):
        raise MatrixContractError(
            f"{case_id} task does not match its independent-reference fixture"
        )
    return (
        True,
        None,
        relative_path,
        fixture_digest,
        baseline_ref,
        reference_ref,
    )


def _parse_case(value: object, expected_type: TaskType) -> MatrixCase:
    raw = _require_exact_fields(value, _CASE_FIELDS, expected_type.value)
    expected_case_id = _CASE_ID_BY_TASK_TYPE[expected_type]
    if raw["case_id"] != expected_case_id:
        raise MatrixContractError(
            f"{expected_type.value} must use fixture {expected_case_id}"
        )
    if raw["task_type"] != expected_type.value:
        raise MatrixContractError(f"{expected_case_id} TaskType identity drifted")
    try:
        role = TaskRole(cast(str, raw["role"]))
    except (TypeError, ValueError) as exc:
        raise MatrixContractError(f"{expected_case_id} role is invalid") from exc
    task_kind = raw["task_kind"]
    if not isinstance(task_kind, str) or task_kind not in DEFAULT_TASK_CONTRACTS:
        raise MatrixContractError(f"{expected_case_id} task contract is unknown")
    contract = DEFAULT_TASK_CONTRACTS[task_kind]
    if role not in contract.allowed_roles:
        raise MatrixContractError(f"{expected_case_id} role is outside its contract")

    checks_raw = raw["required_acceptance_checks"]
    if not isinstance(checks_raw, list) or not all(
        isinstance(check, str) for check in checks_raw
    ):
        raise MatrixContractError(f"{expected_case_id} checks are malformed")
    checks = tuple(cast(list[str], checks_raw))
    expected_checks = tuple(sorted(contract.required_acceptance_checks))
    if checks != expected_checks:
        raise MatrixContractError(f"{expected_case_id} acceptance checks drifted")

    max_attempts = raw["max_attempts"]
    if type(max_attempts) is not int or not 1 <= max_attempts <= 2:
        raise MatrixContractError(f"{expected_case_id} max_attempts must be 1 or 2")
    path_limit = 6 if expected_type is TaskType.INTEGRATION else 3
    allowed_paths = _require_path_tuple(
        raw["allowed_changed_paths"],
        label=f"{expected_case_id} allowed_changed_paths",
        maximum=path_limit,
    )
    if expected_type is TaskType.INTEGRATION and len(allowed_paths) < 4:
        raise MatrixContractError("integration fixture must span four to six paths")
    required_tests = _require_path_tuple(
        raw["required_test_paths"],
        label=f"{expected_case_id} required_test_paths",
        maximum=2,
    )
    if not set(required_tests) <= set(allowed_paths) or any(
        not path.startswith("tests/") for path in required_tests
    ):
        raise MatrixContractError(
            f"{expected_case_id} required tests must be allowed test paths"
        )

    task = _task_spec(raw["task"], expected_case_id)
    try:
        inferred_type = infer_task_type(task.objective)
    except ValueError as exc:
        raise MatrixContractError(
            f"{expected_case_id} objective has no TaskType mapping"
        ) from exc
    mapped = map_task_to_capabilities(task.objective)
    if inferred_type is not expected_type:
        raise MatrixContractError(
            f"{expected_case_id} objective TaskType drifted: "
            f"expected={expected_type.value} actual={inferred_type.value}"
        )
    if not mapped or mapped[0] != (task_kind, role):
        raise MatrixContractError(f"{expected_case_id} objective task contract drifted")
    (
        eligible,
        ineligible_reason,
        fixture_path,
        fixture_digest,
        baseline_ref,
        reference_ref,
    ) = _validate_evidence(raw, case_id=expected_case_id, task=task)

    return MatrixCase(
        case_id=expected_case_id,
        task_type=expected_type,
        task_kind=task_kind,
        role=role,
        required_acceptance_checks=checks,
        eligible=eligible,
        ineligible_reason=ineligible_reason,
        fixture_path=fixture_path,
        fixture_digest=fixture_digest,
        baseline_ref=baseline_ref,
        reference_ref=reference_ref,
        max_attempts=max_attempts,
        allowed_changed_paths=allowed_paths,
        required_test_paths=required_tests,
        task=task,
    )


def load_manifest(path: Path) -> MatrixManifest:
    """Load one canonical, exact, immutable ten-shape matrix manifest."""
    try:
        if path.is_symlink() or not path.is_file():
            raise MatrixContractError("matrix manifest must be one regular file")
        if path.stat().st_size > _MAX_MANIFEST_BYTES:
            raise MatrixContractError("matrix manifest exceeds 262144 bytes")
        raw_text = path.read_text(encoding="utf-8")
        payload = json.loads(raw_text)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MatrixContractError("matrix manifest is not canonical UTF-8 JSON") from exc
    canonical = json.dumps(
        payload,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ) + "\n"
    if raw_text != canonical:
        raise MatrixContractError("matrix manifest JSON is not canonical")

    raw = _require_exact_fields(payload, _TOP_LEVEL_FIELDS, "matrix manifest")
    if raw["schema_version"] != 1 or type(raw["schema_version"]) is not int:
        raise MatrixContractError("matrix schema_version must be exactly 1")
    if raw["suite_id"] != "self-improvement-acceptance-matrix-v1":
        raise MatrixContractError("matrix suite_id is unsupported")
    if raw["serial"] is not True:
        raise MatrixContractError("matrix execution must be serial")
    max_total_attempts = raw["max_total_attempts"]
    if (
        type(max_total_attempts) is not int
        or not 1 <= max_total_attempts <= _MAX_TOTAL_ATTEMPTS
    ):
        raise MatrixContractError("matrix attempt budget must be between 1 and 20")
    timeout = raw["case_timeout_seconds"]
    if (
        type(timeout) is not int
        or not 1 <= timeout <= _MAX_CASE_TIMEOUT_SECONDS
    ):
        raise MatrixContractError("matrix case timeout must be between 1 and 3600")

    sentinel = _require_exact_fields(
        raw["sentinel"],
        frozenset({"case_id", "phases"}),
        "matrix sentinel",
    )
    if sentinel["case_id"] != "AM-BUG-01" or sentinel["phases"] != [
        "cold",
        "warm",
    ]:
        raise MatrixContractError("matrix sentinel must be AM-BUG-01 cold then warm")

    cases_raw = raw["cases"]
    if not isinstance(cases_raw, list) or len(cases_raw) != len(TaskType):
        raise MatrixContractError("matrix must contain exactly one case per TaskType")
    cases = tuple(
        _parse_case(value, expected_type)
        for value, expected_type in zip(cases_raw, TaskType, strict=True)
    )
    task_ids = [case.task.task_id for case in cases]
    if len(task_ids) != len(set(task_ids)):
        raise MatrixContractError("matrix task IDs must be unique")
    if cases[0].max_attempts != 1 or any(
        case.max_attempts != 2 for case in cases[1:]
    ):
        raise MatrixContractError(
            "sentinel must use one attempt per replay and all other cases two"
        )
    eligible_pairs = [
        (case.baseline_ref, case.reference_ref) for case in cases if case.eligible
    ]
    if len(eligible_pairs) != len(set(eligible_pairs)):
        raise MatrixContractError("independent reference evidence cannot be reused")

    manifest = MatrixManifest(
        schema_version=1,
        suite_id=raw["suite_id"],
        serial=True,
        max_total_attempts=max_total_attempts,
        case_timeout_seconds=timeout,
        sentinel_case_id=sentinel["case_id"],
        sentinel_phases=tuple(cast(list[str], sentinel["phases"])),
        cases=cases,
    )
    steps = build_execution_plan(manifest)
    if sum(step.max_attempts for step in steps) > manifest.max_total_attempts:
        raise MatrixContractError("matrix execution plan exceeds its attempt budget")
    return manifest


def build_execution_plan(manifest: MatrixManifest) -> tuple[MatrixStep, ...]:
    """Expand ten fixtures into one ordered serial plan with a cold/warm replay."""
    steps: list[MatrixStep] = []
    for case in manifest.cases:
        phases = (
            manifest.sentinel_phases
            if case.case_id == manifest.sentinel_case_id
            else ("standard",)
        )
        for phase in phases:
            steps.append(
                MatrixStep(
                    ordinal=len(steps) + 1,
                    case=case,
                    phase=phase,
                    max_attempts=case.max_attempts,
                )
            )
    if sum(step.max_attempts for step in steps) > _MAX_TOTAL_ATTEMPTS:
        raise MatrixContractError("matrix execution plan exceeds twenty attempts")
    return tuple(steps)


def _commit_boundary(
    runner: _TargetRunner,
    sha: str,
    *,
    case_id: str,
) -> tuple[tuple[str, ...], int]:
    """Return exact parent identities and committer epoch from the Make seam."""
    summary = runner.run(
        "git-show-commit",
        {"C": sha},
        read_only=True,
    )
    if summary.returncode != 0:
        raise MatrixContractError(f"{case_id} reference commit is not reachable")
    parent_lines = tuple(
        line.removeprefix("parent: ")
        for line in summary.stdout.splitlines()
        if line.startswith("parent: ")
    )
    timestamp_lines = tuple(
        line.removeprefix("committer_unix: ")
        for line in summary.stdout.splitlines()
        if line.startswith("committer_unix: ")
    )
    if len(parent_lines) != 1 or len(timestamp_lines) != 1:
        raise MatrixContractError(
            f"{case_id} commit metadata is incomplete or ambiguous"
        )
    try:
        committed_at = int(timestamp_lines[0])
    except ValueError as exc:
        raise MatrixContractError(
            f"{case_id} committer timestamp is not an integer"
        ) from exc
    if committed_at < 0 or str(committed_at) != timestamp_lines[0]:
        raise MatrixContractError(
            f"{case_id} committer timestamp is not canonical"
        )
    parents = tuple(parent_lines[0].split()) if parent_lines[0] else ()
    if any(_SHA_RE.fullmatch(parent) is None for parent in parents):
        raise MatrixContractError(f"{case_id} parent identity is invalid")
    return parents, committed_at


def validate_reference_boundaries(
    manifest: MatrixManifest,
    *,
    runner: _TargetRunner | None = None,
) -> tuple[CodexReference, ...]:
    """Resolve every eligible direct-parent reference and exact path boundary."""
    active_runner = runner or MakeRunner(_ROOT)
    references: list[CodexReference] = []
    for case in manifest.cases:
        if not case.eligible:
            continue
        assert case.baseline_ref is not None
        assert case.reference_ref is not None
        _baseline_parents, baseline_committed_at = _commit_boundary(
            active_runner,
            case.baseline_ref,
            case_id=case.case_id,
        )
        reference_parents, reference_committed_at = _commit_boundary(
            active_runner,
            case.reference_ref,
            case_id=case.case_id,
        )
        if reference_parents != (case.baseline_ref,):
            raise MatrixContractError(
                f"{case.case_id} reference must be a direct child of its baseline"
            )
        elapsed_seconds = reference_committed_at - baseline_committed_at
        if elapsed_seconds <= 0:
            raise MatrixContractError(
                f"{case.case_id} committer-time delta must be positive"
            )
        if float(elapsed_seconds) != case.task.reference_elapsed_seconds:
            raise MatrixContractError(
                f"{case.case_id} reference elapsed bound drifted from commit metadata"
            )
        try:
            reference = build_reference(
                active_runner,
                case.baseline_ref,
                case.reference_ref,
                case.task.reference_elapsed_seconds,
            )
        except (OSError, RuntimeError, ValueError) as exc:
            raise MatrixContractError(
                f"{case.case_id} reference facts are unavailable"
            ) from exc
        if reference.changed_files != frozenset(case.allowed_changed_paths):
            raise MatrixContractError(
                f"{case.case_id} reference changed-file scope drifted"
            )
        if reference.test_files != frozenset(case.required_test_paths):
            raise MatrixContractError(
                f"{case.case_id} reference test-file scope drifted"
            )
        if reference.changed_lines <= 0:
            raise MatrixContractError(
                f"{case.case_id} reference must contain a non-empty patch"
            )
        references.append(reference)
    return tuple(references)


def aggregate_outcomes(
    steps: tuple[MatrixStep, ...],
    outcomes: tuple[MatrixOutcome, ...],
) -> MatrixSummary:
    """Pass only one complete, duplicate-free set of successful outcomes."""
    expected = tuple(step.step_id for step in steps)
    by_id: dict[str, list[MatrixOutcome]] = {}
    for outcome in outcomes:
        by_id.setdefault(outcome.step_id, []).append(outcome)
    failed = [
        step_id
        for step_id, step in zip(expected, steps, strict=True)
        if not step.case.eligible
        or len(by_id.get(step_id, ())) != 1
        or by_id[step_id][0].returncode != 0
    ]
    failed.extend(sorted(set(by_id) - set(expected)))
    passed_steps = sum(
        step.case.eligible
        and len(by_id.get(step_id, ())) == 1
        and by_id[step_id][0].returncode == 0
        for step_id, step in zip(expected, steps, strict=True)
    )
    status = (
        "passed"
        if not failed and len(outcomes) == len(expected) and passed_steps == len(expected)
        else "failed"
    )
    return MatrixSummary(
        status=status,
        total_steps=len(expected),
        passed_steps=passed_steps,
        failed_step_ids=tuple(failed),
    )


def _write_task_files(manifest: MatrixManifest, root: Path) -> dict[str, Path]:
    task_files: dict[str, Path] = {}
    for case in manifest.cases:
        payload = {
            "canonical_make_commands": list(case.task.canonical_make_commands),
            "objective": case.task.objective,
            "reference_elapsed_seconds": case.task.reference_elapsed_seconds,
            "task_id": case.task.task_id,
        }
        path = root / f"{case.case_id.lower()}.json"
        path.write_text(
            json.dumps(
                payload,
                ensure_ascii=True,
                separators=(",", ":"),
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        task_files[case.case_id] = path
    return task_files


def execute_matrix(
    manifest: MatrixManifest,
    *,
    live: bool,
    model_path: str,
    runner: _ObservableRunner | None = None,
) -> MatrixSummary:
    """Execute every step serially through the existing observable Make seam."""
    active_runner = runner or MakeRunner(_ROOT)
    steps = build_execution_plan(manifest)
    outcomes: list[MatrixOutcome] = []
    if live and any(not case.eligible for case in manifest.cases):
        for case in manifest.cases:
            if not case.eligible:
                print(
                    "SELF_IMPROVE_MATRIX_CASE_INELIGIBLE "
                    f"case={case.case_id} reason={case.ineligible_reason}",
                    flush=True,
                )
        print("SELF_IMPROVE_MATRIX_LIVE_BLOCKED reason=incomplete_matrix", flush=True)
        summary = aggregate_outcomes(steps, ())
        _print_summary(manifest, summary)
        return summary
    with tempfile.TemporaryDirectory(prefix="gludd-self-improve-matrix-") as raw:
        task_files = _write_task_files(manifest, Path(raw))
        for step in steps:
            print(
                "SELF_IMPROVE_MATRIX_CASE_START "
                f"step={step.ordinal}/{len(steps)} case={step.case.case_id} "
                f"phase={step.phase} attempts={step.max_attempts} "
                f"mode={'live' if live else 'validate-only'}",
                flush=True,
            )
            if not step.case.eligible:
                returncode = 2
                outcomes.append(MatrixOutcome(step.step_id, returncode))
                print(
                    "SELF_IMPROVE_MATRIX_CASE_END "
                    f"case={step.case.case_id} phase={step.phase} rc={returncode} "
                    f"reason={step.case.ineligible_reason}",
                    flush=True,
                )
                continue
            try:
                result = active_runner.run_observable(
                    "test-self-improve",
                    {
                        "TARGET": (
                            f"acceptance-{step.case.case_id.lower()}-{step.phase}"
                        ),
                        "SELF_IMPROVE_MODEL_PATH": model_path,
                        "SELF_IMPROVE_BASELINE_REF": cast(
                            str, step.case.baseline_ref
                        ),
                        "SELF_IMPROVE_REFERENCE_REF": cast(
                            str, step.case.reference_ref
                        ),
                        "SELF_IMPROVE_TASK_FILE": str(task_files[step.case.case_id]),
                        "SELF_IMPROVE_MAX_ATTEMPTS": str(step.max_attempts),
                        "SELF_IMPROVE_VALIDATE_ONLY": "0" if live else "1",
                    },
                    timeout=manifest.case_timeout_seconds,
                )
                returncode = result.returncode
            except (OSError, RuntimeError, ValueError):
                returncode = 2
            outcomes.append(MatrixOutcome(step.step_id, returncode))
            print(
                "SELF_IMPROVE_MATRIX_CASE_END "
                f"case={step.case.case_id} phase={step.phase} rc={returncode}",
                flush=True,
            )
    summary = aggregate_outcomes(steps, tuple(outcomes))
    _print_summary(manifest, summary)
    return summary


def _print_summary(manifest: MatrixManifest, summary: MatrixSummary) -> None:
    """Emit one stable terminal envelope for automation and operator review."""
    print(
        json.dumps(
            {
                "failed_step_ids": list(summary.failed_step_ids),
                "passed_steps": summary.passed_steps,
                "status": summary.status,
                "suite_id": manifest.suite_id,
                "total_steps": summary.total_steps,
            },
            separators=(",", ":"),
            sort_keys=True,
        ),
        flush=True,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the bounded self-improvement acceptance matrix"
    )
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--model-path", default="")
    parser.add_argument("--live", action="store_true")
    return parser


def main() -> int:
    """Validate the matrix and return success only for a complete passing run."""
    args = _parser().parse_args()
    try:
        manifest = load_manifest(Path(args.manifest))
        validate_reference_boundaries(manifest)
        summary = execute_matrix(
            manifest,
            live=bool(args.live),
            model_path=str(args.model_path),
        )
    except (MatrixContractError, OSError, RuntimeError, ValueError) as exc:
        print(
            f"SELF_IMPROVE_MATRIX_ERROR type={type(exc).__name__}",
            file=sys.stderr,
            flush=True,
        )
        return 2
    return int(summary.status != "passed")


if __name__ == "__main__":
    raise SystemExit(main())
