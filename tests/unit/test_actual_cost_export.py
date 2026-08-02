"""ActualCostExport class: file-based CSV/JSON export ingestion and storage preparation."""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

import pytest

from general_ludd.infra.azure_cost_export_ingestion import (
    ActualCostExport,
    AzureCostExportColumnMap,
    AzureCostExportError,
    AzureCostExportParseError,
)

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


def _make_csv_content() -> str:
    return _csv_lines(
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


def _make_csv_multiline_content() -> str:
    return _csv_lines(
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


def _make_json_content() -> str:
    return json.dumps(
        [
            {
                "BillingAccountId": "ba-1",
                "Date": "2026-08-01",
                "ResourceId": "/subscriptions/sub-1/resourceGroups/rg-1/providers/Microsoft.App/containerApps/app-1",
                "MeterId": "gpu-meter",
                "MeterCategory": "Compute",
                "MeterSubCategory": "Container Apps",
                "MeterName": "GPU Usage",
                "MeterRegion": "US East",
                "UnitOfMeasure": "1 Hour",
                "Quantity": "24",
                "EffectivePrice": "0.10",
                "CostInBillingCurrency": 2.40,
                "CostInPricingCurrency": 2.40,
                "PricingCurrencyCode": "USD",
                "BillingCurrencyCode": "USD",
                "ServiceName": "Azure Container Apps",
                "ServiceTier": "Standard",
                "ChargeType": "Usage",
                "BillingPeriodStartDate": "2026-08-01",
                "BillingPeriodEndDate": "2026-08-01",
                "ResourceGroup": "rg-1",
                "ResourceLocation": "eastus",
                "ConsumedService": "Microsoft.App",
                "InvoiceSection": "invoice-section-1",
                "SubscriptionId": "sub-1",
                "SubscriptionName": "My Subscription",
                "ProductName": "Container Apps",
                "PublisherType": "Azure",
                "AdditionalInfo": "",
                "Tags": "",
                "CostAllocationRuleName": "",
            },
            {
                "BillingAccountId": "ba-1",
                "Date": "2026-08-01",
                "ResourceId": (
                    "/subscriptions/sub-1/resourceGroups/rg-1/providers/Microsoft.Network/publicIPAddresses/ip-1"
                ),
                "MeterId": "ip-meter",
                "MeterCategory": "Networking",
                "MeterSubCategory": "Public IP",
                "MeterName": "IP Hours",
                "MeterRegion": "US East",
                "UnitOfMeasure": "1 Hour",
                "Quantity": "24",
                "EffectivePrice": "0.005",
                "CostInBillingCurrency": 0.12,
                "CostInPricingCurrency": 0.12,
                "PricingCurrencyCode": "USD",
                "BillingCurrencyCode": "USD",
                "ServiceName": "Virtual Network",
                "ServiceTier": "Standard",
                "ChargeType": "Usage",
                "BillingPeriodStartDate": "2026-08-01",
                "BillingPeriodEndDate": "2026-08-01",
                "ResourceGroup": "rg-1",
                "ResourceLocation": "eastus",
                "ConsumedService": "Microsoft.Network",
                "InvoiceSection": "invoice-section-1",
                "SubscriptionId": "sub-1",
                "SubscriptionName": "My Subscription",
                "ProductName": "Public IP",
                "PublisherType": "Azure",
                "AdditionalInfo": "",
                "Tags": "",
                "CostAllocationRuleName": "",
            },
        ]
    )


# ---------------------------------------------------------------------------
# ActualCostExport — CSV file ingestion
# ---------------------------------------------------------------------------


class TestActualCostExportCsv:
    def test_ingests_csv_file_and_returns_observations(self) -> None:
        content = _make_csv_content()
        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False) as f:
            f.write(content)
            csv_path = Path(f.name)

        try:
            exporter = ActualCostExport(
                source="actual-cost-export",
                snapshot_id="2026-08-01/run-1/etag-a",
            )
            result = exporter.ingest(csv_path, now=_NOW)
            assert len(result.observations) == 1
            assert result.observations[0].cost_usd == 2.40
            assert result.observations[0].currency == "USD"
            assert result.row_count == 1
            assert result.total_cost_usd == 2.40
        finally:
            os.unlink(csv_path)

    def test_ingests_multiline_csv_file(self) -> None:
        content = _make_csv_multiline_content()
        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False) as f:
            f.write(content)
            csv_path = Path(f.name)

        try:
            exporter = ActualCostExport(
                source="actual-cost-export",
                snapshot_id="2026-08-01/run-1/etag-a",
            )
            result = exporter.ingest(csv_path, now=_NOW)
            assert len(result.observations) == 2
            assert result.row_count == 2
            assert result.total_cost_usd == 2.52
        finally:
            os.unlink(csv_path)

    def test_rejects_empty_csv_file(self) -> None:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False) as f:
            f.write(_EXPORT_CSV_HEADER)
            csv_path = Path(f.name)

        try:
            exporter = ActualCostExport(
                source="actual-cost-export",
                snapshot_id="snap-1",
            )
            with pytest.raises(AzureCostExportParseError, match="empty"):
                exporter.ingest(csv_path, now=_NOW)
        finally:
            os.unlink(csv_path)

    def test_rejects_csv_file_with_missing_format(self) -> None:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False) as f:
            f.write("BillingAccountId,Date,ResourceId\r\nba-1,2026-08-01,res-1\r\n")
            csv_path = Path(f.name)

        try:
            exporter = ActualCostExport(
                source="actual-cost-export",
                snapshot_id="snap-1",
            )
            with pytest.raises(AzureCostExportParseError, match="columns"):
                exporter.ingest(csv_path, now=_NOW)
        finally:
            os.unlink(csv_path)

    def test_rejects_nonexistent_file(self) -> None:
        exporter = ActualCostExport(
            source="actual-cost-export",
            snapshot_id="snap-1",
        )
        with pytest.raises(AzureCostExportError, match="does not exist"):
            exporter.ingest(Path("/nonexistent/path/export.csv"), now=_NOW)

    def test_rejects_file_with_unknown_extension(self) -> None:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".xml", delete=False) as f:
            f.write("<root></root>")
            xml_path = Path(f.name)

        try:
            exporter = ActualCostExport(
                source="actual-cost-export",
                snapshot_id="snap-1",
            )
            with pytest.raises(AzureCostExportError, match="Unsupported"):
                exporter.ingest(xml_path, now=_NOW)
        finally:
            os.unlink(xml_path)


# ---------------------------------------------------------------------------
# ActualCostExport — JSON file ingestion
# ---------------------------------------------------------------------------


class TestActualCostExportJson:
    def test_ingests_json_file_and_returns_observations(self) -> None:
        content = _make_json_content()
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            f.write(content)
            json_path = Path(f.name)

        try:
            exporter = ActualCostExport(
                source="actual-cost-export",
                snapshot_id="2026-08-01/run-1/etag-a",
            )
            result = exporter.ingest(json_path, now=_NOW)
            assert len(result.observations) == 2
            assert result.row_count == 2
            assert result.total_cost_usd == 2.52
            assert result.observations[0].cost_usd == 2.40
            assert result.observations[1].cost_usd == 0.12
        finally:
            os.unlink(json_path)

    def test_json_file_preserves_row_identity(self) -> None:
        content = _make_json_content()
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            f.write(content)
            json_path = Path(f.name)

        try:
            exporter = ActualCostExport(
                source="actual-cost-export",
                snapshot_id="snap-1",
            )
            result = exporter.ingest(json_path, now=_NOW)
            assert len(result.observations) == 2
            assert result.observations[0].row_identity != result.observations[1].row_identity
            assert all(o.row_identity.startswith("actual-cost-export:") for o in result.observations)
        finally:
            os.unlink(json_path)

    def test_rejects_empty_json_list(self) -> None:
        content = "[]"
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            f.write(content)
            json_path = Path(f.name)

        try:
            exporter = ActualCostExport(
                source="actual-cost-export",
                snapshot_id="snap-1",
            )
            with pytest.raises(AzureCostExportParseError, match="empty"):
                exporter.ingest(json_path, now=_NOW)
        finally:
            os.unlink(json_path)

    def test_rejects_json_with_nonlist_top(self) -> None:
        content = '{"rows": []}'
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            f.write(content)
            json_path = Path(f.name)

        try:
            exporter = ActualCostExport(
                source="actual-cost-export",
                snapshot_id="snap-1",
            )
            with pytest.raises(AzureCostExportParseError, match="list"):
                exporter.ingest(json_path, now=_NOW)
        finally:
            os.unlink(json_path)

    def test_rejects_malformed_json_file(self) -> None:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            f.write("{not valid json}")
            json_path = Path(f.name)

        try:
            exporter = ActualCostExport(
                source="actual-cost-export",
                snapshot_id="snap-1",
            )
            with pytest.raises(AzureCostExportParseError, match="JSON"):
                exporter.ingest(json_path, now=_NOW)
        finally:
            os.unlink(json_path)

    def test_json_file_deterministic_between_identical_exports(self) -> None:
        content = _make_json_content()
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            f.write(content)
            json_path = Path(f.name)

        try:
            exporter = ActualCostExport(
                source="actual-cost-export",
                snapshot_id="snap-1",
            )
            result_a = exporter.ingest(json_path, now=_NOW)
            result_b = exporter.ingest(json_path, now=_NOW)
            assert result_a.total_cost_usd == result_b.total_cost_usd
            assert result_a.row_count == result_b.row_count
            assert [o.row_identity for o in result_a.observations] == [o.row_identity for o in result_b.observations]
        finally:
            os.unlink(json_path)


# ---------------------------------------------------------------------------
# ActualCostExport — string/bytes ingestion
# ---------------------------------------------------------------------------


class TestActualCostExportIngestString:
    def test_ingests_csv_string_content_directly(self) -> None:
        content = _make_csv_content()
        exporter = ActualCostExport(
            source="actual-cost-export",
            snapshot_id="2026-08-01/run-1/etag-a",
        )
        result = exporter.ingest_string(content, format="csv", now=_NOW)
        assert len(result.observations) == 1
        assert result.observations[0].cost_usd == 2.40
        assert result.row_count == 1
        assert result.total_cost_usd == 2.40

    def test_ingests_json_string_content_directly(self) -> None:
        content = _make_json_content()
        exporter = ActualCostExport(
            source="actual-cost-export",
            snapshot_id="snap-1",
        )
        result = exporter.ingest_string(content, format="json", now=_NOW)
        assert len(result.observations) == 2
        assert result.row_count == 2
        assert result.total_cost_usd == 2.52

    def test_ingests_json_bytes_content(self) -> None:
        content = _make_json_content().encode("utf-8")
        exporter = ActualCostExport(
            source="actual-cost-export",
            snapshot_id="snap-1",
        )
        result = exporter.ingest_bytes(content, format="json", now=_NOW)
        assert len(result.observations) == 2
        assert result.row_count == 2

    def test_ingests_csv_bytes_content(self) -> None:
        content = _make_csv_content().encode("utf-8")
        exporter = ActualCostExport(
            source="actual-cost-export",
            snapshot_id="2026-08-01/run-1/etag-a",
        )
        result = exporter.ingest_bytes(content, format="csv", now=_NOW)
        assert len(result.observations) == 1
        assert result.observations[0].cost_usd == 2.40


# ---------------------------------------------------------------------------
# ActualCostExportSummary — structured storage data
# ---------------------------------------------------------------------------


class TestActualCostExportSummary:
    def test_summary_holds_totals_and_metadata(self) -> None:
        content = _make_csv_multiline_content()
        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False) as f:
            f.write(content)
            csv_path = Path(f.name)

        try:
            exporter = ActualCostExport(
                source="actual-cost-export",
                snapshot_id="2026-08-01/run-1/etag-a",
            )
            result = exporter.ingest(csv_path, now=_NOW)
            assert result.row_count == 2
            assert result.total_cost_usd == 2.52
            assert result.source == "actual-cost-export"
            assert result.snapshot_id == "2026-08-01/run-1/etag-a"
            assert result.ingested_at == _NOW
        finally:
            os.unlink(csv_path)

    def test_summary_asdict_roundtrips(self) -> None:
        content = _make_csv_content()
        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False) as f:
            f.write(content)
            csv_path = Path(f.name)

        try:
            exporter = ActualCostExport(
                source="actual-cost-export",
                snapshot_id="snap-1",
            )
            result = exporter.ingest(csv_path, now=_NOW)
            d = asdict(result)
            assert d["source"] == "actual-cost-export"
            assert d["snapshot_id"] == "snap-1"
            assert d["row_count"] == 1
            assert d["total_cost_usd"] == 2.40
            assert len(d["observations"]) == 1
        finally:
            os.unlink(csv_path)

    def test_summary_with_custom_column_map(self) -> None:
        column_map = AzureCostExportColumnMap(
            date="Date",
            resource_id="ResourceId",
            meter_id="MeterId",
            cost_in_billing_currency="CostInBillingCurrency",
            billing_currency_code="BillingCurrencyCode",
            charge_type="ChargeType",
            service_name="ServiceName",
        )
        content = (
            "Date,ResourceId,MeterId,CostInBillingCurrency,BillingCurrencyCode,"
            "ChargeType,ServiceName\r\n"
            "2026-08-01,res-1,meter-1,1.50,USD,Usage,Svc\r\n"
        )
        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False) as f:
            f.write(content)
            csv_path = Path(f.name)

        try:
            exporter = ActualCostExport(
                source="actual-cost-export",
                snapshot_id="snap-1",
                column_map=column_map,
            )
            result = exporter.ingest(csv_path, now=_NOW)
            assert len(result.observations) == 1
            assert result.observations[0].cost_usd == 1.50
        finally:
            os.unlink(csv_path)


# ---------------------------------------------------------------------------
# ActualCostExportSummary — metadata
# ---------------------------------------------------------------------------


class TestActualCostExportSummaryMetadata:
    def test_metadata_stores_file_name_and_format(self) -> None:
        content = _make_csv_content()
        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False) as f:
            f.write(content)
            csv_path = Path(f.name)

        try:
            exporter = ActualCostExport(
                source="actual-cost-export",
                snapshot_id="snap-1",
            )
            result = exporter.ingest(csv_path, now=_NOW)
            assert result.format == "csv"
            assert result.file_name == csv_path.name
        finally:
            os.unlink(csv_path)

    def test_metadata_json_format_recorded(self) -> None:
        content = _make_json_content()
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            f.write(content)
            json_path = Path(f.name)

        try:
            exporter = ActualCostExport(
                source="actual-cost-export",
                snapshot_id="snap-1",
            )
            result = exporter.ingest(json_path, now=_NOW)
            assert result.format == "json"
            assert result.file_name == json_path.name
        finally:
            os.unlink(json_path)

    def test_ingest_string_sets_format_in_summary(self) -> None:
        exporter = ActualCostExport(
            source="actual-cost-export",
            snapshot_id="snap-1",
        )
        result = exporter.ingest_string(_make_csv_content(), format="csv", now=_NOW)
        assert result.format == "csv"
        assert result.file_name == "<string>"
