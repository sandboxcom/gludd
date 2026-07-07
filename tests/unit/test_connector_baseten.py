"""Unit tests for the Baseten model-hosting connector.

All HTTP is MOCKED via an injectable ``http_request`` callable — no network is
touched. Tests cover construction, ``list_deployments()``, ``invoke()``,
``health()``, and the documented error paths (404 unknown deployment, 401
invalid key, 5xx Baseten outage).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import pytest

from general_ludd.connectors.baseten import (
    BasetenClient,
    BasetenConfigError,
    BasetenDeployment,
    BasetenInvocationError,
)

# --- canned payloads --------------------------------------------------------


def _models_payload() -> dict[str, Any]:
    """GET /v1/models response (management API)."""
    return {
        "id": "mdl_abc123",
        "items": [
            {
                "id": "mdl_abc123",
                "name": "qwen-2.5-7b",
                "deployments": [
                    {
                        "id": "dep_xyz789",
                        "model_id": "mdl_abc123",
                        "status": "ACTIVE",
                        "environment": "production",
                        "created_at": "2026-07-01T10:00:00Z",
                    },
                    {
                        "id": "dep_def000",
                        "model_id": "mdl_abc123",
                        "status": "INACTIVE",
                        "environment": "development",
                        "created_at": "2026-07-02T11:00:00Z",
                    },
                ],
            },
        ],
    }


def _chat_completions_payload() -> dict[str, Any]:
    """POST /chat/completions response (inference API, OpenAI-compatible)."""
    return {
        "id": "chatcmpl-xyz",
        "object": "chat.completion",
        "created": 1_720_000_000,
        "model": "qwen-2.5-7b",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": "hello there"},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8},
    }


class _Transport:
    """Records calls and returns scripted ``(status, json)`` tuples by URL substring."""

    def __init__(self, routes: list[tuple[str, int, dict[str, Any]]]) -> None:
        self._routes = routes
        self.calls: list[dict[str, Any]] = []

    def __call__(
        self,
        method: str,
        url: str,
        headers: Mapping[str, str],
        body: bytes | None,
    ) -> tuple[int, dict[str, Any]]:
        self.calls.append(
            {"method": method, "url": url, "headers": dict(headers), "body": body}
        )
        for needle, status, payload in self._routes:
            if needle in url:
                return status, payload
        raise AssertionError(f"no canned route for url {url!r}")


@pytest.fixture
def api_key_env(monkeypatch: pytest.MonkeyPatch) -> str:
    monkeypatch.setenv("BASETEN_API_KEY", "key-deadbeef")
    return "BASETEN_API_KEY"


# --- construction -----------------------------------------------------------


def test_constructs_with_defaults(api_key_env: str) -> None:
    client = BasetenClient({"api_key_env": api_key_env})
    assert client.KIND == "pipeline"
    assert isinstance(client.name, str)
    assert client.name


def test_constructs_with_custom_base_url(api_key_env: str) -> None:
    client = BasetenClient(
        {"api_key_env": api_key_env, "base_url": "https://custom.example.com/v1"}
    )
    assert client._base_url == "https://custom.example.com/v1"


def test_missing_api_key_env_raises_config_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BASETEN_API_KEY", raising=False)
    with pytest.raises(BasetenConfigError):
        BasetenClient({"api_key_env": "BASETEN_API_KEY"})


# --- list_deployments() -----------------------------------------------------


def test_list_deployments_returns_normalized(api_key_env: str) -> None:
    transport = _Transport([("/v1/models", 200, _models_payload())])
    client = BasetenClient(
        {"api_key_env": api_key_env},
        http_request=transport,
    )
    deployments = client.list_deployments()

    assert len(deployments) == 2
    first: BasetenDeployment = deployments[0]
    assert first["id"] == "dep_xyz789"
    assert first["model_id"] == "mdl_abc123"
    assert first["status"] == "ACTIVE"
    assert first["environment"] == "production"
    # GET against the management API
    call = transport.calls[0]
    assert call["method"] == "GET"
    assert "/v1/models" in call["url"]


def test_list_deployments_5xx_raises_invocation_error(api_key_env: str) -> None:
    transport = _Transport([("/v1/models", 503, {"error": "upstream offline"})])
    client = BasetenClient({"api_key_env": api_key_env}, http_request=transport)
    with pytest.raises(BasetenInvocationError):
        client.list_deployments()


# --- invoke() ---------------------------------------------------------------


def test_invoke_posts_to_chat_completions(api_key_env: str) -> None:
    transport = _Transport([("/chat/completions", 200, _chat_completions_payload())])
    client = BasetenClient({"api_key_env": api_key_env}, http_request=transport)

    result = client.invoke(
        "qwen-2.5-7b",
        {"messages": [{"role": "user", "content": "hi"}]},
    )

    call = transport.calls[0]
    assert call["method"] == "POST"
    assert call["url"].endswith("/chat/completions")
    # body carries model + caller inputs merged
    import json as _json

    sent = _json.loads(call["body"])
    assert sent["model"] == "qwen-2.5-7b"
    assert sent["messages"] == [{"role": "user", "content": "hi"}]

    assert isinstance(result, dict)
    assert result["model"] == "qwen-2.5-7b"


def test_invoke_404_unknown_deployment_raises(api_key_env: str) -> None:
    transport = _Transport(
        [("/chat/completions", 404, {"error": {"message": "model not found"}})]
    )
    client = BasetenClient({"api_key_env": api_key_env}, http_request=transport)
    with pytest.raises(BasetenInvocationError):
        client.invoke("does-not-exist", {"messages": []})


def test_invoke_401_invalid_key_raises(api_key_env: str) -> None:
    transport = _Transport(
        [("/chat/completions", 401, {"error": {"message": "invalid api key"}})]
    )
    client = BasetenClient({"api_key_env": api_key_env}, http_request=transport)
    with pytest.raises(BasetenInvocationError):
        client.invoke("qwen-2.5-7b", {"messages": []})


# --- health() ---------------------------------------------------------------


def test_health_ok_when_models_endpoint_2xx(api_key_env: str) -> None:
    transport = _Transport([("/v1/models", 200, _models_payload())])
    client = BasetenClient({"api_key_env": api_key_env}, http_request=transport)
    result = client.health()
    assert result["ok"] is True
    assert result["reachable"] is True
    assert result["api_key_valid"] is True


def test_health_401_reports_invalid_key(api_key_env: str) -> None:
    transport = _Transport([("/v1/models", 401, {"error": "unauthorized"})])
    client = BasetenClient({"api_key_env": api_key_env}, http_request=transport)
    result = client.health()
    assert result["ok"] is False
    assert result["api_key_valid"] is False


def test_health_5xx_reports_outage(api_key_env: str) -> None:
    transport = _Transport([("/v1/models", 503, {"error": "down"})])
    client = BasetenClient({"api_key_env": api_key_env}, http_request=transport)
    result = client.health()
    assert result["ok"] is False
    assert result["reachable"] is False


def test_health_transport_exception_never_raises(api_key_env: str) -> None:
    def _boom(
        method: str, url: str, headers: Mapping[str, str], body: bytes | None
    ) -> tuple[int, dict[str, Any]]:
        raise OSError("network down")

    client = BasetenClient({"api_key_env": api_key_env}, http_request=_boom)
    result = client.health()
    assert result["ok"] is False
    assert result["reachable"] is False
