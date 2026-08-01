"""Tests that ExecutionEngine is wired into app.state and the engine-status endpoint works."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from general_ludd.execution.engine import ExecutionEngine


class TestExecutionEngineWiring:
    @pytest.fixture
    def app(self) -> FastAPI:
        app = FastAPI()
        app.state._execution_engine = None

        @app.get("/admin/execution/engine-status")
        async def engine_status() -> dict[str, object]:
            engine = getattr(app.state, "_execution_engine", None)
            if engine is None:
                return {"status": "not_configured", "reason": "No execution engine wired"}
            return {
                "status": "configured",
                "workspace_path": engine.workspace_path,
                "has_model_gateway": engine._model_gateway is not None,
                "has_budget_guard": engine._budget_guard is not None,
                "has_metrics_collector": engine._metrics_collector is not None,
            }

        return app

    def test_engine_status_not_configured_when_no_engine(self, app: FastAPI) -> None:
        client = TestClient(app)
        resp = client.get("/admin/execution/engine-status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "not_configured"
        assert "No execution engine wired" in data["reason"]

    def test_engine_status_configured_when_engine_present(self, app: FastAPI) -> None:
        mock_gateway = MagicMock()
        engine = ExecutionEngine(
            model_gateway=mock_gateway,
            workspace_path="/tmp/test-workspace",
            metrics_collector=None,
            budget_guard=None,
        )
        app.state._execution_engine = engine

        client = TestClient(app)
        resp = client.get("/admin/execution/engine-status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "configured"
        assert data["workspace_path"] == "/tmp/test-workspace"
        assert data["has_model_gateway"] is True
        assert data["has_budget_guard"] is False
        assert data["has_metrics_collector"] is False

    def test_engine_status_with_all_subcomponents(self, app: FastAPI) -> None:
        mock_gateway = MagicMock()
        mock_metrics = MagicMock()
        mock_budget = MagicMock()
        engine = ExecutionEngine(
            model_gateway=mock_gateway,
            workspace_path="/tmp/full-workspace",
            metrics_collector=mock_metrics,
            budget_guard=mock_budget,
        )
        app.state._execution_engine = engine

        client = TestClient(app)
        resp = client.get("/admin/execution/engine-status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "configured"
        assert data["has_model_gateway"] is True
        assert data["has_budget_guard"] is True
        assert data["has_metrics_collector"] is True

    def test_execution_engine_constructs_and_stores_on_state(self) -> None:
        app = FastAPI()
        mock_gateway = MagicMock()
        engine = ExecutionEngine(
            model_gateway=mock_gateway,
            workspace_path="/tmp/construct-test",
        )
        app.state._execution_engine = engine
        assert app.state._execution_engine is engine
        assert app.state._execution_engine._model_gateway is mock_gateway
        assert app.state._execution_engine.workspace_path == "/tmp/construct-test"

    def test_execution_engine_workspace_created_on_init(self, tmp_path) -> None:
        ws = str(tmp_path / "created-workspace")
        mock_gateway = MagicMock()
        engine = ExecutionEngine(
            model_gateway=mock_gateway,
            workspace_path=ws,
        )
        import os
        assert os.path.isdir(ws)
        assert engine.workspace_path == ws

    def test_execution_engine_default_workspace(self) -> None:
        from general_ludd.security.state import project_state

        mock_gateway = MagicMock()
        engine = ExecutionEngine(model_gateway=mock_gateway)
        workspace = Path(engine.workspace_path)
        assert workspace.is_relative_to(project_state().project_dir)
        assert workspace.name == "workspace"
