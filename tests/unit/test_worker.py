"""Unit tests for worker app."""

import pytest
from httpx import ASGITransport, AsyncClient

from agentic_harness.worker.app import create_app


@pytest.fixture
def app():
    return create_app()


@pytest.fixture
def transport(app):
    return ASGITransport(app=app)


class TestWorkerApp:
    @pytest.mark.asyncio
    async def test_healthz(self, transport):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/healthz")
            assert resp.status_code == 200
            assert resp.json() == {"status": "healthy"}

    @pytest.mark.asyncio
    async def test_worker_rejects_unknown_playbook(self, transport):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/jobs/execute", json={
                "job_id": "JOB-001",
                "playbook": "nonexistent.yml",
                "queue": "core",
            })
            assert resp.status_code == 400
            assert "Unknown playbook" in resp.json()["detail"]

    @pytest.mark.asyncio
    async def test_worker_writes_task_return_for_noop_playbook(self, transport):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/jobs/execute", json={
                "job_id": "JOB-002",
                "todo_id": "TODO-001",
                "playbook": "noop.yml",
                "queue": "core",
            })
            assert resp.status_code == 200
            data = resp.json()
            assert data["exit_code"] == 0
            assert data["playbook"] == "noop.yml"
            assert data["job_id"] == "JOB-002"

    @pytest.mark.asyncio
    async def test_worker_return_review_endpoint(self, transport):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/jobs/return-review", json={
                "job_id": "JOB-003",
                "playbook": "return_review.yml",
                "queue": "model",
            })
            assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_worker_validate_endpoint(self, transport):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/jobs/validate", json={
                "job_id": "JOB-004",
                "playbook": "noop.yml",
                "queue": "qa",
            })
            assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_worker_policy_validate_endpoint(self, transport):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/jobs/policy-validate", json={
                "job_id": "JOB-005",
                "playbook": "noop.yml",
                "queue": "core",
            })
            assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_worker_reload_endpoint(self, transport):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/jobs/reload-request", json={
                "job_id": "JOB-006",
                "playbook": "noop.yml",
                "queue": "self_improve",
            })
            assert resp.status_code == 200
