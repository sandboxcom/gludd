"""Schedule router: POST /api/schedule — compute concurrency-safe work batches.

PSK auth is enforced by the daemon middleware (same as every non-public path).

Request body: list of work-item descriptors (JSON objects matching WorkItemRequest).
Response: ``{"batches": [[id, ...], ...]}`` — ordered batches where items within
each batch are mutually concurrency-safe; batches are ordered so that each item's
dependencies appear in strictly earlier batches.

The last computed plan is stored in ``app.state._schedule_last_plan`` so it can
be surfaced by the /api/facts ``schedule`` facet.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class WorkItemRequest(BaseModel):
    """API-level representation of a work item for the scheduler."""

    id: str = Field(min_length=1, description="Unique work-item identifier.")
    resources: list[str] = Field(
        default_factory=list,
        description="Exclusive resource names this item requires.",
    )
    depends_on: list[str] = Field(
        default_factory=list,
        description="Ids of items that must complete before this one starts.",
    )
    is_greenfield: bool = Field(
        default=False,
        description=(
            "True when this item is greenfield work not yet wired into any "
            "shared resource.  It slots freely and never forces serialization."
        ),
    )


class ScheduleRequest(BaseModel):
    items: list[WorkItemRequest]


class ScheduleResponse(BaseModel):
    batches: list[list[str]]


def register(app: FastAPI, _daemon_state: dict[str, Any]) -> None:
    @app.post("/api/schedule")
    async def post_schedule(body: ScheduleRequest) -> ScheduleResponse | JSONResponse:
        """Compute concurrency-safe ordered batches for the supplied work items.

        Returns a list of batches (each batch is a list of item ids).  Items
        within a batch may run concurrently; later batches depend on earlier
        ones having completed.

        Raises HTTP 422 (via FastAPI validation) on malformed input.
        Returns HTTP 409 (Conflict) when a dependency cycle is detected.
        """
        from general_ludd.scheduling.scheduler import CycleError, Scheduler, WorkItem

        work_items = [
            WorkItem(
                id=req.id,
                resources=frozenset(req.resources),
                depends_on=frozenset(req.depends_on),
                is_greenfield=req.is_greenfield,
            )
            for req in body.items
        ]

        scheduler = Scheduler()
        try:
            batches = scheduler.plan(work_items)
        except CycleError as exc:
            logger.warning("Schedule request rejected — dependency cycle: %s", exc)
            return JSONResponse(
                status_code=409,
                content={"error": "dependency_cycle", "detail": str(exc)},
            )
        except ValueError as exc:
            logger.warning("Schedule request rejected — bad input: %s", exc)
            return JSONResponse(
                status_code=422,
                content={"error": "invalid_input", "detail": str(exc)},
            )

        # Store for /api/facts ``schedule`` facet.
        app.state._schedule_last_plan = {
            "items": [req.model_dump() for req in body.items],
            "batches": batches,
        }

        return ScheduleResponse(batches=batches)
