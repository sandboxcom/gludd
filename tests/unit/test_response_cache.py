"""Test diskcache-based model response caching."""

from __future__ import annotations

import os
import stat
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
    # D-21: estimate_cost() reads these numeric token-budget fields when it
    # re-estimates cost server-side (gateway.check_budget). A bare MagicMock
    # would return MagicMocks here and break the numeric min()/comparison, so
    # mirror the real ModelProfile defaults (max_output_tokens=8000).
    profile.max_output_tokens = 8000
    profile.max_input_tokens = 8000
    return profile


class TestModelResponseCache:
    def test_cache_miss_returns_none(self):
        from general_ludd.models.response_cache import ModelResponseCache

        with tempfile.TemporaryDirectory() as tmpdir:
            cache = ModelResponseCache(cache_dir=tmpdir)
            result = cache.get("nonexistent-key")
            assert result is None

    def test_cache_dir_is_owner_only(self):
        # Defense in depth for diskcache CVE-2025-69872: strict MessagePack
        # serialization removes executable payloads, while owner-only permissions
        # also prevent another local user from planting cache rows. See
        # docs/SECURITY.md "Known dependency advisories".
        from general_ludd.models.response_cache import ModelResponseCache

        with tempfile.TemporaryDirectory() as tmpdir:
            cache_path = os.path.join(tmpdir, "response-cache")
            ModelResponseCache(cache_dir=cache_path)
            mode = stat.S_IMODE(os.stat(cache_path).st_mode)
            # No group/other permission bits set.
            assert mode & (stat.S_IRWXG | stat.S_IRWXO) == 0
            # Owner retains full access.
            assert mode & stat.S_IRWXU == stat.S_IRWXU

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

        with (
            tempfile.TemporaryDirectory() as tmpdir,
            ModelResponseCache(cache_dir=tmpdir) as cache,
        ):
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


class TestModelResponseCacheDeep:
    def test_context_manager_closes_backend_once(self):
        from general_ludd.models.response_cache import ModelResponseCache

        backend = MagicMock()
        with (
            tempfile.TemporaryDirectory() as tmpdir,
            patch("general_ludd.models.response_cache.open_safe_diskcache", return_value=backend),
            ModelResponseCache(cache_dir=tmpdir) as cache,
        ):
            assert cache._cache is backend

        cache.close()

        backend.close.assert_called_once_with()

    def test_close_releases_resources(self):
        from general_ludd.models.response_cache import ModelResponseCache

        with tempfile.TemporaryDirectory() as tmpdir:
            cache = ModelResponseCache(cache_dir=tmpdir)
            cache.set("k", _sample_response())
            cache.close()

    def test_get_non_dict_value_returns_none(self):
        from general_ludd.models.response_cache import ModelResponseCache

        with tempfile.TemporaryDirectory() as tmpdir:
            cache = ModelResponseCache(cache_dir=tmpdir)
            cache._cache.set("string-val", "not a dict")
            cache._cache.set("int-val", 42)
            cache._cache.set("none-val", None)
            assert cache.get("string-val") is None
            assert cache.get("int-val") is None
            assert cache.get("none-val") is None

    def test_set_with_custom_expire(self):
        from general_ludd.models.response_cache import ModelResponseCache

        with tempfile.TemporaryDirectory() as tmpdir:
            cache = ModelResponseCache(cache_dir=tmpdir)
            response = _sample_response()
            cache.set("short-ttl", response, expire=60)
            result = cache.get("short-ttl")
            assert result == response

    def test_set_with_expire_none(self):
        from general_ludd.models.response_cache import ModelResponseCache

        with tempfile.TemporaryDirectory() as tmpdir:
            cache = ModelResponseCache(cache_dir=tmpdir)
            response = _sample_response()
            cache.set("no-expire", response, expire=None)
            result = cache.get("no-expire")
            assert result == response

    def test_set_uses_default_ttl(self):
        from general_ludd.models.response_cache import (
            DEFAULT_CACHE_TTL_SECONDS,
            ModelResponseCache,
        )

        assert DEFAULT_CACHE_TTL_SECONDS == 3600
        with tempfile.TemporaryDirectory() as tmpdir:
            cache = ModelResponseCache(cache_dir=tmpdir)
            response = _sample_response()
            cache.set("default-ttl", response)
            result = cache.get("default-ttl")
            assert result == response

    def test_cache_persists_across_close_reopen(self):
        from general_ludd.models.response_cache import ModelResponseCache

        with tempfile.TemporaryDirectory() as tmpdir:
            cache1 = ModelResponseCache(cache_dir=tmpdir)
            response = _sample_response()
            cache1.set("persistent-key", response)
            cache1.close()

            cache2 = ModelResponseCache(cache_dir=tmpdir)
            result = cache2.get("persistent-key")
            assert result == response
            cache2.close()

    def test_multiple_entries_cached_independently(self):
        from general_ludd.models.response_cache import ModelResponseCache

        with tempfile.TemporaryDirectory() as tmpdir:
            cache = ModelResponseCache(cache_dir=tmpdir)
            r1 = {"content": "alpha", "model_name": "m1"}
            r2 = {"content": "beta", "model_name": "m2"}
            r3 = {"content": "gamma", "model_name": "m3"}
            cache.set("a", r1)
            cache.set("b", r2)
            cache.set("c", r3)
            assert cache.get("a") == r1
            assert cache.get("b") == r2
            assert cache.get("c") == r3

    def test_invalidate_nonexistent_key_does_not_error(self):
        from general_ludd.models.response_cache import ModelResponseCache

        with tempfile.TemporaryDirectory() as tmpdir:
            cache = ModelResponseCache(cache_dir=tmpdir)
            cache.invalidate("never-set-key")

    def test_clear_empty_cache_does_not_error(self):
        from general_ludd.models.response_cache import ModelResponseCache

        with tempfile.TemporaryDirectory() as tmpdir:
            cache = ModelResponseCache(cache_dir=tmpdir)
            cache.clear()
            assert cache.get("any-key") is None

    def test_cache_key_includes_model_name(self):
        from general_ludd.models.response_cache import _make_cache_key

        k1 = _make_cache_key("p1", _sample_messages(), model_name="gpt-4")
        k2 = _make_cache_key("p1", _sample_messages(), model_name="gpt-3.5")
        assert k1 != k2

    def test_cache_key_differs_by_kwargs(self):
        from general_ludd.models.response_cache import _make_cache_key

        k1 = _make_cache_key("p1", _sample_messages(), temperature=0.0)
        k2 = _make_cache_key("p1", _sample_messages(), temperature=1.0)
        assert k1 != k2

    def test_cache_key_with_model_name_none_vs_absent(self):
        from general_ludd.models.response_cache import _make_cache_key

        k_none = _make_cache_key("p1", _sample_messages(), model_name=None)
        k_absent = _make_cache_key("p1", _sample_messages())
        assert k_none == k_absent

    def test_cache_key_ordering_independent(self):
        from general_ludd.models.response_cache import _make_cache_key

        k1 = _make_cache_key("p1", _sample_messages(), temperature=0.5, top_p=0.9)
        k2 = _make_cache_key("p1", _sample_messages(), top_p=0.9, temperature=0.5)
        assert k1 == k2

    def test_default_cache_dir_expands_tilde(self):
        from general_ludd.models.response_cache import (
            DEFAULT_CACHE_DIR,
            ModelResponseCache,
        )

        assert DEFAULT_CACHE_DIR.startswith("~")
        with tempfile.TemporaryDirectory() as tmpdir:
            cache = ModelResponseCache(cache_dir=tmpdir)
            response = _sample_response()
            cache.set("k", response)
            assert cache.get("k") == response

    def test_cve_mitigation_dir_owner_only_on_reopen(self):
        from general_ludd.models.response_cache import ModelResponseCache

        with tempfile.TemporaryDirectory() as tmpdir:
            cache_path = os.path.join(tmpdir, "existing-cache")
            os.makedirs(cache_path, mode=0o755)
            ModelResponseCache(cache_dir=cache_path)
            mode = stat.S_IMODE(os.stat(cache_path).st_mode)
            assert mode & (stat.S_IRWXG | stat.S_IRWXO) == 0
