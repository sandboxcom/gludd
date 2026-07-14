"""Tests verifying that every provider has a live pricing fetcher wired via CachedSource.

Covers 8 providers: Anthropic, OpenAI, RunPod, Lambda, AWS, GCP, HuggingFace, Z.AI.
Each live source is tested for:
  - Existence (class is importable, implements PricingSource protocol)
  - Correct provider slug (distinct from static, suffixed with _live)
  - Billing semantics match the static counterpart
  - HTTP fetch is callable and returns [] when credentials are missing (fail-soft)
  - With mocked httpx, parse responses correctly (structural shape assertions)
  - Registered in all_sources() wrapped in CachedSource with correct static fallback
"""

from __future__ import annotations

from typing import Any, ClassVar
from unittest.mock import MagicMock, patch

import pytest

from general_ludd.pricing_intel.models import (
    BillingGranularity,
    BillingTerms,
    ComputePrice,
    ModelPrice,
    ProviderBilling,
)
from general_ludd.pricing_intel.sources import (
    AnthropicSource,
    AWSPricingSource,
    AWSSource,
    CachedSource,
    GCPPricingSource,
    GCPSource,
    HuggingFacePricingSource,
    HuggingFaceSource,
    LambdaLabsPricingSource,
    LambdaLabsSource,
    LiteLLMJSONSource,
    OpenAISource,
    RunPodPricingSource,
    RunPodSource,
    ZAIPricingSource,
    ZAISource,
    all_sources,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_httpx_client(resp_json: dict[str, Any] | None = None,
                        resp_text: str = "",
                        status_code: int = 200) -> MagicMock:
    client = MagicMock()
    client.__enter__ = MagicMock(return_value=client)
    client.__exit__ = MagicMock(return_value=False)
    mock_resp = MagicMock()
    mock_resp.status_code = status_code
    if resp_json is not None:
        mock_resp.json.return_value = resp_json
    if resp_text:
        mock_resp.text = resp_text
    client.get = MagicMock(return_value=mock_resp)
    client.post = MagicMock(return_value=mock_resp)
    return client


# ---------------------------------------------------------------------------
# Anthropic — LiteLLMJSONSource wired with CachedSource
# ---------------------------------------------------------------------------


class TestAnthropicLiveWiring:
    def test_cached_anthropic_registered(self) -> None:
        sources = all_sources()
        litellm_anthropic = [s for s in sources
                             if s.provider_slug() == "litellm_anthropic"]
        assert len(litellm_anthropic) >= 1, (
            "litellm_anthropic must be registered via CachedSource"
        )
        cached = litellm_anthropic[0]
        assert isinstance(cached, CachedSource)
        assert isinstance(cached._live, LiteLLMJSONSource)
        assert cached._live._provider == "anthropic"
        assert isinstance(cached._static, AnthropicSource)

    def test_billing_delegates_to_live(self) -> None:
        src = CachedSource(LiteLLMJSONSource("anthropic"), AnthropicSource())
        b = src.billing()
        assert b.provider == "litellm_anthropic"


# ---------------------------------------------------------------------------
# OpenAI — LiteLLMJSONSource wired with CachedSource
# ---------------------------------------------------------------------------


class TestOpenAILiveWiring:
    def test_cached_openai_registered(self) -> None:
        sources = all_sources()
        litellm_openai = [s for s in sources
                          if s.provider_slug() == "litellm_openai"]
        assert len(litellm_openai) >= 1, (
            "litellm_openai must be registered via CachedSource"
        )
        cached = litellm_openai[0]
        assert isinstance(cached, CachedSource)
        assert isinstance(cached._live, LiteLLMJSONSource)
        assert cached._live._provider == "openai"
        assert isinstance(cached._static, OpenAISource)

    def test_billing_delegates_to_live(self) -> None:
        src = CachedSource(LiteLLMJSONSource("openai"), OpenAISource())
        b = src.billing()
        assert b.provider == "litellm_openai"


# ---------------------------------------------------------------------------
# RunPod — already had RunPodPricingSource; verify CachedSource wiring
# ---------------------------------------------------------------------------


class TestRunPodLiveWiring:
    def test_cached_runpod_registered(self) -> None:
        sources = all_sources()
        runpod_live = [s for s in sources
                       if s.provider_slug() == "runpod_live"]
        assert len(runpod_live) >= 1
        cached = runpod_live[0]
        assert isinstance(cached, CachedSource)
        assert isinstance(cached._live, RunPodPricingSource)
        assert isinstance(cached._static, RunPodSource)


# ---------------------------------------------------------------------------
# Lambda Labs — LambdaLabsPricingSource
# ---------------------------------------------------------------------------

_LAMBDA_API_RESPONSE: dict[str, Any] = {
    "data": {
        "gpu_1x_a10": {
            "instance_type": {
                "name": "gpu_1x_a10",
                "description": "1x A10 GPU",
                "price_cents_per_hour": 75,
                "specs": {"gpus": 1, "memory_gib": 960, "vcpus": 30},
            }
        },
        "gpu_8x_h100_sxm5": {
            "instance_type": {
                "name": "gpu_8x_h100_sxm5",
                "description": "8x H100 SXM5",
                "price_cents_per_hour": 2480,
                "specs": {"gpus": 8},
            }
        },
    }
}


class TestLambdaLabsPricingIdentity:
    def test_provider_slug(self) -> None:
        assert LambdaLabsPricingSource().provider_slug() == "lambda_labs_live"

    def test_billing_terms(self) -> None:
        b = LambdaLabsPricingSource().billing()
        assert isinstance(b, ProviderBilling)
        assert b.provider == "lambda_labs_live"
        assert b.terms == BillingTerms.prepaid_balance
        assert b.granularity == BillingGranularity.per_minute
        assert b.currency == "USD"

    def test_fetch_model_prices_returns_empty(self) -> None:
        assert LambdaLabsPricingSource().fetch_model_prices() == []

    def test_cached_wired_in_all_sources(self) -> None:
        sources = all_sources()
        live = [s for s in sources if s.provider_slug() == "lambda_labs_live"]
        assert len(live) >= 1, "lambda_labs_live must be registered"
        cached = live[0]
        assert isinstance(cached, CachedSource)
        assert isinstance(cached._live, LambdaLabsPricingSource)
        assert isinstance(cached._static, LambdaLabsSource)

    def test_static_still_registered(self) -> None:
        slugs = [s.provider_slug() for s in all_sources()]
        assert "lambda_labs" in slugs, "static lambda_labs must remain registered"


class TestLambdaLabsPricingFetch:
    def test_no_api_key_returns_empty(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("LAMBDA_API_KEY", raising=False)
        with patch(
            "general_ludd.pricing_intel.sources.httpx.Client"
        ) as mock_cls:
            prices = LambdaLabsPricingSource().fetch_compute_prices()
        assert prices == []
        mock_cls.assert_not_called()

    def test_parses_api_response(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("LAMBDA_API_KEY", "test-key")
        mock_client = _mock_httpx_client(resp_json=_LAMBDA_API_RESPONSE)
        with patch(
            "general_ludd.pricing_intel.sources.httpx.Client",
            return_value=mock_client,
        ):
            prices = LambdaLabsPricingSource().fetch_compute_prices()
        assert len(prices) == 2

        a10 = next(p for p in prices if p.sku == "gpu_1x_a10")
        assert a10.provider == "lambda_labs_live"
        assert a10.usd_per_unit == pytest.approx(0.75 / 60.0)
        assert a10.granularity == BillingGranularity.per_minute
        assert a10.gpu_count == 1
        assert a10.spot is False

        h100 = next(p for p in prices if p.sku == "gpu_8x_h100_sxm5")
        assert h100.usd_per_unit == pytest.approx(24.80 / 60.0)
        assert h100.gpu_count == 8

    def test_http_error_returns_empty(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("LAMBDA_API_KEY", "test-key")
        mock_client = _mock_httpx_client(resp_json={}, status_code=500)
        with patch(
            "general_ludd.pricing_intel.sources.httpx.Client",
            return_value=mock_client,
        ):
            prices = LambdaLabsPricingSource().fetch_compute_prices()
        assert prices == []

    def test_empty_data_returns_empty(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("LAMBDA_API_KEY", "test-key")
        mock_client = _mock_httpx_client(resp_json={"data": {}})
        with patch(
            "general_ludd.pricing_intel.sources.httpx.Client",
            return_value=mock_client,
        ):
            prices = LambdaLabsPricingSource().fetch_compute_prices()
        assert prices == []

    def test_skips_zero_price_entries(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("LAMBDA_API_KEY", "test-key")
        resp = {
            "data": {
                "free-gpu": {
                    "instance_type": {
                        "name": "free-gpu",
                        "description": "Free tier",
                        "price_cents_per_hour": 0,
                        "specs": {"gpus": 1},
                    }
                }
            }
        }
        mock_client = _mock_httpx_client(resp_json=resp)
        with patch(
            "general_ludd.pricing_intel.sources.httpx.Client",
            return_value=mock_client,
        ):
            prices = LambdaLabsPricingSource().fetch_compute_prices()
        assert prices == []

    def test_authorization_header_sent(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("LAMBDA_API_KEY", "test-key-abc")
        mock_client = _mock_httpx_client(resp_json={"data": {}})
        with patch(
            "general_ludd.pricing_intel.sources.httpx.Client",
            return_value=mock_client,
        ):
            LambdaLabsPricingSource().fetch_compute_prices()
        call_args = mock_client.get.call_args
        headers = call_args[1].get("headers", {})
        assert headers.get("Authorization") == "Bearer test-key-abc"


# ---------------------------------------------------------------------------
# HuggingFace — HuggingFacePricingSource
# ---------------------------------------------------------------------------

_HF_HTML = """
<html><body>
<h2>Dedicated Endpoints</h2>
<table>
<tr><td>NVIDIA T4 16GB</td><td>$0.60/hr</td></tr>
<tr><td>NVIDIA A100 80GB</td><td>$4.50/hr</td></tr>
<tr><td>NVIDIA H100 80GB</td><td>$11.00/hr</td></tr>
<tr><td>NVIDIA L40S 48GB</td><td>$1.95/hr</td></tr>
</table>
</body></html>
"""


class TestHuggingFacePricingIdentity:
    def test_provider_slug(self) -> None:
        assert HuggingFacePricingSource().provider_slug() == "huggingface_live"

    def test_billing_terms(self) -> None:
        b = HuggingFacePricingSource().billing()
        assert isinstance(b, ProviderBilling)
        assert b.provider == "huggingface_live"
        assert b.granularity == BillingGranularity.per_hour
        assert b.terms == BillingTerms.prepaid_balance
        assert b.spot_available is False

    def test_fetch_model_prices_returns_empty(self) -> None:
        assert HuggingFacePricingSource().fetch_model_prices() == []

    def test_cached_wired_in_all_sources(self) -> None:
        sources = all_sources()
        live = [s for s in sources if s.provider_slug() == "huggingface_live"]
        assert len(live) >= 1, "huggingface_live must be registered"
        cached = live[0]
        assert isinstance(cached, CachedSource)
        assert isinstance(cached._live, HuggingFacePricingSource)
        assert isinstance(cached._static, HuggingFaceSource)

    def test_static_still_registered(self) -> None:
        slugs = [s.provider_slug() for s in all_sources()]
        assert "huggingface" in slugs


class TestHuggingFacePricingFetch:
    def test_parses_html_prices(self) -> None:
        mock_client = _mock_httpx_client(resp_text=_HF_HTML)
        with patch(
            "general_ludd.pricing_intel.sources.httpx.Client",
            return_value=mock_client,
        ):
            prices = HuggingFacePricingSource().fetch_compute_prices()
        assert len(prices) > 0

        for p in prices:
            assert isinstance(p, ComputePrice)
            assert p.provider == "huggingface_live"
            assert p.granularity == BillingGranularity.per_hour
            assert p.spot is False
            assert p.source

        gpu_types = {p.gpu_type for p in prices}
        assert "T4" in " ".join(gpu_types)

    def test_http_error_returns_empty(self) -> None:
        mock_client = _mock_httpx_client(resp_text="", status_code=500)
        with patch(
            "general_ludd.pricing_intel.sources.httpx.Client",
            return_value=mock_client,
        ):
            prices = HuggingFacePricingSource().fetch_compute_prices()
        assert prices == []

    def test_no_matches_returns_empty(self) -> None:
        html = "<html><body>No prices here</body></html>"
        mock_client = _mock_httpx_client(resp_text=html)
        with patch(
            "general_ludd.pricing_intel.sources.httpx.Client",
            return_value=mock_client,
        ):
            prices = HuggingFacePricingSource().fetch_compute_prices()
        assert prices == []

    def test_deduplicates_skus(self) -> None:
        html = """
        <tr><td>NVIDIA A100 80GB</td><td>$4.50/hr</td></tr>
        <tr><td>NVIDIA A100 80GB</td><td>$4.50/hr</td></tr>
        """
        mock_client = _mock_httpx_client(resp_text=html)
        with patch(
            "general_ludd.pricing_intel.sources.httpx.Client",
            return_value=mock_client,
        ):
            prices = HuggingFacePricingSource().fetch_compute_prices()
        assert len(prices) == 1


# ---------------------------------------------------------------------------
# Z.AI — ZAIPricingSource
# ---------------------------------------------------------------------------

_ZAI_HTML = """
<html><body>
<table>
<tr><th>Model</th><th>Input/1M</th><th>Output/1M</th></tr>
<tr><td>GLM-5.2</td><td>$1.4</td><td>$4.4</td></tr>
<tr><td>GLM-5</td><td>$1.0</td><td>$3.2</td></tr>
<tr><td>GLM-4.5</td><td>$0.6</td><td>$2.2</td></tr>
</table>
</body></html>
"""


class TestZAIPricingIdentity:
    def test_provider_slug(self) -> None:
        assert ZAIPricingSource().provider_slug() == "zai_live"

    def test_billing_terms(self) -> None:
        b = ZAIPricingSource().billing()
        assert isinstance(b, ProviderBilling)
        assert b.provider == "zai_live"
        assert b.terms == BillingTerms.postpaid_per_use
        assert b.granularity == BillingGranularity.per_token
        assert b.spot_available is False

    def test_fetch_compute_prices_returns_empty(self) -> None:
        assert ZAIPricingSource().fetch_compute_prices() == []

    def test_cached_wired_in_all_sources(self) -> None:
        sources = all_sources()
        live = [s for s in sources if s.provider_slug() == "zai_live"]
        assert len(live) >= 1, "zai_live must be registered"
        cached = live[0]
        assert isinstance(cached, CachedSource)
        assert isinstance(cached._live, ZAIPricingSource)
        assert isinstance(cached._static, ZAISource)

    def test_static_still_registered(self) -> None:
        slugs = [s.provider_slug() for s in all_sources()]
        assert "zai" in slugs


class TestZAIPricingFetch:
    def test_parses_html_prices(self) -> None:
        mock_client = _mock_httpx_client(resp_text=_ZAI_HTML)
        with patch(
            "general_ludd.pricing_intel.sources.httpx.Client",
            return_value=mock_client,
        ):
            prices = ZAIPricingSource().fetch_model_prices()
        assert len(prices) == 3

        ids = {p.model_id for p in prices}
        assert "glm-5.2" in ids
        assert "glm-5" in ids
        assert "glm-4.5" in ids

        for p in prices:
            assert isinstance(p, ModelPrice)
            assert p.provider == "zai"
            assert p.input_usd_per_1k > 0
            assert p.output_usd_per_1k > 0
            assert p.source
            assert p.fetched_at > 0

        glm52 = next(p for p in prices if p.model_id == "glm-5.2")
        assert glm52.input_usd_per_1k == pytest.approx(1.4 / 1000)
        assert glm52.output_usd_per_1k == pytest.approx(4.4 / 1000)

    def test_http_error_returns_empty(self) -> None:
        mock_client = _mock_httpx_client(resp_text="", status_code=500)
        with patch(
            "general_ludd.pricing_intel.sources.httpx.Client",
            return_value=mock_client,
        ):
            prices = ZAIPricingSource().fetch_model_prices()
        assert prices == []

    def test_no_matches_returns_empty(self) -> None:
        html = "<html><body>No GLM models here</body></html>"
        mock_client = _mock_httpx_client(resp_text=html)
        with patch(
            "general_ludd.pricing_intel.sources.httpx.Client",
            return_value=mock_client,
        ):
            prices = ZAIPricingSource().fetch_model_prices()
        assert prices == []

    def test_deduplicates_models(self) -> None:
        html = "<tr><td>GLM-5.2</td><td>$1.4</td><td>$4.4</td></tr>" * 3
        mock_client = _mock_httpx_client(resp_text=html)
        with patch(
            "general_ludd.pricing_intel.sources.httpx.Client",
            return_value=mock_client,
        ):
            prices = ZAIPricingSource().fetch_model_prices()
        assert len(prices) == 1


# ---------------------------------------------------------------------------
# AWS — already had AWSPricingSource; verify CachedSource wiring
# ---------------------------------------------------------------------------


class TestAWSLiveWiring:
    def test_cached_aws_registered(self) -> None:
        sources = all_sources()
        aws_live = [s for s in sources if s.provider_slug() == "aws_live"]
        assert len(aws_live) >= 1
        cached = aws_live[0]
        assert isinstance(cached, CachedSource)
        assert isinstance(cached._live, AWSPricingSource)
        assert isinstance(cached._static, AWSSource)


# ---------------------------------------------------------------------------
# GCP — already had GCPPricingSource; verify CachedSource wiring
# ---------------------------------------------------------------------------


class TestGCPLiveWiring:
    def test_cached_gcp_registered(self) -> None:
        sources = all_sources()
        gcp_live = [s for s in sources if s.provider_slug() == "gcp_live"]
        assert len(gcp_live) >= 1
        cached = gcp_live[0]
        assert isinstance(cached, CachedSource)
        assert isinstance(cached._live, GCPPricingSource)
        assert isinstance(cached._static, GCPSource)


# ---------------------------------------------------------------------------
# Cross-cutting: all 8 providers have live sources registered as CachedSource
# ---------------------------------------------------------------------------


class TestAllProvidersHaveLiveSources:
    PROVIDER_MAP: ClassVar[dict[str, str]] = {
        "anthropic": "litellm_anthropic",
        "openai": "litellm_openai",
        "runpod": "runpod_live",
        "lambda_labs": "lambda_labs_live",
        "aws": "aws_live",
        "gcp": "gcp_live",
        "huggingface": "huggingface_live",
        "zai": "zai_live",
    }

    def test_each_static_provider_has_live_counterpart(self) -> None:
        sources = all_sources()
        slugs = {s.provider_slug() for s in sources}
        for _static_slug, live_slug in self.PROVIDER_MAP.items():
            assert live_slug in slugs, (
                f"Live slug '{live_slug}' for '{_static_slug}' not in all_sources()"
            )

    def test_each_live_source_is_cached(self) -> None:
        sources = all_sources()
        for s in sources:
            if s.provider_slug() in self.PROVIDER_MAP.values():
                assert isinstance(s, CachedSource), (
                    f"Live source {s.provider_slug()} must be wrapped in CachedSource"
                )

    def test_each_cached_source_has_static_fallback(self) -> None:
        sources = all_sources()
        for s in sources:
            if (s.provider_slug() in self.PROVIDER_MAP.values()
                    and isinstance(s, CachedSource)):
                assert s._static is not None, (
                    f"CachedSource({s.provider_slug()}) must have a static fallback"
                )

    def test_all_eight_providers_have_live(self) -> None:
        sources = all_sources()
        live_slugs = {s.provider_slug() for s in sources}
        expected_live = set(self.PROVIDER_MAP.values())
        missing = expected_live - live_slugs
        assert not missing, f"Missing live sources: {missing}"

    def test_no_todo_integration_in_source_file(self) -> None:
        path = "src/general_ludd/pricing_intel/sources.py"
        with open(path) as f:
            content = f.read()
        for lineno, line in enumerate(content.splitlines(), 1):
            assert "TODO(integration)" not in line, (
                f"{path}:{lineno} still has TODO(integration): {line.strip()}"
            )
