"""Tests for AWSPricingSource — LIVE AWS Price List Query API (GetProducts) via boto3.

Covers:
  - provider slug + billing semantics (postpaid_monthly, per_second)
  - GetProducts response parsing (PriceList JSON strings)
  - GPU instance-type filtering (only p3./p4d./p5./g5. prefixes pass)
  - Linux + Shared tenancy filter is sent to the API
  - AWS_ACCESS_KEY_ID gating: skip cleanly when unset (no boto3 import / network)
  - Fail-soft: boto3 ImportError, API error, malformed price entries
  - Per-second pricing conversion (USD/hr -> USD/s)
  - Source URL documented on every ComputePrice
  - fetch_model_prices returns [] (AWS offers compute, not model API)

boto3 is an OPTIONAL runtime dependency; the source uses a lazy import inside
``_get_client``. Tests mock that method so the suite runs without boto3
installed.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from general_ludd.pricing_intel.models import (
    BillingGranularity,
    BillingTerms,
)
from general_ludd.pricing_intel.sources import AWSPricingSource

# ---------------------------------------------------------------------------
# Helpers: build GetProducts-style responses
# ---------------------------------------------------------------------------


def _price_entry(
    *,
    instance_type: str,
    gpu_count: int,
    gpu_type: str,
    usd_per_hour: str,
) -> str:
    """Build a single AWS Price List entry as a JSON string (as the API does).

    The GetProducts API returns ``PriceList`` as a list of JSON *strings*,
    each describing one product + its OnDemand terms.
    """
    return json.dumps(
        {
            "serviceCode": "AmazonEC2",
            "product": {
                "sku": f"SKUID-{instance_type}",
                "productFamily": "Compute Instance",
                "attributes": {
                    "instanceType": instance_type,
                    "operatingSystem": "Linux",
                    "tenancy": "Shared",
                    "capacitystatus": "Used",
                    "preInstalledSw": "NA",
                    "location": "US East (N. Virginia)",
                    "gpu": str(gpu_count),
                    "gpuType": gpu_type,
                    "vcpu": "96",
                    "memory": "768 GiB",
                },
            },
            "terms": {
                "OnDemand": {
                    "TERMID.SKVU": {
                        "priceDimensions": {
                            "TERMID.SKVU.RATE": {
                                "unit": "Hrs",
                                "pricePerUnit": {"USD": usd_per_hour},
                            }
                        }
                    }
                }
            },
        }
    )


# A representative slice of the AWS GPU catalog (us-east-1, Linux, Shared).
SAMPLE_PRICE_LIST: list[str] = [
    _price_entry(
        instance_type="p3.2xlarge", gpu_count=1, gpu_type="V100", usd_per_hour="3.0600"
    ),
    _price_entry(
        instance_type="p4d.24xlarge", gpu_count=8, gpu_type="A100", usd_per_hour="32.7723"
    ),
    _price_entry(
        instance_type="p5.48xlarge", gpu_count=8, gpu_type="H100", usd_per_hour="98.3200"
    ),
    _price_entry(
        instance_type="g5.xlarge", gpu_count=1, gpu_type="A10G", usd_per_hour="1.0060"
    ),
    # Non-GPU instance that sneaks through the API filter; must be dropped.
    _price_entry(
        instance_type="m5.large", gpu_count=0, gpu_type="", usd_per_hour="0.0960"
    ),
]


def _mock_paginator(price_lists: list[str]) -> MagicMock:
    """Build a mock boto3 pricing client whose paginate() yields one page."""
    paginator = MagicMock()
    paginator.paginate.return_value = [{"PriceLists": price_lists}]
    return paginator


def _mock_boto3_client(price_lists: list[str]) -> MagicMock:
    """Build a mock boto3 pricing client returning ``price_lists``."""
    client = MagicMock()
    client.get_paginator.return_value = _mock_paginator(price_lists)
    return client


# ---------------------------------------------------------------------------
# Identity / billing semantics
# ---------------------------------------------------------------------------


class TestAWSPricingIdentity:
    def test_provider_slug(self) -> None:
        assert AWSPricingSource().provider_slug() == "aws"

    def test_billing_terms(self) -> None:
        b = AWSPricingSource().billing()
        assert b.provider == "aws"
        # AWS bills postpaid monthly — critical distinction from RunPod/Lambda.
        assert b.terms == BillingTerms.postpaid_monthly
        assert b.granularity == BillingGranularity.per_second
        assert b.spot_available is True
        assert b.currency == "USD"


# ---------------------------------------------------------------------------
# AWS_ACCESS_KEY_ID gating — skip cleanly when unset
# ---------------------------------------------------------------------------


class TestAWSPricingCredsGate:
    def test_no_creds_returns_empty_and_no_boto3(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Without AWS_ACCESS_KEY_ID, the source skips cleanly (no boto3, no network)."""
        monkeypatch.delenv("AWS_ACCESS_KEY_ID", raising=False)
        src = AWSPricingSource()
        # Spy on _get_client; it must NOT be invoked.
        with patch.object(
            AWSPricingSource, "_get_client"
        ) as mock_get_client:
            prices = src.fetch_compute_prices()
        assert prices == []
        mock_get_client.assert_not_called()


# ---------------------------------------------------------------------------
# Response parsing — happy path
# ---------------------------------------------------------------------------


class TestAWSPricingFetch:
    def test_parses_pricelist_into_compute_prices(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Each GPU PriceList entry becomes a per-second ComputePrice."""
        monkeypatch.setenv("AWS_ACCESS_KEY_ID", "AKIATEST")
        monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "secret")
        client = _mock_boto3_client(SAMPLE_PRICE_LIST)
        with patch.object(
            AWSPricingSource, "_get_client", return_value=client
        ):
            prices = AWSPricingSource().fetch_compute_prices()

        # 4 GPU entries; the m5.large (gpu=0) is dropped.
        assert len(prices) == 4
        skus = {p.sku for p in prices}
        assert skus == {"p3.2xlarge", "p4d.24xlarge", "p5.48xlarge", "g5.xlarge"}

    def test_per_second_conversion(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """USD/hour from the API is converted to USD/second (hourly / 3600)."""
        monkeypatch.setenv("AWS_ACCESS_KEY_ID", "AKIATEST")
        monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "secret")
        client = _mock_boto3_client(SAMPLE_PRICE_LIST)
        with patch.object(
            AWSPricingSource, "_get_client", return_value=client
        ):
            prices = AWSPricingSource().fetch_compute_prices()

        p4d = next(p for p in prices if p.sku == "p4d.24xlarge")
        assert p4d.usd_per_unit == pytest.approx(32.7723 / 3600.0)
        assert p4d.granularity == BillingGranularity.per_second
        assert p4d.terms == BillingTerms.postpaid_monthly
        assert p4d.spot is False
        assert p4d.gpu_count == 8
        assert p4d.gpu_type == "A100"
        assert p4d.provider == "aws"

    def test_filters_to_gpu_families(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Only p3./p4d./p5./g5. instance types survive client-side filtering."""
        monkeypatch.setenv("AWS_ACCESS_KEY_ID", "AKIATEST")
        # Add non-GPU-family entries alongside GPU ones.
        non_gpu = [
            _price_entry(
                instance_type="c5.large", gpu_count=0, gpu_type="", usd_per_hour="0.085"
            ),
            _price_entry(
                instance_type="r5n.8xlarge", gpu_count=0, gpu_type="", usd_per_hour="2.016"
            ),
        ]
        client = _mock_boto3_client([*SAMPLE_PRICE_LIST, *non_gpu])
        with patch.object(
            AWSPricingSource, "_get_client", return_value=client
        ):
            prices = AWSPricingSource().fetch_compute_prices()
        assert prices, "expected at least the GPU entries to survive"
        for p in prices:
            assert p.sku.split(".")[0] in {"p3", "p4d", "p5", "g5"}

    def test_all_prices_have_source(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("AWS_ACCESS_KEY_ID", "AKIATEST")
        client = _mock_boto3_client(SAMPLE_PRICE_LIST)
        with patch.object(
            AWSPricingSource, "_get_client", return_value=client
        ):
            prices = AWSPricingSource().fetch_compute_prices()
        assert prices, "expected non-empty price list"
        for p in prices:
            assert p.source, f"Price for {p.sku} missing source"
            assert "aws" in p.source.lower() or "pricing" in p.source.lower()

    def test_getproducts_filters_include_linux_shared(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The boto3 paginator must be called with Linux + Shared tenancy filters."""
        monkeypatch.setenv("AWS_ACCESS_KEY_ID", "AKIATEST")
        client = _mock_boto3_client(SAMPLE_PRICE_LIST)
        with patch.object(
            AWSPricingSource, "_get_client", return_value=client
        ):
            AWSPricingSource().fetch_compute_prices()
        client.get_paginator.assert_called_once_with("get_products")
        paginator = client.get_paginator.return_value
        paginator.paginate.assert_called_once()
        _, kwargs = paginator.paginate.call_args
        assert kwargs.get("ServiceCode") == "AmazonEC2"
        filters = kwargs.get("Filters") or []
        # Flatten filter {Field: Value} pairs for easy assertion.
        flat = {f["Field"]: f["Value"] for f in filters}
        assert flat.get("operatingSystem") == "Linux"
        assert flat.get("tenancy") == "Shared"

    def test_getproducts_filters_include_gpu_instance_prefixes(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """No API-level family filter exists; client-side filter by prefix only.

        This test documents that the API request itself does NOT carry an
        instanceType filter (GetProducts uses TERM_MATCH exact-match, so
        filtering by family is done client-side on returned results).
        """
        monkeypatch.setenv("AWS_ACCESS_KEY_ID", "AKIATEST")
        client = _mock_boto3_client(SAMPLE_PRICE_LIST)
        with patch.object(
            AWSPricingSource, "_get_client", return_value=client
        ):
            AWSPricingSource().fetch_compute_prices()
        paginator = client.get_paginator.return_value
        _, kwargs = paginator.paginate.call_args
        filters = kwargs.get("Filters") or []
        fields = {f["Field"] for f in filters}
        # instanceType filter not used (we filter client-side by prefix).
        assert "instanceType" not in fields


# ---------------------------------------------------------------------------
# Fail-soft paths
# ---------------------------------------------------------------------------


class TestAWSPricingFailSoft:
    def test_boto3_import_error_returns_empty(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """If boto3 is not installed, the source skips cleanly."""
        monkeypatch.setenv("AWS_ACCESS_KEY_ID", "AKIATEST")

        def _raise_importerror() -> Any:
            raise ImportError("boto3 is not installed")

        with patch.object(
            AWSPricingSource, "_get_client", side_effect=_raise_importerror
        ):
            prices = AWSPricingSource().fetch_compute_prices()
        assert prices == []

    def test_api_error_returns_empty(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A botocore ClientError during paginate() -> fail-soft -> []."""
        monkeypatch.setenv("AWS_ACCESS_KEY_ID", "AKIATEST")
        client = MagicMock()
        paginator = MagicMock()
        paginator.paginate.side_effect = RuntimeError("AWS API throttled")
        client.get_paginator.return_value = paginator
        with patch.object(
            AWSPricingSource, "_get_client", return_value=client
        ):
            prices = AWSPricingSource().fetch_compute_prices()
        assert prices == []

    def test_malformed_pricelist_entry_skipped(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Garbage JSON in PriceList is skipped without crashing the loop."""
        monkeypatch.setenv("AWS_ACCESS_KEY_ID", "AKIATEST")
        bad_entries = [
            "not-valid-json",
            json.dumps({"product": {"attributes": {}}}),  # missing terms
            json.dumps({"terms": {"OnDemand": {}}}),  # missing product
        ]
        client = _mock_boto3_client(bad_entries)
        with patch.object(
            AWSPricingSource, "_get_client", return_value=client
        ):
            prices = AWSPricingSource().fetch_compute_prices()
        assert prices == []

    def test_entry_missing_usd_price_skipped(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A valid GPU-family entry without a USD price is dropped, not crashed on."""
        monkeypatch.setenv("AWS_ACCESS_KEY_ID", "AKIATEST")
        no_usd = [
            json.dumps(
                {
                    "product": {
                        "attributes": {
                            "instanceType": "p3.2xlarge",
                            "gpu": "1",
                            "gpuType": "V100",
                        }
                    },
                    "terms": {
                        "OnDemand": {
                            "T": {
                                "priceDimensions": {
                                    "R": {"pricePerUnit": {"EUR": "2.50"}}
                                }
                            }
                        }
                    },
                }
            )
        ]
        client = _mock_boto3_client(no_usd)
        with patch.object(
            AWSPricingSource, "_get_client", return_value=client
        ):
            prices = AWSPricingSource().fetch_compute_prices()
        assert prices == []


# ---------------------------------------------------------------------------
# Model prices — AWS offers compute, not model API
# ---------------------------------------------------------------------------


class TestAWSPricingModels:
    def test_fetch_model_prices_returns_empty(self) -> None:
        assert AWSPricingSource().fetch_model_prices() == []
