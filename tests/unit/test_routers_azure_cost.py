"""Structural tests for routers/azure_cost.py."""

from general_ludd.routers.azure_cost import (
    CostHealthResponse,
    CostIngestRequest,
    CostIngestResponse,
)


class TestAzureCostRouter:
    def test_imports(self):
        pass

    def test_cost_ingest_request(self):
        req = CostIngestRequest(
            content="Date,ResourceId,ServiceName,Cost\n2024-01-01,/sub/rg/vm,Compute,1.23",
            snapshot_id="snap-001",
        )
        assert req.format == "csv"
        assert req.snapshot_id == "snap-001"
        assert req.strict_columns is True

    def test_cost_ingest_response(self):
        resp = CostIngestResponse(
            source="export",
            snapshot_id="snap-001",
            ingested_at="2024-01-01T00:00:00Z",
            row_count=5,
            total_cost_usd=12.50,
            format="csv",
        )
        assert resp.row_count == 5

    def test_cost_health_response(self):
        resp = CostHealthResponse(azure_cost_ingest_available=True)
        assert resp.azure_cost_ingest_available is True
