"""Deterministic routing and exception-boundary tests for the models router."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient

from general_ludd.routers.models import register


def _client(workspace_root: str) -> TestClient:
    app = FastAPI()
    app.state._workspace_root = workspace_root
    register(app, {})
    return TestClient(app)


def _scorer() -> MagicMock:
    score = MagicMock()
    score.model_dump.return_value = {"cyclomatic_complexity": 2.0}
    scorer = MagicMock()
    scorer.score_file.return_value = score
    scorer.suggest_task_type.return_value = SimpleNamespace(value="feature")
    return scorer


def _local_client(manager: MagicMock) -> TestClient:
    app = FastAPI()
    app.state._local_inference_manager = manager
    register(app, {})
    return TestClient(app)


def test_suggest_model_serializes_adaptive_routing_decision(tmp_path: Path) -> None:
    source = tmp_path / "sample.py"
    source.write_text("def answer():\n    return 42\n", encoding="utf-8")
    scorer = _scorer()
    decision = SimpleNamespace(
        selected_prompt_profile_id="prompt-fast",
        selected_model_profile_id="model-local",
        composite_score=0.91,
        estimated_cost_usd=0.002,
        sample_count=7,
        fallback=False,
        reason="measured",
    )
    router = MagicMock()
    router.route = AsyncMock(return_value=decision)

    with (
        patch("general_ludd.routers.models.CodeComplexityScorer", return_value=scorer),
        patch("general_ludd.routers.models.AdaptiveRouter", return_value=router),
        _client(str(tmp_path)) as client,
    ):
        response = client.post("/admin/code/suggest-model", json={"path": str(source)})

    assert response.status_code == 200, response.text
    assert response.json()["model_recommendation"] == {
        "selected_prompt_profile_id": "prompt-fast",
        "selected_model_profile_id": "model-local",
        "composite_score": 0.91,
        "estimated_cost_usd": 0.002,
        "sample_count": 7,
        "fallback": False,
        "reason": "measured",
    }
    router.route.assert_awaited_once()


def test_suggest_model_router_failure_is_deterministic_and_redacted(tmp_path: Path) -> None:
    source = tmp_path / "sample.py"
    source.write_text("value = 1\n", encoding="utf-8")
    router = MagicMock()
    router.route = AsyncMock(side_effect=RuntimeError("secret-provider-token"))

    with (
        patch("general_ludd.routers.models.CodeComplexityScorer", return_value=_scorer()),
        patch("general_ludd.routers.models.AdaptiveRouter", return_value=router),
        _client(str(tmp_path)) as client,
    ):
        response = client.post("/admin/code/suggest-model", json={"path": str(source)})

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["model_recommendation"]["reason"] == "router_error"
    assert "secret-provider-token" not in response.text


def test_local_consume_serializes_bounded_upstream_response() -> None:
    server = SimpleNamespace(
        server_id="server-1",
        status="running",
        endpoint_url="http://model.local",
    )
    manager = MagicMock()
    manager.list_servers.return_value = [server]

    upstream_response = MagicMock()
    upstream_response.json.return_value = {
        "choices": [{"text": "answer"}],
        "usage": {"prompt_tokens": 2, "completion_tokens": 1},
    }
    upstream = MagicMock()
    upstream.__aenter__ = AsyncMock(return_value=upstream)
    upstream.__aexit__ = AsyncMock(return_value=None)
    upstream.post = AsyncMock(return_value=upstream_response)

    with (
        patch("httpx.AsyncClient", return_value=upstream),
        _local_client(manager) as client,
    ):
        response = client.post(
            "/admin/models/local/consume",
            json={"server_id": "server-1", "prompt": "question", "max_tokens": 8},
        )

    assert response.status_code == 200, response.text
    assert response.json() == {
        "server_id": "server-1",
        "text": "answer",
        "usage": {"prompt_tokens": 2, "completion_tokens": 1},
    }
    upstream.post.assert_awaited_once_with(
        "http://model.local/completions",
        json={"prompt": "question", "max_tokens": 8},
    )


def test_local_consume_redacts_upstream_http_failure() -> None:
    server = SimpleNamespace(
        server_id="server-1",
        status="running",
        endpoint_url="http://model.local",
    )
    manager = MagicMock()
    manager.list_servers.return_value = [server]
    upstream = MagicMock()
    upstream.__aenter__ = AsyncMock(return_value=upstream)
    upstream.__aexit__ = AsyncMock(return_value=None)
    upstream.post = AsyncMock(
        side_effect=httpx.ConnectError("secret-upstream-credential")
    )

    with (
        patch("httpx.AsyncClient", return_value=upstream),
        _local_client(manager) as client,
    ):
        response = client.post(
            "/admin/models/local/consume",
            json={"server_id": "server-1", "prompt": "question"},
        )

    assert response.status_code == 502
    assert response.json() == {"detail": "local model call failed"}
    assert "secret-upstream-credential" not in response.text
