"""Additional coverage tests for src/general_ludd/models/gateway.py.

Targets branches and paths not covered by the existing test_timeout_detector.py
integration tests:
- _coerce_token_count edge cases
- ModelProfile validators (_strip_and_require, _positive_int, _non_negative_float)
- ModelGateway.__init__ (list vs dict profiles, all-None optionals)
- get_profile / is_available / check_budget / list_profiles
- call_model: profile not found, budget rejected, cache hit first-check,
  cache hit single-flight double-check, happy path no cache, happy path with cache
- _invoke_and_bill: registry not installed, no registry, secrets resolve api_key,
  api_base_alias SSRF guard, empty response, usage fallback keys, billing, metrics
- _empty_response_error: returns httpx.HTTPStatusError 503
- call_model_by_role / call_model_by_pattern: no router, router returns None, success
- call_model_with_fallback: primary succeeds, primary fails+fallback succeeds, all fail
- add_profile / remove_profile: with/without event_bus/hooks/broadcaster
- _notify_profile_change: broadcaster raises → warning only
- _cache_key_lock: creates new, returns existing
- call_model_with_retry: profile not found, non-retryable kind re-raises
- _walk_fallbacks: skips unhealthy fallback, first success wins, all fail
- record_timeout_on_failure: no health_tracker noop, with tracker calls record_event
"""

from __future__ import annotations

import math
from unittest.mock import MagicMock, patch

import httpx
import pytest


# ---------------------------------------------------------------------------
# _coerce_token_count
# ---------------------------------------------------------------------------

class TestCoerceTokenCount:
    def test_bool_true_returns_zero(self) -> None:
        from general_ludd.models.gateway import _coerce_token_count
        assert _coerce_token_count(True) == 0

    def test_bool_false_returns_zero(self) -> None:
        from general_ludd.models.gateway import _coerce_token_count
        assert _coerce_token_count(False) == 0

    def test_negative_int_clamped_to_zero(self) -> None:
        from general_ludd.models.gateway import _coerce_token_count
        assert _coerce_token_count(-5) == 0

    def test_positive_int_returned(self) -> None:
        from general_ludd.models.gateway import _coerce_token_count
        assert _coerce_token_count(42) == 42

    def test_float_truncated(self) -> None:
        from general_ludd.models.gateway import _coerce_token_count
        assert _coerce_token_count(3.9) == 3

    def test_negative_float_clamped_to_zero(self) -> None:
        from general_ludd.models.gateway import _coerce_token_count
        assert _coerce_token_count(-1.5) == 0

    def test_string_returns_zero(self) -> None:
        from general_ludd.models.gateway import _coerce_token_count
        assert _coerce_token_count("100") == 0

    def test_none_returns_zero(self) -> None:
        from general_ludd.models.gateway import _coerce_token_count
        assert _coerce_token_count(None) == 0

    def test_zero_int_returns_zero(self) -> None:
        from general_ludd.models.gateway import _coerce_token_count
        assert _coerce_token_count(0) == 0


# ---------------------------------------------------------------------------
# ModelProfile validators
# ---------------------------------------------------------------------------

class TestModelProfileValidators:
    def test_empty_profile_id_raises(self) -> None:
        from general_ludd.models.gateway import ModelProfile
        with pytest.raises(Exception):
            ModelProfile(model_profile_id="")

    def test_whitespace_only_profile_id_raises(self) -> None:
        from general_ludd.models.gateway import ModelProfile
        with pytest.raises(Exception):
            ModelProfile(model_profile_id="   ")

    def test_profile_id_strips_whitespace(self) -> None:
        from general_ludd.models.gateway import ModelProfile
        p = ModelProfile(model_profile_id="  gpt-4  ")
        assert p.model_profile_id == "gpt-4"

    def test_context_window_zero_raises(self) -> None:
        from general_ludd.models.gateway import ModelProfile
        with pytest.raises(Exception):
            ModelProfile(model_profile_id="m", context_window=0)

    def test_context_window_negative_raises(self) -> None:
        from general_ludd.models.gateway import ModelProfile
        with pytest.raises(Exception):
            ModelProfile(model_profile_id="m", context_window=-1)

    def test_max_input_tokens_zero_raises(self) -> None:
        from general_ludd.models.gateway import ModelProfile
        with pytest.raises(Exception):
            ModelProfile(model_profile_id="m", max_input_tokens=0)

    def test_max_output_tokens_zero_raises(self) -> None:
        from general_ludd.models.gateway import ModelProfile
        with pytest.raises(Exception):
            ModelProfile(model_profile_id="m", max_output_tokens=0)

    def test_negative_cost_per_input_token_raises(self) -> None:
        from general_ludd.models.gateway import ModelProfile
        with pytest.raises(Exception):
            ModelProfile(model_profile_id="m", cost_per_input_token=-0.001)

    def test_nan_cost_raises(self) -> None:
        from general_ludd.models.gateway import ModelProfile
        with pytest.raises(Exception):
            ModelProfile(model_profile_id="m", cost_per_input_token=float("nan"))

    def test_inf_run_budget_raises(self) -> None:
        from general_ludd.models.gateway import ModelProfile
        with pytest.raises(Exception):
            ModelProfile(model_profile_id="m", run_budget_usd=math.inf)

    def test_valid_profile_accepts_zero_cost(self) -> None:
        from general_ludd.models.gateway import ModelProfile
        p = ModelProfile(model_profile_id="m", cost_per_input_token=0.0)
        assert p.cost_per_input_token == 0.0


# ---------------------------------------------------------------------------
# ModelGateway.__init__ and basic methods
# ---------------------------------------------------------------------------

class TestModelGatewayInit:
    def test_init_with_list_profiles(self) -> None:
        from general_ludd.models.gateway import ModelGateway, ModelProfile
        p = ModelProfile(model_profile_id="gpt4", enabled=True)
        gw = ModelGateway(profiles=[p])
        assert gw.get_profile("gpt4") is p

    def test_init_with_dict_profiles(self) -> None:
        from general_ludd.models.gateway import ModelGateway, ModelProfile
        p = ModelProfile(model_profile_id="gpt4", enabled=True)
        gw = ModelGateway(profiles={"gpt4": p})
        assert gw.get_profile("gpt4") is p

    def test_init_all_none_optionals(self) -> None:
        from general_ludd.models.gateway import ModelGateway
        gw = ModelGateway()
        assert gw._registry is None
        assert gw._secrets is None
        assert gw._budget_guard is None
        assert gw._router is None
        assert gw._event_bus is None
        assert gw._hooks is None
        assert gw._broadcaster is None
        assert gw._metrics_collector is None
        assert gw._health_tracker is None

    def test_get_profile_found(self) -> None:
        from general_ludd.models.gateway import ModelGateway, ModelProfile
        p = ModelProfile(model_profile_id="m1")
        gw = ModelGateway(profiles=[p])
        assert gw.get_profile("m1") is p

    def test_get_profile_not_found(self) -> None:
        from general_ludd.models.gateway import ModelGateway
        gw = ModelGateway()
        assert gw.get_profile("nonexistent") is None

    def test_is_available_not_found(self) -> None:
        from general_ludd.models.gateway import ModelGateway
        gw = ModelGateway()
        assert gw.is_available("nonexistent") is False

    def test_is_available_disabled(self) -> None:
        from general_ludd.models.gateway import ModelGateway, ModelProfile
        p = ModelProfile(model_profile_id="m1", enabled=False)
        gw = ModelGateway(profiles=[p])
        assert gw.is_available("m1") is False

    def test_is_available_enabled(self) -> None:
        from general_ludd.models.gateway import ModelGateway, ModelProfile
        p = ModelProfile(model_profile_id="m1", enabled=True)
        gw = ModelGateway(profiles=[p])
        assert gw.is_available("m1") is True

    def test_list_profiles_returns_all(self) -> None:
        from general_ludd.models.gateway import ModelGateway, ModelProfile
        p1 = ModelProfile(model_profile_id="a")
        p2 = ModelProfile(model_profile_id="b")
        gw = ModelGateway(profiles=[p1, p2])
        ids = {p.model_profile_id for p in gw.list_profiles()}
        assert ids == {"a", "b"}


# ---------------------------------------------------------------------------
# check_budget
# ---------------------------------------------------------------------------

class TestCheckBudget:
    def test_profile_not_found_returns_false(self) -> None:
        from general_ludd.models.gateway import ModelGateway
        gw = ModelGateway()
        assert gw.check_budget("missing", 1.0, 100.0) is False

    def test_cost_exceeds_remaining_returns_false(self) -> None:
        from general_ludd.models.gateway import ModelGateway, ModelProfile
        p = ModelProfile(model_profile_id="m", run_budget_usd=200.0)
        gw = ModelGateway(profiles=[p])
        assert gw.check_budget("m", 50.0, 10.0) is False

    def test_api_metered_cost_exceeds_run_budget_returns_false(self) -> None:
        from general_ludd.models.gateway import ModelGateway, ModelProfile
        p = ModelProfile(model_profile_id="m", api_metered=True, run_budget_usd=5.0)
        gw = ModelGateway(profiles=[p])
        assert gw.check_budget("m", 10.0, 1000.0) is False

    def test_all_ok_returns_true(self) -> None:
        from general_ludd.models.gateway import ModelGateway, ModelProfile
        p = ModelProfile(model_profile_id="m", api_metered=True, run_budget_usd=200.0)
        gw = ModelGateway(profiles=[p])
        assert gw.check_budget("m", 1.0, 100.0) is True

    def test_not_api_metered_skips_run_budget_check(self) -> None:
        from general_ludd.models.gateway import ModelGateway, ModelProfile
        p = ModelProfile(model_profile_id="m", api_metered=False, run_budget_usd=1.0)
        gw = ModelGateway(profiles=[p])
        # cost=5 > run_budget=1, but api_metered=False so still passes
        assert gw.check_budget("m", 5.0, 1000.0) is True


# ---------------------------------------------------------------------------
# _cache_key_lock
# ---------------------------------------------------------------------------

class TestCacheKeyLock:
    def test_creates_new_lock(self) -> None:
        from general_ludd.models.gateway import ModelGateway
        import threading
        gw = ModelGateway()
        lock = gw._cache_key_lock("key1")
        assert isinstance(lock, type(threading.Lock()))

    def test_returns_same_lock_for_same_key(self) -> None:
        from general_ludd.models.gateway import ModelGateway
        gw = ModelGateway()
        lock1 = gw._cache_key_lock("key1")
        lock2 = gw._cache_key_lock("key1")
        assert lock1 is lock2

    def test_different_keys_get_different_locks(self) -> None:
        from general_ludd.models.gateway import ModelGateway
        gw = ModelGateway()
        lock1 = gw._cache_key_lock("key1")
        lock2 = gw._cache_key_lock("key2")
        assert lock1 is not lock2


# ---------------------------------------------------------------------------
# call_model basic paths
# ---------------------------------------------------------------------------

class TestCallModel:
    def _make_profile(self, pid: str = "m", enabled: bool = True) -> "ModelProfile":
        from general_ludd.models.gateway import ModelProfile
        return ModelProfile(model_profile_id=pid, enabled=enabled)

    def _make_mock_registry(self, response_content: str = "hello") -> tuple:
        mock_registry = MagicMock()
        mock_registry.is_installed.return_value = True
        mock_response = MagicMock()
        mock_response.content = response_content
        mock_response.usage_metadata = {"input_tokens": 5, "output_tokens": 2}
        mock_provider = MagicMock()
        mock_provider.return_value.invoke.return_value = mock_response
        mock_registry.get_provider_class.return_value = mock_provider
        return mock_registry, mock_response

    def test_call_model_profile_not_found_raises(self) -> None:
        from general_ludd.models.gateway import ModelGateway
        gw = ModelGateway()
        with pytest.raises(ValueError, match="not found"):
            gw.call_model("nonexistent", [])

    def test_call_model_budget_rejected_raises(self) -> None:
        from general_ludd.models.gateway import ModelGateway, ModelProfile
        p = ModelProfile(model_profile_id="m", api_metered=True, run_budget_usd=1.0)
        gw = ModelGateway(profiles=[p])
        with pytest.raises(ValueError, match="over budget"):
            gw.call_model("m", [], estimated_cost=10.0, budget_remaining=1000.0)

    def test_call_model_cache_hit_first_check(self) -> None:
        from general_ludd.models.gateway import ModelGateway, ModelProfile, ModelResponse
        p = ModelProfile(model_profile_id="m", enabled=True, model_name="gpt-4")
        mock_cache = MagicMock()
        mock_cache.get.return_value = {
            "content": "cached",
            "usage_metadata": {},
            "cost_estimate": 0.0,
            "model_name": "gpt-4",
        }
        gw = ModelGateway(profiles=[p], response_cache=mock_cache)
        result = gw.call_model("m", [{"role": "user", "content": "hi"}])
        assert result.content == "cached"

    def test_call_model_cache_hit_single_flight(self) -> None:
        from general_ludd.models.gateway import ModelGateway, ModelProfile
        p = ModelProfile(model_profile_id="m", enabled=True, model_name="gpt-4")
        call_count = [0]
        def fake_get(key: str) -> dict | None:
            call_count[0] += 1
            if call_count[0] == 1:
                return None  # first check misses
            return {
                "content": "from-single-flight",
                "usage_metadata": {},
                "cost_estimate": 0.0,
                "model_name": "gpt-4",
            }
        mock_cache = MagicMock()
        mock_cache.get.side_effect = fake_get
        gw = ModelGateway(profiles=[p], response_cache=mock_cache)
        result = gw.call_model("m", [{"role": "user", "content": "hi"}])
        assert result.content == "from-single-flight"

    def test_call_model_happy_path_no_cache(self) -> None:
        from general_ludd.models.gateway import ModelGateway, ModelProfile
        p = ModelProfile(model_profile_id="m", enabled=True, model_name="gpt-4")
        gw = ModelGateway(profiles=[p])
        mock_registry, _ = self._make_mock_registry("hello world")
        with patch.object(gw, "_registry", mock_registry):
            result = gw.call_model("m", [{"role": "user", "content": "hi"}])
        assert result.content == "hello world"

    def test_call_model_happy_path_sets_cache(self) -> None:
        from general_ludd.models.gateway import ModelGateway, ModelProfile
        p = ModelProfile(model_profile_id="m", enabled=True, model_name="gpt-4")
        mock_cache = MagicMock()
        mock_cache.get.return_value = None  # always miss
        gw = ModelGateway(profiles=[p], response_cache=mock_cache)
        mock_registry, _ = self._make_mock_registry("fresh")
        with patch.object(gw, "_registry", mock_registry):
            result = gw.call_model("m", [{"role": "user", "content": "hi"}])
        assert result.content == "fresh"
        assert mock_cache.set.called


# ---------------------------------------------------------------------------
# _invoke_and_bill paths
# ---------------------------------------------------------------------------

class TestInvokeAndBill:
    def _make_profile(self, **kwargs) -> "ModelProfile":
        from general_ludd.models.gateway import ModelProfile
        defaults = dict(model_profile_id="m", enabled=True, model_name="gpt-4")
        defaults.update(kwargs)
        return ModelProfile(**defaults)

    def test_registry_not_installed_raises_import_error(self) -> None:
        from general_ludd.models.gateway import ModelGateway
        p = self._make_profile()
        gw = ModelGateway(profiles=[p])
        mock_registry = MagicMock()
        mock_registry.is_installed.return_value = False
        with patch.object(gw, "_registry", mock_registry):
            with pytest.raises(ImportError, match="not installed"):
                gw.call_model("m", [])

    def test_no_registry_raises_value_error(self) -> None:
        from general_ludd.models.gateway import ModelGateway
        p = self._make_profile()
        gw = ModelGateway(profiles=[p])
        # registry is None by default
        with pytest.raises(ValueError, match="No provider registry"):
            gw.call_model("m", [])

    def test_secrets_resolve_api_key(self) -> None:
        from general_ludd.models.gateway import ModelGateway, ModelProfile
        p = ModelProfile(
            model_profile_id="m", enabled=True, model_name="gpt-4",
            credential_alias="MY_KEY",
        )
        mock_secrets = MagicMock()
        mock_secrets.resolve.return_value = "sk-test-key"
        gw = ModelGateway(profiles=[p], secrets_manager=mock_secrets)

        mock_response = MagicMock()
        mock_response.content = "ok"
        mock_response.usage_metadata = {}
        captured_kwargs: list[dict] = []

        def fake_provider(**kwargs):
            captured_kwargs.append(kwargs)
            inst = MagicMock()
            inst.invoke.return_value = mock_response
            return inst

        mock_registry = MagicMock()
        mock_registry.is_installed.return_value = True
        mock_registry.get_provider_class.return_value = fake_provider
        with patch.object(gw, "_registry", mock_registry):
            gw.call_model("m", [])
        assert captured_kwargs[0].get("api_key") == "sk-test-key"

    def test_api_base_alias_ssrf_guard_blocks(self) -> None:
        from general_ludd.models.gateway import ModelGateway, ModelProfile
        p = ModelProfile(
            model_profile_id="m", enabled=True, model_name="gpt-4",
            api_base_alias="BLOCKED_URL",
        )
        mock_secrets = MagicMock()
        mock_secrets.resolve.return_value = "http://169.254.169.254/latest"
        gw = ModelGateway(profiles=[p], secrets_manager=mock_secrets)

        mock_registry = MagicMock()
        mock_registry.is_installed.return_value = True
        mock_registry.get_provider_class.return_value = MagicMock()
        with patch.object(gw, "_registry", mock_registry):
            with patch("general_ludd.security.auth.is_safe_fetch_url", return_value=False):
                with pytest.raises(ValueError, match="SSRF"):
                    gw.call_model("m", [])

    def test_empty_response_raises_and_records(self) -> None:
        from general_ludd.models.gateway import ModelGateway, ModelProfile
        p = ModelProfile(model_profile_id="m", enabled=True, model_name="gpt-4")
        gw = ModelGateway(profiles=[p])

        mock_response = MagicMock()
        mock_response.content = "   "  # whitespace only = empty
        mock_response.usage_metadata = {}
        mock_provider = MagicMock()
        mock_provider.return_value.invoke.return_value = mock_response
        mock_registry = MagicMock()
        mock_registry.is_installed.return_value = True
        mock_registry.get_provider_class.return_value = mock_provider

        with patch.object(gw, "_registry", mock_registry):
            with pytest.raises(httpx.HTTPStatusError) as exc_info:
                gw.call_model("m", [])
        assert exc_info.value.response.status_code == 503

    def test_usage_fallback_keys_prompt_completion(self) -> None:
        """prompt_tokens / completion_tokens used when input/output_tokens absent."""
        from general_ludd.models.gateway import ModelGateway, ModelProfile
        p = ModelProfile(
            model_profile_id="m", enabled=True, model_name="gpt-4",
            cost_per_input_token=0.001, cost_per_output_token=0.002,
        )
        gw = ModelGateway(profiles=[p])

        mock_response = MagicMock()
        mock_response.content = "result"
        mock_response.usage_metadata = {"prompt_tokens": 10, "completion_tokens": 5}
        mock_provider = MagicMock()
        mock_provider.return_value.invoke.return_value = mock_response
        mock_registry = MagicMock()
        mock_registry.is_installed.return_value = True
        mock_registry.get_provider_class.return_value = mock_provider

        with patch.object(gw, "_registry", mock_registry):
            result = gw.call_model("m", [])
        # 10 * 0.001 + 5 * 0.002 = 0.02
        assert abs(result.cost_estimate - 0.02) < 1e-9

    def test_bills_budget_guard_on_success(self) -> None:
        from general_ludd.models.gateway import ModelGateway, ModelProfile
        p = ModelProfile(
            model_profile_id="m", enabled=True, model_name="gpt-4",
            cost_per_input_token=0.001, cost_per_output_token=0.002,
        )
        mock_budget_guard = MagicMock()
        gw = ModelGateway(profiles=[p], budget_guard=mock_budget_guard)

        mock_response = MagicMock()
        mock_response.content = "ok"
        mock_response.usage_metadata = {"input_tokens": 10, "output_tokens": 5}
        mock_provider = MagicMock()
        mock_provider.return_value.invoke.return_value = mock_response
        mock_registry = MagicMock()
        mock_registry.is_installed.return_value = True
        mock_registry.get_provider_class.return_value = mock_provider

        with patch.object(gw, "_registry", mock_registry):
            gw.call_model("m", [])
        mock_budget_guard.record_spend.assert_called_once()

    def test_records_health_tracker_success(self) -> None:
        from general_ludd.models.gateway import ModelGateway, ModelProfile
        p = ModelProfile(model_profile_id="m", enabled=True, model_name="gpt-4")
        mock_health_tracker = MagicMock()
        gw = ModelGateway(profiles=[p], health_tracker=mock_health_tracker)

        mock_response = MagicMock()
        mock_response.content = "ok"
        mock_response.usage_metadata = {}
        mock_provider = MagicMock()
        mock_provider.return_value.invoke.return_value = mock_response
        mock_registry = MagicMock()
        mock_registry.is_installed.return_value = True
        mock_registry.get_provider_class.return_value = mock_provider

        with patch.object(gw, "_registry", mock_registry):
            gw.call_model("m", [])
        mock_health_tracker.record_success.assert_called_once_with("m")

    def test_records_metrics_on_success(self) -> None:
        from general_ludd.models.gateway import ModelGateway, ModelProfile
        p = ModelProfile(model_profile_id="m", enabled=True, model_name="gpt-4")
        mock_metrics = MagicMock()
        gw = ModelGateway(profiles=[p], metrics_collector=mock_metrics, metrics_agent_id="agent1")

        mock_response = MagicMock()
        mock_response.content = "ok"
        mock_response.usage_metadata = {"input_tokens": 1, "output_tokens": 1}
        mock_provider = MagicMock()
        mock_provider.return_value.invoke.return_value = mock_response
        mock_registry = MagicMock()
        mock_registry.is_installed.return_value = True
        mock_registry.get_provider_class.return_value = mock_provider

        with patch.object(gw, "_registry", mock_registry):
            gw.call_model("m", [])
        mock_metrics.record_model_call.assert_called_once()


# ---------------------------------------------------------------------------
# _empty_response_error
# ---------------------------------------------------------------------------

class TestEmptyResponseError:
    def test_returns_httpx_http_status_error(self) -> None:
        from general_ludd.models.gateway import ModelGateway
        exc = ModelGateway._empty_response_error("my-profile")
        assert isinstance(exc, httpx.HTTPStatusError)
        assert exc.response.status_code == 503
        assert "my-profile" in str(exc)


# ---------------------------------------------------------------------------
# record_timeout_on_failure
# ---------------------------------------------------------------------------

class TestRecordTimeoutOnFailure:
    def test_no_health_tracker_is_noop(self) -> None:
        from general_ludd.models.gateway import ModelGateway
        gw = ModelGateway()
        # Should not raise
        gw.record_timeout_on_failure("m", RuntimeError("oops"))

    def test_with_tracker_calls_record_event(self) -> None:
        from general_ludd.models.gateway import ModelGateway
        mock_tracker = MagicMock()
        gw = ModelGateway(health_tracker=mock_tracker)
        exc = httpx.ReadTimeout("timed out")
        gw.record_timeout_on_failure("m", exc)
        mock_tracker.record_event.assert_called_once()
        event = mock_tracker.record_event.call_args[0][0]
        from general_ludd.models.timeout_detector import TimeoutKind
        assert event.model_id == "m"
        assert event.kind == TimeoutKind.READ_TIMEOUT


# ---------------------------------------------------------------------------
# call_model_by_role / call_model_by_pattern
# ---------------------------------------------------------------------------

class TestCallModelByRoleAndPattern:
    def _make_gateway_with_profile(self) -> "ModelGateway":
        from general_ludd.models.gateway import ModelGateway, ModelProfile
        p = ModelProfile(model_profile_id="m", enabled=True, model_name="gpt-4")
        gw = ModelGateway(profiles=[p])
        return gw

    def test_call_model_by_role_no_router_raises(self) -> None:
        from general_ludd.models.gateway import ModelGateway
        gw = ModelGateway()
        with pytest.raises(ValueError, match="No router"):
            gw.call_model_by_role("writer", [])

    def test_call_model_by_role_router_returns_none_raises(self) -> None:
        from general_ludd.models.gateway import ModelGateway
        mock_router = MagicMock()
        mock_router.resolve_role.return_value = None
        gw = ModelGateway(router=mock_router)
        with pytest.raises(ValueError, match="No profile resolved"):
            gw.call_model_by_role("writer", [])

    def test_call_model_by_role_success(self) -> None:
        gw = self._make_gateway_with_profile()
        mock_router = MagicMock()
        mock_router.resolve_role.return_value = "m"
        gw._router = mock_router

        mock_response = MagicMock()
        mock_response.content = "role response"
        mock_response.usage_metadata = {}
        mock_provider = MagicMock()
        mock_provider.return_value.invoke.return_value = mock_response
        mock_registry = MagicMock()
        mock_registry.is_installed.return_value = True
        mock_registry.get_provider_class.return_value = mock_provider

        with patch.object(gw, "_registry", mock_registry):
            result = gw.call_model_by_role("writer", [])
        assert result.content == "role response"

    def test_call_model_by_pattern_no_router_raises(self) -> None:
        from general_ludd.models.gateway import ModelGateway
        gw = ModelGateway()
        with pytest.raises(ValueError, match="No router"):
            gw.call_model_by_pattern("coding", [])

    def test_call_model_by_pattern_router_returns_none_raises(self) -> None:
        from general_ludd.models.gateway import ModelGateway
        mock_router = MagicMock()
        mock_router.resolve_pattern.return_value = None
        gw = ModelGateway(router=mock_router)
        with pytest.raises(ValueError, match="No profile resolved"):
            gw.call_model_by_pattern("coding", [])

    def test_call_model_by_pattern_success(self) -> None:
        gw = self._make_gateway_with_profile()
        mock_router = MagicMock()
        mock_router.resolve_pattern.return_value = "m"
        gw._router = mock_router

        mock_response = MagicMock()
        mock_response.content = "pattern response"
        mock_response.usage_metadata = {}
        mock_provider = MagicMock()
        mock_provider.return_value.invoke.return_value = mock_response
        mock_registry = MagicMock()
        mock_registry.is_installed.return_value = True
        mock_registry.get_provider_class.return_value = mock_provider

        with patch.object(gw, "_registry", mock_registry):
            result = gw.call_model_by_pattern("coding", [])
        assert result.content == "pattern response"


# ---------------------------------------------------------------------------
# call_model_with_fallback
# ---------------------------------------------------------------------------

class TestCallModelWithFallback:
    def _make_gateway(self, primary_fails: bool = False) -> tuple:
        from general_ludd.models.gateway import ModelGateway, ModelProfile
        primary = ModelProfile(
            model_profile_id="primary", enabled=True, model_name="gpt-4",
            fallback_profiles=["fallback"],
        )
        fallback = ModelProfile(
            model_profile_id="fallback", enabled=True, model_name="gpt-3.5",
        )
        gw = ModelGateway(profiles=[primary, fallback])
        return gw

    def test_primary_succeeds(self) -> None:
        gw = self._make_gateway()
        mock_response = MagicMock()
        mock_response.content = "primary ok"
        mock_response.usage_metadata = {}
        mock_provider = MagicMock()
        mock_provider.return_value.invoke.return_value = mock_response
        mock_registry = MagicMock()
        mock_registry.is_installed.return_value = True
        mock_registry.get_provider_class.return_value = mock_provider

        with patch.object(gw, "_registry", mock_registry):
            result = gw.call_model_with_fallback("primary", [])
        assert result.content == "primary ok"

    def test_primary_fails_fallback_succeeds(self) -> None:
        gw = self._make_gateway()
        mock_response = MagicMock()
        mock_response.content = "fallback ok"
        mock_response.usage_metadata = {}

        call_count = [0]
        def make_provider():
            call_count[0] += 1
            inst = MagicMock()
            if call_count[0] == 1:
                inst.invoke.side_effect = ValueError("primary broke")
            else:
                inst.invoke.return_value = mock_response
            return inst

        mock_registry = MagicMock()
        mock_registry.is_installed.return_value = True
        mock_registry.get_provider_class.return_value = make_provider

        with patch.object(gw, "_registry", mock_registry):
            result = gw.call_model_with_fallback("primary", [])
        assert result.content == "fallback ok"

    def test_all_fail_raises_value_error(self) -> None:
        from general_ludd.models.gateway import ModelGateway, ModelProfile
        primary = ModelProfile(model_profile_id="primary", enabled=True, model_name="gpt-4")
        gw = ModelGateway(profiles=[primary])
        # No registry → primary fails with ValueError("No provider registry")
        with pytest.raises(ValueError, match="All profiles"):
            gw.call_model_with_fallback("primary", [])


# ---------------------------------------------------------------------------
# add_profile / remove_profile / _notify_profile_change
# ---------------------------------------------------------------------------

class TestAddRemoveProfile:
    def test_add_profile_with_no_optionals(self) -> None:
        from general_ludd.models.gateway import ModelGateway
        gw = ModelGateway()
        profile = gw.add_profile("new-model", provider="openai", model="gpt-4")
        assert gw.get_profile("new-model") is profile
        assert profile.enabled is True

    def test_add_profile_fires_event_bus(self) -> None:
        from general_ludd.models.gateway import ModelGateway
        mock_event_bus = MagicMock()
        gw = ModelGateway(event_bus=mock_event_bus)
        gw.add_profile("m", provider="openai", model="gpt-4")
        mock_event_bus.publish.assert_called_once()

    def test_add_profile_fires_hooks(self) -> None:
        from general_ludd.models.gateway import ModelGateway
        mock_hooks = MagicMock()
        gw = ModelGateway(hook_system=mock_hooks)
        gw.add_profile("m", provider="openai", model="gpt-4")
        mock_hooks.fire.assert_called_once_with("on_model_added", pytest.approx({"model_id": "m", "profile": mock_hooks.fire.call_args[0][1]["profile"]}, abs=0))

    def test_add_profile_broadcasts(self) -> None:
        from general_ludd.models.gateway import ModelGateway
        mock_broadcaster = MagicMock()
        gw = ModelGateway(worker_broadcaster=mock_broadcaster)
        gw.add_profile("m", provider="openai", model="gpt-4")
        mock_broadcaster.broadcast_model_update.assert_called_once()

    def test_remove_profile_removes_from_profiles(self) -> None:
        from general_ludd.models.gateway import ModelGateway, ModelProfile
        p = ModelProfile(model_profile_id="m")
        gw = ModelGateway(profiles=[p])
        gw.remove_profile("m")
        assert gw.get_profile("m") is None

    def test_remove_profile_nonexistent_does_not_raise(self) -> None:
        from general_ludd.models.gateway import ModelGateway
        gw = ModelGateway()
        gw.remove_profile("nonexistent")  # should not raise

    def test_remove_profile_fires_event_bus(self) -> None:
        from general_ludd.models.gateway import ModelGateway, ModelProfile
        mock_event_bus = MagicMock()
        p = ModelProfile(model_profile_id="m")
        gw = ModelGateway(profiles=[p], event_bus=mock_event_bus)
        gw.remove_profile("m")
        mock_event_bus.publish.assert_called_once()

    def test_notify_profile_change_broadcaster_raises_logs_warning(self) -> None:
        """Broadcaster exception must be swallowed (logged as warning)."""
        from general_ludd.models.gateway import ModelGateway
        mock_broadcaster = MagicMock()
        mock_broadcaster.broadcast_model_update.side_effect = RuntimeError("network error")
        gw = ModelGateway(worker_broadcaster=mock_broadcaster)
        # Should NOT propagate the broadcaster exception
        gw.add_profile("m", provider="openai", model="gpt-4")
        # Still added the profile
        assert gw.get_profile("m") is not None


# ---------------------------------------------------------------------------
# call_model_with_retry edge cases
# ---------------------------------------------------------------------------

class TestCallModelWithRetryEdgeCases:
    def test_profile_not_found_raises(self) -> None:
        from general_ludd.models.gateway import ModelGateway
        gw = ModelGateway()
        with pytest.raises(ValueError, match="not found"):
            gw.call_model_with_retry("nonexistent", [])

    def test_non_retryable_auth_error_reraises_immediately(self) -> None:
        from general_ludd.models.gateway import ModelGateway, ModelProfile
        p = ModelProfile(model_profile_id="m", enabled=True, model_name="gpt-4")
        gw = ModelGateway(profiles=[p])

        auth_exc = httpx.HTTPStatusError(
            "unauthorized",
            request=MagicMock(),
            response=MagicMock(status_code=401),
        )
        mock_provider = MagicMock()
        mock_provider.return_value.invoke.side_effect = auth_exc
        mock_registry = MagicMock()
        mock_registry.is_installed.return_value = True
        mock_registry.get_provider_class.return_value = mock_provider

        with patch.object(gw, "_registry", mock_registry):
            with pytest.raises(httpx.HTTPStatusError):
                gw.call_model_with_retry("m", [], max_retries=3)


# ---------------------------------------------------------------------------
# _walk_fallbacks
# ---------------------------------------------------------------------------

class TestWalkFallbacks:
    def test_skips_unhealthy_fallback(self) -> None:
        from general_ludd.models.gateway import ModelGateway, ModelProfile
        primary = ModelProfile(
            model_profile_id="primary", enabled=True,
            fallback_profiles=["fb1", "fb2"],
        )
        fb1 = ModelProfile(model_profile_id="fb1", enabled=True)
        fb2 = ModelProfile(model_profile_id="fb2", enabled=True)
        mock_tracker = MagicMock()
        # fb1 unhealthy, fb2 healthy
        mock_tracker.is_healthy.side_effect = lambda mid: mid != "fb1"
        gw = ModelGateway(profiles=[primary, fb1, fb2], health_tracker=mock_tracker)

        mock_response = MagicMock()
        mock_response.content = "fb2 ok"
        mock_response.usage_metadata = {}
        mock_provider = MagicMock()
        mock_provider.return_value.invoke.return_value = mock_response
        mock_registry = MagicMock()
        mock_registry.is_installed.return_value = True
        mock_registry.get_provider_class.return_value = mock_provider

        with patch.object(gw, "_registry", mock_registry):
            result, exc = gw._walk_fallbacks(["fb1", "fb2"], [])
        assert result is not None
        assert result.content == "fb2 ok"

    def test_all_fallbacks_fail_returns_last_exc(self) -> None:
        from general_ludd.models.gateway import ModelGateway, ModelProfile
        fb1 = ModelProfile(model_profile_id="fb1", enabled=True)
        gw = ModelGateway(profiles=[fb1])

        mock_provider = MagicMock()
        mock_provider.return_value.invoke.side_effect = RuntimeError("fb1 failed")
        mock_registry = MagicMock()
        mock_registry.is_installed.return_value = True
        mock_registry.get_provider_class.return_value = mock_provider

        with patch.object(gw, "_registry", mock_registry):
            result, exc = gw._walk_fallbacks(["fb1"], [])
        assert result is None
        assert isinstance(exc, (RuntimeError, ValueError))

    def test_empty_fallback_list_returns_none_none(self) -> None:
        from general_ludd.models.gateway import ModelGateway
        gw = ModelGateway()
        result, exc = gw._walk_fallbacks([], [])
        assert result is None
        assert exc is None
