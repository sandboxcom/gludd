"""Integration tests for Azure cost export ingestion endpoints.

Exercises ``POST /api/azure/cost/ingest`` (CSV and JSON) and
``GET /api/azure/cost/health`` through the real daemon app via ASGITransport
with PSK auth enabled.
"""

from __future__ import annotations

import json

import pytest
from httpx import ASGITransport, AsyncClient

PSK = "test-psk-secret"
AUTH = {"Authorization": f"Bearer {PSK}"}

_CSV_HEADER = (
    "ResourceId,MeterId,CostInBillingCurrency,BillingCurrencyCode,"
    "ChargeType,ServiceName,Date,SubscriptionId,SubscriptionName,"
    "ResourceGroup,ResourceLocation,ConsumedService,InvoiceSection,"
    "ServiceTier,MeterCategory,MeterSubCategory,MeterName,"
    "MeterRegion,UnitOfMeasure,Quantity,EffectivePrice,"
    "CostInPricingCurrency,PricingCurrencyCode,"
    "BillingPeriodStartDate,BillingPeriodEndDate,"
    "ProductName,PublisherType,AdditionalInfo,Tags,CostAllocationRuleName\n"
)
_ROW1 = (
    "/subscriptions/sub-123/resourceGroups/rg1/providers/Microsoft.Compute/"
    "virtualMachines/vm1,meter-001,12.50,USD,Usage,Azure Compute,2025-03-01,"
    "sub-123,MySub,rg1,eastus,Microsoft.Compute,inv-001,Standard,"
    "Virtual Machines,Compute,VM Standard,East US,Hours,10,1.25,"
    "12.50,USD,2025-03-01,2025-03-01,VM Standard D4,Microsoft,"
    '{"tags":"prod"},prod,rule-a\n'
)
_ROW2 = (
    "/subscriptions/sub-123/resourceGroups/rg1/providers/Microsoft.Compute/"
    "virtualMachines/vm2,meter-002,8.75,USD,Usage,Azure Compute,2025-03-02,"
    "sub-123,MySub,rg1,eastus,Microsoft.Compute,inv-001,Standard,"
    "Virtual Machines,Compute,VM Standard,East US,Hours,5,1.75,"
    "8.75,USD,2025-03-02,2025-03-02,VM Standard D8,Microsoft,"
    '{"tags":"prod"},prod,rule-a\n'
)
_VALID_CSV = _CSV_HEADER + _ROW1 + _ROW2

_VALID_JSON_ROWS = [
    {
        "ResourceId": "/subscriptions/sub-123/resourceGroups/rg1/providers/Microsoft.Compute/virtualMachines/vm1",
        "MeterId": "meter-001",
        "CostInBillingCurrency": "12.50",
        "BillingCurrencyCode": "USD",
        "ChargeType": "Usage",
        "ServiceName": "Azure Compute",
        "Date": "2025-03-01",
    },
    {
        "ResourceId": "/subscriptions/sub-123/resourceGroups/rg1/providers/Microsoft.Compute/virtualMachines/vm2",
        "MeterId": "meter-002",
        "CostInBillingCurrency": "8.75",
        "BillingCurrencyCode": "USD",
        "ChargeType": "Usage",
        "ServiceName": "Azure Compute",
        "Date": "2025-03-02",
    },
]


async def _make_app(monkeypatch):
    monkeypatch.setenv("GLUDD_AUTH_PSK", PSK)
    from general_ludd.daemon import create_daemon_app

    app = create_daemon_app(tick_interval=1.0)
    transport = ASGITransport(app=app)
    client = AsyncClient(transport=transport, base_url="http://test")
    return app, client


class TestAzureCostHealth:
    @pytest.mark.asyncio
    async def test_health_endpoint_requires_auth(self, monkeypatch):
        _app, client = await _make_app(monkeypatch)
        try:
            resp = await client.get("/api/azure/cost/health")
            assert resp.status_code == 401
        finally:
            await client.aclose()

    @pytest.mark.asyncio
    async def test_health_endpoint_with_auth(self, monkeypatch):
        _app, client = await _make_app(monkeypatch)
        try:
            resp = await client.get("/api/azure/cost/health", headers=AUTH)
            assert resp.status_code == 200
            body = resp.json()
            assert body["azure_cost_ingest_available"] is True
        finally:
            await client.aclose()


class TestAzureCostIngestCsv:
    @pytest.mark.asyncio
    async def test_ingest_valid_csv(self, monkeypatch):
        _app, client = await _make_app(monkeypatch)
        try:
            resp = await client.post(
                "/api/azure/cost/ingest",
                json={
                    "content": _VALID_CSV,
                    "format": "csv",
                    "source": "test-source",
                    "snapshot_id": "snap-001",
                    "strict_columns": False,
                },
                headers=AUTH,
            )
            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert body["source"] == "test-source"
            assert body["snapshot_id"] == "snap-001"
            assert body["row_count"] == 2
            assert body["total_cost_usd"] == 21.25
            assert body["format"] == "csv"
            assert "cost_per_resource" in body
            assert "cost_per_service" in body
        finally:
            await client.aclose()

    @pytest.mark.asyncio
    async def test_ingest_empty_csv_rejected(self, monkeypatch):
        _app, client = await _make_app(monkeypatch)
        try:
            resp = await client.post(
                "/api/azure/cost/ingest",
                json={
                    "content": _CSV_HEADER,
                    "format": "csv",
                    "source": "test-source",
                    "snapshot_id": "snap-empty",
                    "strict_columns": False,
                },
                headers=AUTH,
            )
            assert resp.status_code == 422
        finally:
            await client.aclose()

    @pytest.mark.asyncio
    async def test_ingest_csv_missing_psk_returns_401(self, monkeypatch):
        _app, client = await _make_app(monkeypatch)
        try:
            resp = await client.post(
                "/api/azure/cost/ingest",
                json={
                    "content": _VALID_CSV,
                    "format": "csv",
                    "source": "test-source",
                    "snapshot_id": "snap-001",
                    "strict_columns": False,
                },
            )
            assert resp.status_code == 401
        finally:
            await client.aclose()

    @pytest.mark.asyncio
    async def test_ingest_invalid_format_rejected(self, monkeypatch):
        _app, client = await _make_app(monkeypatch)
        try:
            resp = await client.post(
                "/api/azure/cost/ingest",
                json={
                    "content": _VALID_CSV,
                    "format": "xml",
                    "source": "test-source",
                    "snapshot_id": "snap-001",
                    "strict_columns": False,
                },
                headers=AUTH,
            )
            assert resp.status_code == 422
        finally:
            await client.aclose()


class TestAzureCostIngestJson:
    @pytest.mark.asyncio
    async def test_ingest_valid_json(self, monkeypatch):
        _app, client = await _make_app(monkeypatch)
        try:
            resp = await client.post(
                "/api/azure/cost/ingest",
                json={
                    "content": json.dumps(_VALID_JSON_ROWS),
                    "format": "json",
                    "source": "test-source",
                    "snapshot_id": "snap-002",
                    "strict_columns": False,
                },
                headers=AUTH,
            )
            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert body["source"] == "test-source"
            assert body["snapshot_id"] == "snap-002"
            assert body["row_count"] == 2
            assert body["total_cost_usd"] == 21.25
            assert body["format"] == "json"
        finally:
            await client.aclose()

    @pytest.mark.asyncio
    async def test_ingest_json_single_object_not_array_rejected(self, monkeypatch):
        _app, client = await _make_app(monkeypatch)
        try:
            resp = await client.post(
                "/api/azure/cost/ingest",
                json={
                    "content": json.dumps(_VALID_JSON_ROWS[0]),
                    "format": "json",
                    "source": "test-source",
                    "snapshot_id": "snap-003",
                    "strict_columns": False,
                },
                headers=AUTH,
            )
            assert resp.status_code == 422
        finally:
            await client.aclose()

    @pytest.mark.asyncio
    async def test_ingest_json_invalid_content(self, monkeypatch):
        _app, client = await _make_app(monkeypatch)
        try:
            resp = await client.post(
                "/api/azure/cost/ingest",
                json={
                    "content": "not valid json",
                    "format": "json",
                    "source": "test-source",
                    "snapshot_id": "snap-004",
                    "strict_columns": False,
                },
                headers=AUTH,
            )
            assert resp.status_code == 422
        finally:
            await client.aclose()
