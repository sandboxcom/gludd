"""Live e2e streaming test for Z.AI / GLM via the LangChain ChatOpenAI provider.

ModelGateway.call_model() uses chat_model.invoke() — a single-shot blocking call.
The gateway has NO call_model_stream / astream method (see src/general_ludd/models/
gateway.py).  Streaming is therefore NOT exposed at the gateway level; it lives in
the underlying LangChain ChatOpenAI provider which supports .stream().

This test exercises streaming at the provider level, constructed exactly the same
way ModelGateway._invoke_and_bill() builds the chat model, so we are testing the
real wire path for streaming even though the gateway does not yet wrap it.

Gap noted: ModelGateway lacks a call_model_stream() entry point.
See src/general_ludd/models/gateway.py — no stream/astream method exists.

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


def _build_chat_model() -> object:
    """Construct the LangChain ChatOpenAI instance the same way
    ModelGateway._invoke_and_bill() does, so we can call .stream() on it.

    NOTE: ModelGateway has no call_model_stream() — streaming must be driven
    directly against the provider class.  See gateway.py lines 256-277.
    """
    registry = ProviderRegistry()
    registry.register_provider("openai", "langchain_openai", "ChatOpenAI")

    api_key = _get_zai_api_key()
    base_url = _get_zai_base_url()
    model_name = _get_zai_model()

    provider_cls = registry.get_provider_class("openai")

    init_kwargs: dict = {"model": model_name}
    if api_key:
        init_kwargs["api_key"] = api_key
    if base_url:
        init_kwargs["base_url"] = base_url

    return provider_cls(**init_kwargs)


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

@pytest.mark.skipif(not _get_zai_api_key(), reason=_SKIP_REASON)
class TestZAIStreamingGatewayGap:
    """Structural check: confirm the gateway has no streaming entry point.

    This is a documentation test — it asserts the ABSENCE of streaming on
    ModelGateway so we get a test failure (not a silent gap) if streaming is
    later added without updating this suite.
    """

    def test_gateway_has_no_call_model_stream(self):
        """ModelGateway.call_model_stream does not exist — streaming lives in the
        provider.  This test documents the gap; see gateway.py for the full call
        surface.  Add call_model_stream() to the gateway to close this gap.
        """
        gw = _build_zai_gateway()
        assert not hasattr(gw, "call_model_stream"), (
            "ModelGateway.call_model_stream now exists — update this streaming "
            "test suite to use it directly and remove this gap assertion."
        )
        assert not hasattr(gw, "astream"), (
            "ModelGateway.astream now exists — update this streaming test suite."
        )


@pytest.mark.skipif(not _get_zai_api_key(), reason=_SKIP_REASON)
class TestZAILiveStreaming:
    """Live streaming completions via the LangChain ChatOpenAI provider.

    The provider is constructed identically to how ModelGateway._invoke_and_bill()
    builds it (same init_kwargs), so this is a faithful end-to-end streaming test
    of the Z.AI wire path.

    All tests are xfail(raises=Exception) because a 429 / code 1113 (account
    balance exhausted) is the expected outcome on a quota-exhausted key — the
    important thing is that the test REACHES the provider and fails on an API
    error rather than on import / construction errors.
    """

    def test_stream_yields_multiple_chunks(self):
        """Streaming response produces more than one chunk and non-empty text."""
        chat_model = _build_chat_model()
        messages = [{"role": "user", "content": "Count from 1 to 5, one number per word."}]

        chunks = list(chat_model.stream(messages))

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
        chat_model = _build_chat_model()
        messages = [{"role": "user", "content": "Say exactly: HELLO streaming"}]

        collected: list[str] = []
        for chunk in chat_model.stream(messages):
            text = getattr(chunk, "content", str(chunk))
            if text:
                collected.append(text)

        full = "".join(collected)
        assert full.strip(), (
            f"Streaming concatenation is empty; {len(collected)} chunks received"
        )

    def test_stream_chunk_objects_have_content_attr(self):
        """Each chunk from .stream() carries a .content attribute (LangChain AIMessageChunk)."""
        chat_model = _build_chat_model()
        messages = [{"role": "user", "content": "Say OK"}]

        first_chunk = None
        for chunk in chat_model.stream(messages):
            first_chunk = chunk
            break

        assert first_chunk is not None, "No chunks received from .stream()"
        assert hasattr(first_chunk, "content"), (
            f"First chunk {type(first_chunk)} has no .content attribute"
        )
