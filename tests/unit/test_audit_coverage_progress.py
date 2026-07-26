"""Regression tests for auditable E2E coverage progress and failure reports."""

import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).parents[2]
AUDIT_SCRIPT = ROOT / "scripts" / "audit_coverage.py"


def _load_audit_module(name: str):
    spec = importlib.util.spec_from_file_location(name, AUDIT_SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _stub_e2e_files(monkeypatch, module, names: list[str]) -> None:
    """Expose a deterministic, small shard set without touching the checkout."""
    e2e_root = ROOT / "tests" / "e2e"
    files = [e2e_root / name for name in names]
    original_rglob = Path.rglob

    def fake_rglob(path: Path, pattern: str):
        if path == e2e_root and pattern == "test_*.py":
            return files
        return original_rglob(path, pattern)

    monkeypatch.setattr(Path, "rglob", fake_rglob)


def test_shard_identifiers_are_sorted_relative_and_unique(monkeypatch):
    """Progress records identify each file stably without leaking absolute paths."""
    module = _load_audit_module("audit_coverage_progress_ids")
    _stub_e2e_files(monkeypatch, module, [
        "test_zeta.py",
        "nested/test_alpha.py",
    ])

    calls: list[list[str]] = []

    def fake_run(args, **kwargs):
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


def test_failed_shard_is_persisted_and_stops_following_files(monkeypatch):
    """A failing shard remains auditable and prevents a false all-files result."""
    module = _load_audit_module("audit_coverage_progress_failure")
    _stub_e2e_files(monkeypatch, module, ["test_first.py", "test_second.py"])

    calls: list[list[str]] = []

    def fake_run(args, **kwargs):
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


def test_failure_report_never_claims_completion(monkeypatch, tmp_path, capsys):
    """Missing coverage JSON produces an explicit failed report, never completion text."""
    module = _load_audit_module("audit_coverage_progress_report")
    json_out = tmp_path / "failed-report.json"

    def fake_run(source, path, shards):
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


def test_progress_test_does_not_mutate_process_arguments(monkeypatch):
    """Loading the helper module must not alter the caller's command-line state."""
    original_argv = list(sys.argv)
    _load_audit_module("audit_coverage_progress_import")
    assert sys.argv == original_argv
