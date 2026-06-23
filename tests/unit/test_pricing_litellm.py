"""Tests for LiteLLMJSONSource — LIVE JSON fetch with mocked httpx.

litellm publishes per-token pricing for all providers in a single JSON file:
  https://raw.githubusercontent.com/BerriAI/litellm/main/model_prices_and_context_window.json

The file is a flat dict keyed by model_id; each entry has a ``litellm_provider``
field (e.g. ``"anthropic"``, ``"openai"``) plus ``input_cost_per_token`` and
``output_cost_per_token`` (USD per token). LiteLLMJSONSource filters the file
to one provider and returns ModelPrice entries with USD-per-1K pricing.

Coverage:
  - provider slug + billing semantics (postpaid_per_use, per_token)
  - LIVE JSON fetch parsing (mocked httpx → ModelPrice list)
  - filter by ``litellm_provider``
  - per-token → per-1K conversion (multiply by 1000)
  - skip entries without pricing (e.g. the ``sample_spec`` metadata key)
  - every price carries a source URL
  - fail-soft: HTTP error, network error, malformed JSON, non-dict body → []
  - registration in all_sources() for both anthropic and openai providers
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from general_ludd.pricing_intel.models import (
    BillingGranularity,
    BillingTerms,
    ProviderBilling,
)
from general_ludd.pricing_intel.sources import LiteLLMJSONSource, all_sources

# ---------------------------------------------------------------------------
# Sample litellm JSON payload (mirrors the real file's shape)
# ---------------------------------------------------------------------------

SAMPLE_LITELLM_JSON: dict[str, Any] = {
    # Metadata key litellm includes in the file — must be skipped because it
    # has no matching litellm_provider (and no real pricing).
    "sample_spec": {
        "litellm_provider": "litellm_proxy",
        "max_tokens": "N/A",
        "input_cost_per_token": 0.0,
        "output_cost_per_token": 0.0,
        "mode": "sample",
    },
    # Anthropic models
    "claude-3-5-sonnet-20241022": {
        "max_tokens": 200000,
        "max_input_tokens": 200000,
        "max_output_tokens": 8192,
        "input_cost_per_token": 0.000003,
        "output_cost_per_token": 0.000015,
        "litellm_provider": "anthropic",
        "mode": "chat",
    },
    "claude-3-haiku-20240307": {
        "max_tokens": 200000,
        "input_cost_per_token": 0.00000025,
        "output_cost_per_token": 0.00000125,
        "litellm_provider": "anthropic",
        "mode": "chat",
    },
    # OpenAI models
    "gpt-4o": {
        "max_tokens": 128000,
        "input_cost_per_token": 0.000005,
        "output_cost_per_token": 0.000015,
        "litellm_provider": "openai",
        "mode": "chat",
    },
    "gpt-4o-mini": {
        "max_tokens": 128000,
        "input_cost_per_token": 0.00000015,
        "output_cost_per_token": 0.0000006,
        "litellm_provider": "openai",
        "mode": "chat",
    },
    # Entry missing pricing fields — must be skipped without error
    "anthropic/no-pricing-yet": {
        "max_tokens": 100000,
        "litellm_provider": "anthropic",
        "mode": "chat",
    },
    # Entry with non-numeric pricing — must be skipped without error
    "openai/bad-pricing": {
        "input_cost_per_token": "not-a-number",
        "output_cost_per_token": 0.000001,
        "litellm_provider": "openai",
        "mode": "chat",
    },
}


def _mock_litellm_response(payload: dict[str, Any]) -> MagicMock:
    """Build a mock httpx response for the litellm raw JSON endpoint."""
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = payload
    return resp


def _mock_client_get(mock_resp: MagicMock) -> MagicMock:
    """Build a mock httpx.Client whose .get() returns mock_resp."""
    client = MagicMock()
    client.__enter__ = MagicMock(return_value=client)
    client.__exit__ = MagicMock(return_value=False)
    client.get = MagicMock(return_value=mock_resp)
    return client


# ---------------------------------------------------------------------------
# Identity / billing semantics
# ---------------------------------------------------------------------------

class TestLiteLLMIdentity:
    def test_anthropic_provider_slug(self) -> None:
        assert LiteLLMJSONSource("anthropic").provider_slug() == "litellm_anthropic"

    def test_openai_provider_slug(self) -> None:
        assert LiteLLMJSONSource("openai").provider_slug() == "litellm_openai"

    def test_fireworks_ai_provider_slug(self) -> None:
        assert LiteLLMJSONSource("fireworks_ai").provider_slug() == "litellm_fireworks_ai"

    def test_billing_terms_anthropic(self) -> None:
        b = LiteLLMJSONSource("anthropic").billing()
        assert isinstance(b, ProviderBilling)
        assert b.granularity == BillingGranularity.per_token
        assert b.terms == BillingTerms.postpaid_per_use
        assert b.currency == "USD"
        assert b.spot_available is False

    def test_billing_notes_reference_source(self) -> None:
        b = LiteLLMJSONSource("openai").billing()
        assert "litellm" in b.notes.lower(), (
            f"billing notes should reference litellm source, got: {b.notes}"
        )

    def test_billing_provider_matches_filter(self) -> None:
        assert LiteLLMJSONSource("anthropic").billing().provider == "litellm_anthropic"
        assert LiteLLMJSONSource("openai").billing().provider == "litellm_openai"


# ---------------------------------------------------------------------------
# LIVE fetch — parsing correctness
# ---------------------------------------------------------------------------

class TestLiteLLMFetchModelPrices:
    def test_anthropic_filters_only_anthropic_models(self) -> None:
        with patch(
            "general_ludd.pricing_intel.sources.httpx.Client",
            return_value=_mock_client_get(_mock_litellm_response(SAMPLE_LITELLM_JSON)),
        ):
            prices = LiteLLMJSONSource("anthropic").fetch_model_prices()

        ids = {p.model_id for p in prices}
        assert "claude-3-5-sonnet-20241022" in ids
        assert "claude-3-haiku-20240307" in ids
        # OpenAI models must NOT appear in the anthropic-filtered results
        assert "gpt-4o" not in ids
        assert "gpt-4o-mini" not in ids
        # The metadata sample_spec key must never appear
        assert "sample_spec" not in ids

    def test_openai_filters_only_openai_models(self) -> None:
        with patch(
            "general_ludd.pricing_intel.sources.httpx.Client",
            return_value=_mock_client_get(_mock_litellm_response(SAMPLE_LITELLM_JSON)),
        ):
            prices = LiteLLMJSONSource("openai").fetch_model_prices()

        ids = {p.model_id for p in prices}
        assert "gpt-4o" in ids
        assert "gpt-4o-mini" in ids
        assert "claude-3-5-sonnet-20241022" not in ids

    def test_per_token_to_per_1k_conversion(self) -> None:
        """input_cost_per_token * 1000 == input_usd_per_1k."""
        with patch(
            "general_ludd.pricing_intel.sources.httpx.Client",
            return_value=_mock_client_get(_mock_litellm_response(SAMPLE_LITELLM_JSON)),
        ):
            prices = LiteLLMJSONSource("anthropic").fetch_model_prices()

        sonnet = next(p for p in prices if p.model_id == "claude-3-5-sonnet-20241022")
        # 0.000003 USD/token * 1000 = 0.003 USD/1k
        assert sonnet.input_usd_per_1k == pytest.approx(0.003)
        assert sonnet.output_usd_per_1k == pytest.approx(0.015)

    def test_haiku_conversion(self) -> None:
        with patch(
            "general_ludd.pricing_intel.sources.httpx.Client",
            return_value=_mock_client_get(_mock_litellm_response(SAMPLE_LITELLM_JSON)),
        ):
            prices = LiteLLMJSONSource("anthropic").fetch_model_prices()

        haiku = next(p for p in prices if p.model_id == "claude-3-haiku-20240307")
        assert haiku.input_usd_per_1k == pytest.approx(0.00025)
        assert haiku.output_usd_per_1k == pytest.approx(0.00125)

    def test_provider_field_on_returned_prices(self) -> None:
        """Each ModelPrice.provider should be the canonical provider, not 'litellm'."""
        with patch(
            "general_ludd.pricing_intel.sources.httpx.Client",
            return_value=_mock_client_get(_mock_litellm_response(SAMPLE_LITELLM_JSON)),
        ):
            prices = LiteLLMJSONSource("openai").fetch_model_prices()

        for p in prices:
            assert p.provider == "openai", (
                f"expected provider='openai' for {p.model_id}, got {p.provider}"
            )

    def test_context_window_populated_from_max_tokens(self) -> None:
        with patch(
            "general_ludd.pricing_intel.sources.httpx.Client",
            return_value=_mock_client_get(_mock_litellm_response(SAMPLE_LITELLM_JSON)),
        ):
            prices = LiteLLMJSONSource("openai").fetch_model_prices()

        gpt = next(p for p in prices if p.model_id == "gpt-4o")
        assert gpt.context_window == 128000

    def test_all_prices_have_source_url(self) -> None:
        with patch(
            "general_ludd.pricing_intel.sources.httpx.Client",
            return_value=_mock_client_get(_mock_litellm_response(SAMPLE_LITELLM_JSON)),
        ):
            prices = (
                LiteLLMJSONSource("anthropic").fetch_model_prices()
                + LiteLLMJSONSource("openai").fetch_model_prices()
            )

        assert prices, "expected non-empty price list"
        for p in prices:
            assert p.source, f"price for {p.model_id} missing source"
            assert "litellm" in p.source.lower() or "github" in p.source.lower(), (
                f"source should reference litellm JSON URL, got: {p.source}"
            )

    def test_fetched_at_is_set(self) -> None:
        with patch(
            "general_ludd.pricing_intel.sources.httpx.Client",
            return_value=_mock_client_get(_mock_litellm_response(SAMPLE_LITELLM_JSON)),
        ):
            prices = LiteLLMJSONSource("anthropic").fetch_model_prices()

        for p in prices:
            assert p.fetched_at > 0

    def test_entries_without_pricing_are_skipped(self) -> None:
        """Entries with no input_cost_per_token must be silently dropped."""
        with patch(
            "general_ludd.pricing_intel.sources.httpx.Client",
            return_value=_mock_client_get(_mock_litellm_response(SAMPLE_LITELLM_JSON)),
        ):
            prices = LiteLLMJSONSource("anthropic").fetch_model_prices()

        ids = {p.model_id for p in prices}
        assert "anthropic/no-pricing-yet" not in ids

    def test_entries_with_bad_pricing_are_skipped(self) -> None:
        """Entries with non-numeric pricing must be silently dropped."""
        with patch(
            "general_ludd.pricing_intel.sources.httpx.Client",
            return_value=_mock_client_get(_mock_litellm_response(SAMPLE_LITELLM_JSON)),
        ):
            prices = LiteLLMJSONSource("openai").fetch_model_prices()

        ids = {p.model_id for p in prices}
        assert "openai/bad-pricing" not in ids

    def test_correct_count_after_skips(self) -> None:
        """2 valid anthropic models (sonnet, haiku) + 2 valid openai (gpt-4o, mini)."""
        with patch(
            "general_ludd.pricing_intel.sources.httpx.Client",
            return_value=_mock_client_get(_mock_litellm_response(SAMPLE_LITELLM_JSON)),
        ):
            anthropic = LiteLLMJSONSource("anthropic").fetch_model_prices()
            openai = LiteLLMJSONSource("openai").fetch_model_prices()

        assert len(anthropic) == 2
        assert len(openai) == 2


# ---------------------------------------------------------------------------
# Fail-soft behavior
# ---------------------------------------------------------------------------

class TestLiteLLMFailSoft:
    def test_http_error_returns_empty(self) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 503
        with patch(
            "general_ludd.pricing_intel.sources.httpx.Client",
            return_value=_mock_client_get(mock_resp),
        ):
            prices = LiteLLMJSONSource("anthropic").fetch_model_prices()
        assert prices == []

    def test_network_error_returns_empty(self) -> None:
        client = MagicMock()
        client.__enter__ = MagicMock(return_value=client)
        client.__exit__ = MagicMock(return_value=False)
        client.get = MagicMock(side_effect=ConnectionError("network down"))
        with patch(
            "general_ludd.pricing_intel.sources.httpx.Client",
            return_value=client,
        ):
            prices = LiteLLMJSONSource("openai").fetch_model_prices()
        assert prices == []

    def test_malformed_json_returns_empty(self) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.side_effect = ValueError("not json")
        with patch(
            "general_ludd.pricing_intel.sources.httpx.Client",
            return_value=_mock_client_get(mock_resp),
        ):
            prices = LiteLLMJSONSource("anthropic").fetch_model_prices()
        assert prices == []

    def test_non_dict_body_returns_empty(self) -> None:
        """If the JSON parses to a list or None instead of a dict, return []."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = ["not", "a", "dict"]
        with patch(
            "general_ludd.pricing_intel.sources.httpx.Client",
            return_value=_mock_client_get(mock_resp),
        ):
            prices = LiteLLMJSONSource("anthropic").fetch_model_prices()
        assert prices == []

    def test_empty_payload_returns_empty(self) -> None:
        with patch(
            "general_ludd.pricing_intel.sources.httpx.Client",
            return_value=_mock_client_get(_mock_litellm_response({})),
        ):
            prices = LiteLLMJSONSource("anthropic").fetch_model_prices()
        assert prices == []

    def test_no_matching_provider_returns_empty(self) -> None:
        """If no entries match the requested provider, return []."""
        with patch(
            "general_ludd.pricing_intel.sources.httpx.Client",
            return_value=_mock_client_get(_mock_litellm_response(SAMPLE_LITELLM_JSON)),
        ):
            prices = LiteLLMJSONSource("vertex_ai").fetch_model_prices()
        assert prices == []

    def test_compute_prices_always_empty(self) -> None:
        """LiteLLM is a model-pricing catalog; no compute offering."""
        assert LiteLLMJSONSource("anthropic").fetch_compute_prices() == []


# ---------------------------------------------------------------------------
# Registration in all_sources()
# ---------------------------------------------------------------------------

class TestLiteLLMRegistration:
    def test_anthropic_registered(self) -> None:
        slugs = [s.provider_slug() for s in all_sources()]
        assert "litellm_anthropic" in slugs, (
            "LiteLLMJSONSource('anthropic') must be registered in all_sources(); "
            "got: " + ", ".join(slugs)
        )

    def test_openai_registered(self) -> None:
        slugs = [s.provider_slug() for s in all_sources()]
        assert "litellm_openai" in slugs, (
            "LiteLLMJSONSource('openai') must be registered in all_sources(); "
            "got: " + ", ".join(slugs)
        )

    def test_fireworks_ai_registered(self) -> None:
        slugs = [s.provider_slug() for s in all_sources()]
        assert "litellm_fireworks_ai" in slugs, (
            "LiteLLMJSONSource('fireworks_ai') must be registered in all_sources(); "
            "got: " + ", ".join(slugs)
        )

    def test_each_registered_once(self) -> None:
        slugs = [s.provider_slug() for s in all_sources()]
        assert slugs.count("litellm_anthropic") == 1
        assert slugs.count("litellm_openai") == 1
        assert slugs.count("litellm_fireworks_ai") == 1
