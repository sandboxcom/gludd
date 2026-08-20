"""Hermetic E2E coverage for an OpenAI-compatible local-model endpoint.

The server uses a real loopback TCP socket and Gludd's production
``ChatSession`` client, but returns deterministic responses in-process.  This
keeps startup, readiness, request, failure, and shutdown behavior realistic
without downloading a model or contacting a paid API.
"""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import TypedDict, cast

import httpx
import pytest

from general_ludd.chat.session import ChatSession

_MODEL_ID = "gludd-local-e2e"
_API_KEY = "local-e2e-token"
_MAX_REQUEST_BYTES = 64 * 1024

pytestmark = [pytest.mark.e2e, pytest.mark.timeout(15)]


class _RequestRecord(TypedDict):
    """Bounded request evidence captured by the local endpoint."""

    path: str
    authorization: str
    payload: dict[str, object]


class _OpenAIEndpoint(ThreadingHTTPServer):
    """Threaded, loopback-only endpoint with bounded request evidence."""

    daemon_threads = True
    allow_reuse_address = False

    def __init__(self) -> None:
        super().__init__(("127.0.0.1", 0), _OpenAIHandler)
        self.requests: list[_RequestRecord] = []


class _OpenAIHandler(BaseHTTPRequestHandler):
    """Minimal OpenAI response surface used by the lifecycle tests."""

    server: _OpenAIEndpoint

    def log_message(self, format: str, *args: object) -> None:
        """Keep deterministic test output; assertions retain request evidence."""

    def _send_json(self, status: int, payload: dict[str, object]) -> None:
        encoded = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def do_GET(self) -> None:
        """Expose the readiness/model-discovery contract."""
        if self.path == "/v1/models":
            self._send_json(
                200,
                {"object": "list", "data": [{"id": _MODEL_ID, "object": "model"}]},
            )
            return
        self._send_json(404, {"error": {"message": "route not found"}})

    def do_POST(self) -> None:
        """Serve bounded chat/completion requests and deterministic failures."""
        raw_length = self.headers.get("Content-Length", "0")
        try:
            length = int(raw_length)
        except ValueError:
            self._send_json(400, {"error": {"message": "invalid content length"}})
            return
        if length <= 0 or length > _MAX_REQUEST_BYTES:
            self._send_json(413, {"error": {"message": "request body outside bounds"}})
            return
        try:
            decoded = json.loads(self.rfile.read(length))
        except (json.JSONDecodeError, UnicodeDecodeError):
            self._send_json(400, {"error": {"message": "malformed JSON"}})
            return
        if not isinstance(decoded, dict):
            self._send_json(422, {"error": {"message": "JSON object required"}})
            return
        payload = cast("dict[str, object]", decoded)
        self.server.requests.append(
            {
                "path": self.path,
                "authorization": self.headers.get("Authorization", ""),
                "payload": payload,
            }
        )

        if self.path == "/v1/chat/completions":
            self._chat_completion(payload)
            return
        if self.path == "/v1/completions":
            self._text_completion(payload)
            return
        self._send_json(404, {"error": {"message": "route not found"}})

    def _chat_completion(self, payload: dict[str, object]) -> None:
        messages = payload.get("messages")
        if payload.get("model") != _MODEL_ID or not isinstance(messages, list) or not messages:
            self._send_json(422, {"error": {"message": "model and messages are required"}})
            return
        last = messages[-1]
        content = last.get("content") if isinstance(last, dict) else None
        if content == "trigger-server-error":
            self._send_json(503, {"error": {"message": "local model overloaded"}})
            return
        self._send_json(
            200,
            {
                "id": "chatcmpl-local-e2e",
                "object": "chat.completion",
                "model": _MODEL_ID,
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "stop",
                        "message": {"role": "assistant", "content": f"local:{content}"},
                    }
                ],
                "usage": {"prompt_tokens": 4, "completion_tokens": 2, "total_tokens": 6},
            },
        )

    def _text_completion(self, payload: dict[str, object]) -> None:
        prompt = payload.get("prompt")
        if payload.get("model") != _MODEL_ID or not isinstance(prompt, str) or not prompt:
            self._send_json(422, {"error": {"message": "model and prompt are required"}})
            return
        self._send_json(
            200,
            {
                "id": "cmpl-local-e2e",
                "object": "text_completion",
                "model": _MODEL_ID,
                "choices": [{"index": 0, "finish_reason": "stop", "text": f"local:{prompt}"}],
            },
        )


class _EndpointLifecycle:
    """Own the namespaced server thread and idempotent cleanup."""

    def __init__(self) -> None:
        self.server = _OpenAIEndpoint()
        self.thread = threading.Thread(
            target=self.server.serve_forever,
            name="gludd-local-model-e2e-endpoint",
            daemon=False,
        )
        host, port = cast("tuple[str, int]", self.server.server_address)
        self.base_url = f"http://{host}:{port}/v1"
        self._started = False

    def start(self) -> None:
        self.thread.start()
        self._started = True
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            try:
                response = httpx.get(f"{self.base_url}/models", timeout=0.25)
                if response.status_code == 200:
                    return
            except httpx.TransportError:
                pass
            time.sleep(0.01)
        self.stop()
        raise RuntimeError("local endpoint did not become ready within 3 seconds")

    def stop(self) -> None:
        if not self._started:
            return
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3.0)
        self._started = False
        if self.thread.is_alive():
            raise RuntimeError("local endpoint thread did not stop within 3 seconds")


@pytest.fixture
def local_endpoint() -> Iterator[_EndpointLifecycle]:
    """Start a fresh endpoint and guarantee idempotent shutdown."""
    endpoint = _EndpointLifecycle()
    endpoint.start()
    try:
        yield endpoint
    finally:
        endpoint.stop()


def _chat_session(endpoint: _EndpointLifecycle) -> ChatSession:
    return ChatSession(
        model=f"openai/{_MODEL_ID}",
        api_base_url=endpoint.base_url,
        api_key=_API_KEY,
        system_prompt="Answer locally and concisely.",
        save_interval=100,
    )


@pytest.mark.asyncio
async def test_ready_endpoint_serves_chat_and_text_completion(local_endpoint: _EndpointLifecycle) -> None:
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
async def test_malformed_request_and_server_error_fail_closed(local_endpoint: _EndpointLifecycle) -> None:
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
    local_endpoint: _EndpointLifecycle,
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
    local_endpoint: _EndpointLifecycle,
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
