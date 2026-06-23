"""Tests for RunPodPricingSource — LIVE GraphQL fetch with mocked httpx.

Covers:
  - provider slug + billing semantics
  - GraphQL gpuTypes response parsing (securePrice, communityPrice, spot)
  - Authorization header carries RUNPOD_API_KEY as Bearer token
  - Fail-soft: missing API key, HTTP error, network error, malformed body
  - Source URL documented on every ComputePrice
  - fetch_model_prices returns [] (RunPod offers compute, not model API)
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from general_ludd.pricing_intel.models import (
    BillingGranularity,
    BillingTerms,
)
from general_ludd.pricing_intel.sources import RunPodPricingSource


def _mock_graphql_response(gpu_types: list[dict[str, Any]]) -> MagicMock:
    """Build a mock httpx response for the RunPod graphql endpoint."""
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {"data": {"gpuTypes": gpu_types}}
    return resp


SAMPLE_GPU_TYPES: list[dict[str, Any]] = [
    {
        "id": "NVIDIA RTX 4090",
        "displayName": "RTX 4090",
        "memoryInGb": 24,
        "securePrice": 0.74,
        "communityPrice": 0.44,
        "spot": 0.34,
    },
    {
        "id": "NVIDIA A100 80GB",
        "displayName": "A100 80GB",
        "memoryInGb": 80,
        "securePrice": 2.49,
        "communityPrice": 1.64,
        "spot": 1.10,
    },
    # Minimal entry — only secure price present (community/spot null)
    {
        "id": "NVIDIA H100 80GB",
        "displayName": "H100 80GB",
        "memoryInGb": 80,
        "securePrice": 4.69,
        "communityPrice": None,
        "spot": None,
    },
]


def _mock_client_post(mock_resp: MagicMock) -> MagicMock:
    """Build a mock httpx.Client whose .post() returns mock_resp."""
    client = MagicMock()
    client.__enter__ = MagicMock(return_value=client)
    client.__exit__ = MagicMock(return_value=False)
    client.post = MagicMock(return_value=mock_resp)
    return client


class TestRunPodPricingIdentity:
    def test_provider_slug(self) -> None:
        assert RunPodPricingSource().provider_slug() == "runpod"

    def test_billing_terms(self) -> None:
        b = RunPodPricingSource().billing()
        assert b.provider == "runpod"
        assert b.terms == BillingTerms.prepaid_balance
        assert b.granularity == BillingGranularity.per_second
        assert b.spot_available is True
        assert b.currency == "USD"


class TestRunPodPricingFetch:
    def test_no_api_key_returns_empty(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Without RUNPOD_API_KEY, the source skips cleanly (no network call)."""
        monkeypatch.delenv("RUNPOD_API_KEY", raising=False)
        with patch(
            "general_ludd.pricing_intel.sources.httpx.Client"
        ) as mock_client_cls:
            prices = RunPodPricingSource().fetch_compute_prices()
        assert prices == []
        mock_client_cls.assert_not_called()

    def test_parses_graphql_response(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """All three price tiers are parsed per GPU type; H100 has only secure."""
        monkeypatch.setenv("RUNPOD_API_KEY", "test-key")
        mock_resp = _mock_graphql_response(SAMPLE_GPU_TYPES)
        with patch(
            "general_ludd.pricing_intel.sources.httpx.Client",
            return_value=_mock_client_post(mock_resp),
        ):
            prices = RunPodPricingSource().fetch_compute_prices()

        # RTX 4090: 3 prices, A100: 3 prices, H100: 1 price (secure only) = 7
        assert len(prices) == 7

        secure = [p for p in prices if not p.spot]
        spot = [p for p in prices if p.spot]
        assert len(secure) == 3
        assert len(spot) == 4  # 2 community + 2 spot

        rtx_secure = next(p for p in secure if p.gpu_type == "RTX 4090")
        # $0.74/hr → per_second
        assert rtx_secure.usd_per_unit == pytest.approx(0.74 / 3600)
        assert rtx_secure.granularity == BillingGranularity.per_second
        assert rtx_secure.terms == BillingTerms.prepaid_balance
        assert rtx_secure.provider == "runpod"

        rtx_spot = next(
            p for p in spot if p.gpu_type == "RTX 4090" and "spot" in p.sku
        )
        assert rtx_spot.usd_per_unit == pytest.approx(0.34 / 3600)

    def test_all_prices_have_source(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("RUNPOD_API_KEY", "test-key")
        mock_resp = _mock_graphql_response(SAMPLE_GPU_TYPES)
        with patch(
            "general_ludd.pricing_intel.sources.httpx.Client",
            return_value=_mock_client_post(mock_resp),
        ):
            prices = RunPodPricingSource().fetch_compute_prices()
        assert prices, "expected non-empty price list"
        for p in prices:
            assert p.source, f"Price for {p.sku} missing source"
            assert "runpod" in p.source.lower()

    def test_authorization_header_sent(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The RUNPOD_API_KEY must be sent as a Bearer token."""
        monkeypatch.setenv("RUNPOD_API_KEY", "secret-key-123")
        mock_resp = _mock_graphql_response(SAMPLE_GPU_TYPES[:1])
        client = _mock_client_post(mock_resp)
        with patch(
            "general_ludd.pricing_intel.sources.httpx.Client",
            return_value=client,
        ):
            RunPodPricingSource().fetch_compute_prices()
        client.post.assert_called_once()
        _, kwargs = client.post.call_args
        headers = kwargs.get("headers") or {}
        auth = headers.get("Authorization") or headers.get("authorization")
        assert auth == "Bearer secret-key-123"

    def test_post_body_contains_gputypes_query(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The request JSON must carry a graphql query referencing gpuTypes."""
        monkeypatch.setenv("RUNPOD_API_KEY", "test-key")
        mock_resp = _mock_graphql_response(SAMPLE_GPU_TYPES[:1])
        client = _mock_client_post(mock_resp)
        with patch(
            "general_ludd.pricing_intel.sources.httpx.Client",
            return_value=client,
        ):
            RunPodPricingSource().fetch_compute_prices()
        _, kwargs = client.post.call_args
        body = kwargs.get("json") or {}
        query = str(body.get("query", ""))
        assert "gpuTypes" in query

    def test_http_error_returns_empty(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("RUNPOD_API_KEY", "test-key")
        mock_resp = MagicMock()
        mock_resp.status_code = 500
        with patch(
            "general_ludd.pricing_intel.sources.httpx.Client",
            return_value=_mock_client_post(mock_resp),
        ):
            prices = RunPodPricingSource().fetch_compute_prices()
        assert prices == []

    def test_network_error_returns_empty(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("RUNPOD_API_KEY", "test-key")
        client = MagicMock()
        client.__enter__ = MagicMock(return_value=client)
        client.__exit__ = MagicMock(return_value=False)
        client.post = MagicMock(side_effect=ConnectionError("network down"))
        with patch(
            "general_ludd.pricing_intel.sources.httpx.Client",
            return_value=client,
        ):
            prices = RunPodPricingSource().fetch_compute_prices()
        assert prices == []

    def test_graphql_errors_return_empty(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A response body with GraphQL errors (no data.gpuTypes) → []."""
        monkeypatch.setenv("RUNPOD_API_KEY", "test-key")
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "errors": [{"message": "bad query"}]
        }
        with patch(
            "general_ludd.pricing_intel.sources.httpx.Client",
            return_value=_mock_client_post(mock_resp),
        ):
            prices = RunPodPricingSource().fetch_compute_prices()
        assert prices == []

    def test_fetch_model_prices_returns_empty(self) -> None:
        """RunPod offers compute, not a model API."""
        assert RunPodPricingSource().fetch_model_prices() == []
