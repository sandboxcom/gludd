"""Azure Cost Export ingestion daemon endpoints.

Endpoints:
    POST /api/azure/cost/ingest — ingest a CSV or JSON Azure ActualCost Export
    GET  /api/azure/cost/health — health check for the Azure cost subsystem
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from fastapi import FastAPI
from pydantic import BaseModel, Field

from general_ludd.infra.azure_cost_export_ingestion import (
    AzureActualCostExportIngester,
    AzureCostExportError,
    AzureCostExportParseError,
)

logger = logging.getLogger(__name__)


class CostIngestRequest(BaseModel):
    content: str = Field(description="CSV or JSON content of the Azure ActualCost Export")
    format: str = Field(default="csv", description="Export format: 'csv' or 'json'")
    source: str = Field(default="actual-cost-export", description="Source identifier")
    snapshot_id: str = Field(description="Snapshot identifier for idempotent ingestion")
    strict_columns: bool = Field(default=True, description="Reject unknown columns")


class CostIngestResponse(BaseModel):
    source: str
    snapshot_id: str
    ingested_at: str
    row_count: int
    total_cost_usd: float
    format: str
    cost_per_resource: dict[str, float] = Field(default_factory=dict)
    cost_per_service: dict[str, float] = Field(default_factory=dict)


class CostHealthResponse(BaseModel):
    azure_cost_ingest_available: bool


def register(app: FastAPI, _daemon_state: dict[str, object]) -> None:

    @app.post("/api/azure/cost/ingest", response_model=CostIngestResponse)
    async def api_azure_cost_ingest(req: CostIngestRequest):
        try:
            effective_format = req.format.lower().strip()
            if effective_format not in ("csv", "json"):
                raise AzureCostExportError(f"Unsupported format {req.format!r}. Use 'csv' or 'json'.")

            now = datetime.now(UTC)
            ingester = AzureActualCostExportIngester(
                snapshot_id=req.snapshot_id,
                source=req.source,
                strict_columns=req.strict_columns,
            )

            if effective_format == "csv":
                observations = ingester.ingest_csv(req.content, now=now)
            else:
                import json as _json

                try:
                    raw = _json.loads(req.content)
                except _json.JSONDecodeError as exc:
                    raise AzureCostExportParseError(f"Invalid JSON content: {exc}") from exc
                if not isinstance(raw, list):
                    raise AzureCostExportError("JSON ingest requires a top-level array of records")
                observations = ingester.ingest_raw(raw, now=now)

            total_cost = sum(obs.cost_usd for obs in observations)
            cost_per_resource: dict[str, float] = {}
            cost_per_service: dict[str, float] = {}
            for obs in observations:
                resource_id = obs.payload.get("resource_id")
                service_name = str(obs.payload.get("servicename", obs.payload.get("service_name", "Unknown")))
                if isinstance(resource_id, str):
                    cost_per_resource[resource_id] = cost_per_resource.get(resource_id, 0.0) + obs.cost_usd
                cost_per_service[service_name] = cost_per_service.get(service_name, 0.0) + obs.cost_usd

            return CostIngestResponse(
                source=req.source,
                snapshot_id=req.snapshot_id,
                ingested_at=now.isoformat(),
                row_count=len(observations),
                total_cost_usd=round(total_cost, 6),
                format=effective_format,
                cost_per_resource=cost_per_resource,
                cost_per_service=cost_per_service,
            )

        except AzureCostExportParseError as exc:
            from fastapi.responses import JSONResponse

            logger.warning("Azure cost ingest parse error: %s", exc)
            return JSONResponse(
                status_code=422,
                content={"detail": str(exc), "error_type": "ParseError"},
            )
        except AzureCostExportError as exc:
            from fastapi.responses import JSONResponse

            logger.warning("Azure cost ingest error: %s", exc)
            return JSONResponse(
                status_code=422,
                content={"detail": str(exc), "error_type": "ExportError"},
            )

    @app.get("/api/azure/cost/health", response_model=CostHealthResponse)
    async def api_azure_cost_health() -> CostHealthResponse:
        return CostHealthResponse(azure_cost_ingest_available=True)
