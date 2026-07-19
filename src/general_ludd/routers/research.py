"""POST /api/research/validate — batch research validation for E2E test scenarios.

Accepts a list of query strings, dispatches each through ResearcherAgent
(backed by SearXNG), and returns structured ResearchReport results.
When SearXNG is unavailable the endpoint still returns 200 with empty
findings (callers fall back to heuristic scoring).
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class ValidateRequest(BaseModel):
    queries: list[str] = Field(default_factory=list, min_length=1, max_length=50)
    categories: list[str] = Field(default_factory=lambda: ["general", "it"])
    time_range: str = "year"
    max_results: int = Field(default=10, ge=1, le=50)


class ValidateResponse(BaseModel):
    reports: list[dict[str, Any]]
    query_count: int
    findings_count: int
    searx_available: bool


def register(app: FastAPI, _daemon_state: dict[str, object]) -> None:

    @app.post("/api/research/validate", response_model=ValidateResponse)
    async def research_validate(body: ValidateRequest) -> ValidateResponse:
        from general_ludd.agents.researcher import ResearcherAgent

        searx = getattr(app.state, "_searx_client", None)
        agent = ResearcherAgent(searx_client=searx)
        searx_available = searx is not None

        reports: list[dict[str, Any]] = []
        findings_count = 0

        for query in body.queries:
            try:
                report = await agent.research(
                    query,
                    categories=body.categories,
                    time_range=body.time_range,
                    max_results=body.max_results,
                )
                reports.append(report.model_dump())
                findings_count += len(report.findings)
            except Exception:
                logger.exception("Research failed for query %r", query)
                reports.append({
                    "report_id": "",
                    "query": query,
                    "findings": [],
                    "sources_consulted": 0,
                    "sources_used": 0,
                    "search_engines_used": [],
                    "elapsed_seconds": 0.0,
                    "generated_at": "",
                    "summary": f"Research failed: query={query!r}",
                    "confidence_overall": 0.0,
                })

        return ValidateResponse(
            reports=reports,
            query_count=len(body.queries),
            findings_count=findings_count,
            searx_available=searx_available,
        )
