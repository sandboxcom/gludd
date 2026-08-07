"""Unit tests for routers/generate.py — POST /api/generate endpoints."""

from __future__ import annotations

from dataclasses import dataclass
from unittest.mock import MagicMock

from fastapi import FastAPI
from fastapi.testclient import TestClient


@dataclass
class _MockResponse:
    content: str


class TestGenerateListTypes:
    def test_list_types_returns_all_registered_types(self) -> None:
        from general_ludd.cloud.project_types import PROJECT_TYPE_REGISTRY
        from general_ludd.routers.generate import register

        app = FastAPI()
        register(app, {})
        client = TestClient(app)

        resp = client.post("/api/generate/list-types")
        assert resp.status_code == 200
        data = resp.json()
        assert "project_types" in data
        items = data["project_types"]
        assert len(items) == len(PROJECT_TYPE_REGISTRY)
        type_ids = {item["name"] for item in items}
        assert type_ids == set(PROJECT_TYPE_REGISTRY)

    def test_list_types_has_expected_fields(self) -> None:
        from general_ludd.routers.generate import register

        app = FastAPI()
        register(app, {})
        client = TestClient(app)

        resp = client.post("/api/generate/list-types")
        assert resp.status_code == 200
        for item in resp.json()["project_types"]:
            assert "name" in item
            assert "display_name" in item
            assert "default_entry_point" in item
            assert "output_structure" in item
            assert "acceptance_criteria" in item


class TestGenerateCreate:
    def test_create_returns_503_when_no_gateway(self) -> None:
        from general_ludd.routers.generate import register

        app = FastAPI()
        register(app, {})
        client = TestClient(app)

        resp = client.post(
            "/api/generate/create",
            json={"project_type": "game", "description": "A test game"},
        )
        assert resp.status_code == 503
        assert "gateway" in resp.json()["detail"].lower()

    def test_create_rejects_empty_description(self) -> None:
        from general_ludd.routers.generate import register

        app = FastAPI()
        app.state._model_gateway = MagicMock()
        register(app, {})
        client = TestClient(app)

        resp = client.post(
            "/api/generate/create",
            json={"project_type": "game", "description": "   "},
        )
        assert resp.status_code == 422

    def test_create_rejects_missing_description(self) -> None:
        from general_ludd.routers.generate import register

        app = FastAPI()
        app.state._model_gateway = MagicMock()
        register(app, {})
        client = TestClient(app)

        resp = client.post(
            "/api/generate/create",
            json={"project_type": "game"},
        )
        assert resp.status_code == 422

    def test_create_rejects_invalid_project_type(self) -> None:
        from general_ludd.routers.generate import register

        app = FastAPI()
        app.state._model_gateway = MagicMock()
        register(app, {})
        client = TestClient(app)

        resp = client.post(
            "/api/generate/create",
            json={"project_type": "nonexistent_type", "description": "Test"},
        )
        assert resp.status_code == 422

    def test_create_with_valid_project_type_returns_200(self) -> None:
        from general_ludd.routers.generate import register

        app = FastAPI()
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
            _MockResponse(content="import pygame\npygame.init()\nprint('hello')\n"),
            _MockResponse(content="issues:\nfixes:\nscore:0.9\npassed:true\n"),
        ]
        app.state._model_gateway = mock_gateway
        register(app, {})
        client = TestClient(app)

        resp = client.post(
            "/api/generate/create",
            json={"project_type": "game", "description": "A space shooter"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["project_type"] == "game"
        assert "code" in data
        assert "design" in data
        assert "review" in data

    def test_create_response_shape(self) -> None:
        from general_ludd.routers.generate import register

        app = FastAPI()
        mock_gateway = MagicMock()
        mock_gateway.call_model.side_effect = [
            _MockResponse(
                content="name:BlogDB\n"
                "tables:posts,comments,users\n"
                "architecture:relational\n"
                "tech:postgresql\n"
                "acceptance:schema parses,has PKs,has FKs\n"
            ),
            _MockResponse(content="CREATE TABLE posts (id INT PRIMARY KEY);\n"),
            _MockResponse(content="issues:\nfixes:\nscore:0.95\npassed:true\n"),
        ]
        app.state._model_gateway = mock_gateway
        register(app, {})
        client = TestClient(app)

        resp = client.post(
            "/api/generate/create",
            json={"project_type": "database_schema", "description": "A blog schema"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["project_type"] == "database_schema"
        assert isinstance(data["code"], str)
        assert "description" in data["design"]
        assert data["design"]["description"] == "A blog schema"
        assert "planner_model" in data["design"]
        assert "model" in data["review"]
        assert "rounds" in data["review"]

    def test_create_respects_model_profile_overrides(self) -> None:
        from general_ludd.routers.generate import register

        app = FastAPI()
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
            _MockResponse(content="import pygame\npygame.init()\nprint('vroom')\n"),
            _MockResponse(content="issues:\nfixes:\nscore:0.8\npassed:true\n"),
        ]
        app.state._model_gateway = mock_gateway
        register(app, {})
        client = TestClient(app)

        resp = client.post(
            "/api/generate/create",
            json={
                "project_type": "game",
                "description": "A game",
                "planner_model": "claude-3",
                "coder_model": "claude-3",
                "reviewer_model": "claude-3",
                "max_review_rounds": 5,
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["design"]["planner_model"] == "claude-3"
        assert data["review"]["model"] == "claude-3"
        assert data["review"]["rounds"] == 5

    def test_create_defaults_model_profiles_to_default(self) -> None:
        from general_ludd.routers.generate import register

        app = FastAPI()
        mock_gateway = MagicMock()
        mock_gateway.call_model.side_effect = [
            _MockResponse(
                content="name:Pong\n"
                "genre:arcade\n"
                "architecture:simple\n"
                "components:Ball,Paddle\n"
                "tech:pygame\n"
                "acceptance:runs,bounces ball\n"
            ),
            _MockResponse(content="import pygame\npygame.init()\nprint('pong')\n"),
            _MockResponse(content="issues:\nfixes:\nscore:0.9\npassed:true\n"),
        ]
        app.state._model_gateway = mock_gateway
        register(app, {})
        client = TestClient(app)

        resp = client.post(
            "/api/generate/create",
            json={"project_type": "game", "description": "A game"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["design"]["planner_model"] == "default"
        assert data["review"]["model"] == "default"

    def test_create_pipeline_failure_returns_500(self) -> None:
        from general_ludd.routers.generate import register

        app = FastAPI()
        mock_gateway = MagicMock()
        mock_gateway.call_model.side_effect = RuntimeError("gateway down")
        app.state._model_gateway = mock_gateway
        register(app, {})
        client = TestClient(app)

        resp = client.post(
            "/api/generate/create",
            json={"project_type": "game", "description": "A game"},
        )
        assert resp.status_code == 500
        assert "error" in resp.json()["detail"]


class TestGenerateValidate:
    def test_validate_rejects_unknown_project_type(self) -> None:
        from general_ludd.routers.generate import register

        app = FastAPI()
        register(app, {})
        client = TestClient(app)

        resp = client.post(
            "/api/generate/validate",
            json={"project_type": "bogus", "project_dir": "/tmp"},
        )
        assert resp.status_code == 200
        assert resp.json()["valid"] is False

    def test_validate_accepts_known_project_type(self) -> None:
        import tempfile
        from pathlib import Path

        from general_ludd.routers.generate import register

        app = FastAPI()
        register(app, {})
        client = TestClient(app)

        with tempfile.TemporaryDirectory() as tmpdir:
            Path(tmpdir, "game.py").write_text("print('hello')")
            resp = client.post(
                "/api/generate/validate",
                json={"project_type": "game", "project_dir": tmpdir},
            )
            assert resp.status_code == 200
            assert resp.json()["valid"] is True
            assert resp.json()["errors"] == []

    def test_validate_reports_missing_files(self) -> None:
        import tempfile

        from general_ludd.routers.generate import register

        app = FastAPI()
        register(app, {})
        client = TestClient(app)

        with tempfile.TemporaryDirectory() as tmpdir:
            resp = client.post(
                "/api/generate/validate",
                json={"project_type": "website", "project_dir": tmpdir},
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["valid"] is False
            assert len(data["errors"]) > 0

    def test_validate_reports_missing_directory(self) -> None:
        from general_ludd.routers.generate import register

        app = FastAPI()
        register(app, {})
        client = TestClient(app)

        resp = client.post(
            "/api/generate/validate",
            json={"project_type": "game", "project_dir": "/nonexistent/path/xyz"},
        )
        assert resp.status_code == 200
        assert resp.json()["valid"] is False
