"""llama.cpp E2E tests.

Same shape as ``test_vllm_e2e.py``, gated by ``LLAMACPP_BASE_URL`` (pre-running
server) or ``LLAMACPP_E2E_SPAWN=1`` + ``LLAMACPP_TEST_MODEL`` (spawn a real
``python -m llama_cpp.server`` via ``LocalInferenceManager``). Skips cleanly
when neither is configured.
"""

from __future__ import annotations

import os

import httpx


def _client(base_url: str) -> httpx.Client:
    return httpx.Client(base_url=base_url.rstrip("/"), timeout=60.0)


def _model_id(base_url: str) -> str:
    return (
        os.environ.get("LLAMACPP_TEST_MODEL")
        or os.environ.get("LLAMACPP_MODEL", "")
        or httpx.get(f"{base_url.rstrip('/')}/v1/models", timeout=10.0)
        .json()["data"][0]["id"]
    )


def test_llamacpp_health_endpoint(llamacpp_base_url: str) -> None:
    """``GET /health`` returns 200 (llama-server exposes this at ``/health``)."""
    with _client(llamacpp_base_url) as c:
        r = c.get("/health")
        assert r.status_code == 200, r.text


def test_llamacpp_list_models(llamacpp_base_url: str) -> None:
    """``GET /v1/models`` returns a non-empty model list."""
    with _client(llamacpp_base_url) as c:
        r = c.get("/v1/models")
        assert r.status_code == 200, r.text
        objs = r.json().get("data", [])
        assert isinstance(objs, list) and len(objs) >= 1
        assert all("id" in m for m in objs)


def test_llamacpp_completion_round_trip(llamacpp_base_url: str) -> None:
    """``POST /v1/completions`` with a tiny prompt returns text."""
    model = _model_id(llamacpp_base_url)
    with _client(llamacpp_base_url) as c:
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
        assert choices
        text = choices[0].get("text", "")
        assert isinstance(text, str) and len(text) > 0


def test_llamacpp_chat_completion(llamacpp_base_url: str) -> None:
    """``POST /v1/chat/completions`` returns a valid chat response."""
    model = _model_id(llamacpp_base_url)
    with _client(llamacpp_base_url) as c:
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
        assert choices
        msg = choices[0].get("message", {})
        assert msg.get("role") == "assistant"
        content = msg.get("content", "")
        assert isinstance(content, str) and len(content) > 0


def test_llamacpp_streaming_completion(llamacpp_base_url: str) -> None:
    """``stream=true`` returns SSE chunks."""
    model = _model_id(llamacpp_base_url)
    with _client(llamacpp_base_url) as c, c.stream(
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


def test_llamacpp_token_usage_reported(llamacpp_base_url: str) -> None:
    """Non-streaming completion reports positive token usage."""
    model = _model_id(llamacpp_base_url)
    with _client(llamacpp_base_url) as c:
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
