"""Integration tests for chat contracts daemon endpoints and CLI commands."""

from __future__ import annotations

import subprocess
import sys

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


def _gludd(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "general_ludd.cli", *list(args)],
        capture_output=True,
        text=True,
        timeout=30,
    )


@pytest.fixture
def chat_app() -> FastAPI:
    from general_ludd.routers.chat import register

    app = FastAPI()
    register(app, {})
    return app


@pytest.fixture
def client(chat_app: FastAPI) -> TestClient:
    return TestClient(chat_app, raise_server_exceptions=False)


class TestChatSessionsEndpoint:
    def test_list_sessions_returns_200(self, client: TestClient) -> None:
        resp = client.get("/api/chat/sessions")
        assert resp.status_code == 200
        data = resp.json()
        assert "sessions" in data
        assert "total" in data
        assert "limit" in data
        assert isinstance(data["sessions"], list)

    def test_list_sessions_respects_limit(self, client: TestClient) -> None:
        resp = client.get("/api/chat/sessions?limit=5")
        assert resp.status_code == 200
        data = resp.json()
        assert data["limit"] == 5
        assert len(data["sessions"]) <= 5

    def test_list_sessions_with_model_filter(self, client: TestClient) -> None:
        resp = client.get("/api/chat/sessions?model=openai/gpt-4o")
        assert resp.status_code == 200
        data = resp.json()
        assert data.get("total", -1) >= 0


class TestChatSessionDetailEndpoint:
    def test_nonexistent_session_returns_404(self, client: TestClient) -> None:
        resp = client.get("/api/chat/sessions//tmp/nonexistent-chat-session-99999.jsonl")
        assert resp.status_code == 404


class TestChatSearchEndpoint:
    def test_search_returns_200(self, client: TestClient) -> None:
        resp = client.post(
            "/api/chat/sessions/search",
            json={"query": "hello", "limit": 5},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "results" in data
        assert "total" in data
        assert "query" in data

    def test_search_large_limit_rejected(self, client: TestClient) -> None:
        resp = client.post(
            "/api/chat/sessions/search",
            json={"query": "hello", "limit": 200},
        )
        assert resp.status_code == 422

    def test_search_empty_query(self, client: TestClient) -> None:
        resp = client.post(
            "/api/chat/sessions/search",
            json={"query": "", "limit": 5},
        )
        assert resp.status_code == 200


class TestChatValidateEndpoint:
    def test_validate_valid_message(self, client: TestClient) -> None:
        resp = client.post(
            "/api/chat/validate",
            json={"role": "user", "content": "Hello"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["valid"] is True
        assert data["as_api"] == {"role": "user", "content": "Hello"}
        assert data["as_persistent"]["role"] == "user"
        assert data["as_persistent"]["content"] == "Hello"

    def test_validate_message_with_timestamp(self, client: TestClient) -> None:
        resp = client.post(
            "/api/chat/validate",
            json={
                "role": "assistant",
                "content": "Hi",
                "timestamp": "2026-01-01T00:00:00Z",
                "model": "openai/gpt-4o",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["valid"] is True
        assert "timestamp" not in data["as_api"]
        assert data["as_persistent"]["timestamp"] == "2026-01-01T00:00:00Z"
        assert data["as_persistent"]["model"] == "openai/gpt-4o"

    def test_validate_invalid_role(self, client: TestClient) -> None:
        resp = client.post(
            "/api/chat/validate",
            json={"role": "admin", "content": "hi"},
        )
        assert resp.status_code == 422

    def test_validate_missing_role(self, client: TestClient) -> None:
        resp = client.post(
            "/api/chat/validate",
            json={"content": "hi"},
        )
        assert resp.status_code == 422

    def test_validate_missing_content(self, client: TestClient) -> None:
        resp = client.post(
            "/api/chat/validate",
            json={"role": "user"},
        )
        assert resp.status_code == 422

    def test_validate_system_role(self, client: TestClient) -> None:
        resp = client.post(
            "/api/chat/validate",
            json={"role": "system", "content": "You are helpful."},
        )
        assert resp.status_code == 200

    def test_validate_tool_role(self, client: TestClient) -> None:
        resp = client.post(
            "/api/chat/validate",
            json={"role": "tool", "content": '{"result": "ok"}'},
        )
        assert resp.status_code == 200


class TestChatConfigValidateEndpoint:
    def test_validate_config_defaults(self, client: TestClient) -> None:
        resp = client.post("/api/chat/config/validate", json={})
        assert resp.status_code == 200
        data = resp.json()
        assert data["valid"] is True
        kwargs = data["session_kwargs"]
        assert kwargs["model"] == "default"
        assert kwargs["save_interval"] == 5
        assert kwargs["resume"] is False

    def test_validate_config_custom(self, client: TestClient) -> None:
        resp = client.post(
            "/api/chat/config/validate",
            json={
                "model": "deepseek/deepseek-chat",
                "system_prompt": "Be brief.",
                "save_interval": 10,
                "max_context": 4096,
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["valid"] is True
        kwargs = data["session_kwargs"]
        assert kwargs["model"] == "deepseek/deepseek-chat"
        assert kwargs["system_prompt"] == "Be brief."
        assert kwargs["save_interval"] == 10
        assert kwargs["max_context"] == 4096

    def test_validate_config_invalid_save_interval(self, client: TestClient) -> None:
        resp = client.post(
            "/api/chat/config/validate",
            json={"save_interval": 0},
        )
        assert resp.status_code == 422


class TestChatStatsEndpoint:
    def test_stats_returns_200(self, client: TestClient) -> None:
        resp = client.get("/api/chat/stats")
        assert resp.status_code == 200
        data = resp.json()
        assert "total_sessions" in data
        assert "total_messages" in data
        assert "unique_models" in data


class TestChatCliHelp:
    def test_chat_help_shows_daemon_url(self) -> None:
        result = _gludd("chat", "--help")
        assert result.returncode == 0
        assert "--daemon-url" in result.stdout

    def test_chat_help_shows_search(self) -> None:
        result = _gludd("chat", "--help")
        assert result.returncode == 0
        assert "--search" in result.stdout

    def test_chat_search_requires_daemon_url(self) -> None:
        result = _gludd("chat", "--search", "hello")
        assert result.returncode != 0
        assert "requires --daemon-url" in result.stderr

    def test_chat_list_sessions_local(self) -> None:
        result = _gludd("chat", "--list-sessions")
        assert result.returncode == 0


class TestChatDaemonRoundtrip:
    def test_validate_roundtrip_matches_contract(self, client: TestClient) -> None:
        from general_ludd.chat.contracts import ChatMessage

        original = ChatMessage(
            role="assistant",
            content="Hello world",
            timestamp="2026-01-01T00:00:00Z",
            model="openai/gpt-4o",
        )
        record = original.as_persistent_record()

        resp = client.post("/api/chat/validate", json=record)
        assert resp.status_code == 200
        data = resp.json()
        assert data["valid"] is True
        assert data["as_api"] == {"role": "assistant", "content": "Hello world"}

        restored = ChatMessage.from_dict(data["as_persistent"])
        assert restored == original
