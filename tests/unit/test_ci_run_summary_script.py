"""Tests for the concise GitHub Actions job-summary formatter."""

from __future__ import annotations

import importlib.util
import io
import json
import subprocess
import sys
from pathlib import Path
from types import ModuleType
from typing import cast

import pytest

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


def _run(
    module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    payload: dict[str, object],
) -> int:
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))
    return cast(int, module.main())


def test_empty_run_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    module = _load_script()
    assert _run(module, monkeypatch, {"jobs": []}) == 1
    assert "No jobs found" in capsys.readouterr().out


def test_failed_job_is_reported_and_fails(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
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


def test_success_and_running_jobs_are_grouped(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
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
    assert result == 1
    assert "package" in output
    assert "integration" in output
    assert "1 running" in output
    assert "1 passed" in output


def test_completed_stdin_run_succeeds(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    module = _load_script()
    result = _run(
        module,
        monkeypatch,
        {
            "jobs": [{
                "name": "package",
                "status": "completed",
                "conclusion": "success",
            }],
        },
    )

    assert result == 0
    assert "1 passed" in capsys.readouterr().out


def _completed_run_payload(run_id: int = 123) -> dict[str, object]:
    return {
        "databaseId": run_id,
        "headSha": "a" * 40,
        "status": "completed",
        "conclusion": "success",
        "url": f"https://github.com/sandboxcom/gludd/actions/runs/{run_id}",
        "workflowName": "Build",
        "jobs": [{
            "name": "unit-3b",
            "status": "completed",
            "conclusion": "success",
        }],
    }


def test_immutable_run_fetch_uses_exact_numeric_id(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    module = _load_script()
    calls: list[list[str]] = []

    def fake_run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=json.dumps(_completed_run_payload()),
            stderr="",
        )

    monkeypatch.setattr(module.subprocess, "run", fake_run)

    assert module.main(["--run", "123", "--repo", "sandboxcom/gludd"]) == 0
    assert calls == [[
        "gh",
        "run",
        "view",
        "123",
        "--repo",
        "sandboxcom/gludd",
        "--json",
        "databaseId,headSha,status,conclusion,url,workflowName,jobs",
    ]]
    assert "RUN 123" in capsys.readouterr().out


def test_immutable_run_fetch_preserves_api_failure(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    module = _load_script()

    def fake_run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            command,
            4,
            stdout="",
            stderr="HTTP 503 from GitHub API\n",
        )

    monkeypatch.setattr(module.subprocess, "run", fake_run)

    assert module.main(["--run", "123"]) == 4
    captured = capsys.readouterr()
    assert "HTTP 503 from GitHub API" in captured.err
    assert "No jobs found" not in captured.out


def test_immutable_run_fetch_rejects_mismatched_response_id(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    module = _load_script()

    def fake_run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=json.dumps(_completed_run_payload(run_id=124)),
            stderr="",
        )

    monkeypatch.setattr(module.subprocess, "run", fake_run)

    assert module.main(["--run", "123"]) == 1
    assert "requested run 123 but received 124" in capsys.readouterr().err


def test_immutable_run_fetch_rejects_unbound_head_sha(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    module = _load_script()
    payload = _completed_run_payload()
    payload["headSha"] = "abc123"

    def fake_run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=json.dumps(payload),
            stderr="",
        )

    monkeypatch.setattr(module.subprocess, "run", fake_run)

    assert module.main(["--run", "123"]) == 1
    assert "invalid immutable head SHA" in capsys.readouterr().err


def test_immutable_run_fetch_rejects_malformed_json(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    module = _load_script()

    def fake_run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(command, 0, stdout="not-json", stderr="")

    monkeypatch.setattr(module.subprocess, "run", fake_run)

    assert module.main(["--run", "123"]) == 1
    assert "invalid JSON" in capsys.readouterr().err


def test_validate_only_is_network_free_and_observable(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    module = _load_script()

    def unexpected_run(*_args: object, **_kwargs: object) -> None:
        pytest.fail("validate-only must not call GitHub")

    monkeypatch.setattr(module.subprocess, "run", unexpected_run)

    assert module.main([
        "--run",
        "123",
        "--repo",
        "sandboxcom/gludd",
        "--validate-only",
    ]) == 0
    assert (
        "CI-RUN-SUMMARY VALIDATED run=123 repo=sandboxcom/gludd"
        in capsys.readouterr().out
    )
