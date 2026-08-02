"""Unit tests for POST /admin/models/chat-stream SSE streaming endpoint."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


class _FakeStreamChunk:
    def __init__(self, content: str = "", tool_calls: Any = None, usage_metadata: Any = None) -> None:
        self.content = content
        self.tool_calls = tool_calls or []
        self.usage_metadata = usage_metadata or {}


def _parse_sse_body(body: bytes) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for line in body.decode("utf-8").strip().split("\n\n"):
        if not line.strip():
            continue
        for sub in line.split("\n"):
            if sub.startswith("data: "):
                events.append(json.loads(sub[len("data: ") :]))
    return events


def _streaming_client(app: FastAPI) -> TestClient:
    class _StreamingTestClient(TestClient):
        def stream_post(self, path: str, **kwargs: Any) -> bytes:
            content = bytearray()
            with self.stream("POST", path, **kwargs) as response:
                response.raise_for_status()
                for chunk in response.iter_bytes():
                    content.extend(chunk)
            return bytes(content)

    return _StreamingTestClient(app, raise_server_exceptions=False)


def _chunks(chunks: list[_FakeStreamChunk]) -> list[_FakeStreamChunk]:
    return chunks


def _minimal_app(monkeypatch: pytest.MonkeyPatch) -> FastAPI:
    from general_ludd.routers import models as models_mod

    app = FastAPI()
    gw = MagicMock()
    profile = MagicMock()
    profile.model_profile_id = "default"
    gw.list_profiles.return_value = [profile]
    app.state._model_gateway = gw
    app.state._budget_guard = None
    app.state._health_tracker = None
    app.state._project_manager = None
    app.state._metrics_collector = None
    app.state._session_factory = None
    app.state._model_registry = MagicMock()
    app.state._model_registry.search.return_value = []
    app.state._model_registry.list_downloaded.return_value = []
    models_mod.register(app, {})
    return app


class TestChatStreamBasic:
    def test_streams_chunks_as_sse_events(self, monkeypatch: pytest.MonkeyPatch) -> None:
        app = _minimal_app(monkeypatch)
        gw = app.state._model_gateway
        gw.call_model_stream.return_value = iter(
            _chunks(
                [
                    _FakeStreamChunk(content="Hello"),
                    _FakeStreamChunk(content=" world"),
                    _FakeStreamChunk(
                        content="!",
                        usage_metadata={"input_tokens": 10, "output_tokens": 3, "total_tokens": 13},
                    ),
                ]
            )
        )

        client = _streaming_client(app)
        body = client.stream_post(
            "/admin/models/chat-stream",
            json={"messages": [{"role": "user", "content": "Say hello"}]},
        )
        events = _parse_sse_body(body)
        assert len(events) == 4
        assert events[0] == {"content": "Hello", "done": False}
        assert events[1] == {"content": " world", "done": False}
        assert events[2] == {"content": "!", "done": False}
        assert events[3]["done"] is True
        assert "usage" in events[3]

    def test_empty_model_profile_resolves_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        app = _minimal_app(monkeypatch)
        gw = app.state._model_gateway
        chunk = _FakeStreamChunk(
            content="ok",
            usage_metadata={"input_tokens": 1, "output_tokens": 1},
        )
        gw.call_model_stream.return_value = iter(_chunks([chunk]))

        client = _streaming_client(app)
        body = client.stream_post(
            "/admin/models/chat-stream",
            json={"messages": [{"role": "user", "content": "hi"}]},
        )
        events = _parse_sse_body(body)
        assert len(events) == 2
        assert events[0]["content"] == "ok"
        assert events[1]["done"] is True

    def test_explicit_profile_passed_to_gateway(self, monkeypatch: pytest.MonkeyPatch) -> None:
        app = _minimal_app(monkeypatch)
        gw = app.state._model_gateway
        gw.call_model_stream.return_value = iter(
            _chunks(
                [
                    _FakeStreamChunk(content="x", usage_metadata={"input_tokens": 1, "output_tokens": 1}),
                ]
            )
        )

        client = _streaming_client(app)
        body = client.stream_post(
            "/admin/models/chat-stream",
            json={
                "messages": [{"role": "user", "content": "hi"}],
                "model_profile_id": "custom-profile",
            },
        )
        events = _parse_sse_body(body)
        assert len(events) == 2
        gw.call_model_stream.assert_called_once_with(
            "custom-profile",
            [{"role": "user", "content": "hi"}],
        )

    def test_system_message_included(self, monkeypatch: pytest.MonkeyPatch) -> None:
        app = _minimal_app(monkeypatch)
        gw = app.state._model_gateway
        gw.call_model_stream.return_value = iter(
            _chunks(
                [
                    _FakeStreamChunk(content="yes", usage_metadata={"input_tokens": 5, "output_tokens": 1}),
                ]
            )
        )

        client = _streaming_client(app)
        client.stream_post(
            "/admin/models/chat-stream",
            json={
                "messages": [
                    {"role": "system", "content": "You are helpful."},
                    {"role": "user", "content": "hi"},
                ],
            },
        )
        gw.call_model_stream.assert_called_once_with(
            "default",
            [
                {"role": "system", "content": "You are helpful."},
                {"role": "user", "content": "hi"},
            ],
        )


class TestChatStreamValidation:
    def test_empty_messages_returns_422(self, monkeypatch: pytest.MonkeyPatch) -> None:
        app = _minimal_app(monkeypatch)
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.post("/admin/models/chat-stream", json={"messages": []})
        assert resp.status_code == 422

    def test_missing_messages_returns_422(self, monkeypatch: pytest.MonkeyPatch) -> None:
        app = _minimal_app(monkeypatch)
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.post("/admin/models/chat-stream", json={})
        assert resp.status_code == 422

    def test_messages_not_list_returns_422(self, monkeypatch: pytest.MonkeyPatch) -> None:
        app = _minimal_app(monkeypatch)
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.post("/admin/models/chat-stream", json={"messages": "bad"})
        assert resp.status_code == 422

    def test_message_missing_role_returns_422(self, monkeypatch: pytest.MonkeyPatch) -> None:
        app = _minimal_app(monkeypatch)
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.post(
            "/admin/models/chat-stream",
            json={"messages": [{"content": "hi"}]},
        )
        assert resp.status_code == 422

    def test_invalid_role_returns_422(self, monkeypatch: pytest.MonkeyPatch) -> None:
        app = _minimal_app(monkeypatch)
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.post(
            "/admin/models/chat-stream",
            json={"messages": [{"role": "admin", "content": "hi"}]},
        )
        assert resp.status_code == 422

    def test_message_missing_content_returns_422(self, monkeypatch: pytest.MonkeyPatch) -> None:
        app = _minimal_app(monkeypatch)
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.post(
            "/admin/models/chat-stream",
            json={"messages": [{"role": "user"}]},
        )
        assert resp.status_code == 422

    def test_content_not_string_returns_422(self, monkeypatch: pytest.MonkeyPatch) -> None:
        app = _minimal_app(monkeypatch)
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.post(
            "/admin/models/chat-stream",
            json={"messages": [{"role": "user", "content": 123}]},
        )
        assert resp.status_code == 422


class TestChatStreamErrors:
    def test_budget_exhausted_returns_429(self, monkeypatch: pytest.MonkeyPatch) -> None:
        app = _minimal_app(monkeypatch)
        guard = MagicMock()
        guard.check_all_limits.return_value = {"allowed": False, "reason": "daily cap"}
        app.state._budget_guard = guard
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.post(
            "/admin/models/chat-stream",
            json={"messages": [{"role": "user", "content": "hi"}]},
        )
        assert resp.status_code == 429

    def test_gateway_raises_returns_502(self, monkeypatch: pytest.MonkeyPatch) -> None:
        app = _minimal_app(monkeypatch)
        gw = app.state._model_gateway
        gw.call_model_stream.side_effect = RuntimeError("kaboom")
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.post(
            "/admin/models/chat-stream",
            json={"messages": [{"role": "user", "content": "hi"}]},
        )
        assert resp.status_code == 502
        assert "kaboom" not in resp.json()["detail"]

    def test_budget_check_exception_returns_503(self, monkeypatch: pytest.MonkeyPatch) -> None:
        app = _minimal_app(monkeypatch)
        guard = MagicMock()
        guard.check_all_limits.side_effect = RuntimeError("db down")
        app.state._budget_guard = guard
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.post(
            "/admin/models/chat-stream",
            json={"messages": [{"role": "user", "content": "hi"}]},
        )
        assert resp.status_code == 503


class TestChatStreamContentType:
    def test_sse_content_type(self, monkeypatch: pytest.MonkeyPatch) -> None:
        app = _minimal_app(monkeypatch)
        gw = app.state._model_gateway
        gw.call_model_stream.return_value = iter(
            _chunks(
                [
                    _FakeStreamChunk(content="ok", usage_metadata={"input_tokens": 1, "output_tokens": 1}),
                ]
            )
        )

        with TestClient(app, raise_server_exceptions=True) as client, client.stream(
            "POST",
            "/admin/models/chat-stream",
            json={"messages": [{"role": "user", "content": "hi"}]},
        ) as resp:
            assert resp.status_code == 200
            assert "text/event-stream" in resp.headers.get("content-type", "")


class TestChatStreamMaxTokens:
    def test_max_tokens_passed_to_gateway(self, monkeypatch: pytest.MonkeyPatch) -> None:
        app = _minimal_app(monkeypatch)
        gw = app.state._model_gateway
        gw.call_model_stream.return_value = iter(
            _chunks(
                [
                    _FakeStreamChunk(content="ok", usage_metadata={"input_tokens": 1, "output_tokens": 1}),
                ]
            )
        )

        client = _streaming_client(app)
        client.stream_post(
            "/admin/models/chat-stream",
            json={
                "messages": [{"role": "user", "content": "hi"}],
                "max_tokens": 512,
            },
        )
        gw.call_model_stream.assert_called_once_with(
            "default",
            [{"role": "user", "content": "hi"}],
            requested_max_output_tokens=512,
        )
