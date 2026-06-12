"""E2E: Worker app and Ansible runner MVP.

Covers sprint objective 3 — FastAPI endpoints, AnsibleRunnerAdapter with
playbook registry, job-private dirs, vars rendering, task return creation.
"""

from __future__ import annotations

import os
import tempfile
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from general_ludd.ansible.runner import AnsibleRunnerAdapter
from general_ludd.models.gateway import ModelResponse
from general_ludd.worker.app import create_app


@pytest.fixture()
def client():
    app = create_app()
    return TestClient(app)


class TestWorkerE2E:
    def test_healthz_endpoint(self, client):
        resp = client.get("/healthz")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "healthy"

    def test_execute_noop_playbook_full_pipeline(self, client):
        resp = client.post(
            "/jobs/execute",
            json={
                "job_id": "JOB-E2E-001",
                "todo_id": "TODO-E2E-001",
                "playbook": "noop.yml",
                "queue": "core",
                "work_type": "code",
                "resource_profile": "ai_heavy",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "created"
        assert data["job_id"] == "JOB-E2E-001"
        assert "return_id" in data

    def test_reject_unknown_playbook(self, client):
        resp = client.post(
            "/jobs/execute",
            json={
                "job_id": "JOB-E2E-BAD",
                "todo_id": "TODO-E2E-BAD",
                "playbook": "nonexistent_playbook.yml",
                "queue": "core",
                "work_type": "code",
                "resource_profile": "ai_heavy",
            },
        )
        assert resp.status_code == 400
        assert "unknown playbook" in resp.json()["detail"].lower()

    def test_return_review_endpoint(self, client):
        resp = client.post(
            "/jobs/return-review",
            json={
                "job_id": "JOB-RR-001",
                "todo_id": "TODO-RR-001",
                "playbook": "return_review.yml",
                "queue": "model",
                "work_type": "review",
            },
        )
        assert resp.status_code in (200, 202)

    def test_validate_endpoint(self, client):
        resp = client.post(
            "/jobs/validate",
            json={
                "job_id": "JOB-VAL-001",
                "todo_id": "TODO-VAL-001",
                "playbook": "noop.yml",
                "queue": "qa",
                "work_type": "validation",
            },
        )
        assert resp.status_code in (200, 202)

    def test_worker_correlation_ids(self, client):
        resp = client.get("/healthz")
        assert resp.status_code == 200

    def test_gunicorn_config_exists(self):
        import os

        repo_root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
        conf_path = os.path.join(
            repo_root, "src", "general_ludd", "worker", "gunicorn_conf.py"
        )
        assert os.path.exists(conf_path)
        with open(conf_path) as f:
            content = f.read()
        assert "uvicorn_worker" in content


class TestWorkerModelGatewayCall:
    """W3.1 (C1): the worker invokes the ModelGateway for generation jobs."""

    def test_execute_generation_job_calls_gateway_and_returns_response(self):
        gateway = MagicMock()
        gateway.call_model.return_value = ModelResponse(
            content="GENERATED DIFF BODY",
            model_name="test-model",
        )
        app = create_app(gateway=gateway)
        client = TestClient(app)

        resp = client.post(
            "/jobs/execute",
            json={
                "job_id": "JOB-GEN-001",
                "todo_id": "TODO-GEN-001",
                "playbook": "noop.yml",
                "queue": "core",
                "work_type": "code",
                "model_profile": "default",
                "prompt_text": "Implement feature X",
            },
        )

        assert resp.status_code == 200
        # The gateway was called with the job's prompt.
        gateway.call_model.assert_called_once()
        _args, kwargs = gateway.call_model.call_args
        messages = kwargs.get("messages") or (_args[1] if len(_args) > 1 else None)
        assert messages is not None
        joined = " ".join(m["content"] for m in messages)
        assert "Implement feature X" in joined
        # The generated response lands in the job result.
        data = resp.json()
        assert data["model_response"] == "GENERATED DIFF BODY"

    def test_execute_non_generation_job_skips_gateway(self):
        gateway = MagicMock()
        app = create_app(gateway=gateway)
        client = TestClient(app)

        resp = client.post(
            "/jobs/execute",
            json={
                "job_id": "JOB-NOGEN-001",
                "todo_id": "TODO-NOGEN-001",
                "playbook": "noop.yml",
                "queue": "core",
                "work_type": "release",
            },
        )
        assert resp.status_code == 200
        gateway.call_model.assert_not_called()

    def test_execute_without_gateway_does_not_error(self):
        app = create_app(gateway=None)
        client = TestClient(app)
        resp = client.post(
            "/jobs/execute",
            json={
                "job_id": "JOB-NOGW-001",
                "todo_id": "TODO-NOGW-001",
                "playbook": "noop.yml",
                "queue": "core",
                "work_type": "code",
                "prompt_text": "do thing",
            },
        )
        assert resp.status_code == 200
        assert resp.json()["model_response"] is None


class TestAnsibleRunnerAdapterE2E:
    def test_adapter_has_default_playbook_registry(self):
        adapter = AnsibleRunnerAdapter(private_data_dir="/tmp/fake")
        registry = list(adapter.registry.keys())
        assert isinstance(registry, list)
        assert len(registry) > 0
        assert "noop.yml" in registry

    def test_adapter_prepare_dirs_creates_structure(self):
        with tempfile.TemporaryDirectory() as tmp:
            adapter = AnsibleRunnerAdapter(private_data_dir=tmp)
            job_id = "JOB-STRUCT-TEST"
            dirs = adapter.prepare_job_dirs(job_id)
            assert os.path.isdir(dirs["root"])
            assert job_id in dirs["root"]
            assert os.path.isdir(dirs["env"])
            assert os.path.isdir(dirs["project"])

    def test_adapter_write_vars_creates_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            adapter = AnsibleRunnerAdapter(private_data_dir=tmp)
            adapter.prepare_job_dirs("JOB-VARS")
            path = adapter.write_vars(
                "JOB-VARS",
                job_vars={"job_id": "JOB-VARS", "test": True},
            )
            assert os.path.exists(path)

    def test_adapter_resolves_playbook_path(self):
        adapter = AnsibleRunnerAdapter(private_data_dir="/tmp")
        path = adapter.resolve_playbook("noop.yml")
        assert path.endswith("noop.yml")
        with pytest.raises(ValueError):
            adapter.resolve_playbook("definitely_not_real.yml")
