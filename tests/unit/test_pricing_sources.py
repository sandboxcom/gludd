"""Tests for pricing_intel.sources: PricingSource implementations."""

from __future__ import annotations

from general_ludd.pricing_intel.models import ProviderBilling
from general_ludd.pricing_intel.sources import (
    AnthropicSource,
    AWSSource,
    GCPSource,
    LambdaLabsSource,
    OpenAISource,
    OpenRouterSource,
    PricingSource,
    RunPodSource,
)


class TestOpenRouterSource:
    def test_provider_slug(self):
        src = OpenRouterSource()
        assert src.provider_slug() == "openrouter"

    def test_billing_returns_provider_billing(self):
        src = OpenRouterSource()
        billing = src.billing()
        assert isinstance(billing, ProviderBilling)
        assert billing.provider == "openrouter"

    def test_fetch_compute_prices_returns_empty(self):
        src = OpenRouterSource()
        assert src.fetch_compute_prices() == []


class TestAnthropicSource:
    def test_provider_slug(self):
        src = AnthropicSource()
        assert src.provider_slug() == "anthropic"

    def test_billing_returns_provider_billing(self):
        src = AnthropicSource()
        billing = src.billing()
        assert isinstance(billing, ProviderBilling)
        assert billing.provider == "anthropic"

    def test_fetch_model_prices_returns_list(self):
        src = AnthropicSource()
        prices = src.fetch_model_prices()
        assert len(prices) > 0
        assert prices[0].provider == "anthropic"

    def test_fetch_compute_prices_returns_empty(self):
        src = AnthropicSource()
        assert src.fetch_compute_prices() == []


class TestOpenAISource:
    def test_provider_slug(self):
        src = OpenAISource()
        assert src.provider_slug() == "openai"

    def test_billing(self):
        src = OpenAISource()
        billing = src.billing()
        assert billing.provider == "openai"

    def test_fetch_model_prices_returns_list(self):
        src = OpenAISource()
        prices = src.fetch_model_prices()
        assert len(prices) > 0
        assert prices[0].provider == "openai"

    def test_fetch_compute_prices_returns_empty(self):
        src = OpenAISource()
        assert src.fetch_compute_prices() == []


class TestRunPodSource:
    def test_provider_slug(self):
        src = RunPodSource()
        assert src.provider_slug() == "runpod"

    def test_billing(self):
        src = RunPodSource()
        billing = src.billing()
        assert billing.provider == "runpod"
        assert billing.spot_available

    def test_fetch_model_prices_returns_empty(self):
        src = RunPodSource()
        assert src.fetch_model_prices() == []

    def test_fetch_compute_prices_returns_list(self):
        src = RunPodSource()
        prices = src.fetch_compute_prices()
        assert len(prices) > 0
        assert prices[0].provider == "runpod"


class TestLambdaLabsSource:
    def test_provider_slug(self):
        src = LambdaLabsSource()
        assert src.provider_slug() == "lambda_labs"

    def test_billing(self):
        src = LambdaLabsSource()
        billing = src.billing()
        assert billing.provider == "lambda_labs"

    def test_fetch_model_prices_returns_empty(self):
        src = LambdaLabsSource()
        assert src.fetch_model_prices() == []

    def test_fetch_compute_prices_returns_list(self):
        src = LambdaLabsSource()
        prices = src.fetch_compute_prices()
        assert len(prices) > 0


class TestAWSSource:
    def test_provider_slug(self):
        src = AWSSource()
        assert src.provider_slug() == "aws"

    def test_billing(self):
        src = AWSSource()
        billing = src.billing()
        assert billing.provider == "aws"
        assert billing.spot_available

    def test_fetch_model_prices_returns_empty(self):
        src = AWSSource()
        assert src.fetch_model_prices() == []

    def test_fetch_compute_prices_returns_list(self):
        src = AWSSource()
        prices = src.fetch_compute_prices()
        assert len(prices) > 0
        assert prices[0].provider == "aws"


class TestGCPSource:
    def test_provider_slug(self):
        src = GCPSource()
        assert src.provider_slug() == "gcp"

    def test_billing(self):
        src = GCPSource()
        billing = src.billing()
        assert billing.provider == "gcp"

    def test_fetch_model_prices_returns_empty(self):
        src = GCPSource()
        assert src.fetch_model_prices() == []

    def test_fetch_compute_prices_returns_list(self):
        src = GCPSource()
        prices = src.fetch_compute_prices()
        assert len(prices) > 0


class TestPricingSourceProtocol:
    def test_openrouter_satisfies_protocol(self):
        assert isinstance(OpenRouterSource(), PricingSource)

    def test_anthropic_satisfies_protocol(self):
        assert isinstance(AnthropicSource(), PricingSource)

    def test_runpod_satisfies_protocol(self):
        assert isinstance(RunPodSource(), PricingSource)

    def test_aws_satisfies_protocol(self):
        assert isinstance(AWSSource(), PricingSource)
