"""Deep endpoint tests for routers/chat.py — exercises all 7 registered routes."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from general_ludd.chat.contracts import ChatConfig, ChatMessage


def _build_app(daemon_state: dict[str, object] | None = None) -> FastAPI:
    from general_ludd.routers import chat as chat_mod

    app = FastAPI()
    app.state._model_gateway = None
    chat_mod.register(app, daemon_state or {})
    return app


# ── sessions list ──────────────────────────────────────────────────────


class TestListSessions:
    def test_empty_history_returns_zero(self):
        app = _build_app()
        c = TestClient(app, raise_server_exceptions=False)
        with patch("general_ludd.routers.chat.ChatHistory") as mk:
            mk.return_value.list_sessions.return_value = []
            r = c.get("/api/chat/sessions")
        assert r.status_code == 200
        assert r.json() == {"sessions": [], "total": 0, "limit": 20}

    def test_limit_param_respected(self):
        app = _build_app()
        c = TestClient(app, raise_server_exceptions=False)
        with patch("general_ludd.routers.chat.ChatHistory") as mk:
            mk.return_value.list_sessions.return_value = [{"id": 1}]
            r = c.get("/api/chat/sessions?limit=5")
        mk.return_value.list_sessions.assert_called_once_with(limit=5, model_filter=None)
        assert r.json()["limit"] == 5

    def test_model_filter_passed_through(self):
        app = _build_app()
        c = TestClient(app, raise_server_exceptions=False)
        with patch("general_ludd.routers.chat.ChatHistory") as mk:
            mk.return_value.list_sessions.return_value = []
            c.get("/api/chat/sessions?model=gpt-4")
        mk.return_value.list_sessions.assert_called_once_with(limit=20, model_filter="gpt-4")

    def test_limit_below_min_returns_422(self):
        app = _build_app()
        c = TestClient(app, raise_server_exceptions=False)
        r = c.get("/api/chat/sessions?limit=0")
        assert r.status_code == 422

    def test_limit_above_max_returns_422(self):
        app = _build_app()
        c = TestClient(app, raise_server_exceptions=False)
        r = c.get("/api/chat/sessions?limit=101")
        assert r.status_code == 422


# ── get session ────────────────────────────────────────────────────────


class TestGetSession:
    def test_found_session_returns_detail(self):
        app = _build_app()
        with patch("general_ludd.routers.chat.ChatHistory") as mk:
            mk.return_value.get_session.return_value = {"file": "s1.jsonl", "message_count": 3}
            mk.return_value.get_messages.return_value = [{"role": "user", "content": "hi"}]
            c = TestClient(app, raise_server_exceptions=False)
            r = c.get("/api/chat/sessions/s1.jsonl")
        data = r.json()
        assert r.status_code == 200
        assert data["session"] == {"file": "s1.jsonl", "message_count": 3}
        assert data["message_count"] == 1
        assert len(data["messages"]) == 1

    def test_not_found_returns_404(self):
        app = _build_app()
        with patch("general_ludd.routers.chat.ChatHistory") as mk:
            mk.return_value.get_session.return_value = None
            c = TestClient(app, raise_server_exceptions=False)
            r = c.get("/api/chat/sessions/missing.jsonl")
        assert r.status_code == 404

    def test_url_encoded_path_decoded(self):
        app = _build_app()
        with patch("general_ludd.routers.chat.ChatHistory") as mk:
            mk.return_value.get_session.return_value = {"file": "dir/sub/s.jsonl"}
            mk.return_value.get_messages.return_value = []
            c = TestClient(app, raise_server_exceptions=False)
            r = c.get("/api/chat/sessions/dir%2Fsub%2Fs.jsonl")
        assert r.status_code == 200
        mk.return_value.get_session.assert_called_once_with("dir/sub/s.jsonl")


# ── search ─────────────────────────────────────────────────────────────


class TestSearchSessions:
    def test_search_returns_results(self):
        app = _build_app()
        with patch("general_ludd.routers.chat.ChatHistory") as mk:
            mk.return_value.search.return_value = [{"file": "a.jsonl"}, {"file": "b.jsonl"}]
            c = TestClient(app, raise_server_exceptions=False)
            r = c.post("/api/chat/sessions/search", json={"query": "test", "limit": 5})
        data = r.json()
        assert r.status_code == 200
        assert data["total"] == 2
        assert data["query"] == "test"
        assert len(data["results"]) == 2

    def test_query_required(self):
        app = _build_app()
        c = TestClient(app, raise_server_exceptions=False)
        r = c.post("/api/chat/sessions/search", json={"limit": 5})
        assert r.status_code == 422

    def test_limit_defaults_to_20(self):
        app = _build_app()
        with patch("general_ludd.routers.chat.ChatHistory") as mk:
            mk.return_value.search.return_value = []
            c = TestClient(app, raise_server_exceptions=False)
            c.post("/api/chat/sessions/search", json={"query": "x"})
        mk.return_value.search.assert_called_once_with("x", limit=20)

    def test_limit_below_min_returns_422(self):
        app = _build_app()
        c = TestClient(app, raise_server_exceptions=False)
        r = c.post("/api/chat/sessions/search", json={"query": "x", "limit": 0})
        assert r.status_code == 422

    def test_limit_above_max_returns_422(self):
        app = _build_app()
        c = TestClient(app, raise_server_exceptions=False)
        r = c.post("/api/chat/sessions/search", json={"query": "x", "limit": 101})
        assert r.status_code == 422


# ── stats ──────────────────────────────────────────────────────────────


class TestChatStats:
    def test_stats_delegates_to_history(self):
        app = _build_app()
        with patch("general_ludd.routers.chat.ChatHistory") as mk:
            mk.return_value.stats.return_value = {"total_sessions": 3}
            c = TestClient(app, raise_server_exceptions=False)
            r = c.get("/api/chat/stats")
        assert r.status_code == 200
        assert r.json() == {"total_sessions": 3}


# ── completions (streaming) ────────────────────────────────────────────


class TestCompletionsStreaming:
    def test_no_gateway_returns_503(self):
        app = _build_app()
        app.state._model_gateway = None
        c = TestClient(app, raise_server_exceptions=False)
        r = c.post("/api/chat/completions", json={"messages": [{"role": "user", "content": "hi"}]})
        assert r.status_code == 503

    def test_invalid_role_returns_422(self):
        app = _build_app()
        gw = MagicMock()
        app.state._model_gateway = gw
        c = TestClient(app, raise_server_exceptions=False)
        r = c.post(
            "/api/chat/completions",
            json={"messages": [{"role": "invalid_role", "content": "hi"}]},
        )
        assert r.status_code == 422

    def test_content_type_is_sse(self):
        app = _build_app()
        gw = MagicMock()
        gw.call_model_stream.return_value = iter(["hello"])
        app.state._model_gateway = gw
        with (
            TestClient(app, raise_server_exceptions=False) as c,
            c.stream(
                "POST",
                "/api/chat/completions",
                json={"messages": [{"role": "user", "content": "hi"}]},
            ) as resp,
        ):
            assert resp.status_code == 200
            assert "text/event-stream" in resp.headers.get("content-type", "")

    def test_stream_emits_done_signal(self):
        app = _build_app()
        gw = MagicMock()
        gw.call_model_stream.return_value = iter(["chunk1"])
        app.state._model_gateway = gw
        c = TestClient(app, raise_server_exceptions=False)
        buf = bytearray()
        with c.stream(
            "POST",
            "/api/chat/completions",
            json={"messages": [{"role": "user", "content": "hi"}]},
        ) as resp:
            for chunk in resp.iter_bytes():
                buf.extend(chunk)
        text = buf.decode()
        assert "data: chunk1" in text
        assert "data: [DONE]" in text

    def test_stream_handles_bytes_chunk(self):
        app = _build_app()
        gw = MagicMock()
        gw.call_model_stream.return_value = iter([b"binary"])
        app.state._model_gateway = gw
        c = TestClient(app, raise_server_exceptions=False)
        buf = bytearray()
        with c.stream(
            "POST",
            "/api/chat/completions",
            json={"messages": [{"role": "user", "content": "hi"}]},
        ) as resp:
            for chunk in resp.iter_bytes():
                buf.extend(chunk)
        assert b"data: binary" in buf

    def test_stream_handles_dict_chunk(self):
        app = _build_app()
        gw = MagicMock()
        gw.call_model_stream.return_value = iter([{"delta": "x"}])
        app.state._model_gateway = gw
        c = TestClient(app, raise_server_exceptions=False)
        buf = bytearray()
        with c.stream(
            "POST",
            "/api/chat/completions",
            json={"messages": [{"role": "user", "content": "hi"}]},
        ) as resp:
            for chunk in resp.iter_bytes():
                buf.extend(chunk)
        assert b'{"delta": "x"}' in buf

    def test_stream_error_emitted_in_sse(self):
        app = _build_app()
        gw = MagicMock()
        gw.call_model_stream.side_effect = RuntimeError("boom")
        app.state._model_gateway = gw
        c = TestClient(app, raise_server_exceptions=False)
        buf = bytearray()
        with c.stream(
            "POST",
            "/api/chat/completions",
            json={"messages": [{"role": "user", "content": "hi"}]},
        ) as resp:
            for chunk in resp.iter_bytes():
                buf.extend(chunk)
        text = buf.decode()
        assert "boom" in text

    def test_passes_temperature_and_max_tokens(self):

        app = _build_app()
        gw = MagicMock()
        gw.call_model_stream.return_value = iter(["ok"])
        app.state._model_gateway = gw
        c = TestClient(app, raise_server_exceptions=False)
        buf = bytearray()
        with c.stream(
            "POST",
            "/api/chat/completions",
            json={
                "messages": [{"role": "user", "content": "hi"}],
                "temperature": 0.7,
                "max_tokens": 256,
            },
        ) as resp:
            for chunk in resp.iter_bytes():
                buf.extend(chunk)
        call_args = gw.call_model_stream.call_args
        assert call_args.kwargs["temperature"] == 0.7
        assert call_args.kwargs["max_tokens"] == 256


# ── completions/sync ───────────────────────────────────────────────────


class TestCompletionsSync:
    def test_no_gateway_returns_503(self):
        app = _build_app()
        c = TestClient(app, raise_server_exceptions=False)
        r = c.post("/api/chat/completions/sync", json={"messages": [{"role": "user", "content": "hi"}]})
        assert r.status_code == 503

    def test_successful_call_returns_response(self):
        app = _build_app()
        gw = MagicMock()
        gw.call_model.return_value = "Hello back"
        app.state._model_gateway = gw
        c = TestClient(app, raise_server_exceptions=False)
        r = c.post("/api/chat/completions/sync", json={"messages": [{"role": "user", "content": "hi"}]})
        assert r.status_code == 200
        data = r.json()
        assert data["response"] == "Hello back"
        assert data["model_profile_id"] == "default"

    def test_sync_handles_dict_response(self):
        app = _build_app()
        gw = MagicMock()
        gw.call_model.return_value = {"text": "structured"}
        app.state._model_gateway = gw
        c = TestClient(app, raise_server_exceptions=False)
        r = c.post("/api/chat/completions/sync", json={"messages": [{"role": "user", "content": "hi"}]})
        assert r.status_code == 200
        assert r.json()["response"] == {"text": "structured"}

    def test_gateway_exception_returns_502(self):
        app = _build_app()
        gw = MagicMock()
        gw.call_model.side_effect = RuntimeError("dead")
        app.state._model_gateway = gw
        c = TestClient(app, raise_server_exceptions=False)
        r = c.post("/api/chat/completions/sync", json={"messages": [{"role": "user", "content": "hi"}]})
        assert r.status_code == 502
        assert "dead" in r.json()["detail"]


# ── validate message ───────────────────────────────────────────────────


class TestValidateMessage:
    def test_valid_message_returns_ok(self):
        app = _build_app()
        c = TestClient(app, raise_server_exceptions=False)
        r = c.post(
            "/api/chat/validate",
            json={
                "role": "user",
                "content": "Hello",
                "timestamp": "2024-01-01T00:00:00Z",
                "model": "gpt-4",
            },
        )
        assert r.status_code == 200
        data = r.json()
        assert data["valid"] is True
        assert data["as_api"] == {"role": "user", "content": "Hello"}
        assert data["as_persistent"]["role"] == "user"
        assert data["as_persistent"]["timestamp"] == "2024-01-01T00:00:00Z"

    def test_invalid_role_returns_422(self):
        app = _build_app()
        c = TestClient(app, raise_server_exceptions=False)
        r = c.post("/api/chat/validate", json={"role": "bogus", "content": "x"})
        assert r.status_code == 422

    def test_missing_role_returns_422(self):
        app = _build_app()
        c = TestClient(app, raise_server_exceptions=False)
        r = c.post("/api/chat/validate", json={"content": "x"})
        assert r.status_code == 422

    def test_all_four_valid_roles_accepted(self):
        app = _build_app()
        for role in ("system", "user", "assistant", "tool"):
            c = TestClient(app, raise_server_exceptions=False)
            r = c.post("/api/chat/validate", json={"role": role, "content": "x"})
            assert r.status_code == 200, f"role={role} should be valid"


# ── validate config ────────────────────────────────────────────────────


class TestValidateConfig:
    def test_minimal_valid_config(self):
        app = _build_app()
        c = TestClient(app, raise_server_exceptions=False)
        r = c.post("/api/chat/config/validate", json={"model": "gpt-4", "stream": True})
        assert r.status_code == 200
        data = r.json()
        assert data["valid"] is True
        assert "session_kwargs" in data

    def test_full_config_accepted(self):
        app = _build_app()
        payload = {
            "model": "gpt-4",
            "system_prompt": "Be helpful",
            "eval_mode": True,
            "api_base_url": "https://api.example.com",
            "api_key": "sk-test",
            "project_dir": "/tmp/proj",
            "history_file": "/tmp/hist.jsonl",
            "save_interval": 10,
            "resume": True,
            "max_context": 4096,
            "stream": False,
            "export_format": "json",
            "export_output": "/tmp/out.json",
        }
        c = TestClient(app, raise_server_exceptions=False)
        r = c.post("/api/chat/config/validate", json=payload)
        assert r.status_code == 200
        kw = r.json()["session_kwargs"]
        assert kw["model"] == "gpt-4"
        assert kw["system_prompt"] == "Be helpful"
        assert kw["eval_mode"] is True
        assert kw["max_context"] == 4096
        assert kw["save_interval"] == 10

    def test_zero_save_interval_rejected(self):
        app = _build_app()
        c = TestClient(app, raise_server_exceptions=False)
        r = c.post("/api/chat/config/validate", json={"model": "x", "save_interval": 0})
        assert r.status_code == 422

    def test_empty_body_fields_default(self):
        app = _build_app()
        c = TestClient(app, raise_server_exceptions=False)
        r = c.post("/api/chat/config/validate", json={})
        assert r.status_code == 200
        kw = r.json()["session_kwargs"]
        assert kw["model"] == "default"
        assert kw["save_interval"] == 5
        assert kw["resume"] is False


# ── model contracts ────────────────────────────────────────────────────


class TestChatMessageContract:
    def test_as_api_message_shape(self):
        msg = ChatMessage(role="assistant", content="OK")
        assert msg.as_api_message() == {"role": "assistant", "content": "OK"}

    def test_as_persistent_record_with_metadata(self):
        msg = ChatMessage(role="user", content="Q", timestamp="now", model="m1")
        rec = msg.as_persistent_record()
        assert rec["timestamp"] == "now"
        assert rec["model"] == "m1"

    def test_from_dict_roundtrip(self):
        d = {"role": "tool", "content": "result", "timestamp": "t1"}
        msg = ChatMessage.from_dict(d)
        assert msg.role == "tool"
        assert msg.content == "result"
        assert msg.timestamp == "t1"


class TestChatConfigContract:
    def test_minimal_session_kwargs(self):
        cfg = ChatConfig()
        kw = cfg.to_session_kwargs()
        assert kw["model"] == "default"
        assert kw["save_interval"] == 5
        assert kw["resume"] is False

    def test_save_interval_below_1_raises(self):
        with pytest.raises(ValueError, match="save_interval must be >= 1"):
            ChatConfig(save_interval=0)

    def test_from_cli_args_extracts_attributes(self):
        ns = type(
            "NS",
            (),
            {
                "__dict__": {
                    "model": "sonnet",
                    "system_prompt": "prompt",
                    "eval": True,
                    "api_base": "http://x",
                    "api_key": "k",
                    "project_dir": "p",
                    "history": "h",
                    "save_interval": 3,
                    "resume": True,
                    "max_context": "8192",
                    "stream": False,
                    "export": "json",
                    "export_output": "o.json",
                },
            },
        )
        cfg = ChatConfig.from_cli_args(ns())
        assert cfg.model == "sonnet"
        assert cfg.save_interval == 3
        assert cfg.max_context == 8192
        assert cfg.stream is False
