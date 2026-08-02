"""Live e2e streaming test for Z.AI / GLM through the bounded model gateway.

The tests consume ``ModelGateway.call_model_stream`` so the live provider path is
subject to the same request, byte, token, chunk, time, idle, and decompression
controls as production callers. Direct provider streaming is intentionally not
used because it would bypass D-30 accounting and cancellation.

Run:
    make test-live-zai-streaming
"""

from __future__ import annotations

import os
from typing import Any, cast

import pytest

from general_ludd.models.gateway import ModelGateway, ModelProfile
from general_ludd.models.provider_registry import ProviderRegistry
from general_ludd.secrets.env import EnvSecretsManager

# ---------------------------------------------------------------------------
# Helpers — identical structure to test_zai_live.py
# ---------------------------------------------------------------------------

def _get_zai_api_key() -> str | None:
    return os.environ.get("ZAI_API_KEY") or os.environ.get("OPENAI_API_KEY")


def _get_zai_base_url() -> str:
    return os.environ.get("ZAI_BASE_URL", "https://api.z.ai/api/coding/paas/v4")


def _get_zai_model() -> str:
    return os.environ.get("ZAI_MODEL", "glm-4.6")


def _build_zai_gateway() -> ModelGateway:
    """Build a ModelGateway wired to the Z.AI endpoint — mirrors test_zai_live.py."""
    api_key = _get_zai_api_key()
    base_url = _get_zai_base_url()
    model_name = _get_zai_model()

    profile = ModelProfile(
        model_profile_id="zai_streaming",
        provider="openai",
        provider_package="langchain_openai",
        provider_class_hint="ChatOpenAI",
        model_name=model_name,
        api_base_alias="ZAI_BASE_URL",
        credential_alias="ZAI_API_KEY",
        context_window=64000,
        max_input_tokens=60000,
        max_output_tokens=4096,
        cost_per_input_token=0.0,
        cost_per_output_token=0.0,
        api_metered=False,
        run_budget_usd=1.0,
        enabled=True,
        resource_profile="ai_heavy",
        roles=["coder", "planner", "reviewer"],
        latency_class="fast",
        quality_class="high",
    )

    registry = ProviderRegistry()
    registry.register_provider("openai", "langchain_openai", "ChatOpenAI")

    secrets = EnvSecretsManager()
    if api_key:
        secrets.set("ZAI_API_KEY", api_key)
    if base_url:
        secrets.set("ZAI_BASE_URL", base_url)

    return ModelGateway(
        profiles=[profile],
        provider_registry=registry,
        secrets_manager=cast(Any, secrets),
    )

# ---------------------------------------------------------------------------
# Skip guard
# ---------------------------------------------------------------------------

_SKIP_REASON = (
    "ZAI_API_KEY not set. "
    "Run: make test-live-zai-streaming (extracts key from .zai.key)"
)


# ---------------------------------------------------------------------------
# Streaming tests
# ---------------------------------------------------------------------------

class TestZAIStreamingGateway:
    """Structural check that the live fixture uses the bounded stream entry point."""

    def test_gateway_has_call_model_stream(self) -> None:
        gw = _build_zai_gateway()
        assert callable(gw.call_model_stream)


@pytest.mark.skipif(not _get_zai_api_key(), reason=_SKIP_REASON)
class TestZAILiveStreaming:
    """Live streaming completions via the bounded gateway/provider path.

    All tests are xfail(raises=Exception) because a 429 / code 1113 (account
    balance exhausted) is the expected outcome on a quota-exhausted key — the
    important thing is that the test REACHES the provider and fails on an API
    error rather than on import / construction errors.
    """

    def test_stream_yields_multiple_chunks(self):
        """Streaming response produces more than one chunk and non-empty text."""
        gateway = _build_zai_gateway()
        messages = [{"role": "user", "content": "Count from 1 to 5, one number per word."}]

        chunks = list(gateway.call_model_stream("zai_streaming", messages))

        assert len(chunks) > 1, (
            f"Expected multiple streaming chunks, got {len(chunks)}"
        )
        full_text = "".join(
            getattr(c, "content", str(c)) for c in chunks
        )
        assert len(full_text.strip()) > 0, (
            "Concatenated streaming output is empty"
        )

    def test_stream_concatenated_text_is_nonempty(self):
        """All chunks concatenated form a non-empty, non-whitespace string."""
        gateway = _build_zai_gateway()
        messages = [{"role": "user", "content": "Say exactly: HELLO streaming"}]

        collected: list[str] = []
        for chunk in gateway.call_model_stream("zai_streaming", messages):
            text = getattr(chunk, "content", str(chunk))
            if text:
                collected.append(text)

        full = "".join(collected)
        assert full.strip(), (
            f"Streaming concatenation is empty; {len(collected)} chunks received"
        )

    def test_stream_chunk_objects_have_content_attr(self):
        """Each chunk from .stream() carries a .content attribute (LangChain AIMessageChunk)."""
        gateway = _build_zai_gateway()
        messages = [{"role": "user", "content": "Say OK"}]

        first_chunk = None
        stream = gateway.call_model_stream("zai_streaming", messages)
        for chunk in stream:
            first_chunk = chunk
            break
        stream.close()

        assert first_chunk is not None, "No chunks received from .stream()"
        assert hasattr(first_chunk, "content"), (
            f"First chunk {type(first_chunk)} has no .content attribute"
        )
