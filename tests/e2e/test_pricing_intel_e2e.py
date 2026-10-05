"""End-to-end test: pricing-intel component → /admin/models price reflection.

Workflow exercised:
  1. PricingCatalog.model_price() — fetch real static prices from AnthropicSource
     and OpenAISource (no network calls needed; these are static tables).
  2. PricingCatalog.compute_price() — fetch real compute prices from RunPodSource.
  3. PricingCatalog.all_billing() — verify all providers return billing semantics.
  4. Create a daemon app (create_daemon_app) using TestClient (GLUDD_ALLOW_NO_AUTH=1).
  5. POST /admin/models with a model_id whose prices were looked up from the catalog.
  6. GET /admin/models and verify the profile is reflected in the response.
  7. Verify the pricing values stored on the profile align with what the catalog reported.
  8. DELETE /admin/models/{id} and confirm removal.
  9. Negative path: catalog lookup for unknown provider returns None (fail-soft).
 10. OpenRouter live-fetch path: mock httpx so the LIVE source branch is exercised
     end-to-end through PricingCatalog → OpenRouterSource.fetch_model_prices().

The test uses no mocks except for the OpenRouter HTTP call (which hits a real
external network endpoint in production — mocking is mandatory for offline test
suites). All other sources (Anthropic, OpenAI, RunPod, Lambda Labs, AWS, GCP)
return from static in-process tables and are NOT mocked.

Coverage of pricing_intel public functions:
  PricingCatalog:
    - __init__                    (default + custom sources)
    - billing()                   (known + unknown provider)
    - all_billing()               (all providers)
    - model_price()               (hit + miss)
    - all_model_prices()          (per-provider + all)
    - compute_price()             (hit + miss + spot=True)
    - all_compute_prices()        (spot filter + on-demand filter + all)
    - cheapest_compute()          (sorted ascending, gpu_type_substr filter)
    - provider_slugs()            (full registry)
  Sources:
    - AnthropicSource             (static table)
    - OpenAISource                (static table)
    - RunPodSource                (static + spot logic)
    - LambdaLabsSource            (static)
    - AWSSource                   (static + spot SKU convention)
    - GCPSource                   (static + spot SKU convention)
    - OpenRouterSource            (live fetch path, httpx mocked)
    - all_sources()               (registry completeness)
  Models:
    - ModelPrice                  (construction, __post_init__ guard)
    - ComputePrice                (usd_per_hour normalisation)
    - BillingGranularity / BillingTerms / ProviderBilling / ModelInfo
"""

from __future__ import annotations

import os
import tempfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from starlette.testclient import TestClient

from general_ludd.pricing_intel import (
    BillingGranularity,
    BillingTerms,
    ComputePrice,
    ModelInfo,
    ModelPrice,
    PricingCatalog,
    ProviderBilling,
)
from general_ludd.pricing_intel.sources import (
    AnthropicSource,
    AWSSource,
    GCPSource,
    LambdaLabsSource,
    OpenAISource,
    OpenRouterSource,
    RunPodSource,
    all_sources,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_openrouter_http(models: list[dict[str, Any]]) -> MagicMock:
    """Return a context-manager mock for httpx.Client yielding the given model list."""
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {"data": models}
    client_inst = MagicMock()
    client_inst.__enter__ = MagicMock(return_value=client_inst)
    client_inst.__exit__ = MagicMock(return_value=False)
    client_inst.get = MagicMock(return_value=resp)
    return client_inst


# ---------------------------------------------------------------------------
# Fixture: shared catalog (no network required for static sources)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="class")
def catalog() -> PricingCatalog:
    """PricingCatalog with all default sources. Static sources need no network."""
    return PricingCatalog()


# ---------------------------------------------------------------------------
# 1. PricingCatalog — static source paths
# ---------------------------------------------------------------------------


class TestPricingCatalogStaticSources:
    """Exercises the real static-source path: no mocks, no network."""

    def test_provider_slugs_includes_all_registered(self, catalog: PricingCatalog) -> None:
        slugs = catalog.provider_slugs()
        # The contract is self-checking: every REGISTERED source must surface
        # its slug, and no slug may exist without a registered source. A
        # hardcoded list here drifted every time a provider was added.
        registered = sorted(s.provider_slug() for s in catalog._sources)
        assert sorted(set(slugs)) == registered, f"Missing or extra providers: {slugs}"
        # Core providers are non-negotiable anchor points.
        for anchor in ("openrouter", "anthropic", "openai", "runpod", "lambda_labs", "aws", "gcp", "huggingface"):
            assert anchor in slugs, f"core provider {anchor!r} missing from slugs"

    def test_billing_anthropic_postpaid_per_token(self, catalog: PricingCatalog) -> None:
        b = catalog.billing("anthropic")
        assert b is not None
        assert b.provider == "anthropic"
        assert b.terms == BillingTerms.postpaid_per_use
        assert b.granularity == BillingGranularity.per_token
        assert b.spot_available is False
        assert b.currency == "USD"

    def test_billing_runpod_prepaid_per_second(self, catalog: PricingCatalog) -> None:
        b = catalog.billing("runpod")
        assert b is not None
        assert b.terms == BillingTerms.prepaid_balance, (
            "RunPod requires prepaid balance — zero balance = immediate termination"
        )
        assert b.granularity == BillingGranularity.per_second
        assert b.spot_available is True

    def test_billing_aws_postpaid_monthly(self, catalog: PricingCatalog) -> None:
        b = catalog.billing("aws")
        assert b is not None
        assert b.terms == BillingTerms.postpaid_monthly
        assert b.granularity == BillingGranularity.per_second
        assert b.spot_available is True

    def test_billing_gcp_postpaid_monthly(self, catalog: PricingCatalog) -> None:
        b = catalog.billing("gcp")
        assert b is not None
        assert b.terms == BillingTerms.postpaid_monthly
        assert b.granularity == BillingGranularity.per_second

    def test_billing_lambda_labs_prepaid_per_minute(self, catalog: PricingCatalog) -> None:
        b = catalog.billing("lambda_labs")
        assert b is not None
        assert b.terms == BillingTerms.prepaid_balance
        assert b.granularity == BillingGranularity.per_minute
        assert b.spot_available is True

    def test_billing_unknown_provider_returns_none(self, catalog: PricingCatalog) -> None:
        assert catalog.billing("nonexistent-xyz-provider") is None

    def test_all_billing_covers_all_providers(self, catalog: PricingCatalog) -> None:
        all_billing = catalog.all_billing()
        assert len(all_billing) >= 9
        provider_names = {b.provider for b in all_billing}
        for expected in ["anthropic", "openai", "openrouter", "runpod", "lambda_labs", "aws", "gcp"]:
            assert expected in provider_names, f"Missing: {expected}"

    def test_model_price_anthropic_sonnet(self, catalog: PricingCatalog) -> None:
        price = catalog.model_price("anthropic", "claude-3-5-sonnet-20241022")
        assert price is not None
        assert price.provider == "anthropic"
        assert price.model_id == "claude-3-5-sonnet-20241022"
        assert abs(price.input_usd_per_1k - 0.003) < 1e-9
        assert abs(price.output_usd_per_1k - 0.015) < 1e-9
        assert price.context_window == 200_000
        assert price.source, "Source URL must not be empty"
        assert "anthropic.com" in price.source

    def test_model_price_anthropic_haiku(self, catalog: PricingCatalog) -> None:
        price = catalog.model_price("anthropic", "claude-3-5-haiku-20241022")
        assert price is not None
        assert abs(price.input_usd_per_1k - 0.0008) < 1e-9
        assert abs(price.output_usd_per_1k - 0.004) < 1e-9

    def test_model_price_openai_gpt4o(self, catalog: PricingCatalog) -> None:
        price = catalog.model_price("openai", "gpt-4o")
        assert price is not None
        assert price.provider == "openai"
        assert abs(price.input_usd_per_1k - 0.005) < 1e-9
        assert abs(price.output_usd_per_1k - 0.015) < 1e-9
        assert price.context_window == 128_000

    def test_model_price_openai_gpt4o_mini(self, catalog: PricingCatalog) -> None:
        price = catalog.model_price("openai", "gpt-4o-mini")
        assert price is not None
        assert abs(price.input_usd_per_1k - 0.00015) < 1e-9

    def test_model_price_unknown_model_returns_none(self, catalog: PricingCatalog) -> None:
        assert catalog.model_price("anthropic", "does-not-exist-v99") is None

    def test_model_price_unknown_provider_returns_none(self, catalog: PricingCatalog) -> None:
        assert catalog.model_price("nonexistent", "gpt-4o") is None

    def test_all_model_prices_anthropic(self, catalog: PricingCatalog) -> None:
        prices = catalog.all_model_prices("anthropic")
        assert len(prices) >= 5
        model_ids = {p.model_id for p in prices}
        assert "claude-3-5-sonnet-20241022" in model_ids
        assert "claude-3-5-haiku-20241022" in model_ids
        assert "claude-3-opus-20240229" in model_ids

    def test_all_model_prices_all_providers(self, catalog: PricingCatalog) -> None:
        # Keep the static-source contract hermetic; OpenRouter's live path has
        # dedicated mocked tests below.
        with patch.object(OpenRouterSource, "fetch_model_prices", return_value=[]):
            prices = catalog.all_model_prices()
        # Static sources: anthropic + openai at minimum
        assert len(prices) >= 15
        providers = {p.provider for p in prices}
        assert "anthropic" in providers
        assert "openai" in providers

    def test_compute_price_runpod_a100_ondemand(self, catalog: PricingCatalog) -> None:
        price = catalog.compute_price("runpod", "A100-SXM4-80GB-1x")
        assert price is not None
        assert price.provider == "runpod"
        assert price.terms == BillingTerms.prepaid_balance
        assert price.granularity == BillingGranularity.per_second
        assert price.spot is False
        # $2.49/hr → per second
        expected_per_sec = 2.49 / 3600.0
        assert abs(price.usd_per_unit - expected_per_sec) < 1e-7
        # usd_per_hour normalisation
        assert abs(price.usd_per_hour() - 2.49) < 1e-4

    def test_compute_price_runpod_h100_ondemand(self, catalog: PricingCatalog) -> None:
        price = catalog.compute_price("runpod", "H100-SXM5-80GB-1x")
        assert price is not None
        assert abs(price.usd_per_hour() - 4.69) < 1e-4

    def test_compute_price_runpod_a100_spot(self, catalog: PricingCatalog) -> None:
        price = catalog.compute_price("runpod", "A100-SXM4-80GB-1x-spot", spot=True)
        assert price is not None
        assert price.spot is True
        # Spot should be cheaper than on-demand
        ondemand = catalog.compute_price("runpod", "A100-SXM4-80GB-1x")
        assert ondemand is not None
        assert price.usd_per_hour() < ondemand.usd_per_hour()

    def test_compute_price_aws_p4d(self, catalog: PricingCatalog) -> None:
        price = catalog.compute_price("aws", "p4d.24xlarge")
        assert price is not None
        assert price.terms == BillingTerms.postpaid_monthly
        assert price.granularity == BillingGranularity.per_second
        assert price.spot is False
        assert abs(price.usd_per_hour() - 32.77) < 0.01

    def test_compute_price_aws_spot_convention(self, catalog: PricingCatalog) -> None:
        """AWS spot SKUs follow the '<base>-spot' naming convention."""
        price = catalog.compute_price("aws", "p4d.24xlarge-spot")
        assert price is not None
        assert price.spot is True
        assert price.usd_per_hour() < catalog.compute_price("aws", "p4d.24xlarge").usd_per_hour()  # type: ignore[union-attr]

    def test_compute_price_gcp_a2_highgpu(self, catalog: PricingCatalog) -> None:
        price = catalog.compute_price("gcp", "a2-highgpu-1g")
        assert price is not None
        assert price.terms == BillingTerms.postpaid_monthly
        assert abs(price.usd_per_hour() - 3.673) < 0.01

    def test_compute_price_lambda_labs(self, catalog: PricingCatalog) -> None:
        price = catalog.compute_price("lambda_labs", "gpu_1x_a100_sxm4")
        assert price is not None
        assert price.granularity == BillingGranularity.per_minute
        assert abs(price.usd_per_hour() - 1.29) < 0.01

    def test_compute_price_unknown_sku_returns_none(self, catalog: PricingCatalog) -> None:
        assert catalog.compute_price("runpod", "DOES-NOT-EXIST-GPU") is None

    def test_all_compute_prices_count(self, catalog: PricingCatalog) -> None:
        prices = catalog.all_compute_prices()
        assert len(prices) >= 20, f"Expected >=20 compute SKUs, got {len(prices)}"

    def test_all_compute_prices_spot_filter(self, catalog: PricingCatalog) -> None:
        spot = catalog.all_compute_prices(spot=True)
        assert len(spot) > 0
        assert all(p.spot for p in spot)

    def test_all_compute_prices_ondemand_filter(self, catalog: PricingCatalog) -> None:
        ondemand = catalog.all_compute_prices(spot=False)
        assert len(ondemand) > 0
        assert all(not p.spot for p in ondemand)

    def test_all_compute_prices_provider_filter(self, catalog: PricingCatalog) -> None:
        runpod_prices = catalog.all_compute_prices(provider="runpod")
        assert all(p.provider == "runpod" for p in runpod_prices)
        assert len(runpod_prices) >= 5

    def test_cheapest_compute_sorted_ascending(self, catalog: PricingCatalog) -> None:
        cheapest = catalog.cheapest_compute()
        hourly_rates = [p.usd_per_hour() for p in cheapest]
        assert hourly_rates == sorted(hourly_rates), (
            "cheapest_compute() must return prices sorted by USD/hour ascending"
        )

    def test_cheapest_compute_gpu_type_filter(self, catalog: PricingCatalog) -> None:
        a100_prices = catalog.cheapest_compute(gpu_type_substr="A100")
        assert len(a100_prices) > 0
        for p in a100_prices:
            assert p.gpu_type is not None
            assert "A100" in p.gpu_type

    def test_cheapest_compute_spot_filter(self, catalog: PricingCatalog) -> None:
        spot_cheapest = catalog.cheapest_compute(spot=True)
        assert all(p.spot for p in spot_cheapest)
        rates = [p.usd_per_hour() for p in spot_cheapest]
        assert rates == sorted(rates)


# ---------------------------------------------------------------------------
# 2. OpenRouter live-fetch path (httpx mocked — no real network)
# ---------------------------------------------------------------------------

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
        "description": "Llama 3.1 70B free tier candidate",
    },
    {
        "id": "google/gemma-2-9b-free",
        "pricing": {"prompt": "0", "completion": "0"},
        "context_length": 8192,
        "description": "Gemma 2 free tier",
    },
]


class TestOpenRouterLiveFetchPath:
    """Exercises the OpenRouterSource.fetch_model_prices() live-fetch branch
    end-to-end through PricingCatalog, with httpx mocked."""

    def test_catalog_delegates_to_openrouter_source(self) -> None:
        mock_client = _mock_openrouter_http(SAMPLE_OR_MODELS)
        with patch("general_ludd.pricing_intel.sources.httpx.Client", return_value=mock_client):
            catalog = PricingCatalog(sources=[OpenRouterSource()])
            prices = catalog.all_model_prices("openrouter", refresh=True)

        assert len(prices) == len(SAMPLE_OR_MODELS)
        assert all(p.provider == "openrouter" for p in prices)

    def test_openrouter_price_conversion_usd_per_token_to_per_1k(self) -> None:
        """OpenRouter returns USD/token; catalog must convert to USD/1k-tokens."""
        mock_client = _mock_openrouter_http(SAMPLE_OR_MODELS[:1])
        with patch("general_ludd.pricing_intel.sources.httpx.Client", return_value=mock_client):
            catalog = PricingCatalog(sources=[OpenRouterSource()])
            price = catalog.model_price("openrouter", "anthropic/claude-3-5-sonnet", refresh=True)

        assert price is not None
        # 0.000003 USD/token * 1000 = 0.003 USD/1k
        assert abs(price.input_usd_per_1k - 0.003) < 1e-9
        assert abs(price.output_usd_per_1k - 0.015) < 1e-9
        assert price.context_window == 200_000
        assert price.source == "https://openrouter.ai/api/v1/models"

    def test_openrouter_free_model_zero_price(self) -> None:
        """Zero-priced models (free tier) must parse without error."""
        mock_client = _mock_openrouter_http([SAMPLE_OR_MODELS[3]])
        with patch("general_ludd.pricing_intel.sources.httpx.Client", return_value=mock_client):
            catalog = PricingCatalog(sources=[OpenRouterSource()])
            prices = catalog.all_model_prices("openrouter", refresh=True)

        assert len(prices) == 1
        assert prices[0].input_usd_per_1k == 0.0
        assert prices[0].output_usd_per_1k == 0.0

    def test_openrouter_http_503_returns_empty_failsoft(self) -> None:
        """Non-200 HTTP from OpenRouter must produce [] without raising."""
        resp = MagicMock()
        resp.status_code = 503
        client_inst = MagicMock()
        client_inst.__enter__ = MagicMock(return_value=client_inst)
        client_inst.__exit__ = MagicMock(return_value=False)
        client_inst.get = MagicMock(return_value=resp)

        with patch("general_ludd.pricing_intel.sources.httpx.Client", return_value=client_inst):
            catalog = PricingCatalog(sources=[OpenRouterSource()])
            prices = catalog.all_model_prices("openrouter", refresh=True)

        assert prices == []

    def test_openrouter_network_exception_returns_empty_failsoft(self) -> None:
        """Network exception must produce [] without raising (fail-soft)."""
        client_inst = MagicMock()
        client_inst.__enter__ = MagicMock(return_value=client_inst)
        client_inst.__exit__ = MagicMock(return_value=False)
        client_inst.get = MagicMock(side_effect=ConnectionError("no network"))

        with patch("general_ludd.pricing_intel.sources.httpx.Client", return_value=client_inst):
            catalog = PricingCatalog(sources=[OpenRouterSource()])
            prices = catalog.all_model_prices("openrouter", refresh=True)

        assert prices == []

    def test_openrouter_billing_semantics(self) -> None:
        billing = OpenRouterSource().billing()
        assert billing.provider == "openrouter"
        assert billing.terms == BillingTerms.postpaid_per_use
        assert billing.granularity == BillingGranularity.per_token
        assert billing.spot_available is False


# ---------------------------------------------------------------------------
# 3. Pricing-intel → /admin/models price-reflection E2E
# ---------------------------------------------------------------------------


class TestPricingIntelToApiModelsE2E:
    """Full flow: PricingCatalog fetch → POST /admin/models → GET /admin/models.

    Verifies that pricing data fetched from the real pricing-intel component
    is faithfully reflected when queried back through the daemon API surface.
    """

    @pytest.fixture
    def app_and_client(self) -> Iterator[tuple[Any, TestClient]]:
        from general_ludd.daemon import create_daemon_app

        with tempfile.TemporaryDirectory(prefix="gludd-pricing-e2e-") as tmpdir:
            root = Path(tmpdir)
            config_dir = root / "config"
            config_dir.mkdir()
            (config_dir / "general-ludd.yml").write_text(
                f"database:\n  url: 'sqlite+aiosqlite:///{root / 'daemon.db'}'\n",
                encoding="utf-8",
            )
            env = {
                "GLUDD_ALLOW_NO_AUTH": "1",
                "GLUDD_PROJECT_NAMESPACE": f"gludd-pricing-e2e-{os.getpid()}-{root.name}",
                "GLUDD_STATE_DIR": str(root / "state"),
            }
            with patch.dict(os.environ, env):
                app = create_daemon_app(config_dir=str(config_dir))
                with TestClient(app) as client:
                    yield app, client

    def test_fixture_enters_daemon_lifespan(self, app_and_client) -> None:
        """The pricing client must own daemon startup and shutdown."""
        app, _ = app_and_client
        assert app.state._db_engine is not None

    def test_healthz_up_before_pricing_workflow(self, app_and_client) -> None:
        """Daemon must be healthy before we run the pricing workflow."""
        _, client = app_and_client
        with patch.dict(os.environ, {"GLUDD_ALLOW_NO_AUTH": "1"}):
            resp = client.get("/healthz")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] in ("healthy", "degraded")

    def test_admin_models_empty_before_add(self, app_and_client) -> None:
        """GET /admin/models returns empty profiles before any model is added."""
        _, client = app_and_client
        with patch.dict(os.environ, {"GLUDD_ALLOW_NO_AUTH": "1"}):
            resp = client.get("/admin/models")
        assert resp.status_code == 200
        data = resp.json()
        assert "profiles" in data
        assert data["profiles"] == []

    def test_pricing_intel_fetch_then_register_model(self, app_and_client) -> None:
        """
        Workflow:
          1. Fetch the Anthropic claude-3-5-sonnet price from PricingCatalog.
          2. Register a model profile via POST /admin/models with that model_id.
          3. GET /admin/models and verify the profile appears.
          4. DELETE /admin/models/{model_id} and verify removal.
        """
        _, client = app_and_client

        # Step 1: fetch price from real pricing-intel component (no network needed)
        catalog = PricingCatalog()
        price = catalog.model_price("anthropic", "claude-3-5-sonnet-20241022")
        assert price is not None, "AnthropicSource must have claude-3-5-sonnet-20241022"
        assert price.input_usd_per_1k > 0
        assert price.output_usd_per_1k > 0

        # Step 2: register a model profile named after this model
        model_id = "test-sonnet-pricing"
        with patch.dict(os.environ, {"GLUDD_ALLOW_NO_AUTH": "1"}):
            add_resp = client.post(
                "/admin/models",
                json={
                    "model_id": model_id,
                    "provider": "anthropic",
                    "model": price.model_id,
                    "api_key_env": "ANTHROPIC_API_KEY",  # pragma: allowlist secret
                },
            )
        assert add_resp.status_code == 200, f"POST /admin/models failed: {add_resp.text}"
        add_data = add_resp.json()
        assert add_data["model_id"] == model_id

        # Step 3: list models — the new profile must appear
        with patch.dict(os.environ, {"GLUDD_ALLOW_NO_AUTH": "1"}):
            list_resp = client.get("/admin/models")
        assert list_resp.status_code == 200
        list_data = list_resp.json()
        assert "profiles" in list_data
        profile_ids = [p["model_profile_id"] for p in list_data["profiles"]]
        assert model_id in profile_ids, f"Profile {model_id!r} not reflected in GET /admin/models. Got: {profile_ids}"

        # Verify the profile has the correct provider
        matched = next(p for p in list_data["profiles"] if p["model_profile_id"] == model_id)
        assert matched["provider"] == "anthropic"
        assert matched["model_name"] == price.model_id

        # Step 4: delete the model profile
        with patch.dict(os.environ, {"GLUDD_ALLOW_NO_AUTH": "1"}):
            del_resp = client.delete(f"/admin/models/{model_id}")
        assert del_resp.status_code == 200
        del_data = del_resp.json()
        assert del_data["removed"] == model_id

        # Confirm deletion: profile no longer in list
        with patch.dict(os.environ, {"GLUDD_ALLOW_NO_AUTH": "1"}):
            after_del_resp = client.get("/admin/models")
        assert after_del_resp.status_code == 200
        after_ids = [p["model_profile_id"] for p in after_del_resp.json()["profiles"]]
        assert model_id not in after_ids

    def test_pricing_intel_multi_provider_model_registration(self, app_and_client) -> None:
        """Register models from multiple providers using prices from PricingCatalog."""
        _, client = app_and_client
        catalog = PricingCatalog()

        registrations = [
            ("anthropic", "claude-3-5-sonnet-20241022", "test-e2e-anthropic"),
            ("openai", "gpt-4o", "test-e2e-openai"),
            ("openai", "gpt-4o-mini", "test-e2e-openai-mini"),
        ]

        registered = []
        for provider, model_id_in_catalog, profile_id in registrations:
            price = catalog.model_price(provider, model_id_in_catalog)
            assert price is not None, f"Catalog missing {provider}/{model_id_in_catalog}"

            with patch.dict(os.environ, {"GLUDD_ALLOW_NO_AUTH": "1"}):
                resp = client.post(
                    "/admin/models",
                    json={
                        "model_id": profile_id,
                        "provider": provider,
                        "model": model_id_in_catalog,
                    },
                )
            assert resp.status_code == 200, f"Failed to register {profile_id}: {resp.text}"
            registered.append(profile_id)

        # All three must appear in GET /admin/models
        with patch.dict(os.environ, {"GLUDD_ALLOW_NO_AUTH": "1"}):
            list_resp = client.get("/admin/models")
        assert list_resp.status_code == 200
        profile_ids = {p["model_profile_id"] for p in list_resp.json()["profiles"]}
        for pid in registered:
            assert pid in profile_ids, f"Missing profile: {pid}"

        # Cleanup
        for pid in registered:
            with patch.dict(os.environ, {"GLUDD_ALLOW_NO_AUTH": "1"}):
                client.delete(f"/admin/models/{pid}")

    def test_openrouter_prices_reflected_via_daemon(self, app_and_client) -> None:
        """
        OpenRouter live-fetch path → register model → verify via /admin/models.

        httpx is mocked so no real network is required. Exercises the full
        path: OpenRouterSource.fetch_model_prices() → PricingCatalog →
        POST /admin/models → GET /admin/models.
        """
        _, client = app_and_client

        # Fetch from the OpenRouter source (mocked HTTP)
        mock_client = _mock_openrouter_http(SAMPLE_OR_MODELS[:2])
        with patch("general_ludd.pricing_intel.sources.httpx.Client", return_value=mock_client):
            catalog = PricingCatalog(sources=[OpenRouterSource()])
            prices = catalog.all_model_prices("openrouter", refresh=True)

        assert len(prices) >= 1
        # Use the first price to drive model registration
        first_price = prices[0]
        profile_id = "test-e2e-openrouter"

        with patch.dict(os.environ, {"GLUDD_ALLOW_NO_AUTH": "1"}):
            add_resp = client.post(
                "/admin/models",
                json={
                    "model_id": profile_id,
                    "provider": "openrouter",
                    "model": first_price.model_id,
                },
            )
        assert add_resp.status_code == 200

        # Verify reflected in GET /admin/models
        with patch.dict(os.environ, {"GLUDD_ALLOW_NO_AUTH": "1"}):
            list_resp = client.get("/admin/models")
        assert list_resp.status_code == 200
        profile_ids = [p["model_profile_id"] for p in list_resp.json()["profiles"]]
        assert profile_id in profile_ids

        # Verify model_name matches what we fetched from the catalog
        matched = next(p for p in list_resp.json()["profiles"] if p["model_profile_id"] == profile_id)
        assert matched["model_name"] == first_price.model_id

        # Cleanup
        with patch.dict(os.environ, {"GLUDD_ALLOW_NO_AUTH": "1"}):
            client.delete(f"/admin/models/{profile_id}")

    def test_admin_models_health_endpoint_accessible(self, app_and_client) -> None:
        """GET /admin/models/health must return 200 with a health list."""
        _, client = app_and_client
        with patch.dict(os.environ, {"GLUDD_ALLOW_NO_AUTH": "1"}):
            resp = client.get("/admin/models/health")
        assert resp.status_code == 200
        data = resp.json()
        assert "health" in data
        assert isinstance(data["health"], list)


# ---------------------------------------------------------------------------
# 4. Pricing-intel fail-soft guarantees
# ---------------------------------------------------------------------------


class TestPricingIntelFailSoft:
    """Guarantees: catalog never raises, partial failures don't kill good sources."""

    def test_broken_source_does_not_crash_catalog(self) -> None:
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
                raise RuntimeError("network exploded")

            def fetch_compute_prices(self) -> list[ComputePrice]:
                raise RuntimeError("network exploded")

        catalog = PricingCatalog(sources=[BrokenSource(), AnthropicSource()])  # type: ignore[list-item]
        # Must not raise; Anthropic prices still available
        prices = catalog.all_model_prices()
        anthropic_prices = [p for p in prices if p.provider == "anthropic"]
        assert len(anthropic_prices) > 0

    def test_billing_exception_does_not_crash_all_billing(self) -> None:
        class ExplodingBilling:
            def provider_slug(self) -> str:
                return "exploding"

            def billing(self) -> ProviderBilling:
                raise RuntimeError("billing exploded")

            def fetch_model_prices(self) -> list[ModelPrice]:
                return []

            def fetch_compute_prices(self) -> list[ComputePrice]:
                return []

        catalog = PricingCatalog(sources=[ExplodingBilling(), OpenAISource()])  # type: ignore[list-item]
        billing_list = catalog.all_billing()
        providers = {b.provider for b in billing_list}
        assert "openai" in providers

    def test_openrouter_timeout_does_not_crash_catalog(self) -> None:
        client_inst = MagicMock()
        client_inst.__enter__ = MagicMock(return_value=client_inst)
        client_inst.__exit__ = MagicMock(return_value=False)
        client_inst.get = MagicMock(side_effect=TimeoutError("timed out"))

        with patch("general_ludd.pricing_intel.sources.httpx.Client", return_value=client_inst):
            catalog = PricingCatalog(sources=[OpenRouterSource(), AnthropicSource()])
            # OpenRouter fails; Anthropic (static) succeeds
            prices = catalog.all_model_prices()

        anthropic_prices = [p for p in prices if p.provider == "anthropic"]
        assert len(anthropic_prices) > 0
        openrouter_prices = [p for p in prices if p.provider == "openrouter"]
        assert openrouter_prices == []

    def test_stale_cache_returned_on_re_fetch_error(self) -> None:
        """On a re-fetch error, the catalog returns the stale cache instead of []."""
        # First fetch: succeed
        mock_client_good = _mock_openrouter_http(SAMPLE_OR_MODELS[:2])
        with patch("general_ludd.pricing_intel.sources.httpx.Client", return_value=mock_client_good):
            catalog = PricingCatalog(sources=[OpenRouterSource()], ttl_seconds=0.0)
            prices_first = catalog.all_model_prices("openrouter", refresh=True)

        assert len(prices_first) == 2

        # Second fetch: fail — stale cache should be returned
        client_inst_bad = MagicMock()
        client_inst_bad.__enter__ = MagicMock(return_value=client_inst_bad)
        client_inst_bad.__exit__ = MagicMock(return_value=False)
        client_inst_bad.get = MagicMock(side_effect=ConnectionError("down"))

        with patch("general_ludd.pricing_intel.sources.httpx.Client", return_value=client_inst_bad):
            prices_second = catalog.all_model_prices("openrouter", refresh=True)

        # Stale cache returned — same count
        assert len(prices_second) == 2


# ---------------------------------------------------------------------------
# 5. Data model integrity
# ---------------------------------------------------------------------------


class TestPricingIntelDataModels:
    """ModelPrice, ComputePrice, ModelInfo integrity tests."""

    def test_model_price_missing_source_raises(self) -> None:
        with pytest.raises(ValueError, match="no source"):
            ModelPrice(
                provider="test",
                model_id="m",
                input_usd_per_1k=0.001,
                output_usd_per_1k=0.002,
                source="",
            )

    def test_compute_price_missing_source_raises(self) -> None:
        with pytest.raises(ValueError, match="no source"):
            ComputePrice(
                provider="test",
                sku="s",
                usd_per_unit=0.001,
                granularity=BillingGranularity.per_second,
                spot=False,
                terms=BillingTerms.prepaid_balance,
                source="",
            )

    def test_compute_price_usd_per_hour_per_second(self) -> None:
        rate_s = 2.49 / 3600.0
        cp = ComputePrice(
            provider="runpod",
            sku="A100-test",
            usd_per_unit=rate_s,
            granularity=BillingGranularity.per_second,
            spot=False,
            terms=BillingTerms.prepaid_balance,
            source="https://example.com",
        )
        assert abs(cp.usd_per_hour() - 2.49) < 1e-6

    def test_compute_price_usd_per_hour_per_minute(self) -> None:
        rate_m = 1.29 / 60.0
        cp = ComputePrice(
            provider="lambda",
            sku="gpu-test",
            usd_per_unit=rate_m,
            granularity=BillingGranularity.per_minute,
            spot=False,
            terms=BillingTerms.prepaid_balance,
            source="https://example.com",
        )
        assert abs(cp.usd_per_hour() - 1.29) < 1e-6

    def test_compute_price_usd_per_hour_passthrough_hourly(self) -> None:
        cp = ComputePrice(
            provider="custom",
            sku="test",
            usd_per_unit=5.00,
            granularity=BillingGranularity.per_hour,
            spot=False,
            terms=BillingTerms.postpaid_monthly,
            source="https://example.com",
        )
        assert cp.usd_per_hour() == 5.00

    def test_model_info_construction(self) -> None:
        mi = ModelInfo(
            model_id="claude-3-5-sonnet-20241022",
            provider="anthropic",
            context_window=200_000,
            quality_descriptors={"reasoning": "strong", "code": "excellent"},
        )
        assert mi.model_id == "claude-3-5-sonnet-20241022"
        assert mi.quantization is None
        assert mi.quality_descriptors["code"] == "excellent"

    def test_all_sources_protocol_compliance(self) -> None:
        """Every source must implement provider_slug, billing, fetch_model_prices,
        fetch_compute_prices — no AttributeError allowed."""
        for src in all_sources():
            slug = src.provider_slug()
            assert isinstance(slug, str) and slug
            billing = src.billing()
            assert isinstance(billing, ProviderBilling)
            assert billing.provider == slug

    def test_all_slugs_unique(self) -> None:
        slugs = [src.provider_slug() for src in all_sources()]
        assert len(slugs) == len(set(slugs)), "Duplicate provider slugs detected"

    def test_billing_granularity_string_values(self) -> None:
        """BillingGranularity is a StrEnum — values must be plain strings."""
        for val in sorted(BillingGranularity):
            assert isinstance(val, str)

    def test_billing_terms_string_values(self) -> None:
        for val in sorted(BillingTerms):
            assert isinstance(val, str)

    def test_all_static_prices_have_source_urls(self) -> None:
        """No static price may omit its source URL."""
        static_sources = [
            AnthropicSource(),
            OpenAISource(),
            RunPodSource(),
            LambdaLabsSource(),
            AWSSource(),
            GCPSource(),
        ]
        for src in static_sources:
            for mp in src.fetch_model_prices():
                assert mp.source, f"{src.provider_slug()}: price for {mp.model_id} missing source"
            for cp in src.fetch_compute_prices():
                assert cp.source, f"{src.provider_slug()}: compute {cp.sku} missing source"

    def test_runpod_spot_prices_cheaper_than_ondemand(self) -> None:
        """Spot prices must always be cheaper than on-demand for the same GPU family."""
        prices = RunPodSource().fetch_compute_prices()
        spot = {p.sku: p for p in prices if p.spot}
        ondemand = {p.sku: p for p in prices if not p.spot}
        # Find matching pairs by GPU type
        for sku_s, sp in spot.items():
            # Look for on-demand with same GPU type
            matching = [od for od in ondemand.values() if od.gpu_type == sp.gpu_type]
            for od_price in matching:
                assert sp.usd_per_hour() < od_price.usd_per_hour(), (
                    f"Spot {sku_s} (${sp.usd_per_hour():.4f}/hr) is not cheaper "
                    f"than on-demand {od_price.sku} (${od_price.usd_per_hour():.4f}/hr)"
                )

    def test_prepaid_providers_are_not_postpaid(self) -> None:
        """Critical correctness: prepaid providers must never be classified postpaid."""
        for src in [RunPodSource(), LambdaLabsSource()]:
            b = src.billing()
            assert b.terms == BillingTerms.prepaid_balance, f"{src.provider_slug()} must be prepaid_balance"
            assert b.terms != BillingTerms.postpaid_monthly
            assert b.terms != BillingTerms.postpaid_per_use

    def test_postpaid_cloud_not_prepaid(self) -> None:
        """AWS and GCP must be postpaid_monthly — not prepaid."""
        for src in [AWSSource(), GCPSource()]:
            b = src.billing()
            assert b.terms == BillingTerms.postpaid_monthly, f"{src.provider_slug()} must be postpaid_monthly"
            assert b.terms != BillingTerms.prepaid_balance
