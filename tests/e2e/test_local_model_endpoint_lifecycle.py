"""Hermetic E2E coverage for an OpenAI-compatible local-model endpoint.

The server uses a real loopback TCP socket and Gludd's production
``ChatSession`` client, but returns deterministic responses in-process.  This
keeps startup, readiness, request, failure, and shutdown behavior realistic
without downloading a model or contacting a paid API.
"""

from __future__ import annotations

import time
from collections.abc import Iterator

import httpx
import pytest

from general_ludd.chat.session import ChatSession
from tests.e2e._local_model_endpoint import EndpointLifecycle

_MODEL_ID = "gludd-local-e2e"
_API_KEY = "local-e2e-token"

pytestmark = [pytest.mark.e2e, pytest.mark.timeout(15)]


@pytest.fixture
def local_endpoint() -> Iterator[EndpointLifecycle]:
    """Start a fresh endpoint and guarantee idempotent shutdown."""
    endpoint = EndpointLifecycle(model_id=_MODEL_ID)
    endpoint.start()
    try:
        yield endpoint
    finally:
        endpoint.stop()


def _chat_session(endpoint: EndpointLifecycle) -> ChatSession:
    return ChatSession(
        model=f"openai/{_MODEL_ID}",
        api_base_url=endpoint.base_url,
        api_key=_API_KEY,
        system_prompt="Answer locally and concisely.",
        save_interval=100,
    )


@pytest.mark.asyncio
async def test_ready_endpoint_serves_chat_and_text_completion(local_endpoint: EndpointLifecycle) -> None:
    """Exercise readiness, Gludd chat, completion compatibility, and evidence."""
    models = httpx.get(f"{local_endpoint.base_url}/models", timeout=1.0)
    assert models.status_code == 200
    assert models.json()["data"][0]["id"] == _MODEL_ID

    answer = await _chat_session(local_endpoint).run_once("say hello")
    assert answer == "local:say hello"

    completion = httpx.post(
        f"{local_endpoint.base_url}/completions",
        json={"model": _MODEL_ID, "prompt": "complete me", "max_tokens": 4},
        timeout=1.0,
    )
    assert completion.status_code == 200
    assert completion.json()["choices"][0]["text"] == "local:complete me"
    assert [record["path"] for record in local_endpoint.server.requests] == [
        "/v1/chat/completions",
        "/v1/completions",
    ]
    assert local_endpoint.server.requests[0]["authorization"] == f"Bearer {_API_KEY}"


@pytest.mark.asyncio
async def test_malformed_request_and_server_error_fail_closed(local_endpoint: EndpointLifecycle) -> None:
    """Reject malformed input and surface a real endpoint 503 through Gludd."""
    malformed = httpx.post(
        f"{local_endpoint.base_url}/chat/completions",
        content=b"{",
        headers={"Content-Type": "application/json"},
        timeout=1.0,
    )
    assert malformed.status_code == 400
    assert malformed.json() == {"error": {"message": "malformed JSON"}}

    answer = await _chat_session(local_endpoint).run_once("trigger-server-error")
    assert answer.startswith("[Error:")
    assert "503 Service Unavailable" in answer


def test_endpoint_rejects_unknown_routes_and_invalid_contracts(
    local_endpoint: EndpointLifecycle,
) -> None:
    """Keep route, body, chat, and completion validation fail-closed."""
    unknown_get = httpx.get(f"{local_endpoint.base_url}/unknown", timeout=1.0)
    empty_body = httpx.post(f"{local_endpoint.base_url}/chat/completions", content=b"", timeout=1.0)
    non_object = httpx.post(
        f"{local_endpoint.base_url}/chat/completions",
        json=["not", "an", "object"],
        timeout=1.0,
    )
    invalid_chat = httpx.post(
        f"{local_endpoint.base_url}/chat/completions",
        json={"model": _MODEL_ID, "messages": []},
        timeout=1.0,
    )
    invalid_completion = httpx.post(
        f"{local_endpoint.base_url}/completions",
        json={"model": _MODEL_ID, "prompt": ""},
        timeout=1.0,
    )
    unknown_post = httpx.post(
        f"{local_endpoint.base_url}/unknown",
        json={"model": _MODEL_ID},
        timeout=1.0,
    )

    assert unknown_get.status_code == 404
    assert empty_body.status_code == 413
    assert non_object.status_code == 422
    assert invalid_chat.status_code == 422
    assert invalid_completion.status_code == 422
    assert unknown_post.status_code == 404


@pytest.mark.asyncio
async def test_early_endpoint_exit_is_bounded_and_cleanup_is_idempotent(
    local_endpoint: EndpointLifecycle,
) -> None:
    """A stopped local model must return a bounded error and leave no thread."""
    session = _chat_session(local_endpoint)
    local_endpoint.stop()

    started = time.monotonic()
    answer = await session.run_once("after exit")
    elapsed = time.monotonic() - started

    assert answer == "[Error: Could not connect to the API server. Check your network and API base URL.]"
    assert elapsed < 6.0
    assert not local_endpoint.thread.is_alive()
    local_endpoint.stop()
