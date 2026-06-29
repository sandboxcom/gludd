"""vLLM E2E tests.

Gated by ``VLLM_BASE_URL`` (point at an already-running server) OR
``VLLM_E2E_SPAWN=1`` + ``VLLM_TEST_MODEL`` (spawn a real vllm process via
``LocalInferenceManager``). When neither is set, every test in this module
skips — so CI (which has no vllm) stays green.
"""

from __future__ import annotations

import os

import httpx


def _client(base_url: str) -> httpx.Client:
    return httpx.Client(base_url=base_url.rstrip("/"), timeout=60.0)


def test_vllm_health_endpoint(vllm_base_url: str) -> None:
    """``GET /health`` returns 200 once the server is ready."""
    with _client(vllm_base_url) as c:
        r = c.get("/health")
        assert r.status_code == 200, r.text


def test_vllm_list_models(vllm_base_url: str) -> None:
    """``GET /v1/models`` returns a non-empty model list."""
    with _client(vllm_base_url) as c:
        r = c.get("/v1/models")
        assert r.status_code == 200, r.text
        data = r.json()
        objs = data.get("data", [])
        assert isinstance(objs, list) and len(objs) >= 1, data
        assert all("id" in m for m in objs)


def test_vllm_completion_round_trip(vllm_base_url: str) -> None:
    """``POST /v1/completions`` with a tiny prompt returns text."""
    model = os.environ.get("VLLM_TEST_MODEL") or os.environ.get("VLLM_MODEL", "")
    if not model:
        # Discover from /v1/models when no explicit model env was provided.
        with _client(vllm_base_url) as c:
            model = c.get("/v1/models").json()["data"][0]["id"]
    with _client(vllm_base_url) as c:
        r = c.post(
            "/v1/completions",
            json={
                "model": model,
                "prompt": "Say the word: hello",
                "max_tokens": 16,
                "temperature": 0.0,
            },
        )
        assert r.status_code == 200, r.text
        choices = r.json().get("choices", [])
        assert choices, r.json()
        text = choices[0].get("text", "")
        assert isinstance(text, str) and len(text) > 0


def test_vllm_chat_completion(vllm_base_url: str) -> None:
    """``POST /v1/chat/completions`` returns a valid chat response."""
    model = os.environ.get("VLLM_TEST_MODEL") or os.environ.get("VLLM_MODEL", "")
    if not model:
        with _client(vllm_base_url) as c:
            model = c.get("/v1/models").json()["data"][0]["id"]
    with _client(vllm_base_url) as c:
        r = c.post(
            "/v1/chat/completions",
            json={
                "model": model,
                "messages": [
                    {"role": "system", "content": "Reply with the single word: pong"},
                    {"role": "user", "content": "ping"},
                ],
                "max_tokens": 16,
                "temperature": 0.0,
            },
        )
        assert r.status_code == 200, r.text
        choices = r.json().get("choices", [])
        assert choices, r.json()
        msg = choices[0].get("message", {})
        assert msg.get("role") == "assistant"
        content = msg.get("content", "")
        assert isinstance(content, str) and len(content) > 0


def test_vllm_streaming_completion(vllm_base_url: str) -> None:
    """``stream=true`` returns Server-Sent-Events chunks."""
    model = os.environ.get("VLLM_TEST_MODEL") or os.environ.get("VLLM_MODEL", "")
    if not model:
        with _client(vllm_base_url) as c:
            model = c.get("/v1/models").json()["data"][0]["id"]
    with _client(vllm_base_url) as c, c.stream(
        "POST",
        "/v1/completions",
        json={
            "model": model,
            "prompt": "Count: 1, 2, 3",
            "max_tokens": 16,
            "temperature": 0.0,
            "stream": True,
        },
    ) as r:
        assert r.status_code == 200, r.read().decode("utf-8", "replace")
        saw_chunk = False
        for line in r.iter_lines():
            if line.startswith("data: ") and "[DONE]" not in line:
                saw_chunk = True
                break
        assert saw_chunk, "no SSE chunk was emitted"


def test_vllm_token_usage_reported(vllm_base_url: str) -> None:
    """Non-streaming completion reports positive token usage."""
    model = os.environ.get("VLLM_TEST_MODEL") or os.environ.get("VLLM_MODEL", "")
    if not model:
        with _client(vllm_base_url) as c:
            model = c.get("/v1/models").json()["data"][0]["id"]
    with _client(vllm_base_url) as c:
        r = c.post(
            "/v1/completions",
            json={
                "model": model,
                "prompt": "hello world",
                "max_tokens": 8,
                "temperature": 0.0,
            },
        )
        assert r.status_code == 200, r.text
        usage = r.json().get("usage", {})
        for field in ("prompt_tokens", "completion_tokens", "total_tokens"):
            assert usage.get(field, 0) > 0, (field, usage)
