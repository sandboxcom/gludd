"""Unit tests for generate router."""

from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from general_ludd.routers import generate


@pytest.fixture
def app():
    app = FastAPI()
    generate.register(app, {})
    return app


class TestGenerateListTypes:
    @pytest.mark.asyncio
    async def test_list_types(self, app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/generate/list-types")
        assert response.status_code == 200
        data = response.json()
        assert "project_types" in data


class TestGenerateCreate:
    @pytest.mark.asyncio
    async def test_gateway_missing(self, app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(
                "/api/generate/create",
                json={
                    "project_type": "game",
                    "description": "a game",
                },
            )
        assert response.status_code == 503

    @pytest.mark.asyncio
    async def test_create_success(self, app):
        code_result = {"files": {"main.py": "print('hello')"}}
        with patch.object(generate.SoftwareGenerator, "generate_multi", return_value=code_result):
            app.state._model_gateway = MagicMock()

            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                response = await client.post(
                    "/api/generate/create",
                    json={
                        "project_type": "game",
                        "description": "a game",
                    },
                )
        assert response.status_code == 200
        data = response.json()
        assert data["project_type"] == "game"
        assert data["code"] == code_result


class TestGenerateValidate:
    @pytest.mark.asyncio
    async def test_validate_unknown_type(self, app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(
                "/api/generate/validate",
                json={
                    "project_type": "not_a_type",
                    "project_dir": ".",
                },
            )
        assert response.status_code == 200
        assert response.json()["valid"] is False
