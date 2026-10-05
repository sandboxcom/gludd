"""Unit tests for terraform_state router."""

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from general_ludd.routers import terraform_state


@pytest.fixture
def app():
    app = FastAPI()
    terraform_state.register(app, {})
    return app


class TestTerraformState:
    @pytest.mark.asyncio
    async def test_state_post_and_get(self, app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            post_resp = await client.post("/api/terraform/state/mystack", json={"resources": []})
            assert post_resp.status_code == 200
            get_resp = await client.get("/api/terraform/state/mystack")
            assert get_resp.status_code == 200
            assert get_resp.json()["state"] == {"resources": []}

    @pytest.mark.asyncio
    async def test_state_get_missing(self, app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/api/terraform/state/missing")
        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_state_delete(self, app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await client.post("/api/terraform/state/mystack", json={"resources": []})
            response = await client.delete("/api/terraform/state/mystack")
            assert response.status_code == 200


class TestTerraformLock:
    @pytest.mark.asyncio
    async def test_lock_and_unlock(self, app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            lock_resp = await client.request("LOCK", "/api/terraform/state/mystack", json={"ID": "lock-1"})
            assert lock_resp.status_code == 200
            assert lock_resp.json()["ID"] == "lock-1"

            lock_again = await client.request("LOCK", "/api/terraform/state/mystack", json={"ID": "lock-2"})
            assert lock_again.status_code == 423

            unlock_resp = await client.request("UNLOCK", "/api/terraform/state/mystack", json={"ID": "lock-1"})
            assert unlock_resp.status_code == 200

    @pytest.mark.asyncio
    async def test_unlock_mismatch(self, app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await client.request("LOCK", "/api/terraform/state/mystack", json={"ID": "lock-1"})
            response = await client.request("UNLOCK", "/api/terraform/state/mystack", json={"ID": "wrong"})
            assert response.status_code == 409
