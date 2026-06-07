"""Test diskcache-based model response caching."""

from __future__ import annotations

import tempfile
from unittest.mock import MagicMock, patch


def _sample_messages():
    return [{"role": "user", "content": "Write a function that adds two numbers"}]


def _sample_response():
    return {
        "content": "def add(a, b):\n    return a + b",
        "usage_metadata": {"input_tokens": 10, "output_tokens": 15},
        "cost_estimate": 0.0005,
        "model_name": "test-model",
    }


def _make_profile(profile_id="test-p", model_name="test"):
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
    return profile


class TestModelResponseCache:
    def test_cache_miss_returns_none(self):
        from general_ludd.models.response_cache import ModelResponseCache

        with tempfile.TemporaryDirectory() as tmpdir:
            cache = ModelResponseCache(cache_dir=tmpdir)
            result = cache.get("nonexistent-key")
            assert result is None

    def test_cache_set_and_get(self):
        from general_ludd.models.response_cache import ModelResponseCache

        with tempfile.TemporaryDirectory() as tmpdir:
            cache = ModelResponseCache(cache_dir=tmpdir)
            response = _sample_response()
            cache.set("test-key-1", response)
            result = cache.get("test-key-1")
            assert result == response

    def test_cache_invalidate_removes_entry(self):
        from general_ludd.models.response_cache import ModelResponseCache

        with tempfile.TemporaryDirectory() as tmpdir:
            cache = ModelResponseCache(cache_dir=tmpdir)
            cache.set("key-a", _sample_response())
            cache.invalidate("key-a")
            assert cache.get("key-a") is None

    def test_cache_clear_removes_all(self):
        from general_ludd.models.response_cache import ModelResponseCache

        with tempfile.TemporaryDirectory() as tmpdir:
            cache = ModelResponseCache(cache_dir=tmpdir)
            cache.set("k1", _sample_response())
            cache.set("k2", {"content": "other"})
            cache.clear()
            assert cache.get("k1") is None
            assert cache.get("k2") is None

    def test_cache_key_is_deterministic(self):
        from general_ludd.models.response_cache import _make_cache_key

        k1 = _make_cache_key("profile-1", _sample_messages(), model="gpt-4")
        k2 = _make_cache_key("profile-1", _sample_messages(), model="gpt-4")
        assert k1 == k2

    def test_cache_key_differs_by_profile(self):
        from general_ludd.models.response_cache import _make_cache_key

        k1 = _make_cache_key("p1", _sample_messages())
        k2 = _make_cache_key("p2", _sample_messages())
        assert k1 != k2

    def test_cache_key_differs_by_messages(self):
        from general_ludd.models.response_cache import _make_cache_key

        k1 = _make_cache_key("p1", [{"role": "user", "content": "a"}])
        k2 = _make_cache_key("p1", [{"role": "user", "content": "b"}])
        assert k1 != k2

    def test_gateway_call_model_caches_response(self):
        from general_ludd.models.gateway import ModelGateway
        from general_ludd.models.response_cache import ModelResponseCache

        with tempfile.TemporaryDirectory() as tmpdir:
            cache = ModelResponseCache(cache_dir=tmpdir)
            profile = _make_profile("test-profile")

            mock_response = MagicMock()
            mock_response.content = "cached response"
            mock_response.usage_metadata = {"input_tokens": 5, "output_tokens": 10}

            mock_chat_model = MagicMock()
            mock_chat_model.invoke.return_value = mock_response

            mock_registry = MagicMock()
            mock_registry.get_provider_class.return_value = MagicMock(return_value=mock_chat_model)
            mock_registry.is_installed.return_value = True

            with patch.dict("os.environ", {"OPENAI_API_KEY": "test-key"}):
                gw = ModelGateway(
                    profiles={"test-profile": profile},
                    provider_registry=mock_registry,
                    response_cache=cache,
                )
                resp1 = gw.call_model("test-profile", _sample_messages())
                resp2 = gw.call_model("test-profile", _sample_messages())

                assert resp1.content == "cached response"
                assert resp2.content == "cached response"
                assert mock_chat_model.invoke.call_count == 1

    def test_gateway_call_model_invokes_when_cache_miss(self):
        from general_ludd.models.gateway import ModelGateway
        from general_ludd.models.response_cache import ModelResponseCache

        with tempfile.TemporaryDirectory() as tmpdir:
            cache = ModelResponseCache(cache_dir=tmpdir)
            profile = _make_profile("test-p")

            mock_response = MagicMock()
            mock_response.content = "fresh response"
            mock_response.usage_metadata = {"input_tokens": 5, "output_tokens": 10}

            mock_chat_model = MagicMock()
            mock_chat_model.invoke.return_value = mock_response

            mock_registry = MagicMock()
            mock_registry.get_provider_class.return_value = MagicMock(return_value=mock_chat_model)
            mock_registry.is_installed.return_value = True

            with patch.dict("os.environ", {"OPENAI_API_KEY": "test-key"}):
                gw = ModelGateway(
                    profiles={"test-p": profile},
                    provider_registry=mock_registry,
                    response_cache=cache,
                )
                resp = gw.call_model("test-p", [{"role": "user", "content": "unique query"}])
                assert resp.content == "fresh response"

    def test_gateway_call_model_no_cache_works_unchanged(self):
        from general_ludd.models.gateway import ModelGateway

        profile = _make_profile("test-p")

        mock_response = MagicMock()
        mock_response.content = "direct response"
        mock_response.usage_metadata = {"input_tokens": 1, "output_tokens": 1}

        mock_chat_model = MagicMock()
        mock_chat_model.invoke.return_value = mock_response

        mock_registry = MagicMock()
        mock_registry.get_provider_class.return_value = MagicMock(return_value=mock_chat_model)
        mock_registry.is_installed.return_value = True

        with patch.dict("os.environ", {"OPENAI_API_KEY": "test-key"}):
            gw = ModelGateway(
                profiles={"test-p": profile},
                provider_registry=mock_registry,
                response_cache=None,
            )
            resp = gw.call_model("test-p", _sample_messages())
            assert resp.content == "direct response"
