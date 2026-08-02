"""Durable Azure ActualCost Export ingestion and column-mapping contracts."""

from __future__ import annotations

import csv
import io
from datetime import UTC, datetime
from typing import Any

import pytest

from general_ludd.infra.azure_cost_export_ingestion import (
    ACTUALCOST_EXPORT_SCHEMA_V1,
    AzureActualCostExportIngester,
    AzureCostExportColumnMap,
    AzureCostExportParseError,
    AzureCostExportSchema,
    parse_actual_cost_csv,
    validate_export_completeness,
)
from general_ludd.infra.azure_cost_reconciliation import AzureActualCostObservation

_NOW = datetime(2026, 8, 1, 16, tzinfo=UTC)

_EXPORT_CSV_HEADER = (
    "BillingAccountId,Date,ResourceId,MeterId,MeterCategory,MeterSubCategory,"
    "MeterName,MeterRegion,UnitOfMeasure,Quantity,EffectivePrice,CostInBillingCurrency,"
    "CostInPricingCurrency,PricingCurrencyCode,BillingCurrencyCode,ServiceName,"
    "ServiceTier,ChargeType,BillingPeriodStartDate,BillingPeriodEndDate,"
    "ResourceGroup,ResourceLocation,ConsumedService,InvoiceSection,"
    "SubscriptionId,SubscriptionName,ProductName,PublisherType,"
    "AdditionalInfo,Tags,CostAllocationRuleName\r\n"
)


def _csv_lines(*rows: list[str]) -> str:
    return _EXPORT_CSV_HEADER + "\r\n".join(",".join(row) for row in rows)


def _read_csv(content: str) -> list[dict[str, str]]:
    reader = csv.DictReader(io.StringIO(content))
    return list(reader)


# ---------------------------------------------------------------------------
# CSV parsing
# ---------------------------------------------------------------------------


class TestParseActualCostCsv:
    def test_parses_valid_export_rows_with_required_columns(self) -> None:
        content = _csv_lines(
            [
                "ba-1",
                "2026-08-01",
                "/subscriptions/sub-1/resourceGroups/rg-1/providers/Microsoft.App/containerApps/app-1",
                "12345abc-meter-id",
                "Compute",
                "Container Apps",
                "GPU Usage",
                "US East",
                "1 Hour",
                "24",
                "0.10",
                "2.40",
                "2.40",
                "USD",
                "USD",
                "Azure Container Apps",
                "Standard",
                "Usage",
                "2026-08-01",
                "2026-08-01",
                "rg-1",
                "eastus",
                "Microsoft.App",
                "invoice-section-1",
                "sub-1",
                "My Subscription",
                "Container Apps",
                "Azure",
                '{"key":"value"}',
                '{"gludd-reconciliation-id":"recon-1"}',
                "",
            ],
        )
        observations = parse_actual_cost_csv(
            content,
            source="actual-cost-export",
            snapshot_id="2026-08-01/run-1/etag-a",
            now=_NOW,
        )
        assert len(observations) == 1
        obs = observations[0]
        assert obs.source == "actual-cost-export"
        assert obs.snapshot_id == "2026-08-01/run-1/etag-a"
        assert obs.row_identity.startswith("actual-cost-export:")
        assert obs.cost_usd == 2.40
        assert obs.currency == "USD"
        assert obs.payload["meter_id"] == "12345abc-meter-id"
        assert obs.payload["resource_id"].startswith("/subscriptions/")

    def test_parses_multiple_rows_preserving_each_row_identity(self) -> None:
        content = _csv_lines(
            [
                "ba-1",
                "2026-08-01",
                "/subscriptions/sub-1/resourceGroups/rg-1/providers/Microsoft.App/containerApps/app-1",
                "gpu-meter",
                "Compute",
                "Container Apps",
                "GPU Usage",
                "US East",
                "1 Hour",
                "24",
                "0.10",
                "2.40",
                "2.40",
                "USD",
                "USD",
                "Azure Container Apps",
                "Standard",
                "Usage",
                "2026-08-01",
                "2026-08-01",
                "rg-1",
                "eastus",
                "Microsoft.App",
                "invoice-section-1",
                "sub-1",
                "My Subscription",
                "Container Apps",
                "Azure",
                "",
                "",
                "",
            ],
            [
                "ba-1",
                "2026-08-01",
                "/subscriptions/sub-1/resourceGroups/rg-1/providers/Microsoft.Network/publicIPAddresses/ip-1",
                "ip-meter",
                "Networking",
                "Public IP",
                "IP Hours",
                "US East",
                "1 Hour",
                "24",
                "0.005",
                "0.12",
                "0.12",
                "USD",
                "USD",
                "Virtual Network",
                "Standard",
                "Usage",
                "2026-08-01",
                "2026-08-01",
                "rg-1",
                "eastus",
                "Microsoft.Network",
                "invoice-section-1",
                "sub-1",
                "My Subscription",
                "Public IP",
                "Azure",
                "",
                "",
                "",
            ],
        )
        observations = parse_actual_cost_csv(
            content,
            source="actual-cost-export",
            snapshot_id="2026-08-01/run-1/etag-a",
            now=_NOW,
        )
        assert len(observations) == 2
        assert observations[0].row_identity != observations[1].row_identity
        assert observations[0].cost_usd == 2.40
        assert observations[1].cost_usd == 0.12

    def test_rejects_empty_csv_with_no_data_rows(self) -> None:
        content = _EXPORT_CSV_HEADER
        with pytest.raises(AzureCostExportParseError, match="empty"):
            parse_actual_cost_csv(
                content,
                source="actual-cost-export",
                snapshot_id="snap-1",
                now=_NOW,
            )

    def test_rejects_csv_with_missing_required_columns(self) -> None:
        content = "Date,MeterId\r\n2026-08-01,gpu-meter\r\n"
        with pytest.raises(AzureCostExportParseError, match="columns"):
            parse_actual_cost_csv(
                content,
                source="actual-cost-export",
                snapshot_id="snap-1",
                now=_NOW,
            )

    def test_rejects_csv_with_unexpected_columns_strict_mode(self) -> None:
        minimal_cols = "Date,ResourceId,MeterId,CostInBillingCurrency,BillingCurrencyCode,ChargeType,ServiceName"
        column_map = AzureCostExportColumnMap(
            date="Date",
            resource_id="ResourceId",
            meter_id="MeterId",
            cost_in_billing_currency="CostInBillingCurrency",
            billing_currency_code="BillingCurrencyCode",
            charge_type="ChargeType",
            service_name="ServiceName",
        )
        content = minimal_cols + ",ExtraColumn\r\n2026-08-01,res-1,meter-1,1.00,USD,Usage,Svc,extra\r\n"
        with pytest.raises(AzureCostExportParseError, match="unknown columns"):
            parse_actual_cost_csv(
                content,
                source="actual-cost-export",
                snapshot_id="snap-1",
                now=_NOW,
                column_map=column_map,
                strict_columns=True,
            )

    def test_allows_extra_columns_in_nonstrict_mode(self) -> None:
        minimal_cols = "Date,ResourceId,MeterId,CostInBillingCurrency,BillingCurrencyCode,ChargeType,ServiceName"
        column_map = AzureCostExportColumnMap(
            date="Date",
            resource_id="ResourceId",
            meter_id="MeterId",
            cost_in_billing_currency="CostInBillingCurrency",
            billing_currency_code="BillingCurrencyCode",
            charge_type="ChargeType",
            service_name="ServiceName",
        )
        content = minimal_cols + ",ExtraColumn\r\n2026-08-01,res-1,meter-1,1.00,USD,Usage,Svc,extra\r\n"
        observations = parse_actual_cost_csv(
            content,
            source="actual-cost-export",
            snapshot_id="snap-1",
            now=_NOW,
            column_map=column_map,
            strict_columns=False,
        )
        assert len(observations) == 1

    def test_rejects_nonpositive_cost_rows(self) -> None:
        content = _csv_lines(
            [
                "ba-1",
                "2026-08-01",
                "/subscriptions/sub-1/resourceGroups/rg-1/providers/Microsoft.App/containerApps/app-1",
                "gpu-meter",
                "Compute",
                "Container Apps",
                "GPU Usage",
                "US East",
                "1 Hour",
                "24",
                "0.10",
                "0.00",
                "0.00",
                "USD",
                "USD",
                "Azure Container Apps",
                "Standard",
                "Usage",
                "2026-08-01",
                "2026-08-01",
                "rg-1",
                "eastus",
                "Microsoft.App",
                "invoice-section-1",
                "sub-1",
                "My Subscription",
                "Container Apps",
                "Azure",
                "",
                "",
                "",
            ],
        )
        with pytest.raises(AzureCostExportParseError, match="positive"):
            parse_actual_cost_csv(
                content,
                source="actual-cost-export",
                snapshot_id="snap-1",
                now=_NOW,
                reject_nonpositive_cost=True,
            )

    def test_allows_nonpositive_cost_with_flag_disabled(self) -> None:
        content = _csv_lines(
            [
                "ba-1",
                "2026-08-01",
                "/subscriptions/sub-1/resourceGroups/rg-1/providers/Microsoft.App/containerApps/app-1",
                "gpu-meter",
                "Compute",
                "Container Apps",
                "GPU Usage",
                "US East",
                "1 Hour",
                "24",
                "0.10",
                "0.00",
                "0.00",
                "USD",
                "USD",
                "Azure Container Apps",
                "Standard",
                "Usage",
                "2026-08-01",
                "2026-08-01",
                "rg-1",
                "eastus",
                "Microsoft.App",
                "invoice-section-1",
                "sub-1",
                "My Subscription",
                "Container Apps",
                "Azure",
                "",
                "",
                "",
            ],
        )
        observations = parse_actual_cost_csv(
            content,
            source="actual-cost-export",
            snapshot_id="snap-1",
            now=_NOW,
            reject_nonpositive_cost=False,
        )
        assert len(observations) == 1
        assert observations[0].cost_usd == 0.0

    def test_rejects_non_usd_currency(self) -> None:
        content = _csv_lines(
            [
                "ba-1",
                "2026-08-01",
                "/subscriptions/sub-1/resourceGroups/rg-1/providers/Microsoft.App/containerApps/app-1",
                "gpu-meter",
                "Compute",
                "Container Apps",
                "GPU Usage",
                "US East",
                "1 Hour",
                "24",
                "0.10",
                "2.40",
                "1.80",
                "EUR",
                "EUR",
                "Azure Container Apps",
                "Standard",
                "Usage",
                "2026-08-01",
                "2026-08-01",
                "rg-1",
                "eastus",
                "Microsoft.App",
                "invoice-section-1",
                "sub-1",
                "My Subscription",
                "Container Apps",
                "Azure",
                "",
                "",
                "",
            ],
        )
        with pytest.raises(AzureCostExportParseError, match="USD"):
            parse_actual_cost_csv(
                content,
                source="actual-cost-export",
                snapshot_id="snap-1",
                now=_NOW,
            )

    def test_preserves_all_export_columns_in_observation_payload(self) -> None:
        content = _csv_lines(
            [
                "ba-1",
                "2026-08-01",
                "/subscriptions/sub-1/resourceGroups/rg-1/providers/Microsoft.App/containerApps/app-1",
                "gpu-meter",
                "Compute",
                "Container Apps",
                "GPU Usage",
                "US East",
                "1 Hour",
                "24",
                "0.10",
                "2.40",
                "2.40",
                "USD",
                "USD",
                "Azure Container Apps",
                "Standard",
                "Usage",
                "2026-08-01",
                "2026-08-01",
                "rg-1",
                "eastus",
                "Microsoft.App",
                "invoice-section-1",
                "sub-1",
                "My Subscription",
                "Container Apps",
                "Azure",
                '{"key":"value"}',
                '{"gludd-reconciliation-id":"recon-1"}',
                "",
            ],
        )
        observations = parse_actual_cost_csv(
            content,
            source="actual-cost-export",
            snapshot_id="2026-08-01/run-1/etag-a",
            now=_NOW,
        )
        assert len(observations) == 1
        payload = observations[0].payload
        # CSV columns are lowercased as-is: MeterCategory -> metercategory
        assert payload["metercategory"] == "Compute"
        assert payload["metersubcategory"] == "Container Apps"
        assert payload["metername"] == "GPU Usage"
        assert payload["unitofmeasure"] == "1 Hour"
        assert payload["quantity"] == "24"
        assert payload["resourcegroup"] == "rg-1"
        assert payload["resourcelocation"] == "eastus"
        assert payload["tags"] == '{"gludd-reconciliation-id":"recon-1"}'

    def test_row_identity_is_deterministic_for_same_input(self) -> None:
        content = _csv_lines(
            [
                "ba-1",
                "2026-08-01",
                "/subscriptions/sub-1/resourceGroups/rg-1/providers/Microsoft.App/containerApps/app-1",
                "gpu-meter",
                "Compute",
                "Container Apps",
                "GPU Usage",
                "US East",
                "1 Hour",
                "24",
                "0.10",
                "2.40",
                "2.40",
                "USD",
                "USD",
                "Azure Container Apps",
                "Standard",
                "Usage",
                "2026-08-01",
                "2026-08-01",
                "rg-1",
                "eastus",
                "Microsoft.App",
                "invoice-section-1",
                "sub-1",
                "My Subscription",
                "Container Apps",
                "Azure",
                "",
                "",
                "",
            ],
        )
        obs_a = parse_actual_cost_csv(
            content,
            source="actual-cost-export",
            snapshot_id="snap-1",
            now=_NOW,
        )
        obs_b = parse_actual_cost_csv(
            content,
            source="actual-cost-export",
            snapshot_id="snap-1",
            now=_NOW,
        )
        assert len(obs_a) == 1
        assert len(obs_b) == 1
        assert obs_a[0].row_identity == obs_b[0].row_identity


# ---------------------------------------------------------------------------
# Export completeness validation
# ---------------------------------------------------------------------------


class TestValidateExportCompleteness:
    def test_passes_with_valid_samples_and_all_categories(self) -> None:
        observations = [
            AzureActualCostObservation(
                source="actual-cost-export",
                snapshot_id="snap-1",
                row_identity="line-1",
                cost_usd=1.0,
                currency="USD",
                payload={"metercategory": "Compute"},
            ),
            AzureActualCostObservation(
                source="actual-cost-export",
                snapshot_id="snap-1",
                row_identity="line-2",
                cost_usd=2.0,
                currency="USD",
                payload={"metercategory": "Compute"},
            ),
        ]
        result = validate_export_completeness(
            observations,
            min_row_count=2,
            required_meter_categories=frozenset({"Compute"}),
        )
        assert result.row_count == 2
        assert result.total_cost_usd == 3.0
        assert result.complete is True

    def test_fails_when_row_count_below_minimum(self) -> None:
        observations: list[AzureActualCostObservation] = []
        result = validate_export_completeness(observations, min_row_count=1)
        assert result.complete is False
        assert result.failure_reason is not None
        assert "row" in result.failure_reason.lower()

    def test_fails_when_meter_category_missing(self) -> None:
        observations = [
            AzureActualCostObservation(
                source="actual-cost-export",
                snapshot_id="snap-1",
                row_identity="line-1",
                cost_usd=1.0,
                currency="USD",
                payload={"metercategory": "Compute"},
            ),
        ]
        result = validate_export_completeness(
            observations,
            min_row_count=1,
            required_meter_categories=frozenset({"Compute", "Networking"}),
        )
        assert result.complete is False
        assert result.failure_reason is not None
        assert "Networking" in result.failure_reason

    def test_fails_when_resource_ids_are_missing_from_payload(self) -> None:
        observations = [
            AzureActualCostObservation(
                source="actual-cost-export",
                snapshot_id="snap-1",
                row_identity="line-1",
                cost_usd=1.0,
                currency="USD",
                payload={"metercategory": "Compute"},
            ),
            AzureActualCostObservation(
                source="actual-cost-export",
                snapshot_id="snap-1",
                row_identity="line-2",
                cost_usd=1.0,
                currency="USD",
                payload={"metercategory": "Compute"},
            ),
        ]
        result = validate_export_completeness(
            observations,
            min_row_count=2,
            required_meter_categories=frozenset({"Compute"}),
            require_resource_ids=True,
        )
        assert result.complete is False
        assert result.failure_reason is not None


# ---------------------------------------------------------------------------
# Export column map (schema pinning)
# ---------------------------------------------------------------------------


class TestExportColumnMap:
    def test_default_map_matches_actualcost_v1_schema(self) -> None:
        column_map = AzureCostExportColumnMap.default()
        assert column_map.resource_id == "ResourceId"
        assert column_map.meter_id == "MeterId"
        assert column_map.cost_in_billing_currency == "CostInBillingCurrency"
        assert column_map.billing_currency_code == "BillingCurrencyCode"
        assert column_map.charge_type == "ChargeType"
        assert column_map.service_name == "ServiceName"
        assert column_map.date == "Date"

    def test_required_column_names_returns_set(self) -> None:
        column_map = AzureCostExportColumnMap.default()
        required = column_map.required_column_names()
        assert "ResourceId" in required
        assert "MeterId" in required
        assert "CostInBillingCurrency" in required
        assert "BillingCurrencyCode" in required
        assert "ChargeType" in required
        assert "ServiceName" in required
        assert "Date" in required


# ---------------------------------------------------------------------------
# Schema version registry
# ---------------------------------------------------------------------------


class TestExportSchemaRegistry:
    def test_v1_schema_exports_column_map(self) -> None:
        assert ACTUALCOST_EXPORT_SCHEMA_V1.version == 1
        assert ACTUALCOST_EXPORT_SCHEMA_V1.column_map.date == "Date"
        assert ACTUALCOST_EXPORT_SCHEMA_V1.column_map.resource_id == "ResourceId"

    def test_schema_rejects_unsupported_version(self) -> None:
        with pytest.raises(ValueError, match="unsupported"):
            AzureCostExportSchema.get(version=0)

        with pytest.raises(ValueError, match="unsupported"):
            AzureCostExportSchema.get(version=99)


# ---------------------------------------------------------------------------
# Ingester integration (CSV parse + observation construction)
# ---------------------------------------------------------------------------


class TestIngesterParseCsv:
    def test_ingester_parses_csv_and_returns_observations(self) -> None:
        ingester = AzureActualCostExportIngester(
            snapshot_id="2026-08-01/run-1/etag-a",
        )
        content = _csv_lines(
            [
                "ba-1",
                "2026-08-01",
                "/subscriptions/sub-1/resourceGroups/rg-1/providers/Microsoft.App/containerApps/app-1",
                "gpu-meter",
                "Compute",
                "Container Apps",
                "GPU Usage",
                "US East",
                "1 Hour",
                "24",
                "0.10",
                "2.40",
                "2.40",
                "USD",
                "USD",
                "Azure Container Apps",
                "Standard",
                "Usage",
                "2026-08-01",
                "2026-08-01",
                "rg-1",
                "eastus",
                "Microsoft.App",
                "invoice-section-1",
                "sub-1",
                "My Subscription",
                "Container Apps",
                "Azure",
                "",
                "",
                "",
            ],
        )
        observations = ingester.ingest_csv(content)
        assert len(observations) == 1
        assert observations[0].source == "actual-cost-export"
        assert observations[0].snapshot_id == "2026-08-01/run-1/etag-a"

    def test_ingester_rejects_parse_errors(self) -> None:
        ingester = AzureActualCostExportIngester(
            snapshot_id="snap-1",
        )
        with pytest.raises(AzureCostExportParseError, match="empty"):
            ingester.ingest_csv(_EXPORT_CSV_HEADER)

    def test_ingester_ingest_raw_json_also_supported(self) -> None:
        ingester = AzureActualCostExportIngester(
            snapshot_id="snap-1",
        )
        records: list[dict[str, Any]] = [
            {
                "Date": "2026-08-01",
                "ResourceId": "/subscriptions/sub-1/resourceGroups/rg-1/providers/Microsoft.App/containerApps/app-1",
                "MeterId": "gpu-meter",
                "CostInBillingCurrency": 1.50,
                "BillingCurrencyCode": "USD",
                "ChargeType": "Usage",
                "ServiceName": "Azure Container Apps",
            },
        ]
        observations = ingester.ingest_raw(records)
        assert len(observations) == 1
        assert observations[0].cost_usd == 1.50
        assert observations[0].currency == "USD"

    def test_ingester_rejects_missing_resource_id_in_raw(self) -> None:
        ingester = AzureActualCostExportIngester(snapshot_id="snap-1")
        with pytest.raises(AzureCostExportParseError, match="resource_id"):
            ingester.ingest_raw(
                [
                    {
                        "Date": "2026-08-01",
                        "MeterId": "gpu-meter",
                        "CostInBillingCurrency": 1.50,
                        "BillingCurrencyCode": "USD",
                        "ChargeType": "Usage",
                        "ServiceName": "Azure Container Apps",
                    },
                ]
            )
