"""Structural tests for pricing source implementations."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from general_ludd.pricing_intel.models import (
    BillingGranularity,
    BillingTerms,
    ComputePrice,
    ModelPrice,
)
from general_ludd.pricing_intel.sources import (
    _ANTHROPIC_PRICES_STATIC,
    _AWS_GPU_INSTANCES,
    _GCP_GPU_INSTANCES,
    _LAMBDA_ONDEMAND,
    _OPENAI_PRICES_STATIC,
    _RUNPOD_ONDEMAND,
    _RUNPOD_SPOT,
    AnthropicSource,
    AWSSource,
    GCPSource,
    LambdaLabsSource,
    OpenAISource,
    OpenRouterSource,
    RunPodSource,
    all_sources,
)


class TestOpenRouterSource:
    def test_provider_slug(self) -> None:
        src = OpenRouterSource()
        assert src.provider_slug() == "openrouter"

    def test_billing(self) -> None:
        src = OpenRouterSource()
        b = src.billing()
        assert b.provider == "openrouter"
        assert b.terms == BillingTerms.postpaid_per_use
        assert b.granularity == BillingGranularity.per_token
        assert b.spot_available is False

    def test_fetch_compute_prices_empty(self) -> None:
        assert OpenRouterSource().fetch_compute_prices() == []

    def test_fetch_model_prices_fail_soft_no_network(self) -> None:
        client = MagicMock()
        client.__enter__ = MagicMock(return_value=client)
        client.__exit__ = MagicMock(return_value=False)
        client.get = MagicMock(side_effect=ConnectionError("offline"))
        with patch(
            "general_ludd.pricing_intel.sources.httpx.Client",
            return_value=client,
        ):
            result = OpenRouterSource().fetch_model_prices()
        assert isinstance(result, list)
        assert result == []


class TestAnthropicSource:
    def test_provider_slug(self) -> None:
        assert AnthropicSource().provider_slug() == "anthropic"

    def test_billing(self) -> None:
        b = AnthropicSource().billing()
        assert b.provider == "anthropic"
        assert b.terms == BillingTerms.postpaid_per_use
        assert b.granularity == BillingGranularity.per_token

    def test_fetch_model_prices_returns_static(self) -> None:
        prices = AnthropicSource().fetch_model_prices()
        assert len(prices) == len(_ANTHROPIC_PRICES_STATIC)
        assert all(isinstance(p, ModelPrice) for p in prices)
        assert all(p.provider == "anthropic" for p in prices)

    def test_fetch_model_prices_has_sonnet(self) -> None:
        prices = AnthropicSource().fetch_model_prices()
        model_ids = {p.model_id for p in prices}
        assert "claude-3-5-sonnet-20241022" in model_ids

    def test_fetch_compute_prices_empty(self) -> None:
        assert AnthropicSource().fetch_compute_prices() == []


class TestOpenAISource:
    def test_provider_slug(self) -> None:
        assert OpenAISource().provider_slug() == "openai"

    def test_billing(self) -> None:
        b = OpenAISource().billing()
        assert b.provider == "openai"
        assert b.terms == BillingTerms.postpaid_per_use

    def test_fetch_model_prices_returns_static(self) -> None:
        prices = OpenAISource().fetch_model_prices()
        assert len(prices) == len(_OPENAI_PRICES_STATIC)
        assert all(isinstance(p, ModelPrice) for p in prices)
        assert all(p.provider == "openai" for p in prices)

    def test_fetch_model_prices_has_gpt4o(self) -> None:
        model_ids = {p.model_id for p in OpenAISource().fetch_model_prices()}
        assert "gpt-4o" in model_ids

    def test_fetch_compute_prices_empty(self) -> None:
        assert OpenAISource().fetch_compute_prices() == []


class TestRunPodSource:
    def test_provider_slug(self) -> None:
        assert RunPodSource().provider_slug() == "runpod"

    def test_billing_prepaid(self) -> None:
        b = RunPodSource().billing()
        assert b.provider == "runpod"
        assert b.terms == BillingTerms.prepaid_balance
        assert b.granularity == BillingGranularity.per_second
        assert b.spot_available is True

    def test_fetch_model_prices_empty(self) -> None:
        assert RunPodSource().fetch_model_prices() == []

    def test_fetch_compute_prices_returns_static(self) -> None:
        prices = RunPodSource().fetch_compute_prices()
        expected_count = len(_RUNPOD_ONDEMAND) + len(_RUNPOD_SPOT)
        assert len(prices) == expected_count
        assert all(isinstance(p, ComputePrice) for p in prices)
        assert all(p.provider == "runpod" for p in prices)

    def test_spot_prices_have_spot_true(self) -> None:
        prices = RunPodSource().fetch_compute_prices()
        spot_prices = [p for p in prices if p.spot]
        assert len(spot_prices) == len(_RUNPOD_SPOT)

    def test_per_second_granularity(self) -> None:
        prices = RunPodSource().fetch_compute_prices()
        assert all(p.granularity == BillingGranularity.per_second for p in prices)


class TestLambdaLabsSource:
    def test_provider_slug(self) -> None:
        assert LambdaLabsSource().provider_slug() == "lambda_labs"

    def test_billing_prepaid(self) -> None:
        b = LambdaLabsSource().billing()
        assert b.provider == "lambda_labs"
        assert b.terms == BillingTerms.prepaid_balance
        assert b.granularity == BillingGranularity.per_minute

    def test_fetch_compute_prices_returns_static(self) -> None:
        prices = LambdaLabsSource().fetch_compute_prices()
        assert len(prices) == len(_LAMBDA_ONDEMAND)
        assert all(p.granularity == BillingGranularity.per_minute for p in prices)

    def test_fetch_model_prices_empty(self) -> None:
        assert LambdaLabsSource().fetch_model_prices() == []


class TestAWSSource:
    def test_provider_slug(self) -> None:
        assert AWSSource().provider_slug() == "aws"

    def test_billing_postpaid(self) -> None:
        b = AWSSource().billing()
        assert b.provider == "aws"
        assert b.terms == BillingTerms.postpaid_monthly
        assert b.granularity == BillingGranularity.per_second

    def test_fetch_compute_prices_returns_static(self) -> None:
        prices = AWSSource().fetch_compute_prices()
        assert len(prices) == len(_AWS_GPU_INSTANCES)
        assert all(p.provider == "aws" for p in prices)

    def test_has_spot_prices(self) -> None:
        prices = AWSSource().fetch_compute_prices()
        spot = [p for p in prices if p.spot]
        assert len(spot) >= 2

    def test_fetch_model_prices_empty(self) -> None:
        assert AWSSource().fetch_model_prices() == []


class TestGCPSource:
    def test_provider_slug(self) -> None:
        assert GCPSource().provider_slug() == "gcp"

    def test_billing_postpaid(self) -> None:
        b = GCPSource().billing()
        assert b.provider == "gcp"
        assert b.terms == BillingTerms.postpaid_monthly
        assert b.granularity == BillingGranularity.per_second

    def test_fetch_compute_prices_returns_static(self) -> None:
        prices = GCPSource().fetch_compute_prices()
        assert len(prices) == len(_GCP_GPU_INSTANCES)
        assert all(p.provider == "gcp" for p in prices)

    def test_fetch_model_prices_empty(self) -> None:
        assert GCPSource().fetch_model_prices() == []


class TestAllSources:
    def test_all_sources_returns_list(self) -> None:
        sources = all_sources()
        assert isinstance(sources, list)
        # At least: openrouter, anthropic, openai, runpod, runpod_live, lambda_labs, aws, gcp
        assert len(sources) >= 7

    def test_all_sources_unique_slugs(self) -> None:
        sources = all_sources()
        slugs = [s.provider_slug() for s in sources]
        assert len(slugs) == len(set(slugs))
