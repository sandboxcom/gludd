"""Unit tests for routers/game.py — MultiModelGamePipeline API endpoint."""

from __future__ import annotations

from dataclasses import dataclass
from unittest.mock import MagicMock

from fastapi import FastAPI
from fastapi.testclient import TestClient


@dataclass
class _MockResponse:
    content: str


class TestRegisterGameRouter:
    """Structural: verify register() exists and adds the expected routes."""

    def test_register_is_callable(self) -> None:
        from general_ludd.routers.game import register

        app = FastAPI()
        register(app, {})
        routes = [r.path for r in app.routes if hasattr(r, "path")]
        assert "/api/game/generate-multi" in routes, f"Expected route not found in {routes}"

    def test_endpoint_returns_503_when_no_gateway(self) -> None:
        from general_ludd.routers.game import register

        app = FastAPI()
        register(app, {})
        client = TestClient(app)
        resp = client.post("/api/game/generate-multi", json={"description": "test game"})
        assert resp.status_code == 503
        assert "gateway" in resp.json()["detail"].lower()

    def test_endpoint_rejects_empty_description(self) -> None:
        from general_ludd.routers.game import register

        app = FastAPI()
        app.state._model_gateway = MagicMock()
        register(app, {})
        client = TestClient(app)
        resp = client.post("/api/game/generate-multi", json={})
        assert resp.status_code == 422
        assert "description" in resp.json()["detail"].lower()

    def test_endpoint_accepts_valid_request(self) -> None:
        from general_ludd.routers.game import register

        mock_gateway = MagicMock()
        mock_gateway.call_model.side_effect = [
            _MockResponse(
                content="name:SpaceShooter\n"
                "genre:shooter\n"
                "architecture:ECS\n"
                "components:Player,Ship,Enemy\n"
                "tech:pygame\n"
                "acceptance:runs without crash,renders ships,moves player\n"
            ),
            _MockResponse(content="import pygame\npygame.init()\nwhile True:\n    pass\n"),
            _MockResponse(content="issues:\nfixes:\nscore:0.9\npassed:true\n"),
        ]

        app = FastAPI()
        app.state._model_gateway = mock_gateway
        register(app, {})
        client = TestClient(app)

        resp = client.post(
            "/api/game/generate-multi",
            json={"description": "A space shooter game with enemy waves"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "code" in data
        assert "design" in data
        assert "review" in data
        assert data["review"]["rounds"] == 3

    def test_endpoint_respects_model_overrides(self) -> None:
        from general_ludd.routers.game import register

        mock_gateway = MagicMock()
        mock_gateway.call_model.side_effect = [
            _MockResponse(
                content="name:Racer\n"
                "genre:racing\n"
                "architecture:simple\n"
                "components:Car,Track\n"
                "tech:pygame\n"
                "acceptance:runs,renders car\n"
            ),
            _MockResponse(content="import pygame\npygame.init()\nwhile True:\n    pass\n"),
            _MockResponse(content="issues:\nfixes:\nscore:0.8\npassed:true\n"),
        ]

        app = FastAPI()
        app.state._model_gateway = mock_gateway
        register(app, {})
        client = TestClient(app)

        resp = client.post(
            "/api/game/generate-multi",
            json={
                "description": "A racing game",
                "planner_model": "gpt-4",
                "coder_model": "claude-3",
                "reviewer_model": "claude-3",
                "max_review_rounds": 2,
            },
        )
        assert resp.status_code == 200
        assert resp.json()["review"]["rounds"] == 2

    def test_endpoint_returns_500_on_pipeline_failure(self) -> None:
        from general_ludd.routers.game import register

        mock_gateway = MagicMock()
        mock_gateway.call_model.side_effect = RuntimeError("model unavailable")

        app = FastAPI()
        app.state._model_gateway = mock_gateway
        register(app, {})
        client = TestClient(app)

        resp = client.post(
            "/api/game/generate-multi",
            json={"description": "A game"},
        )
        assert resp.status_code == 500
        assert "error" in resp.json()["detail"]
