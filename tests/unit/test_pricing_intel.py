"""Tests for pricing intelligence: billing semantics, model prices, compute prices.

Coverage:
  - BillingGranularity / BillingTerms / ProviderBilling / ModelPrice / ComputePrice / ModelInfo
  - OpenRouter LIVE fetch parsing (mocked httpx response → ModelPrice list)
  - Billing-terms correctness per provider:
      RunPod  → prepaid_balance + per_second
      Lambda  → prepaid_balance + per_minute
      AWS     → postpaid_monthly + per_second
      GCP     → postpaid_monthly + per_second
      OpenAI  → postpaid_per_use + per_token
      Anthropic → postpaid_per_use + per_token
      OpenRouter → postpaid_per_use + per_token
  - PricingCatalog aggregation and lookup
  - Fail-soft: availability errors return [] / stale cache; auth/data errors raise
  - Source integrity: no price stored without source URL
  - ModelPrice.__post_init__ raises ValueError on missing source
  - ComputePrice.usd_per_hour() normalizes across granularities
  - cheapest_compute sorting
"""

from __future__ import annotations

import time
from typing import Any, cast
from unittest.mock import MagicMock, patch

import pytest

from general_ludd.pricing_intel.catalog import PricingCatalog
from general_ludd.pricing_intel.models import (
    BillingGranularity,
    BillingTerms,
    ComputePrice,
    ModelInfo,
    ModelPrice,
    ProviderBilling,
)
from general_ludd.pricing_intel.sources import (
    AnthropicSource,
    AWSSource,
    GCPSource,
    HuggingFaceSource,
    LambdaLabsSource,
    OpenAISource,
    OpenRouterSource,
    RunPodSource,
    all_sources,
)

# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

def _mock_openrouter_response(models: list[dict[str, Any]]) -> MagicMock:
    """Build a mock httpx response for the OpenRouter /v1/models endpoint."""
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {"data": models}
    return resp


SAMPLE_OR_MODELS = [
    {
        "id": "anthropic/claude-3-5-sonnet",
        "pricing": {"prompt": "0.000003", "completion": "0.000015"},
        "context_length": 200000,
        "description": "Claude 3.5 Sonnet via OpenRouter",
    },
    {
        "id": "openai/gpt-4o",
        "pricing": {"prompt": "0.000005", "completion": "0.000015"},
        "context_length": 128000,
        "description": "GPT-4o via OpenRouter",
    },
    {
        "id": "meta-llama/llama-3.1-70b",
        "pricing": {"prompt": "0.0000008", "completion": "0.0000008"},
        "context_length": 128000,
        "description": "Llama 3.1 70B",
    },
    # Model with zero pricing (free tier) — should parse without error
    {
        "id": "google/gemma-7b-free",
        "pricing": {"prompt": "0", "completion": "0"},
        "context_length": 8192,
        "description": "Gemma 7B free tier",
    },
    # Model with missing pricing fields — should parse defensively
    {
        "id": "some/model-no-price",
        "pricing": {},
        "context_length": None,
        "description": "",
    },
]


# ---------------------------------------------------------------------------
# Model/dataclass tests
# ---------------------------------------------------------------------------

class TestBillingGranularity:
    def test_all_values_are_strings(self) -> None:
        for val in BillingGranularity:
            assert isinstance(val, str)

    def test_expected_members_exist(self) -> None:
        assert BillingGranularity.per_second == "per_second"
        assert BillingGranularity.per_minute == "per_minute"
        assert BillingGranularity.per_hour == "per_hour"
        assert BillingGranularity.per_token == "per_token"
        assert BillingGranularity.per_request == "per_request"
        assert BillingGranularity.flat == "flat"


class TestBillingTerms:
    def test_all_values_are_strings(self) -> None:
        for val in BillingTerms:
            assert isinstance(val, str)

    def test_expected_members_exist(self) -> None:
        assert BillingTerms.prepaid_balance == "prepaid_balance"
        assert BillingTerms.postpaid_monthly == "postpaid_monthly"
        assert BillingTerms.postpaid_per_use == "postpaid_per_use"


class TestModelPrice:
    def test_valid_model_price_construction(self) -> None:
        mp = ModelPrice(
            provider="test",
            model_id="test-model",
            input_usd_per_1k=0.003,
            output_usd_per_1k=0.015,
            source="https://example.com/pricing",
        )
        assert mp.provider == "test"
        assert mp.input_usd_per_1k == 0.003
        assert mp.output_usd_per_1k == 0.015
        assert mp.fetched_at > 0

    def test_missing_source_raises_value_error(self) -> None:
        """No price may be stored without a source URL — honesty guarantee."""
        with pytest.raises(ValueError, match="no source"):
            ModelPrice(
                provider="test",
                model_id="no-source-model",
                input_usd_per_1k=0.001,
                output_usd_per_1k=0.002,
                source="",  # empty source must fail
            )

    def test_fetched_at_defaults_to_now(self) -> None:
        before = time.time()
        mp = ModelPrice(
            provider="x", model_id="y",
            input_usd_per_1k=0.001, output_usd_per_1k=0.002,
            source="https://example.com",
        )
        after = time.time()
        assert before <= mp.fetched_at <= after


class TestComputePrice:
    def test_valid_compute_price_construction(self) -> None:
        cp = ComputePrice(
            provider="runpod",
            sku="A100-SXM4-80GB-1x",
            usd_per_unit=2.49 / 3600.0,
            granularity=BillingGranularity.per_second,
            spot=False,
            terms=BillingTerms.prepaid_balance,
            source="https://www.runpod.io/gpu-instance/pricing",
        )
        assert cp.provider == "runpod"
        assert cp.spot is False

    def test_missing_source_raises_value_error(self) -> None:
        with pytest.raises(ValueError, match="no source"):
            ComputePrice(
                provider="runpod",
                sku="A100",
                usd_per_unit=0.001,
                granularity=BillingGranularity.per_second,
                spot=False,
                terms=BillingTerms.prepaid_balance,
                source="",
            )

    def test_usd_per_hour_per_second(self) -> None:
        """per_second rate * 3600 == per_hour equivalent."""
        rate_per_sec = 2.49 / 3600.0
        cp = ComputePrice(
            provider="runpod", sku="test", usd_per_unit=rate_per_sec,
            granularity=BillingGranularity.per_second, spot=False,
            terms=BillingTerms.prepaid_balance,
            source="https://example.com",
        )
        assert abs(cp.usd_per_hour() - 2.49) < 1e-6

    def test_usd_per_hour_per_minute(self) -> None:
        """per_minute rate * 60 == per_hour equivalent."""
        rate_per_min = 0.75 / 60.0
        cp = ComputePrice(
            provider="lambda_labs", sku="test", usd_per_unit=rate_per_min,
            granularity=BillingGranularity.per_minute, spot=False,
            terms=BillingTerms.prepaid_balance,
            source="https://example.com",
        )
        assert abs(cp.usd_per_hour() - 0.75) < 1e-6

    def test_usd_per_hour_passthrough_for_hourly(self) -> None:
        cp = ComputePrice(
            provider="some", sku="test", usd_per_unit=3.50,
            granularity=BillingGranularity.per_hour, spot=False,
            terms=BillingTerms.postpaid_monthly,
            source="https://example.com",
        )
        assert cp.usd_per_hour() == 3.50


class TestProviderBilling:
    def test_construction(self) -> None:
        pb = ProviderBilling(
            provider="runpod",
            granularity=BillingGranularity.per_second,
            terms=BillingTerms.prepaid_balance,
        )
        assert pb.currency == "USD"
        assert pb.spot_available is False
        assert pb.min_charge is None


class TestModelInfo:
    def test_construction(self) -> None:
        mi = ModelInfo(
            model_id="claude-3-5-sonnet-20241022",
            provider="anthropic",
            context_window=200_000,
            quality_descriptors={"reasoning": "strong", "code": "excellent"},
        )
        assert mi.model_id == "claude-3-5-sonnet-20241022"
        assert mi.quality_descriptors["code"] == "excellent"
        assert mi.quantization is None


# ---------------------------------------------------------------------------
# OpenRouter source — LIVE fetch with mocked httpx
# ---------------------------------------------------------------------------

class TestOpenRouterSource:
    def test_provider_slug(self) -> None:
        assert OpenRouterSource().provider_slug() == "openrouter"

    def test_billing_terms(self) -> None:
        billing = OpenRouterSource().billing()
        assert billing.granularity == BillingGranularity.per_token
        assert billing.terms == BillingTerms.postpaid_per_use
        assert billing.spot_available is False

    def test_fetch_model_prices_parses_live_response(self) -> None:
        """Mock httpx to verify OpenRouter response → ModelPrice list correctly."""
        mock_resp = _mock_openrouter_response(SAMPLE_OR_MODELS)
        mock_client_instance = MagicMock()
        mock_client_instance.__enter__ = MagicMock(return_value=mock_client_instance)
        mock_client_instance.__exit__ = MagicMock(return_value=False)
        mock_client_instance.get = MagicMock(return_value=mock_resp)

        with patch("general_ludd.pricing_intel.sources.httpx.Client", return_value=mock_client_instance):
            prices = OpenRouterSource().fetch_model_prices()

        assert len(prices) == len(SAMPLE_OR_MODELS)

        # Verify Claude
        claude = next(p for p in prices if p.model_id == "anthropic/claude-3-5-sonnet")
        assert claude.provider == "openrouter"
        # 0.000003 USD/token * 1000 = 0.003 USD/1k
        assert abs(claude.input_usd_per_1k - 0.003) < 1e-9
        assert abs(claude.output_usd_per_1k - 0.015) < 1e-9
        assert claude.context_window == 200000
        assert claude.source == "https://openrouter.ai/api/v1/models"

        # Verify GPT-4o
        gpt = next(p for p in prices if p.model_id == "openai/gpt-4o")
        assert abs(gpt.input_usd_per_1k - 0.005) < 1e-9
        assert abs(gpt.output_usd_per_1k - 0.015) < 1e-9

        # Verify Llama
        llama = next(p for p in prices if p.model_id == "meta-llama/llama-3.1-70b")
        assert abs(llama.input_usd_per_1k - 0.0008) < 1e-9

        # Verify free tier (zero pricing) — must not fail
        free_model = next(p for p in prices if p.model_id == "google/gemma-7b-free")
        assert free_model.input_usd_per_1k == 0.0
        assert free_model.output_usd_per_1k == 0.0

    def test_fetch_model_prices_context_window_none_handled(self) -> None:
        """Model with null context_length must parse without crashing."""
        mock_resp = _mock_openrouter_response([SAMPLE_OR_MODELS[-1]])  # some/model-no-price
        mock_client_instance = MagicMock()
        mock_client_instance.__enter__ = MagicMock(return_value=mock_client_instance)
        mock_client_instance.__exit__ = MagicMock(return_value=False)
        mock_client_instance.get = MagicMock(return_value=mock_resp)

        with patch("general_ludd.pricing_intel.sources.httpx.Client", return_value=mock_client_instance):
            prices = OpenRouterSource().fetch_model_prices()

        assert len(prices) == 1
        assert prices[0].context_window is None

    def test_fetch_model_prices_http_error_returns_empty(self) -> None:
        """Non-200 response → empty list, no exception raised."""
        mock_resp = MagicMock()
        mock_resp.status_code = 503
        mock_client_instance = MagicMock()
        mock_client_instance.__enter__ = MagicMock(return_value=mock_client_instance)
        mock_client_instance.__exit__ = MagicMock(return_value=False)
        mock_client_instance.get = MagicMock(return_value=mock_resp)

        with patch("general_ludd.pricing_intel.sources.httpx.Client", return_value=mock_client_instance):
            prices = OpenRouterSource().fetch_model_prices()

        assert prices == []

    def test_fetch_model_prices_network_error_returns_empty(self) -> None:
        """Network exception → empty list, no exception raised (fail-soft)."""
        mock_client_instance = MagicMock()
        mock_client_instance.__enter__ = MagicMock(return_value=mock_client_instance)
        mock_client_instance.__exit__ = MagicMock(return_value=False)
        mock_client_instance.get = MagicMock(side_effect=ConnectionError("no network"))

        with patch("general_ludd.pricing_intel.sources.httpx.Client", return_value=mock_client_instance):
            prices = OpenRouterSource().fetch_model_prices()

        assert prices == []

    def test_fetch_model_prices_auth_error_is_not_hidden(self) -> None:
        """Authentication failures are configuration errors, not availability probes."""
        mock_resp = MagicMock(status_code=401)
        mock_client_instance = MagicMock()
        mock_client_instance.__enter__ = MagicMock(return_value=mock_client_instance)
        mock_client_instance.__exit__ = MagicMock(return_value=False)
        mock_client_instance.get = MagicMock(return_value=mock_resp)

        with (
            patch("general_ludd.pricing_intel.sources.httpx.Client", return_value=mock_client_instance),
            pytest.raises(RuntimeError, match="authentication"),
        ):
            OpenRouterSource().fetch_model_prices()

    def test_fetch_model_prices_invalid_price_is_not_hidden(self) -> None:
        """A reachable provider returning corrupt prices must fail visibly."""
        mock_resp = _mock_openrouter_response(
            [{"id": "broken/model", "pricing": {"prompt": "not-a-price", "completion": "0.1"}}]
        )
        mock_client_instance = MagicMock()
        mock_client_instance.__enter__ = MagicMock(return_value=mock_client_instance)
        mock_client_instance.__exit__ = MagicMock(return_value=False)
        mock_client_instance.get = MagicMock(return_value=mock_resp)

        with (
            patch("general_ludd.pricing_intel.sources.httpx.Client", return_value=mock_client_instance),
            pytest.raises(ValueError, match="invalid OpenRouter pricing"),
        ):
            OpenRouterSource().fetch_model_prices()

    def test_fetch_model_prices_negative_sentinel_is_omitted_not_zeroed(self) -> None:
        """Provider sentinel rates are unavailable, never free model prices."""
        mock_resp = _mock_openrouter_response(
            [
                {"id": "openrouter/auto", "pricing": {"prompt": "-1", "completion": "-1"}},
                {"id": "valid/model", "pricing": {"prompt": "0.001", "completion": "0.002"}},
            ]
        )
        mock_client_instance = MagicMock()
        mock_client_instance.__enter__ = MagicMock(return_value=mock_client_instance)
        mock_client_instance.__exit__ = MagicMock(return_value=False)
        mock_client_instance.get = MagicMock(return_value=mock_resp)

        with patch(
            "general_ludd.pricing_intel.sources.httpx.Client",
            return_value=mock_client_instance,
        ):
            prices = OpenRouterSource().fetch_model_prices()

        assert [price.model_id for price in prices] == ["valid/model"]

    def test_catalog_does_not_hide_openrouter_auth_errors(self) -> None:
        """The catalog may fail soft on outages but must surface bad credentials."""
        mock_resp = MagicMock(status_code=403)
        mock_client_instance = MagicMock()
        mock_client_instance.__enter__ = MagicMock(return_value=mock_client_instance)
        mock_client_instance.__exit__ = MagicMock(return_value=False)
        mock_client_instance.get = MagicMock(return_value=mock_resp)

        with (
            patch("general_ludd.pricing_intel.sources.httpx.Client", return_value=mock_client_instance),
            pytest.raises(RuntimeError, match="authentication"),
        ):
            PricingCatalog(sources=[OpenRouterSource()]).all_model_prices(
                "openrouter", refresh=True
            )

    def test_all_fetched_prices_have_source(self) -> None:
        """Every ModelPrice from OpenRouter must have a source URL."""
        mock_resp = _mock_openrouter_response(SAMPLE_OR_MODELS[:3])
        mock_client_instance = MagicMock()
        mock_client_instance.__enter__ = MagicMock(return_value=mock_client_instance)
        mock_client_instance.__exit__ = MagicMock(return_value=False)
        mock_client_instance.get = MagicMock(return_value=mock_resp)

        with patch("general_ludd.pricing_intel.sources.httpx.Client", return_value=mock_client_instance):
            prices = OpenRouterSource().fetch_model_prices()

        for price in prices:
            assert price.source, f"Price for {price.model_id} has no source"

    def test_fetch_compute_prices_returns_empty(self) -> None:
        """OpenRouter doesn't offer compute; must return empty list."""
        assert OpenRouterSource().fetch_compute_prices() == []


# ---------------------------------------------------------------------------
# Billing-terms correctness per provider
# ---------------------------------------------------------------------------

class TestBillingTermsPerProvider:
    """The KEY billing semantics assertion: each provider must have the correct
    terms, granularity, and spot_available flag as documented in sources.py.
    """

    def test_runpod_billing(self) -> None:
        """RunPod = PREPAID BALANCE + PER SECOND + SPOT AVAILABLE."""
        b = RunPodSource().billing()
        assert b.provider == "runpod"
        assert b.terms == BillingTerms.prepaid_balance, (
            "RunPod requires a prepaid balance; zero balance = immediate termination"
        )
        assert b.granularity == BillingGranularity.per_second, (
            "RunPod bills to the second (not per minute or per hour)"
        )
        assert b.spot_available is True, (
            "RunPod community cloud offers spot/interruptible instances"
        )
        assert b.currency == "USD"

    def test_lambda_labs_billing(self) -> None:
        """Lambda Labs = PREPAID BALANCE + PER MINUTE + SPOT AVAILABLE."""
        b = LambdaLabsSource().billing()
        assert b.provider == "lambda_labs"
        assert b.terms == BillingTerms.prepaid_balance, (
            "Lambda charges card on consumption; payment failure = service stop"
        )
        assert b.granularity == BillingGranularity.per_minute, (
            "Lambda bills per minute (not per second); minimum 1 minute"
        )
        assert b.spot_available is True, (
            "Lambda offers spot clusters for H100"
        )

    def test_aws_billing(self) -> None:
        """AWS = POSTPAID MONTHLY + PER SECOND + SPOT AVAILABLE."""
        b = AWSSource().billing()
        assert b.provider == "aws"
        assert b.terms == BillingTerms.postpaid_monthly, (
            "AWS bills postpaid monthly; no prepaid balance required"
        )
        assert b.granularity == BillingGranularity.per_second, (
            "AWS EC2 Linux bills per second with 60s minimum"
        )
        assert b.spot_available is True, (
            "AWS EC2 Spot Instances offer up to 90% savings"
        )

    def test_gcp_billing(self) -> None:
        """GCP = POSTPAID MONTHLY + PER SECOND + SPOT AVAILABLE."""
        b = GCPSource().billing()
        assert b.provider == "gcp"
        assert b.terms == BillingTerms.postpaid_monthly, (
            "GCP bills postpaid monthly; no prepaid balance required"
        )
        assert b.granularity == BillingGranularity.per_second, (
            "GCP Compute Engine bills per second with 1-min minimum"
        )
        assert b.spot_available is True, (
            "GCP offers Spot/Preemptible VMs (up to 91% savings)"
        )

    def test_anthropic_billing(self) -> None:
        """Anthropic = POSTPAID PER USE + PER TOKEN + NO SPOT."""
        b = AnthropicSource().billing()
        assert b.provider == "anthropic"
        assert b.terms == BillingTerms.postpaid_per_use
        assert b.granularity == BillingGranularity.per_token
        assert b.spot_available is False

    def test_openai_billing(self) -> None:
        """OpenAI = POSTPAID PER USE + PER TOKEN + NO SPOT."""
        b = OpenAISource().billing()
        assert b.provider == "openai"
        assert b.terms == BillingTerms.postpaid_per_use
        assert b.granularity == BillingGranularity.per_token
        assert b.spot_available is False

    def test_openrouter_billing(self) -> None:
        """OpenRouter = POSTPAID PER USE + PER TOKEN + NO SPOT."""
        b = OpenRouterSource().billing()
        assert b.provider == "openrouter"
        assert b.terms == BillingTerms.postpaid_per_use
        assert b.granularity == BillingGranularity.per_token
        assert b.spot_available is False

    def test_huggingface_billing_registered(self) -> None:
        """HuggingFace dedicated endpoints: per_hour + prepaid_balance (static table)."""
        b = HuggingFaceSource().billing()
        assert b.provider == "huggingface"
        assert b.granularity == BillingGranularity.per_hour
        assert b.terms == BillingTerms.prepaid_balance
        assert b.terms in BillingTerms

    def test_prepaid_providers_not_postpaid(self) -> None:
        """Prepaid providers must not be classified as postpaid — critical correctness."""
        prepaid_providers = ["runpod", "lambda_labs"]
        for slug in prepaid_providers:
            src = next(s for s in all_sources() if s.provider_slug() == slug)
            b = src.billing()
            assert b.terms == BillingTerms.prepaid_balance, (
                f"{slug} must be prepaid_balance — classifying it as postpaid "
                "would lead to incorrect cash-flow planning"
            )
            assert b.terms != BillingTerms.postpaid_monthly

    def test_postpaid_cloud_providers_not_prepaid(self) -> None:
        """AWS and GCP must be classified as postpaid — critical correctness."""
        postpaid_providers = ["aws", "gcp"]
        for slug in postpaid_providers:
            src = next(s for s in all_sources() if s.provider_slug() == slug)
            b = src.billing()
            assert b.terms == BillingTerms.postpaid_monthly, (
                f"{slug} must be postpaid_monthly — classifying it as prepaid "
                "would incorrectly suggest a balance is required"
            )


# ---------------------------------------------------------------------------
# Static source integrity: no fabricated prices
# ---------------------------------------------------------------------------

class TestStaticSourceIntegrity:
    def test_anthropic_prices_all_have_source(self) -> None:
        prices = AnthropicSource().fetch_model_prices()
        assert len(prices) > 0, "Anthropic must have at least one price entry"
        for p in prices:
            assert p.source, f"Anthropic price for {p.model_id} has no source"
            assert "anthropic.com" in p.source, (
                f"Anthropic source should point to anthropic.com, got: {p.source}"
            )

    def test_openai_prices_all_have_source(self) -> None:
        prices = OpenAISource().fetch_model_prices()
        assert len(prices) > 0, "OpenAI must have at least one price entry"
        for p in prices:
            assert p.source, f"OpenAI price for {p.model_id} has no source"
            assert "openai.com" in p.source

    def test_runpod_compute_prices_all_have_source(self) -> None:
        prices = RunPodSource().fetch_compute_prices()
        assert len(prices) > 0, "RunPod must have compute prices"
        for cp in prices:
            assert cp.source, f"RunPod compute price for {cp.sku} has no source"
            assert "runpod.io" in cp.source

    def test_lambda_compute_prices_all_have_source(self) -> None:
        prices = LambdaLabsSource().fetch_compute_prices()
        assert len(prices) > 0, "Lambda Labs must have compute prices"
        for cp in prices:
            assert cp.source, f"Lambda price for {cp.sku} has no source"

    def test_aws_compute_prices_all_have_source(self) -> None:
        prices = AWSSource().fetch_compute_prices()
        assert len(prices) > 0, "AWS must have compute prices"
        for cp in prices:
            assert cp.source, f"AWS price for {cp.sku} has no source"
            assert "aws.amazon.com" in cp.source

    def test_gcp_compute_prices_all_have_source(self) -> None:
        prices = GCPSource().fetch_compute_prices()
        assert len(prices) > 0, "GCP must have compute prices"
        for cp in prices:
            assert cp.source, f"GCP price for {cp.sku} has no source"

    def test_anthropic_context_windows_set(self) -> None:
        """Anthropic prices should include context window info."""
        prices = AnthropicSource().fetch_model_prices()
        for p in prices:
            assert p.context_window is not None, (
                f"Anthropic price for {p.model_id} missing context_window"
            )
            assert p.context_window > 0

    def test_runpod_spot_prices_marked_spot(self) -> None:
        """All RunPod spot prices must have spot=True."""
        prices = RunPodSource().fetch_compute_prices()
        spot_prices = [p for p in prices if p.spot]
        assert len(spot_prices) > 0, "RunPod must have spot prices"
        for cp in spot_prices:
            assert cp.spot is True

    def test_runpod_ondemand_prices_marked_not_spot(self) -> None:
        """All RunPod on-demand prices must have spot=False."""
        prices = RunPodSource().fetch_compute_prices()
        ondemand = [p for p in prices if not p.spot]
        assert len(ondemand) > 0, "RunPod must have on-demand prices"
        for cp in ondemand:
            assert cp.spot is False

    def test_runpod_all_prices_per_second(self) -> None:
        """All RunPod compute prices must use per_second granularity."""
        prices = RunPodSource().fetch_compute_prices()
        for cp in prices:
            assert cp.granularity == BillingGranularity.per_second, (
                f"RunPod {cp.sku} must be per_second; got {cp.granularity}"
            )

    def test_lambda_all_prices_per_minute(self) -> None:
        """All Lambda Labs compute prices must use per_minute granularity."""
        prices = LambdaLabsSource().fetch_compute_prices()
        for cp in prices:
            assert cp.granularity == BillingGranularity.per_minute, (
                f"Lambda {cp.sku} must be per_minute; got {cp.granularity}"
            )

    def test_aws_all_prices_per_second(self) -> None:
        """All AWS compute prices must use per_second granularity."""
        prices = AWSSource().fetch_compute_prices()
        for cp in prices:
            assert cp.granularity == BillingGranularity.per_second

    def test_gcp_all_prices_per_second(self) -> None:
        """All GCP compute prices must use per_second granularity."""
        prices = GCPSource().fetch_compute_prices()
        for cp in prices:
            assert cp.granularity == BillingGranularity.per_second

    def test_runpod_prices_lower_than_aws_for_same_gpu_class(self) -> None:
        """RunPod A100 spot should be cheaper per-hour than AWS A100 on-demand."""
        runpod_prices = RunPodSource().fetch_compute_prices()
        aws_prices = AWSSource().fetch_compute_prices()

        runpod_a100_spot = next(
            (p for p in runpod_prices if "A100" in (p.gpu_type or "") and p.spot), None
        )
        aws_a100_ondemand = next(
            (p for p in aws_prices if "A100" in (p.gpu_type or "") and not p.spot), None
        )

        if runpod_a100_spot and aws_a100_ondemand:
            assert runpod_a100_spot.usd_per_hour() < aws_a100_ondemand.usd_per_hour(), (
                "RunPod A100 spot should be cheaper than AWS A100 on-demand"
            )


# ---------------------------------------------------------------------------
# PricingCatalog tests
# ---------------------------------------------------------------------------

class TestPricingCatalog:
    def test_default_catalog_has_all_providers(self) -> None:
        catalog = PricingCatalog()
        slugs = catalog.provider_slugs()
        for expected in ["openrouter", "anthropic", "openai", "runpod", "lambda_labs", "aws", "gcp"]:
            assert expected in slugs, f"Catalog missing provider: {expected}"

    def test_billing_lookup_runpod(self) -> None:
        catalog = PricingCatalog()
        b = catalog.billing("runpod")
        assert b is not None
        assert b.terms == BillingTerms.prepaid_balance
        assert b.granularity == BillingGranularity.per_second

    def test_billing_lookup_aws(self) -> None:
        catalog = PricingCatalog()
        b = catalog.billing("aws")
        assert b is not None
        assert b.terms == BillingTerms.postpaid_monthly
        assert b.granularity == BillingGranularity.per_second

    def test_billing_lookup_unknown_provider_returns_none(self) -> None:
        catalog = PricingCatalog()
        result = catalog.billing("nonexistent-provider-xyz")
        assert result is None

    def test_all_billing_returns_all_providers(self) -> None:
        catalog = PricingCatalog()
        billing_list = catalog.all_billing()
        assert len(billing_list) >= 7  # at least 7 providers registered
        providers = {b.provider for b in billing_list}
        assert "runpod" in providers
        assert "aws" in providers
        assert "gcp" in providers
        assert "anthropic" in providers

    def test_model_price_anthropic_lookup(self) -> None:
        catalog = PricingCatalog()
        price = catalog.model_price("anthropic", "claude-3-5-sonnet-20241022")
        assert price is not None
        assert abs(price.input_usd_per_1k - 0.003) < 1e-9
        assert abs(price.output_usd_per_1k - 0.015) < 1e-9

    def test_model_price_openai_lookup(self) -> None:
        catalog = PricingCatalog()
        price = catalog.model_price("openai", "gpt-4o")
        assert price is not None
        assert abs(price.input_usd_per_1k - 0.005) < 1e-9

    def test_model_price_unknown_model_returns_none(self) -> None:
        catalog = PricingCatalog()
        result = catalog.model_price("anthropic", "definitely-does-not-exist")
        assert result is None

    def test_model_price_unknown_provider_returns_none(self) -> None:
        catalog = PricingCatalog()
        result = catalog.model_price("nonexistent", "gpt-4o")
        assert result is None

    def test_compute_price_runpod_lookup(self) -> None:
        catalog = PricingCatalog()
        price = catalog.compute_price("runpod", "A100-SXM4-80GB-1x")
        assert price is not None
        assert price.provider == "runpod"
        assert price.terms == BillingTerms.prepaid_balance
        assert price.granularity == BillingGranularity.per_second
        # Expected ~$2.49/hr → ~$0.000691/s
        expected_per_sec = 2.49 / 3600.0
        assert abs(price.usd_per_unit - expected_per_sec) < 1e-6

    def test_compute_price_aws_lookup(self) -> None:
        catalog = PricingCatalog()
        price = catalog.compute_price("aws", "p4d.24xlarge")
        assert price is not None
        assert price.terms == BillingTerms.postpaid_monthly

    def test_compute_price_unknown_sku_returns_none(self) -> None:
        catalog = PricingCatalog()
        result = catalog.compute_price("runpod", "XYZ-DOES-NOT-EXIST")
        assert result is None

    def test_all_compute_prices_all_providers(self) -> None:
        catalog = PricingCatalog()
        all_compute = catalog.all_compute_prices()
        assert len(all_compute) > 10, "Expected compute prices from multiple providers"

    def test_all_compute_prices_spot_filter(self) -> None:
        catalog = PricingCatalog()
        spot_only = catalog.all_compute_prices(spot=True)
        assert all(p.spot for p in spot_only)
        assert len(spot_only) > 0

    def test_all_compute_prices_ondemand_filter(self) -> None:
        catalog = PricingCatalog()
        ondemand = catalog.all_compute_prices(spot=False)
        assert all(not p.spot for p in ondemand)
        assert len(ondemand) > 0

    def test_cheapest_compute_sorted_ascending(self) -> None:
        catalog = PricingCatalog()
        sorted_prices = catalog.cheapest_compute()
        prices_per_hour = [p.usd_per_hour() for p in sorted_prices]
        assert prices_per_hour == sorted(prices_per_hour), "cheapest_compute must be sorted ascending"

    def test_cheapest_compute_gpu_filter(self) -> None:
        catalog = PricingCatalog()
        a100_prices = catalog.cheapest_compute(gpu_type_substr="A100")
        for p in a100_prices:
            assert p.gpu_type is not None
            assert "A100" in p.gpu_type

    def test_openrouter_live_fetch_via_catalog(self) -> None:
        """Catalog correctly delegates fetch to OpenRouterSource (with mock)."""
        mock_resp = _mock_openrouter_response(SAMPLE_OR_MODELS[:2])
        mock_client_instance = MagicMock()
        mock_client_instance.__enter__ = MagicMock(return_value=mock_client_instance)
        mock_client_instance.__exit__ = MagicMock(return_value=False)
        mock_client_instance.get = MagicMock(return_value=mock_resp)

        with patch("general_ludd.pricing_intel.sources.httpx.Client", return_value=mock_client_instance):
            catalog = PricingCatalog()
            # Force a fresh fetch (TTL=0 effectively) by using refresh=True
            prices = catalog.all_model_prices("openrouter", refresh=True)

        assert len(prices) == 2
        assert prices[0].provider == "openrouter"


# ---------------------------------------------------------------------------
# Fail-soft behavior
# ---------------------------------------------------------------------------

class TestFailSoftBehavior:
    def test_source_exception_in_fetch_returns_empty(self) -> None:
        """A source that throws in fetch_model_prices must not propagate to catalog."""
        class BrokenSource:
            def provider_slug(self) -> str:
                return "broken"

            def billing(self) -> ProviderBilling:
                return ProviderBilling(
                    provider="broken",
                    granularity=BillingGranularity.per_token,
                    terms=BillingTerms.postpaid_per_use,
                )

            def fetch_model_prices(self) -> list[ModelPrice]:
                raise RuntimeError("source exploded")

            def fetch_compute_prices(self) -> list[ComputePrice]:
                raise RuntimeError("source exploded")

        catalog = cast(Any, PricingCatalog)(sources=[BrokenSource()])
        # Must not raise
        prices = catalog.all_model_prices("broken", refresh=True)
        assert prices == []

    def test_catalog_survives_partial_source_failure(self) -> None:
        """If one source fails, catalog still returns data from working sources."""
        class BrokenSource:
            def provider_slug(self) -> str:
                return "broken"

            def billing(self) -> ProviderBilling:
                return ProviderBilling(
                    provider="broken",
                    granularity=BillingGranularity.per_token,
                    terms=BillingTerms.postpaid_per_use,
                )

            def fetch_model_prices(self) -> list[ModelPrice]:
                raise ConnectionError("network down")

            def fetch_compute_prices(self) -> list[ComputePrice]:
                return []

        catalog = cast(Any, PricingCatalog)(sources=[BrokenSource(), AnthropicSource()])
        # Anthropic (static) should still work
        prices = catalog.all_model_prices()
        anthropic_prices = [p for p in prices if p.provider == "anthropic"]
        assert len(anthropic_prices) > 0

    def test_catalog_all_billing_survives_billing_exception(self) -> None:
        """A source that raises in billing() must not crash all_billing()."""
        class ExplodingBillingSource:
            def provider_slug(self) -> str:
                return "exploding"

            def billing(self) -> ProviderBilling:
                raise RuntimeError("billing exploded")

            def fetch_model_prices(self) -> list[ModelPrice]:
                return []

            def fetch_compute_prices(self) -> list[ComputePrice]:
                return []

        catalog = cast(Any, PricingCatalog)(
            sources=[ExplodingBillingSource(), AnthropicSource()]
        )
        # Must not raise; must still include Anthropic
        billing_list = catalog.all_billing()
        providers = {b.provider for b in billing_list}
        assert "anthropic" in providers

    def test_openrouter_network_failure_does_not_crash_catalog(self) -> None:
        """OpenRouter fetch failure must not propagate to catalog consumers."""
        mock_client_instance = MagicMock()
        mock_client_instance.__enter__ = MagicMock(return_value=mock_client_instance)
        mock_client_instance.__exit__ = MagicMock(return_value=False)
        mock_client_instance.get = MagicMock(side_effect=TimeoutError("timeout"))

        with patch("general_ludd.pricing_intel.sources.httpx.Client", return_value=mock_client_instance):
            catalog = PricingCatalog()
            prices = catalog.all_model_prices("openrouter", refresh=True)

        assert prices == []


# ---------------------------------------------------------------------------
# All sources protocol compliance
# ---------------------------------------------------------------------------

class TestAllSourcesProtocolCompliance:
    def test_all_sources_have_provider_slug(self) -> None:
        for src in all_sources():
            slug = src.provider_slug()
            assert isinstance(slug, str) and slug, f"Source {type(src).__name__} has empty slug"

    def test_all_sources_billing_returns_provider_billing(self) -> None:
        for src in all_sources():
            billing = src.billing()
            assert isinstance(billing, ProviderBilling), (
                f"{src.provider_slug()}.billing() must return ProviderBilling"
            )

    def test_all_sources_billing_provider_matches_slug(self) -> None:
        for src in all_sources():
            billing = src.billing()
            assert billing.provider == src.provider_slug(), (
                f"{src.provider_slug()}.billing().provider must match provider_slug()"
            )

    def test_all_sources_billing_has_notes(self) -> None:
        """Every provider must document billing semantics in notes."""
        for src in all_sources():
            billing = src.billing()
            assert billing.notes, (
                f"{src.provider_slug()} billing notes are empty — "
                "billing semantics must be documented"
            )

    def test_all_sources_fetch_model_prices_returns_list(self) -> None:
        """Static sources return list directly; live sources mocked."""
        static_only = [
            AnthropicSource(), OpenAISource(), RunPodSource(),
            LambdaLabsSource(), AWSSource(), GCPSource(),
            HuggingFaceSource(),
        ]
        for src in static_only:
            result = src.fetch_model_prices()
            assert isinstance(result, list), (
                f"{src.provider_slug()}.fetch_model_prices() must return list"
            )

    def test_all_sources_fetch_compute_prices_returns_list(self) -> None:
        static_only = [
            AnthropicSource(), OpenAISource(), RunPodSource(),
            LambdaLabsSource(), AWSSource(), GCPSource(),
            HuggingFaceSource(),
        ]
        for src in static_only:
            result = src.fetch_compute_prices()
            assert isinstance(result, list)

    def test_all_slugs_are_unique(self) -> None:
        slugs = [src.provider_slug() for src in all_sources()]
        assert len(slugs) == len(set(slugs)), "Duplicate provider slugs found"
