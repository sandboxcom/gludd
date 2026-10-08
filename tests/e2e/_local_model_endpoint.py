"""Reusable, loopback-only OpenAI endpoint for local-model E2E lifecycles."""

from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import TypedDict, cast

import httpx

_MAX_REQUEST_BYTES = 64 * 1024


class RequestRecord(TypedDict):
    """Bounded request evidence captured by the local endpoint."""

    path: str
    authorization: str
    payload: dict[str, object]


class _OpenAIEndpoint(ThreadingHTTPServer):
    """Threaded, loopback-only endpoint with configurable model output."""

    daemon_threads = True
    allow_reuse_address = False

    def __init__(self, *, model_id: str, chat_content: str | None) -> None:
        super().__init__(("127.0.0.1", 0), _OpenAIHandler)
        self.model_id = model_id
        self.chat_content = chat_content
        self.requests: list[RequestRecord] = []


class _OpenAIHandler(BaseHTTPRequestHandler):
    """Minimal OpenAI response surface used by local-model E2E tests."""

    server: _OpenAIEndpoint

    def log_message(self, format: str, *args: object) -> None:
        """Keep deterministic output; assertions retain request evidence."""

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
                {"object": "list", "data": [{"id": self.server.model_id, "object": "model"}]},
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
        if payload.get("model") != self.server.model_id or not isinstance(messages, list) or not messages:
            self._send_json(422, {"error": {"message": "model and messages are required"}})
            return
        last = messages[-1]
        content = last.get("content") if isinstance(last, dict) else None
        if content == "trigger-server-error":
            self._send_json(503, {"error": {"message": "local model overloaded"}})
            return
        answer = self.server.chat_content if self.server.chat_content is not None else f"local:{content}"
        self._send_json(
            200,
            {
                "id": "chatcmpl-local-e2e",
                "object": "chat.completion",
                "model": self.server.model_id,
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "stop",
                        "message": {"role": "assistant", "content": answer},
                    }
                ],
                "usage": {"prompt_tokens": 4, "completion_tokens": 2, "total_tokens": 6},
            },
        )

    def _text_completion(self, payload: dict[str, object]) -> None:
        prompt = payload.get("prompt")
        if payload.get("model") != self.server.model_id or not isinstance(prompt, str) or not prompt:
            self._send_json(422, {"error": {"message": "model and prompt are required"}})
            return
        self._send_json(
            200,
            {
                "id": "cmpl-local-e2e",
                "object": "text_completion",
                "model": self.server.model_id,
                "choices": [{"index": 0, "finish_reason": "stop", "text": f"local:{prompt}"}],
            },
        )


class EndpointLifecycle:
    """Own one namespaced random-port server thread and idempotent cleanup."""

    def __init__(
        self,
        *,
        model_id: str,
        chat_content: str | None = None,
        namespace: str = "endpoint",
    ) -> None:
        self.server = _OpenAIEndpoint(model_id=model_id, chat_content=chat_content)
        safe_namespace = "".join(
            character if character.isalnum() or character in "-_" else "-" for character in namespace
        )
        self.thread = threading.Thread(
            target=self.server.serve_forever,
            name=f"gludd-local-model-e2e-{safe_namespace[:64] or 'endpoint'}",
            daemon=False,
        )
        host, port = cast("tuple[str, int]", self.server.server_address)
        self.base_url = f"http://{host}:{port}/v1"
        self._started = False
        self._closed = False

    def start(self) -> None:
        """Start the endpoint and fail when readiness exceeds three seconds."""
        if self._closed:
            raise RuntimeError("closed local endpoint cannot be restarted")
        if self._started:
            return
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
        """Stop the owned thread/socket once; repeated calls are harmless."""
        if self._closed:
            return
        if self._started:
            self.server.shutdown()
            self.thread.join(timeout=3.0)
        self.server.server_close()
        self._started = False
        self._closed = True
        if self.thread.is_alive():
            raise RuntimeError("local endpoint thread did not stop within 3 seconds")
