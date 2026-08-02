from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from general_ludd.routers.chat import register


@pytest.fixture
def app_with_chat_router() -> FastAPI:
    app = FastAPI()
    gateway_mock = MagicMock()
    gateway_mock.call_model_stream.return_value = ["Hello", " ", "world"]
    gateway_mock.call_model.return_value = "Hello world"

    app.state._model_gateway = gateway_mock
    register(app, {})
    return app


class TestChatCompletionsEndpoint:
    def test_completions_stream_returns_sse(self, app_with_chat_router: FastAPI) -> None:
        client = TestClient(app_with_chat_router)
        resp = client.post(
            "/api/chat/completions",
            json={
                "messages": [
                    {"role": "system", "content": "Be helpful."},
                    {"role": "user", "content": "Hello"},
                ],
                "model_profile_id": "default",
                "stream": True,
            },
        )
        assert resp.status_code == 200
        assert "text/event-stream" in resp.headers.get("content-type", "")

    def test_completions_sync_returns_json(self, app_with_chat_router: FastAPI) -> None:
        client = TestClient(app_with_chat_router)
        resp = client.post(
            "/api/chat/completions/sync",
            json={
                "messages": [
                    {"role": "user", "content": "Hello"},
                ],
                "model_profile_id": "default",
                "stream": False,
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "response" in data
        assert data["model_profile_id"] == "default"

    def test_completions_rejects_invalid_role(self, app_with_chat_router: FastAPI) -> None:
        client = TestClient(app_with_chat_router)
        resp = client.post(
            "/api/chat/completions/sync",
            json={
                "messages": [
                    {"role": "invalid_role", "content": "Hello"},
                ],
            },
        )
        assert resp.status_code == 422

    def test_completions_no_gateway_returns_503(self) -> None:
        app = FastAPI()
        register(app, {})
        client = TestClient(app)
        resp = client.post(
            "/api/chat/completions/sync",
            json={
                "messages": [
                    {"role": "user", "content": "Hello"},
                ],
            },
        )
        assert resp.status_code == 503

    def test_completions_sync_no_gateway_returns_503(self) -> None:
        app = FastAPI()
        register(app, {})
        client = TestClient(app)
        resp = client.post(
            "/api/chat/completions/sync",
            json={
                "messages": [
                    {"role": "user", "content": "Hello"},
                ],
            },
        )
        assert resp.status_code == 503

    def test_completions_validates_all_messages(self, app_with_chat_router: FastAPI) -> None:
        client = TestClient(app_with_chat_router)
        resp = client.post(
            "/api/chat/completions/sync",
            json={
                "messages": [
                    {"role": "system", "content": "sys"},
                    {"role": "user", "content": "Hello"},
                    {"role": "assistant", "content": "Hi"},
                    {"role": "user", "content": "bad role one"},
                    {"role": "nope", "content": "invalid"},
                ],
            },
        )
        assert resp.status_code == 422

    def test_completions_sync_empty_messages_ok(self, app_with_chat_router: FastAPI) -> None:
        client = TestClient(app_with_chat_router)
        resp = client.post(
            "/api/chat/completions/sync",
            json={
                "messages": [],
            },
        )
        assert resp.status_code == 200

    def test_completions_sync_temperature_max_tokens(self, app_with_chat_router: FastAPI) -> None:
        client = TestClient(app_with_chat_router)
        resp = client.post(
            "/api/chat/completions/sync",
            json={
                "messages": [{"role": "user", "content": "Hello"}],
                "temperature": 0.7,
                "max_tokens": 256,
            },
        )
        assert resp.status_code == 200


class TestChatSendModule:
    def test_chat_send_module_spec_is_valid(self) -> None:

        from ansible.module_utils.basic import AnsibleModule

        module_args = {
            "messages": {"type": "list", "elements": "dict", "required": True},
            "daemon_url": {"type": "str", "default": "http://localhost:8000"},
            "model_profile_id": {"type": "str", "default": "default"},
            "temperature": {"type": "float", "required": False, "default": None},
            "max_tokens": {"type": "int", "required": False, "default": None},
            "stream": {"type": "bool", "default": False},
        }
        mod = AnsibleModule(argument_spec=module_args, check_invalid_arguments=False)
        assert mod is not None
