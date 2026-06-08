"""Tests for audit gap fixes — security hardening, dead code, wiring gaps."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from pydantic import ValidationError

from general_ludd.cli import main


class TestAddTodoRequestValidation:
    def test_work_type_must_be_valid_enum(self):
        from general_ludd.schemas.todo import WorkType

        valid = {w.value for w in WorkType}
        assert "code" in valid
        assert "invalid_type" not in valid

    def test_work_type_enum_values(self):
        from general_ludd.schemas.todo import WorkType

        expected = {
            "code", "test", "review", "refactor", "docs", "infra",
            "prompt", "analysis", "audit", "release", "dependency",
            "security", "model", "unknown",
        }
        actual = {w.value for w in WorkType}
        assert actual == expected

    def test_add_todo_rejects_invalid_work_type(self):
        with patch("sys.argv", ["gludd", "add", "Test title", "--work-type", "INVALID_TYPE"]), \
             pytest.raises(SystemExit):
            main()

    def test_add_todo_rejects_invalid_queue(self):
        with patch("httpx.post") as mock_post:
            mock_post.return_value = MagicMock(status_code=400, text="Invalid queue")
        with patch("sys.argv", ["gludd", "add", "Test", "--queue", "nonexistent_queue_12345"]), \
             pytest.raises(SystemExit):
            main()


class TestPathSanitization:
    def test_sanitize_path_blocks_traversal(self):
        from general_ludd.security.sanitize import sanitize_path

        assert sanitize_path("../../etc/passwd") is None
        assert sanitize_path("../../../etc/shadow") is None
        assert sanitize_path("foo/../../bar") is None

    def test_sanitize_path_blocks_absolute(self):
        from general_ludd.security.sanitize import sanitize_path

        assert sanitize_path("/etc/passwd") is None
        assert sanitize_path("/var/log") is None

    def test_sanitize_path_allows_normal(self):
        from general_ludd.security.sanitize import sanitize_path

        assert sanitize_path("config/settings.yml") == "config/settings.yml"
        assert sanitize_path("playbooks/run.yml") == "playbooks/run.yml"
        assert sanitize_path("templates/prompt.txt") == "templates/prompt.txt"

    def test_sanitize_path_allows_current_dir(self):
        from general_ludd.security.sanitize import sanitize_path

        assert sanitize_path("./config.yml") == "config.yml"
        assert sanitize_path("file.txt") == "file.txt"

    def test_sanitize_job_id_blocks_traversal(self):
        from general_ludd.security.sanitize import sanitize_job_id

        assert sanitize_job_id("../../../etc") is None
        assert sanitize_job_id("EXEC-VALID123") == "EXEC-VALID123"
        assert sanitize_job_id("REVIEW-ABC12345") == "REVIEW-ABC12345"

    def test_sanitize_job_id_rejects_slashes(self):
        from general_ludd.security.sanitize import sanitize_job_id

        assert sanitize_job_id("foo/bar") is None
        assert sanitize_job_id("foo\\bar") is None


class TestDaemonInputValidation:
    def test_add_todo_request_rejects_numeric_garbage_work_type(self):
        from general_ludd.daemon import AddTodoRequest

        with pytest.raises(ValidationError):
            AddTodoRequest(title="Test", work_type="code injection <script>")

    def test_add_todo_request_rejects_empty_title(self):
        from general_ludd.daemon import AddTodoRequest

        with pytest.raises(ValidationError):
            AddTodoRequest(title="")

    def test_add_todo_request_rejects_oversized_title(self):
        from general_ludd.daemon import AddTodoRequest

        with pytest.raises(ValidationError):
            AddTodoRequest(title="x" * 600)

    def test_add_todo_request_validates_queue(self):
        from general_ludd.daemon import AddTodoRequest

        req = AddTodoRequest(title="Test", queue="core")
        assert req.queue == "core"

    def test_add_todo_request_validates_priority(self):
        from general_ludd.daemon import AddTodoRequest

        req = AddTodoRequest(title="Test", priority="high")
        assert req.priority == "high"

        with pytest.raises(ValidationError):
            AddTodoRequest(title="Test", priority="not_a_priority")

    def test_add_todo_request_rejects_special_chars_in_queue(self):
        from general_ludd.daemon import AddTodoRequest

        with pytest.raises(ValidationError):
            AddTodoRequest(title="Test", queue="queue; DROP TABLE")

    def test_add_todo_request_rejects_spaces_in_queue(self):
        from general_ludd.daemon import AddTodoRequest

        with pytest.raises(ValidationError):
            AddTodoRequest(title="Test", queue="my queue")


class TestDeadWorkerEndpoints:
    def test_validate_endpoint_exists_in_worker(self):
        from general_ludd.worker.app import create_app

        app = create_app()
        routes = [r.path for r in app.routes]
        assert "/jobs/validate" in routes
        assert "/jobs/policy-validate" in routes
        assert "/jobs/reload-request" in routes

    def test_validate_endpoint_returns_ack(self):
        from fastapi.testclient import TestClient

        from general_ludd.worker.app import create_app

        app = create_app()
        client = TestClient(app)
        resp = client.post("/jobs/validate", json={
            "job_id": "EXEC-TEST123",
            "playbook": "validate_task.yml",
            "queue": "core",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data.get("status") in ("ack", "validation_dispatched", "accepted")

    def test_policy_validate_endpoint_returns_ack(self):
        from fastapi.testclient import TestClient

        from general_ludd.worker.app import create_app

        app = create_app()
        client = TestClient(app)
        resp = client.post("/jobs/policy-validate", json={
            "job_id": "POLICY-TEST",
            "playbook": "noop.yml",
            "queue": "core",
        })
        assert resp.status_code == 200

    def test_reload_request_endpoint_returns_ack(self):
        from fastapi.testclient import TestClient

        from general_ludd.worker.app import create_app

        app = create_app()
        client = TestClient(app)
        resp = client.post("/jobs/reload-request", json={
            "job_id": "RELOAD-TEST",
            "playbook": "noop.yml",
            "queue": "core",
        })
        assert resp.status_code == 200


class TestEventLoopDispatchesValidate:
    def test_event_loop_can_dispatch_validate(self):
        from general_ludd.event_loop.loop import EventLoop

        el = EventLoop(session=None, config={})
        assert hasattr(el, "_dispatch_validate_job")


class TestFilestorePathSanitization:
    def test_runner_prepare_dirs_rejects_traversal(self):
        from general_ludd.ansible.runner import AnsibleRunnerAdapter

        runner = AnsibleRunnerAdapter()
        with pytest.raises(ValueError, match="Invalid job_id"):
            runner.prepare_job_dirs("../../etc/passwd")

    def test_runner_prepare_dirs_rejects_slashes(self):
        from general_ludd.ansible.runner import AnsibleRunnerAdapter

        runner = AnsibleRunnerAdapter()
        with pytest.raises(ValueError, match="Invalid job_id"):
            runner.prepare_job_dirs("foo/bar")

    def test_runner_prepare_dirs_accepts_valid(self):
        from general_ludd.ansible.runner import AnsibleRunnerAdapter

        runner = AnsibleRunnerAdapter()
        dirs = runner.prepare_job_dirs("EXEC-TEST123")
        assert "root" in dirs
        assert "artifacts" in dirs
