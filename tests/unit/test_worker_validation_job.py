"""Contract tests for the worker validation-job pipeline."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from general_ludd.worker.app import create_app


def _payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "job_id": "VALIDATE-001",
        "todo_id": "TODO-001",
        "playbook": "caller-supplied.yml",
        "queue": "qa",
        "work_type": "code",
        "budget_context": {
            "worktree_path": "/tmp/gludd-validation-worktree",
            "test_commands": ["make test-count"],
        },
    }
    payload.update(overrides)
    return payload


def _runner(
    *,
    result: dict[str, Any] | None = None,
    playbooks: list[str] | None = None,
) -> MagicMock:
    runner = MagicMock()
    runner.list_playbooks.return_value = (
        ["validate_task.yml"] if playbooks is None else playbooks
    )
    runner.prepare_job_dirs.return_value = {
        "root": "/tmp/gludd-validation-job/VALIDATE-001",
    }
    runner.write_vars.return_value = None
    runner.run_playbook.return_value = result or {
        "rc": 0,
        "output": "validation passed",
        "artifacts": ["validation_result.json"],
        "events": [{"event": "runner_on_ok"}],
    }
    return runner


@pytest.fixture(autouse=True)
def _disable_worker_auth(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GLUDD_PSK_DISABLE", "1")
    monkeypatch.delenv("GLUDD_AUTH_PSK", raising=False)


def test_validate_delegates_once_to_canonical_execute_pipeline() -> None:
    runner = _runner()
    app = create_app(gateway=None, dispatcher=None)

    with patch("general_ludd.worker.app.get_runner", return_value=runner):
        response = TestClient(app).post("/jobs/validate", json=_payload())

    assert response.status_code == 200
    assert response.json() == {
        "status": "created",
        "return_id": "RET-VALIDATE-001",
        "todo_id": "TODO-001",
        "job_id": "VALIDATE-001",
        "playbook": "validate_task.yml",
        "model_response": None,
        "tool_calls_detected": [],
        "tool_dispatch_results": [],
        "exit_code": 0,
        "result_summary": "validation passed",
        "artifacts": ["validation_result.json"],
        "events": [{"event": "runner_on_ok"}],
    }
    runner.prepare_job_dirs.assert_called_once_with("VALIDATE-001")
    written_vars = runner.write_vars.call_args.kwargs["job_vars"]
    assert written_vars["work_type"] == "validation"
    assert written_vars["worktree_path"] == "/tmp/gludd-validation-worktree"
    assert written_vars["test_commands"] == ["make test-count"]
    runner.run_playbook.assert_called_once_with(
        playbook_name="validate_task.yml",
        private_data_dir="/tmp/gludd-validation-job/VALIDATE-001",
        extravars=None,
        timeout=600.0,
    )


def test_validate_requires_registered_validation_playbook() -> None:
    runner = _runner(playbooks=[])
    app = create_app(gateway=None, dispatcher=None)

    with patch("general_ludd.worker.app.get_runner", return_value=runner):
        response = TestClient(app).post("/jobs/validate", json=_payload())

    assert response.status_code == 400
    assert response.json()["detail"] == "Unknown playbook: validate_task.yml"
    runner.prepare_job_dirs.assert_not_called()


def test_validate_preserves_duplicate_job_conflict() -> None:
    runner = _runner()
    runner.prepare_job_dirs.side_effect = FileExistsError("already exists")
    app = create_app(gateway=None, dispatcher=None)

    with patch("general_ludd.worker.app.get_runner", return_value=runner):
        response = TestClient(app).post("/jobs/validate", json=_payload())

    assert response.status_code == 409
    assert response.json()["detail"] == "Job already in progress"
    runner.run_playbook.assert_not_called()


def test_validate_preserves_runner_failure_evidence() -> None:
    runner = _runner(
        result={
            "rc": 2,
            "output": "validation failed: unit tests",
            "artifacts": ["validation_result.json"],
            "events": [{"event": "runner_on_failed"}],
        }
    )
    app = create_app(gateway=None, dispatcher=None)

    with patch("general_ludd.worker.app.get_runner", return_value=runner):
        response = TestClient(app).post("/jobs/validate", json=_payload())

    assert response.status_code == 200
    result = response.json()
    assert result["status"] == "created"
    assert result["exit_code"] == 2
    assert result["result_summary"] == "validation failed: unit tests"
    assert result["events"] == [{"event": "runner_on_failed"}]


def test_validate_preserves_worker_auth_boundary(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GLUDD_PSK_DISABLE", raising=False)
    monkeypatch.setenv("GLUDD_AUTH_PSK", "validation-secret")
    runner = _runner()
    app = create_app(gateway=None, dispatcher=None)

    with patch("general_ludd.worker.app.get_runner", return_value=runner):
        response = TestClient(app).post("/jobs/validate", json=_payload())

    assert response.status_code == 401
    assert response.json() == {"error": "unauthorized"}
    runner.list_playbooks.assert_not_called()


def test_validate_preserves_execute_timeout_ceiling(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GLUDD_JOB_TIMEOUT_MAX", "12")
    runner = _runner()
    app = create_app(gateway=None, dispatcher=None)

    with patch("general_ludd.worker.app.get_runner", return_value=runner):
        response = TestClient(app).post(
            "/jobs/validate",
            json=_payload(timeout=120),
        )

    assert response.status_code == 200
    assert runner.run_playbook.call_args.kwargs["timeout"] == 12.0
