"""Regression tests for auditable E2E coverage progress and failure reports."""

import importlib.util
import json
import sys
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).parents[2]
AUDIT_SCRIPT = ROOT / "scripts" / "audit_coverage.py"


def _load_audit_module(name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, AUDIT_SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _stub_e2e_files(monkeypatch: Any, module: Any, names: list[str]) -> None:
    """Expose a deterministic, small shard set without touching the checkout."""
    e2e_root = ROOT / "tests" / "e2e"
    files = [e2e_root / name for name in names]
    original_rglob = Path.rglob

    def fake_rglob(path: Path, pattern: str) -> Iterable[Path]:
        if path == e2e_root and pattern == "test_*.py":
            return files
        return original_rglob(path, pattern)

    monkeypatch.setattr(Path, "rglob", fake_rglob)


def test_shard_identifiers_are_sorted_relative_and_unique(monkeypatch: Any) -> None:
    """Progress records identify each file stably without leaking absolute paths."""
    module = _load_audit_module("audit_coverage_progress_ids")
    _stub_e2e_files(monkeypatch, module, [
        "test_zeta.py",
        "nested/test_alpha.py",
    ])

    calls: list[list[str]] = []

    def fake_run(args: list[str], **kwargs: object) -> Any:
        calls.append(args)
        return type("Result", (), {"returncode": 0})()

    monkeypatch.setattr(module.subprocess, "run", fake_run)
    shards: list[dict[str, object]] = []
    assert module.run_pytest_coverage("src/general_ludd", "/tmp/coverage.json", shards) == 0

    identifiers = [str(shard["path"]) for shard in shards]
    assert identifiers == sorted(identifiers)
    assert len(identifiers) == len(set(identifiers)) == 2
    assert identifiers == ["tests/e2e/nested/test_alpha.py", "tests/e2e/test_zeta.py"]
    assert all(not Path(identifier).is_absolute() for identifier in identifiers)
    assert all(shard["status"] == "passed" for shard in shards)
    assert len(calls) == 3  # two E2E files plus the final coverage JSON command


def test_failed_shard_is_persisted_and_stops_following_files(monkeypatch: Any) -> None:
    """A failing shard remains auditable and prevents a false all-files result."""
    module = _load_audit_module("audit_coverage_progress_failure")
    _stub_e2e_files(monkeypatch, module, ["test_first.py", "test_second.py"])

    calls: list[list[str]] = []

    def fake_run(args: list[str], **kwargs: object) -> Any:
        calls.append(args)
        return type("Result", (), {"returncode": 23})()

    monkeypatch.setattr(module.subprocess, "run", fake_run)
    shards: list[dict[str, object]] = []
    assert module.run_pytest_coverage("src/general_ludd", "/tmp/coverage.json", shards) == 23
    assert shards == [{
        "path": "tests/e2e/test_first.py",
        "status": "failed",
        "returncode": 23,
    }]
    assert len(calls) == 1


@pytest.mark.parametrize(
    ("outcome", "expected_returncode"),
    [("success", 0), ("failure", 23), ("timeout", 124)],
)
def test_coverage_database_is_owner_cleaned_on_every_terminal_path(
    monkeypatch: Any,
    tmp_path: Path,
    outcome: str,
    expected_returncode: int,
) -> None:
    """The audit owner removes its database after success, failure, or timeout."""
    module = _load_audit_module(f"audit_coverage_cleanup_{outcome}")
    _stub_e2e_files(monkeypatch, module, ["test_owned.py"])
    coverage_file = ROOT / f".coverage.audit.{module.os.getpid()}"

    def fake_run(args: list[str], **kwargs: object) -> Any:
        coverage_file.write_bytes(b"owned coverage data")
        if outcome == "timeout":
            raise module.subprocess.TimeoutExpired(args, 3)
        return type(
            "Result",
            (),
            {"returncode": 23 if outcome == "failure" else 0},
        )()

    monkeypatch.setattr(module.subprocess, "run", fake_run)
    shards: list[dict[str, object]] = []
    assert module.run_pytest_coverage(
        "src/general_ludd",
        str(tmp_path / "coverage.json"),
        shards,
        str(tmp_path / "progress.json"),
    ) == expected_returncode
    assert not coverage_file.exists()


def test_stale_coverage_recovery_preserves_live_or_uninspectable_owners(
    monkeypatch: Any,
    tmp_path: Path,
) -> None:
    """Recovery removes dead/legacy data without touching possible live owners."""
    module = _load_audit_module("audit_coverage_stale_recovery")
    dead = tmp_path / ".coverage.audit.900001"
    live = tmp_path / ".coverage.audit.900002"
    uninspectable = tmp_path / ".coverage.audit.900003"
    legacy = tmp_path / ".coverage.audit.hosted-chemistry"
    for path in (dead, live, uninspectable, legacy):
        path.write_bytes(b"coverage")

    def fake_kill(pid: int, signal_number: int) -> None:
        assert signal_number == 0
        if pid == 900001:
            raise ProcessLookupError
        if pid == 900003:
            raise PermissionError

    monkeypatch.setattr(module.os, "kill", fake_kill)
    module._recover_stale_coverage_databases(tmp_path)

    assert not dead.exists()
    assert not legacy.exists()
    assert live.exists()
    assert uninspectable.exists()


def test_failure_report_never_claims_completion(
    monkeypatch: Any, tmp_path: Path, capsys: Any
) -> None:
    """Missing coverage JSON produces an explicit failed report, never completion text."""
    module = _load_audit_module("audit_coverage_progress_report")
    json_out = tmp_path / "failed-report.json"

    def fake_run(
        source: str, path: str, shards: list[dict[str, object]]
    ) -> int:
        shards.append({
            "path": "tests/e2e/test_unfinished.py",
            "status": "failed",
            "returncode": 17,
        })
        return 17

    monkeypatch.setattr(module, "run_pytest_coverage", fake_run)
    monkeypatch.setattr(module.sys, "argv", [
        "audit_coverage.py",
        "--json-out=" + str(json_out),
    ])

    try:
        module.main()
    except SystemExit as exc:
        assert exc.code == 17
    else:
        raise AssertionError("a failed shard must terminate the audit non-zero")

    report = json.loads(json_out.read_text())
    assert report["passed"] is False
    assert report["pytest_exit_code"] == 17
    assert report["failed_shards"] == [{
        "path": "tests/e2e/test_unfinished.py",
        "status": "failed",
        "returncode": 17,
    }]
    assert report["error"] == "coverage JSON was not produced by the audit command"
    captured = capsys.readouterr()
    assert "Coverage audit complete" not in captured.out
    assert "Coverage audit failed" in captured.err


def test_progress_test_does_not_mutate_process_arguments(monkeypatch: Any) -> None:
    """Loading the helper module must not alter the caller's command-line state."""
    original_argv = list(sys.argv)
    _load_audit_module("audit_coverage_progress_import")
    assert sys.argv == original_argv


def test_progress_json_records_per_file_counts_and_run_identity(
    monkeypatch: Any, tmp_path: Path
) -> None:
    """Every shard update is durable and exposes auditable run identity/counts."""
    module = _load_audit_module("audit_coverage_progress_durable")
    _stub_e2e_files(monkeypatch, module, ["test_first.py", "test_second.py"])

    def fake_run(args: list[str], **kwargs: object) -> Any:
        return type("Result", (), {"returncode": 0})()

    monkeypatch.setattr(module.subprocess, "run", fake_run)
    report_path = tmp_path / "coverage.json"
    progress_path = tmp_path / "coverage.progress.json"
    shards: list[dict[str, object]] = []
    assert module.run_pytest_coverage(
        "src/general_ludd", str(report_path), shards, str(progress_path)
    ) == 0

    progress = json.loads(progress_path.read_text())
    assert progress["schema_version"] == 1
    assert progress["status"] == "completed"
    assert progress["complete"] is True
    assert progress["pid"] == module.os.getpid()
    assert progress["run_id"]
    assert progress["current_index"] == 2
    assert progress["total"] == 2
    assert progress["counts"] == {
        "attempted": 2,
        "passed": 2,
        "failed": 0,
        "skipped": 0,
    }
    assert [entry["status"] for entry in progress["files"]] == ["passed", "passed"]


def test_progress_json_marks_unattempted_files_skipped_after_failure(
    monkeypatch: Any, tmp_path: Path
) -> None:
    """A stopped audit records skipped files instead of implying completion."""
    module = _load_audit_module("audit_coverage_progress_partial")
    _stub_e2e_files(monkeypatch, module, ["test_first.py", "test_second.py"])

    def fake_run(args: list[str], **kwargs: object) -> Any:
        return type("Result", (), {"returncode": 23})()

    monkeypatch.setattr(module.subprocess, "run", fake_run)
    report_path = tmp_path / "coverage.json"
    progress_path = tmp_path / "coverage.progress.json"
    shards: list[dict[str, object]] = []
    assert module.run_pytest_coverage(
        "src/general_ludd", str(report_path), shards, str(progress_path)
    ) == 23

    progress = json.loads(progress_path.read_text())
    assert progress["status"] == "failed"
    assert progress["complete"] is False
    assert progress["current_index"] == 1
    assert progress["counts"] == {
        "attempted": 1,
        "passed": 0,
        "failed": 1,
        "skipped": 1,
    }
    assert [entry["status"] for entry in progress["files"]] == ["failed", "skipped"]
    assert progress["files"][1]["reason"] == "stopped_after_failure"


def test_progress_json_marks_timeout_without_claiming_complete(
    monkeypatch: Any, tmp_path: Path
) -> None:
    """Timeouts persist a failed partial state with remaining files skipped."""
    module = _load_audit_module("audit_coverage_progress_timeout")
    _stub_e2e_files(monkeypatch, module, ["test_first.py", "test_second.py"])

    def fake_run(args: list[str], **kwargs: object) -> Any:
        raise module.subprocess.TimeoutExpired(args, 3)

    monkeypatch.setattr(module.subprocess, "run", fake_run)
    report_path = tmp_path / "coverage.json"
    progress_path = tmp_path / "coverage.progress.json"
    shards: list[dict[str, object]] = []
    assert module.run_pytest_coverage(
        "src/general_ludd", str(report_path), shards, str(progress_path)
    ) == 124

    progress = json.loads(progress_path.read_text())
    assert progress["status"] == "failed"
    assert progress["complete"] is False
    assert progress["counts"]["failed"] == 1
    assert progress["counts"]["skipped"] == 1
    assert progress["files"][0]["status"] == "timed_out"


def test_progress_json_records_ordered_test_failure_context_and_namespace(
    monkeypatch: Any, tmp_path: Path
) -> None:
    """Shard summaries retain deterministic JUnit order and namespace context."""
    module = _load_audit_module("audit_coverage_progress_diagnostics")
    _stub_e2e_files(monkeypatch, module, ["test_diagnostics.py"])
    monkeypatch.setenv("GLUDD_PROJECT_NAMESPACE", "project-diagnostics")

    def fake_run(args: list[str], **kwargs: object) -> Any:
        junit_arg = next(arg for arg in args if str(arg).startswith("--junitxml="))
        junit_path = Path(str(junit_arg).split("=", 1)[1])
        junit_path.parent.mkdir(parents=True, exist_ok=True)
        junit_path.write_text(
            "<testsuite tests='2' failures='1'>"
            "<testcase classname='tests.e2e.test_diagnostics' name='test_first'/>"
            "<testcase classname='tests.e2e.test_diagnostics' name='test_second'>"
            "<failure message='assertion failed'>expected value</failure>"
            "</testcase></testsuite>"
        )
        return type("Result", (), {"returncode": 23})()

    monkeypatch.setattr(module.subprocess, "run", fake_run)
    progress_path = tmp_path / "coverage.progress.json"
    shards: list[dict[str, object]] = []
    assert module.run_pytest_coverage(
        "src/general_ludd",
        str(tmp_path / "coverage.json"),
        shards,
        str(progress_path),
    ) == 23

    progress = json.loads(progress_path.read_text())
    assert progress["environment_namespace"] == "project-diagnostics"
    summary = progress["files"][0]
    assert summary["environment_namespace"] == "project-diagnostics"
    assert [test["order"] for test in summary["tests"]] == [1, 2]
    assert [test["nodeid"] for test in summary["tests"]] == [
        "tests.e2e.test_diagnostics::test_first",
        "tests.e2e.test_diagnostics::test_second",
    ]
    assert summary["tests"][1]["status"] == "failed"
    assert summary["tests"][1]["failure_context"] == {
        "message": "assertion failed",
        "text": "expected value",
    }


def test_corrupt_and_skipped_junit_diagnostics_are_deterministic(
    tmp_path: Path,
) -> None:
    """Unreadable diagnostics fail closed while skips retain their reason."""
    module = _load_audit_module("audit_coverage_progress_junit_edges")
    corrupt = tmp_path / "corrupt.xml"
    corrupt.write_text("<testsuite>")
    assert module._read_shard_diagnostics(corrupt) == []

    skipped = tmp_path / "skipped.xml"
    skipped.write_text(
        "<testsuite tests='1' skipped='1'>"
        "<testcase name='test_waiting'><skipped message='service unavailable'/>"
        "</testcase></testsuite>"
    )
    assert module._read_shard_diagnostics(skipped) == [{
        "order": 1,
        "nodeid": "test_waiting",
        "status": "skipped",
        "skip_reason": "service unavailable",
    }]


def test_audit_rejects_an_empty_e2e_inventory(
    monkeypatch: Any, tmp_path: Path, capsys: Any
) -> None:
    """An empty shard inventory is an explicit failure, never a vacuous pass."""
    module = _load_audit_module("audit_coverage_progress_empty")
    _stub_e2e_files(monkeypatch, module, [])
    shards: list[dict[str, object]] = []
    assert module.run_pytest_coverage(
        "src/general_ludd", str(tmp_path / "coverage.json"), shards
    ) == 2
    assert shards == []
    assert "no E2E test files found" in capsys.readouterr().err


def test_coverage_report_failure_remains_observable(
    monkeypatch: Any, tmp_path: Path
) -> None:
    """A failed aggregate render publishes a failed terminal progress snapshot."""
    module = _load_audit_module("audit_coverage_progress_report_failure")
    _stub_e2e_files(monkeypatch, module, ["test_first.py"])
    returncodes = iter([0, 9])
    monkeypatch.setattr(
        module.subprocess,
        "run",
        lambda args, **kwargs: type("Result", (), {"returncode": next(returncodes)})(),
    )
    progress_path = tmp_path / "coverage.progress.json"
    shards: list[dict[str, object]] = []

    assert module.run_pytest_coverage(
        "src/general_ludd",
        str(tmp_path / "coverage.json"),
        shards,
        str(progress_path),
    ) == 9
    assert shards[0]["status"] == "passed"
    progress = json.loads(progress_path.read_text())
    assert progress["status"] == "failed"
    assert progress["complete"] is False
    assert progress["error"] == "coverage json exited with 9"


def test_coverage_report_timeout_marks_the_command_failed(
    monkeypatch: Any, tmp_path: Path
) -> None:
    """A report timeout records its synthetic shard and bounded failure state."""
    module = _load_audit_module("audit_coverage_progress_report_timeout")
    _stub_e2e_files(monkeypatch, module, ["test_first.py"])
    call_count = 0

    def fake_run(args: list[str], **kwargs: object) -> Any:
        nonlocal call_count
        call_count += 1
        if call_count == 2:
            raise module.subprocess.TimeoutExpired(args, 3)
        return type("Result", (), {"returncode": 0})()

    monkeypatch.setattr(module.subprocess, "run", fake_run)
    progress_path = tmp_path / "coverage.progress.json"
    shards: list[dict[str, object]] = []
    assert module.run_pytest_coverage(
        "src/general_ludd",
        str(tmp_path / "coverage.json"),
        shards,
        str(progress_path),
    ) == 124
    assert shards[-1] == {
        "path": "<coverage-json>",
        "status": "timed_out",
        "returncode": 124,
    }
    progress = json.loads(progress_path.read_text())
    assert progress["status"] == "failed"
    assert progress["complete"] is False
    assert progress["error"] == "coverage command timed out"


def test_main_writes_a_passing_report_for_explicit_inputs(
    monkeypatch: Any, tmp_path: Path
) -> None:
    """Explicit CLI thresholds and paths produce a deterministic success report."""
    module = _load_audit_module("audit_coverage_progress_main_success")
    coverage_json = tmp_path / "coverage.json"
    report_json = tmp_path / "report.json"
    coverage_json.write_text(json.dumps({
        "files": {
            "src/general_ludd/example.py": {
                "summary": {
                    "num_statements": 4,
                    "covered_lines": 4,
                    "num_branches": 2,
                    "covered_branches": 2,
                }
            }
        }
    }))
    monkeypatch.setattr(module.sys, "argv", [
        "audit_coverage.py",
        "--threshold=90",
        "--source=src/general_ludd",
        f"--json-file={coverage_json}",
        f"--json-out={report_json}",
        "--per-file-threshold=80",
    ])

    try:
        module.main()
    except SystemExit as exc:
        assert exc.code == 0
    else:
        raise AssertionError("a passing audit must terminate explicitly")

    report = json.loads(report_json.read_text())
    assert report["passed"] is True
    assert report["threshold"] == 90
    assert report["per_file_threshold"] == 80


def test_main_reports_threshold_and_missing_input_failures(
    monkeypatch: Any, tmp_path: Path
) -> None:
    """CLI diagnostics distinguish policy failure from a missing input artifact."""
    module = _load_audit_module("audit_coverage_progress_main_failures")
    coverage_json = tmp_path / "low.json"
    coverage_json.write_text(json.dumps({
        "files": {
            "src/general_ludd/low.py": {
                "summary": {"num_statements": 10, "covered_lines": 5}
            }
        }
    }))
    monkeypatch.setattr(module.sys, "argv", [
        "audit_coverage.py",
        f"--json-file={coverage_json}",
        f"--json-out={tmp_path / 'low-report.json'}",
    ])
    try:
        module.main()
    except SystemExit as exc:
        assert exc.code == 1
    else:
        raise AssertionError("a threshold failure must terminate non-zero")

    monkeypatch.setattr(module.sys, "argv", [
        "audit_coverage.py",
        f"--json-file={tmp_path / 'missing.json'}",
        f"--json-out={tmp_path / 'missing-report.json'}",
    ])
    try:
        module.main()
    except SystemExit as exc:
        assert exc.code == 2
    else:
        raise AssertionError("a missing input must terminate with usage failure")


def test_relative_path_preserves_out_of_scope_artifacts(tmp_path: Path) -> None:
    """Diagnostics retain an external filename that cannot be safely relativized."""
    module = _load_audit_module("audit_coverage_progress_relative_path")
    source = tmp_path / "source"
    external = tmp_path.parent / "external.py"
    assert module._relative_path(str(external), str(source)) == str(external)
