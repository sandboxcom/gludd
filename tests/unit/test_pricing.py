"""Tests for the pricing module: token_cost_usd + infra_cost_usd math."""

from __future__ import annotations

import pytest

from general_ludd.infra.pricing import (
    INFRA_PRICING,
    PRICING,
    infra_cost_usd,
    token_cost_usd,
)


class TestPricingTable:
    def test_pricing_dict_is_nonempty(self) -> None:
        assert len(PRICING) > 0

    def test_all_entries_have_two_positive_floats(self) -> None:
        for model_id, (inp, out) in PRICING.items():
            assert isinstance(inp, float), f"{model_id}: input price not float"
            assert isinstance(out, float), f"{model_id}: output price not float"
            assert inp >= 0.0, f"{model_id}: input price negative"
            assert out >= 0.0, f"{model_id}: output price negative"

    def test_default_key_present(self) -> None:
        # Must have a "__default__" fallback key.
        assert "__default__" in PRICING

    def test_known_model_present(self) -> None:
        # At least one real anthropic/openai model id must be present.
        known = {
            "claude-3-5-sonnet-20241022",
            "claude-3-haiku-20240307",
            "gpt-4o",
            "gpt-4o-mini",
        }
        found = known & set(PRICING.keys())
        assert len(found) >= 1, f"No known model in PRICING; keys={list(PRICING.keys())[:10]}"


class TestTokenCostUsd:
    def test_zero_tokens_is_zero(self) -> None:
        cost = token_cost_usd("__default__", 0, 0)
        assert cost == pytest.approx(0.0)

    def test_input_only(self) -> None:
        # 1000 input tokens at __default__ rate
        inp_rate, _ = PRICING["__default__"]
        cost = token_cost_usd("__default__", 1000, 0)
        assert cost == pytest.approx(inp_rate)

    def test_output_only(self) -> None:
        _, out_rate = PRICING["__default__"]
        cost = token_cost_usd("__default__", 0, 1000)
        assert cost == pytest.approx(out_rate)

    def test_combined(self) -> None:
        inp_rate, out_rate = PRICING["__default__"]
        cost = token_cost_usd("__default__", 1000, 2000)
        assert cost == pytest.approx(inp_rate * 1 + out_rate * 2)

    def test_unknown_model_falls_back_to_default(self) -> None:
        cost_known = token_cost_usd("__default__", 500, 500)
        cost_unknown = token_cost_usd("totally-nonexistent-model-xyz", 500, 500)
        assert cost_unknown == pytest.approx(cost_known)

    def test_known_model_uses_its_own_rate(self) -> None:
        models_not_default = [k for k in PRICING if k != "__default__"]
        assert models_not_default, (
            "PRICING dict must contain at least one non-default model entry"
        )
        model = models_not_default[0]
        inp_rate, out_rate = PRICING[model]
        cost = token_cost_usd(model, 1000, 1000)
        assert cost == pytest.approx(inp_rate + out_rate)

    def test_non_default_rate_differs_from_default(self) -> None:
        models_not_default = [k for k in PRICING if k != "__default__"]
        assert models_not_default, (
            "PRICING dict must contain at least one non-default model entry"
        )
        default_inp, default_out = PRICING["__default__"]
        model = models_not_default[0]
        model_inp, model_out = PRICING[model]
        assert (model_inp != default_inp or model_out != default_out), (
            f"Non-default model {model} has same rate as __default__ — "
            "differential pricing must be verifiable"
        )

    def test_large_token_count(self) -> None:
        # Should not overflow or raise
        cost = token_cost_usd("__default__", 1_000_000, 500_000)
        assert cost > 0.0

    def test_returns_float(self) -> None:
        cost = token_cost_usd("__default__", 100, 100)
        assert isinstance(cost, float)


class TestInfraPricingTable:
    def test_infra_pricing_nonempty(self) -> None:
        assert len(INFRA_PRICING) > 0

    def test_all_entries_have_positive_rate(self) -> None:
        for kind, rate in INFRA_PRICING.items():
            assert isinstance(rate, float), f"{kind}: rate not float"
            assert rate >= 0.0, f"{kind}: rate negative"

    def test_default_key_present(self) -> None:
        assert "__default__" in INFRA_PRICING


class TestInfraCostUsd:
    def test_zero_units_is_zero(self) -> None:
        cost = infra_cost_usd("gpu_second", 0)
        assert cost == pytest.approx(0.0)

    def test_known_kind(self) -> None:
        if "gpu_second" in INFRA_PRICING:
            rate = INFRA_PRICING["gpu_second"]
            cost = infra_cost_usd("gpu_second", 10)
            assert cost == pytest.approx(rate * 10)

    def test_unknown_kind_falls_back_to_default(self) -> None:
        default_rate = INFRA_PRICING["__default__"]
        cost = infra_cost_usd("totally-unknown-kind", 100)
        assert cost == pytest.approx(default_rate * 100)

    def test_returns_float(self) -> None:
        cost = infra_cost_usd("__default__", 1)
        assert isinstance(cost, float)
