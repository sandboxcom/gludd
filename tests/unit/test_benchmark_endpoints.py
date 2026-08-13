from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from general_ludd.daemon import create_daemon_app
from general_ludd.routers import benchmark as benchmark_router


class _FakeSession:
    def __init__(self) -> None:
        self.committed = False

    async def commit(self) -> None:
        self.committed = True


class _SessionContext:
    def __init__(self, session: _FakeSession) -> None:
        self._session = session

    async def __aenter__(self) -> _FakeSession:
        return self._session

    async def __aexit__(
        self,
        exc_type: object,
        exc: object,
        traceback: object,
    ) -> None:
        return None


def _install_session_factory(monkeypatch, session: _FakeSession) -> None:
    def get_session_factory(_app: object) -> Any:
        return lambda: _SessionContext(session)

    monkeypatch.setattr(
        benchmark_router,
        "_get_session_factory",
        get_session_factory,
    )


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
    async def test_get_scores_returns_repository_aggregates(
        self,
        transport,
        monkeypatch,
    ) -> None:
        session = _FakeSession()
        _install_session_factory(monkeypatch, session)
        requested_task_types: list[str | None] = []

        class FakeBenchmarkRepository:
            def __init__(self, bound_session: object) -> None:
                assert bound_session is session

            async def get_aggregate_scores(
                self,
                *,
                task_type: str | None,
            ) -> list[dict[str, object]]:
                requested_task_types.append(task_type)
                return [
                    {
                        "model_profile_id": "model-1",
                        "task_type": task_type,
                        "composite_score": 0.87,
                    }
                ]

        monkeypatch.setattr(
            benchmark_router,
            "BenchmarkRepository",
            FakeBenchmarkRepository,
        )

        async with AsyncClient(
            transport=transport,
            base_url="http://test",
        ) as client:
            response = await client.get(
                "/admin/benchmark/scores",
                params={"task_type": "feature"},
            )

        assert response.status_code == 200
        assert response.json() == {
            "scores": [
                {
                    "model_profile_id": "model-1",
                    "task_type": "feature",
                    "composite_score": 0.87,
                }
            ]
        }
        assert requested_task_types == ["feature"]

    @pytest.mark.asyncio
    async def test_get_recent_serializes_persisted_results(
        self,
        transport,
        monkeypatch,
    ) -> None:
        session = _FakeSession()
        _install_session_factory(monkeypatch, session)

        class FakeBenchmarkRepository:
            def __init__(self, bound_session: object) -> None:
                assert bound_session is session

            async def list_recent(self, *, limit: int) -> list[SimpleNamespace]:
                assert limit == 7
                return [
                    SimpleNamespace(
                        id="result-1",
                        prompt_profile_id="prompt-1",
                        model_profile_id="model-1",
                        task_type="feature",
                        completion_score=0.9,
                        code_quality_score=0.8,
                        instruction_adherence_score=0.7,
                        token_efficiency_score=0.6,
                        success=True,
                        cost_usd=0.012,
                        created_at=datetime(2026, 7, 29, 12, 30, tzinfo=UTC),
                    ),
                    SimpleNamespace(
                        id="result-2",
                        prompt_profile_id=None,
                        model_profile_id="model-2",
                        task_type="bug_fix",
                        completion_score=0.5,
                        code_quality_score=0.4,
                        instruction_adherence_score=0.3,
                        token_efficiency_score=0.2,
                        success=False,
                        cost_usd=0.0,
                        created_at=None,
                    ),
                ]

        monkeypatch.setattr(
            benchmark_router,
            "BenchmarkRepository",
            FakeBenchmarkRepository,
        )

        async with AsyncClient(
            transport=transport,
            base_url="http://test",
        ) as client:
            response = await client.get(
                "/admin/benchmark/recent",
                params={"limit": 7},
            )

        assert response.status_code == 200
        assert response.json() == {
            "results": [
                {
                    "id": "result-1",
                    "prompt_profile_id": "prompt-1",
                    "model_profile_id": "model-1",
                    "task_type": "feature",
                    "completion_score": 0.9,
                    "code_quality_score": 0.8,
                    "instruction_adherence_score": 0.7,
                    "token_efficiency_score": 0.6,
                    "success": True,
                    "cost_usd": 0.012,
                    "created_at": "2026-07-29T12:30:00+00:00",
                },
                {
                    "id": "result-2",
                    "prompt_profile_id": None,
                    "model_profile_id": "model-2",
                    "task_type": "bug_fix",
                    "completion_score": 0.5,
                    "code_quality_score": 0.4,
                    "instruction_adherence_score": 0.3,
                    "token_efficiency_score": 0.2,
                    "success": False,
                    "cost_usd": 0.0,
                    "created_at": None,
                },
            ]
        }

    @pytest.mark.asyncio
    async def test_record_persists_scores_and_commits(
        self,
        transport,
        monkeypatch,
    ) -> None:
        session = _FakeSession()
        _install_session_factory(monkeypatch, session)
        recorded: list[dict[str, object]] = []

        class FakeBenchmarkRepository:
            def __init__(self, bound_session: object) -> None:
                assert bound_session is session

            async def record_result(
                self,
                *,
                data: dict[str, object],
            ) -> SimpleNamespace:
                recorded.append(data)
                return SimpleNamespace(id="result-3", success=data["success"])

        monkeypatch.setattr(
            benchmark_router,
            "BenchmarkRepository",
            FakeBenchmarkRepository,
        )
        payload = {
            "model_profile_id": "model-3",
            "prompt_profile_id": "prompt-3",
            "task_type": "feature",
            "scores": {
                "completion": 0.91,
                "code_quality": 0.82,
                "instruction": 0.73,
                "token_efficiency": 0.64,
            },
            "success": True,
            "time_seconds": 3.5,
            "input_tokens": 120,
            "output_tokens": 45,
            "cost_usd": 0.025,
            "error_message": "",
            "raw_output": "completed",
        }

        async with AsyncClient(
            transport=transport,
            base_url="http://test",
        ) as client:
            response = await client.post(
                "/admin/benchmark/record",
                json=payload,
            )

        assert response.status_code == 200
        assert response.json() == {"id": "result-3", "success": True}
        assert session.committed is True
        assert recorded == [
            {
                "model_profile_id": "model-3",
                "task_type": "feature",
                "success": True,
                "prompt_profile_id": "prompt-3",
                "completion_score": 0.91,
                "code_quality_score": 0.82,
                "instruction_adherence_score": 0.73,
                "token_efficiency_score": 0.64,
                "time_seconds": 3.5,
                "input_tokens": 120,
                "output_tokens": 45,
                "cost_usd": 0.025,
                "error_message": "",
                "raw_output": "completed",
            }
        ]

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
