"""Regression tests for GW-2: truncated responses (finish_reason="length") must
NOT be written to the response cache; finish_reason="stop" MUST be cached.

Harness mirrors test_response_cache.py::test_gateway_call_model_caches_response:
- MagicMock provider registry wired as get_provider_class -> MagicMock(return_value=chat_model)
- Cache injected via ModelGateway(response_cache=mock_cache); mock_cache.get returns None
  (cache miss) so the provider is always called and cache.set is the observable under test.
- response_metadata must be a real dict (not a MagicMock auto-attr) so the gateway's
  getattr(raw_response, "response_metadata", None).get("finish_reason") resolves to a
  string comparison rather than a MagicMock inequality that always passes.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from general_ludd.models.gateway import ModelGateway

# ---------------------------------------------------------------------------
# Helpers (mirrors _make_profile / registry setup from test_response_cache.py)
# ---------------------------------------------------------------------------

def _make_profile(profile_id: str = "test-p", model_name: str = "test") -> MagicMock:
    profile = MagicMock()
    profile.model_profile_id = profile_id
    profile.model_name = model_name
    profile.provider = "openai"
    profile.package = "langchain-openai"
    profile.class_name = "ChatOpenAI"
    profile.credential_alias = "OPENAI_API_KEY"
    profile.input_cost_per_1k = 0.001
    profile.output_cost_per_1k = 0.002
    profile.class_kwargs = {}
    profile.supports_tool_calling = True
    profile.api_metered = False
    profile.run_budget_usd = 200.0
    profile.api_base_alias = None
    profile.cost_per_input_token = 0.000001
    profile.cost_per_output_token = 0.000002
    # estimate_cost() reads these for the budget gate; must be real ints.
    profile.max_output_tokens = 8000
    profile.max_input_tokens = 8000
    return profile


def _make_registry(mock_response: MagicMock) -> tuple[MagicMock, MagicMock]:
    """Return (mock_registry, mock_chat_model) scripted to return mock_response."""
    mock_chat_model = MagicMock()
    mock_chat_model.invoke.return_value = mock_response
    mock_registry = MagicMock()
    mock_registry.get_provider_class.return_value = MagicMock(return_value=mock_chat_model)
    mock_registry.is_installed.return_value = True
    return mock_registry, mock_chat_model


def _make_provider_response(content: str, finish_reason: str) -> MagicMock:
    """Build a fake LangChain AIMessage with explicit response_metadata.

    response_metadata MUST be a real dict — the gateway reads:
        (getattr(raw_response, "response_metadata", None) or {}).get("finish_reason")
    A MagicMock auto-attr for response_metadata would make .get() return a MagicMock
    which != "length" always and would mask the guard under test.

    tool_calls is intentionally left unset (MagicMock auto-attr, not a list/tuple),
    which _extract_tool_calls() treats as "no tool calls" and returns None — so
    `not response.tool_calls` is True and the cache condition depends only on
    finish_reason.
    """
    mock_response = MagicMock()
    mock_response.content = content
    mock_response.usage_metadata = {"input_tokens": 5, "output_tokens": 10}
    mock_response.response_metadata = {"finish_reason": finish_reason}
    return mock_response


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestGW2TruncatedResponseNotCached:
    """GW-2 regression: finish_reason controls whether a response is cached."""

    def test_truncated_response_does_not_call_cache_set(self):
        """A response with finish_reason='length' must NOT be written to the cache.

        Caching a truncated turn would replay the cut-off text as the complete
        answer on every identical future request — silently serving corrupt output.
        """
        mock_response = _make_provider_response(
            content="partial answer cut off mid-sent",
            finish_reason="length",
        )
        mock_registry, _ = _make_registry(mock_response)

        mock_cache = MagicMock()
        mock_cache.get.return_value = None  # force cache miss -> provider is called

        profile = _make_profile("p-trunc")
        with patch.dict("os.environ", {"OPENAI_API_KEY": "test-key"}):
            gw = ModelGateway(
                profiles={"p-trunc": profile},
                provider_registry=mock_registry,
                response_cache=mock_cache,
            )
            resp = gw.call_model("p-trunc", [{"role": "user", "content": "hello"}])

        # Sanity: the truncated content is still returned to the caller.
        assert resp.content == "partial answer cut off mid-sent"
        # GW-2 invariant: cache.set must NOT have been called for a truncated turn.
        mock_cache.set.assert_not_called()

    def test_complete_response_calls_cache_set_once(self):
        """A response with finish_reason='stop' MUST be written to the cache exactly once.

        This test guards against over-correction: the GW-2 guard must block only
        'length', not every response. A normally-completed turn (finish_reason='stop')
        with no tool calls is always cacheable and must reach cache.set().
        """
        mock_response = _make_provider_response(
            content="complete answer",
            finish_reason="stop",
        )
        mock_registry, _ = _make_registry(mock_response)

        mock_cache = MagicMock()
        mock_cache.get.return_value = None  # force cache miss -> provider is called

        profile = _make_profile("p-stop")
        with patch.dict("os.environ", {"OPENAI_API_KEY": "test-key"}):
            gw = ModelGateway(
                profiles={"p-stop": profile},
                provider_registry=mock_registry,
                response_cache=mock_cache,
            )
            resp = gw.call_model("p-stop", [{"role": "user", "content": "hello"}])

        assert resp.content == "complete answer"
        # GW-2 invariant: a non-truncated, non-tool-call turn MUST be cached.
        mock_cache.set.assert_called_once()
        # Verify the cached payload contains the response content (not e.g. raw_response).
        call_args = mock_cache.set.call_args
        cached_payload = call_args[0][1]  # positional arg 1 is the value dict
        assert cached_payload["content"] == "complete answer"

    def test_missing_response_metadata_does_not_block_caching(self):
        """A response with no response_metadata at all (finish_reason=None) is cached.

        Many provider stubs omit response_metadata entirely. The guard must treat
        an absent finish_reason as non-truncated so legacy providers are not silently
        un-cached by the GW-2 change.
        """
        mock_response = MagicMock()
        mock_response.content = "answer without metadata"
        mock_response.usage_metadata = {"input_tokens": 3, "output_tokens": 7}
        # Simulate a provider that returns no response_metadata at all.
        mock_response.response_metadata = None

        mock_registry, _ = _make_registry(mock_response)

        mock_cache = MagicMock()
        mock_cache.get.return_value = None

        profile = _make_profile("p-no-meta")
        with patch.dict("os.environ", {"OPENAI_API_KEY": "test-key"}):
            gw = ModelGateway(
                profiles={"p-no-meta": profile},
                provider_registry=mock_registry,
                response_cache=mock_cache,
            )
            resp = gw.call_model("p-no-meta", [{"role": "user", "content": "hello"}])

        assert resp.content == "answer without metadata"
        # None finish_reason != "length", so caching must proceed.
        mock_cache.set.assert_called_once()
