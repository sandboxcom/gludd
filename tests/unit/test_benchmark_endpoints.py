from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from general_ludd.daemon import create_daemon_app


@pytest.fixture
def app(tmp_path):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    return create_daemon_app(config_dir=str(config_dir))


@pytest.fixture
def transport(app):
    return ASGITransport(app=app)


class TestBenchmarkEndpoints:
    @pytest.mark.asyncio
    async def test_get_scores_no_session(self, transport):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/admin/benchmark/scores")
            assert resp.status_code == 200
            data = resp.json()
            assert data["scores"] == []

    @pytest.mark.asyncio
    async def test_get_recent_no_session(self, transport):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/admin/benchmark/recent")
            assert resp.status_code == 200
            data = resp.json()
            assert data["results"] == []

    @pytest.mark.asyncio
    async def test_get_leaderboard_no_session(self, transport):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/admin/benchmark/leaderboard")
            assert resp.status_code == 200
            data = resp.json()
            assert data["leaderboard"] == []

    @pytest.mark.asyncio
    async def test_get_scores_with_task_type(self, transport):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(
                "/admin/benchmark/scores", params={"task_type": "feature"}
            )
            assert resp.status_code == 200
            data = resp.json()
            assert "scores" in data

    @pytest.mark.asyncio
    async def test_get_leaderboard_with_task_type(self, transport):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(
                "/admin/benchmark/leaderboard", params={"task_type": "bug_fix"}
            )
            assert resp.status_code == 200
            data = resp.json()
            assert "leaderboard" in data

    @pytest.mark.asyncio
    async def test_record_no_session_returns_503(self, transport):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/admin/benchmark/record",
                json={
                    "model_profile_id": "gpt4",
                    "task_type": "feature",
                    "scores": {
                        "completion": 0.9,
                        "code_quality": 0.8,
                        "instruction": 0.85,
                        "token_efficiency": 0.7,
                    },
                    "success": True,
                },
            )
            assert resp.status_code == 503

    @pytest.mark.asyncio
    async def test_get_recent_with_limit(self, transport):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(
                "/admin/benchmark/recent", params={"limit": 10}
            )
            assert resp.status_code == 200
            data = resp.json()
            assert "results" in data


class TestPromptProfileEndpoints:
    @pytest.mark.asyncio
    async def test_get_prompt_profiles_no_session(self, transport):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/admin/prompt-profiles")
            assert resp.status_code == 200
            data = resp.json()
            assert data["profiles"] == []
