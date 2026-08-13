"""Tests for the concise GitHub Actions job-summary formatter."""

from __future__ import annotations

import importlib.util
import io
import json
import sys
from pathlib import Path
from types import ModuleType

SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "ci_run_summary.py"


def _load_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "ci_run_summary_under_test",
        SCRIPT_PATH,
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run(module: ModuleType, monkeypatch, payload: dict[str, object]) -> int:
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))
    return module.main()


def test_empty_run_is_successful(monkeypatch, capsys) -> None:
    module = _load_script()
    assert _run(module, monkeypatch, {"jobs": []}) == 0
    assert "No jobs found" in capsys.readouterr().out


def test_failed_job_is_reported_and_fails(monkeypatch, capsys) -> None:
    module = _load_script()
    result = _run(
        module,
        monkeypatch,
        {
            "jobs": [{
                "name": "unit",
                "status": "completed",
                "conclusion": "failure",
            }],
        },
    )
    output = capsys.readouterr().out
    assert result == 1
    assert "FAIL unit" in output
    assert "1 failed" in output


def test_success_and_running_jobs_are_grouped(monkeypatch, capsys) -> None:
    module = _load_script()
    result = _run(
        module,
        monkeypatch,
        {
            "jobs": [
                {
                    "name": "package",
                    "status": "completed",
                    "conclusion": "success",
                },
                {
                    "name": "integration",
                    "status": "in_progress",
                    "conclusion": None,
                },
            ],
        },
    )
    output = capsys.readouterr().out
    assert result == 0
    assert "package" in output
    assert "integration" in output
    assert "1 running" in output
    assert "1 passed" in output
