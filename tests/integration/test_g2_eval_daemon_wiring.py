"""Integration tests for G2 offline eval harness wiring into the daemon.

Proves EvalHarness at daemon.py:1154-1158/1210 is reachable via
app.state.eval_harness, the /admin/eval/status endpoint returns 200,
and the /admin/eval/run endpoint processes cases through the harness.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from general_ludd.db.models import Base
from general_ludd.eval.harness import EvalHarness

PSK = "test-eval-psk"
AUTH = {"Authorization": f"Bearer {PSK}"}


async def _make_app(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite://", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    monkeypatch.setenv("GLUDD_AUTH_PSK", PSK)
    from general_ludd.daemon import create_daemon_app

    app = create_daemon_app(tick_interval=1.0)
    app.state._session_factory = factory
    if getattr(app.state, "eval_harness", None) is None:
        app.state.eval_harness = EvalHarness(model="sonnet")

    transport = ASGITransport(app=app)
    client = AsyncClient(transport=transport, base_url="http://test")
    return engine, factory, client, app


class TestEvalDaemonWiring:
    @pytest.mark.asyncio
    async def test_eval_status_endpoint_returns_200(self, monkeypatch):
        engine, _factory, client, _app = await _make_app(monkeypatch)
        try:
            resp = await client.get("/admin/eval/status", headers=AUTH)
            assert resp.status_code == 200, resp.text
            data = resp.json()
            assert "status" in data
            assert "ready" in data
            assert isinstance(data["ready"], bool)
        finally:
            await client.aclose()
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_eval_harness_on_app_state_has_expected_methods(self, monkeypatch):
        engine, _factory, client, app = await _make_app(monkeypatch)
        try:
            harness = getattr(app.state, "eval_harness", None)
            assert harness is not None
            assert isinstance(harness, EvalHarness)
            assert hasattr(harness, "run_benchmark")
            assert hasattr(harness, "run_single")
            assert hasattr(harness, "ready")
            assert hasattr(harness, "last_results")
        finally:
            await client.aclose()
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_eval_run_endpoint_with_wired_evaluator(self, monkeypatch):
        engine, _factory, client, app = await _make_app(monkeypatch)
        try:
            mock_evaluator = MagicMock()
            mock_evaluator.generate_patch.return_value = "def foo():\n    return 42\n"
            harness = EvalHarness(model="sonnet", evaluator=mock_evaluator)
            app.state.eval_harness = harness

            resp = await client.post(
                "/admin/eval/run",
                json={
                    "cases": [
                        {
                            "id": "case-1",
                            "description": "test case",
                            "input_files": {"main.py": "old"},
                            "expected_patch": "def foo():\n    return 42\n",
                            "task_type": "code",
                            "assertions": {},
                        }
                    ]
                },
                headers=AUTH,
            )
            assert resp.status_code == 200, resp.text
            data = resp.json()
            assert data["run"] is True
            assert data["total"] == 1
            assert "results" in data
            assert len(data["results"]) == 1
        finally:
            await client.aclose()
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_eval_result_structure_has_required_fields(self, monkeypatch):
        engine, _factory, client, app = await _make_app(monkeypatch)
        try:
            mock_evaluator = MagicMock()
            mock_evaluator.generate_patch.return_value = "def foo():\n    return 42\n"
            harness = EvalHarness(model="sonnet", evaluator=mock_evaluator)
            app.state.eval_harness = harness

            resp = await client.post(
                "/admin/eval/run",
                json={
                    "cases": [
                        {
                            "id": "case-minimal",
                            "description": "minimal",
                            "input_files": {"a": ""},
                            "expected_patch": "def foo():\n    return 42\n",
                            "task_type": "code",
                            "assertions": {"patch_contains": "def foo"},
                        }
                    ]
                },
                headers=AUTH,
            )
            assert resp.status_code == 200, resp.text
            data = resp.json()
            result = data["results"][0]
            assert "case_id" in result
            assert result["case_id"] == "case-minimal"
            assert "passed" in result
            assert isinstance(result["passed"], bool)
            assert "score" in result
            assert isinstance(result["score"], (int, float))
            assert "duration_ms" in result
            assert isinstance(result["duration_ms"], int)
            assert "tokens_used" in result
            assert "errors" in result
            assert isinstance(result["errors"], list)
        finally:
            await client.aclose()
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_eval_results_endpoint_returns_last_results(self, monkeypatch):
        engine, _factory, client, app = await _make_app(monkeypatch)
        try:
            mock_evaluator = MagicMock()
            mock_evaluator.generate_patch.return_value = "patch content"
            harness = EvalHarness(model="sonnet", evaluator=mock_evaluator)
            app.state.eval_harness = harness

            await client.post(
                "/admin/eval/run",
                json={
                    "cases": [
                        {
                            "id": "case-a",
                            "description": "a",
                            "input_files": {"f": ""},
                            "expected_patch": "patch content",
                            "task_type": "code",
                            "assertions": {},
                        }
                    ]
                },
                headers=AUTH,
            )

            resp = await client.get("/admin/eval/results", headers=AUTH)
            assert resp.status_code == 200, resp.text
            data = resp.json()
            assert data["total"] == 1
            assert len(data["results"]) == 1
            assert data["results"][0]["case_id"] == "case-a"
        finally:
            await client.aclose()
            await engine.dispose()
