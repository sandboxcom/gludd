"""Tests that worker cleans up job workspace dir when run_playbook raises."""

from __future__ import annotations

import os
import pathlib
from unittest.mock import MagicMock, patch


def _make_dirs(root: str, job_id: str) -> dict[str, str]:
    job_root = os.path.join(root, "jobs", job_id)
    os.makedirs(job_root, exist_ok=True)
    return {"root": job_root}


def test_workspace_removed_on_run_playbook_failure(tmp_path: pathlib.Path) -> None:
    """When run_playbook raises, the per-job workspace dir must be removed."""
    from general_ludd.worker.app import create_app

    job_id = "test-job-cleanup-001"
    job_root = tmp_path / "jobs" / job_id
    job_root.mkdir(parents=True)

    dirs = {"root": str(job_root)}

    mock_runner = MagicMock()
    mock_runner.list_playbooks.return_value = ["deploy.yml"]
    mock_runner.prepare_job_dirs.return_value = dirs
    mock_runner.write_vars.return_value = None
    mock_runner.run_playbook.side_effect = RuntimeError("playbook exploded")

    app = create_app(gateway=None)

    from fastapi.testclient import TestClient

    with patch("general_ludd.worker.app.get_runner", return_value=mock_runner), \
         patch("general_ludd.worker.app.get_playbook_registry", return_value={"deploy.yml"}):
        client = TestClient(app, raise_server_exceptions=False)
        response = client.post(
            "/jobs/execute",
            json={
                "job_id": job_id,
                "todo_id": "todo-1",
                "queue": "default",
                "work_type": "task",
                "playbook": "deploy.yml",
                "vars_namespace_refs": [],
                "budget_context": {},
            },
        )
    # The endpoint should return a 5xx (exception propagated)
    assert response.status_code >= 500
    # The workspace dir must have been cleaned up
    assert not job_root.exists(), f"Expected {job_root} to be removed after run_playbook failure"


def test_workspace_not_removed_on_success(tmp_path: pathlib.Path) -> None:
    """When run_playbook succeeds, the workspace dir is left intact."""
    job_id = "test-job-cleanup-002"
    job_root = tmp_path / "jobs" / job_id
    job_root.mkdir(parents=True)

    dirs = {"root": str(job_root)}

    mock_runner = MagicMock()
    mock_runner.list_playbooks.return_value = ["deploy.yml"]
    mock_runner.prepare_job_dirs.return_value = dirs
    mock_runner.write_vars.return_value = None
    mock_runner.run_playbook.return_value = {"rc": 0, "output": "ok", "artifacts": [], "events": []}

    from general_ludd.worker.app import create_app
    app = create_app(gateway=None)

    from fastapi.testclient import TestClient

    with patch("general_ludd.worker.app.get_runner", return_value=mock_runner), \
         patch("general_ludd.worker.app.get_playbook_registry", return_value={"deploy.yml"}):
        client = TestClient(app)
        response = client.post(
            "/jobs/execute",
            json={
                "job_id": job_id,
                "todo_id": "todo-1",
                "queue": "default",
                "work_type": "task",
                "playbook": "deploy.yml",
                "vars_namespace_refs": [],
                "budget_context": {},
            },
        )
    assert response.status_code == 200
    # Dir still exists after success
    assert job_root.exists()
