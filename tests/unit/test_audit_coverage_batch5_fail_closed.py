"""Fail-closed regression coverage for late E2E coverage shard failures."""

import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).parents[2]
AUDIT_SCRIPT = ROOT / "scripts" / "audit_coverage.py"


def _load_audit_module(name: str):
    spec = importlib.util.spec_from_file_location(name, AUDIT_SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_batch5_failure_report_preserves_shard_identity_and_stays_unmeasured(
    monkeypatch, tmp_path, capsys
):
    """A late shard failure must identify its file without claiming coverage."""
    module = _load_audit_module("audit_coverage_batch5_failure")
    json_out = tmp_path / "batch5-failure.json"
    failed = {
        "path": "tests/e2e/test_connectors_batch5_workflows.py",
        "status": "failed",
        "returncode": 19,
    }

    def fake_run(source, path, shards):
        shards.extend([
            {
                "path": "tests/e2e/test_connectors_batch4_workflows.py",
                "status": "passed",
                "returncode": 0,
            },
            failed,
        ])
        return failed["returncode"]

    monkeypatch.setattr(module, "run_pytest_coverage", fake_run)
    monkeypatch.setattr(module.sys, "argv", [
        "audit_coverage.py",
        "--json-out=" + str(json_out),
    ])

    try:
        module.main()
    except SystemExit as exc:
        assert exc.code == failed["returncode"]
    else:
        raise AssertionError("a failed batch5 shard must terminate the audit")

    report = json.loads(json_out.read_text())
    assert report["passed"] is False
    assert report["pytest_exit_code"] == failed["returncode"]
    assert report["failed_shards"] == [failed]
    assert report["shards"][-1] == failed
    assert "test_connectors_batch5_workflows.py" in report["failed_shards"][0]["path"]
    for percentage_key in (
        "coverage_percent",
        "line_coverage",
        "branch_coverage",
        "e2e_branch_coverage",
        "covered_branches",
        "total_branches",
    ):
        assert percentage_key not in report

    captured = capsys.readouterr()
    assert "Coverage audit complete" not in captured.out
    assert "Coverage audit failed" in captured.err


def test_batch5_failure_report_keeps_relative_paths(monkeypatch, tmp_path):
    """Failure evidence preserves the portable relative shard identifier."""
    module = _load_audit_module("audit_coverage_batch5_relative_path")
    json_out = tmp_path / "batch5-relative.json"
    relative = "tests/e2e/test_connectors_batch5_workflows.py"

    def fake_run(source, path, shards):
        shards.append({"path": relative, "status": "failed", "returncode": 2})
        return 2

    monkeypatch.setattr(module, "run_pytest_coverage", fake_run)
    monkeypatch.setattr(module.sys, "argv", [
        "audit_coverage.py",
        "--json-out=" + str(json_out),
    ])

    try:
        module.main()
    except SystemExit as exc:
        assert exc.code == 2
    report = json.loads(json_out.read_text())
    assert report["failed_shards"][0]["path"] == relative
    assert not Path(report["failed_shards"][0]["path"]).is_absolute()
    assert report["passed"] is False
