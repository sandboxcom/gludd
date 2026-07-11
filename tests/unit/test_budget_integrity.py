from __future__ import annotations

import math
import threading
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from general_ludd.controllers.spend_limiter import SpendLimiter
from general_ludd.execution.engine import ExecutionEngine

_UNKNOWN_MODEL_COST_PER_1K = 0.01


def _engine_with_mock_gateway(model_name=None, max_input_tokens=1000, max_output_tokens=0):
    gateway = MagicMock()
    profile = SimpleNamespace(
        model_name=model_name,
        max_input_tokens=max_input_tokens,
        max_output_tokens=max_output_tokens,
    )
    gateway.get_profile.return_value = profile
    engine = ExecutionEngine(model_gateway=gateway)
    engine._budget_guard = None
    return engine


class TestProjectedCostUnknownModel:
    def test_unknown_model_in_pricing_table_gets_default_nonzero(self):
        engine = _engine_with_mock_gateway(model_name="nonexistent-model-9999")
        cost = engine._projected_cost()
        assert cost > 0.0, f"Expected >0 for unknown model, got {cost}"

    def test_unknown_model_cost_is_at_least_conservative_per_token(self):
        engine = _engine_with_mock_gateway(
            model_name="nonexistent-model-9999",
            max_input_tokens=2000,
            max_output_tokens=0,
        )
        cost = engine._projected_cost()
        assert cost > 0.0, f"Expected >0 for unknown model, got {cost}"
        min_cost_per_token = 0.00001
        assert cost >= 1000 * min_cost_per_token * 0.5, (
            f"Expected cost >= $0.005 (0.5 * 1K tokens at {min_cost_per_token}/token), "
            f"got {cost}"
        )

    def test_unknown_model_cost_scales_with_tokens(self):
        engine_small = _engine_with_mock_gateway(
            model_name="nonexistent-a",
            max_input_tokens=500,
            max_output_tokens=0,
        )
        engine_large = _engine_with_mock_gateway(
            model_name="nonexistent-b",
            max_input_tokens=2000,
            max_output_tokens=0,
        )
        cost_small = engine_small._projected_cost()
        cost_large = engine_large._projected_cost()
        assert cost_large > cost_small, (
            f"Expected larger cost for more tokens: {cost_small} vs {cost_large}"
        )

    def test_known_model_gets_pricing_table_cost(self):
        engine = _engine_with_mock_gateway(
            model_name="claude-3-5-sonnet-20241022",
            max_input_tokens=1000,
            max_output_tokens=0,
        )
        cost = engine._projected_cost()
        assert cost > 0.0, f"Expected >0 for known model, got {cost}"
        expected = 0.003
        assert abs(cost - expected) < 0.001, (
            f"Expected ~${expected} for claude-3-5-sonnet with 1K input, got {cost}"
        )

    def test_attribute_coercion_failure_uses_conservative_default(self):
        gateway = MagicMock()
        profile = SimpleNamespace(model_name="bad-model")
        profile.max_input_tokens = "not-an-int"
        gateway.get_profile.return_value = profile
        engine = ExecutionEngine(model_gateway=gateway)
        engine._budget_guard = None
        cost = engine._projected_cost()
        assert cost > 0.0, (
            f"Expected >0 for coercion failure (conservative default), got {cost}"
        )

    def test_static_pricing_fallback_failure_uses_conservative_default(self):
        engine = _engine_with_mock_gateway(
            model_name="nonexistent-model-9999",
            max_input_tokens=1000,
        )
        with patch(
            "general_ludd.infra.pricing.token_cost_usd",
            side_effect=RuntimeError("catalog down"),
        ):
            cost = engine._projected_cost()
        assert cost > 0.0, (
            f"Expected >0 when static pricing raises (conservative default), got {cost}"
        )
        min_expected = 1000 / 1000.0 * _UNKNOWN_MODEL_COST_PER_1K
        assert cost >= min_expected

    def test_no_gateway_returns_zero(self):
        engine = ExecutionEngine(model_gateway=None)
        cost = engine._projected_cost()
        assert cost == 0.0

    def test_get_profile_raises_returns_zero(self):
        gateway = MagicMock()
        gateway.get_profile.side_effect = RuntimeError("network down")
        engine = ExecutionEngine(model_gateway=gateway)
        engine._budget_guard = None
        cost = engine._projected_cost()
        assert cost == 0.0

    def test_profile_is_none_returns_zero(self):
        gateway = MagicMock()
        gateway.get_profile.return_value = None
        engine = ExecutionEngine(model_gateway=gateway)
        engine._budget_guard = None
        cost = engine._projected_cost()
        assert cost == 0.0




class TestSpendLimiterReserveCommitRelease:
    def test_reserve_succeeds_under_cap(self):
        sl = SpendLimiter(limit_usd=1.0, window_seconds=3600)
        token = sl.reserve(0.5)
        assert token is not None
        spent = sl.window_spend()
        assert spent == 0.5

    def test_reserve_fails_over_cap(self):
        sl = SpendLimiter(limit_usd=0.5, window_seconds=3600)
        token = sl.reserve(1.0)
        assert token is None

    def test_reserve_rejects_nonfinite(self):
        sl = SpendLimiter(limit_usd=100.0, window_seconds=3600)
        assert sl.reserve(float("nan")) is None
        assert sl.reserve(float("inf")) is None

    def test_reserve_rejects_non_number(self):
        sl = SpendLimiter(limit_usd=100.0, window_seconds=3600)
        assert sl.reserve("fifty") is None

    def test_reserve_rejects_zero_or_negative(self):
        sl = SpendLimiter(limit_usd=100.0, window_seconds=3600)
        assert sl.reserve(0.0) is None
        assert sl.reserve(-1.0) is None

    def test_concurrent_reserves_cannot_exceed_cap(self):
        sl = SpendLimiter(limit_usd=1.0, window_seconds=3600)
        results = []
        barrier = threading.Barrier(10)

        def _try_reserve(amount):
            barrier.wait()
            result = sl.reserve(amount)
            results.append(result)

        threads = [threading.Thread(target=_try_reserve, args=(0.5,)) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        tokens = [r for r in results if r is not None]
        assert len(tokens) <= 2, (
            f"Expected at most 2 reserves under 1.0 cap, got {len(tokens)}"
        )
        spent = sl.window_spend()
        assert spent <= 1.0, f"Spent {spent} exceeds cap 1.0"

    def test_commit_records_actual_cost(self):
        sl = SpendLimiter(limit_usd=100.0, window_seconds=3600)
        token = sl.reserve(0.5)
        assert token is not None
        assert sl.commit(token, 0.3, kind="model_call", model="test-model")
        assert math.isclose(sl.window_spend(), 0.3)

    def test_commit_unknown_token_returns_false(self):
        sl = SpendLimiter(limit_usd=100.0, window_seconds=3600)
        assert sl.commit("nonexistent-token", 0.5, kind="model_call") is False

    def test_commit_does_not_double_count(self):
        sl = SpendLimiter(limit_usd=100.0, window_seconds=3600)
        token = sl.reserve(0.5)
        sl.commit(token, 0.3, kind="model_call")
        assert math.isclose(sl.window_spend(), 0.3)
        assert sl.commit(token, 1.0, kind="model_call") is False

    def test_release_frees_reservation(self):
        sl = SpendLimiter(limit_usd=100.0, window_seconds=3600)
        token = sl.reserve(0.5)
        assert math.isclose(sl.window_spend(), 0.5)
        assert sl.release(token)
        assert sl.window_spend() == 0.0

    def test_release_unknown_token_returns_false(self):
        sl = SpendLimiter(limit_usd=100.0, window_seconds=3600)
        assert sl.release("nonexistent-token") is False

    def test_release_is_idempotent(self):
        sl = SpendLimiter(limit_usd=100.0, window_seconds=3600)
        token = sl.reserve(0.5)
        assert sl.release(token)
        assert sl.release(token) is False
        assert sl.window_spend() == 0.0

    def test_reserve_after_release_works(self):
        sl = SpendLimiter(limit_usd=1.0, window_seconds=3600)
        token1 = sl.reserve(0.6)
        assert token1 is not None
        sl.release(token1)
        token2 = sl.reserve(0.8)
        assert token2 is not None
        assert math.isclose(sl.window_spend(), 0.8)

    def test_commit_replaces_reserved_not_adds(self):
        sl = SpendLimiter(limit_usd=100.0, window_seconds=3600)
        token = sl.reserve(0.5)
        sl.commit(token, 0.25, kind="model_call")
        assert math.isclose(sl.window_spend(), 0.25)

    def test_try_charge_alongside_reserve(self):
        sl = SpendLimiter(limit_usd=100.0, window_seconds=3600)
        assert sl.try_charge(0.1, kind="model_call") is True
        token = sl.reserve(0.5)
        assert token is not None
        sl.commit(token, 0.3, kind="model_call")
        assert math.isclose(sl.window_spend(), 0.4)
