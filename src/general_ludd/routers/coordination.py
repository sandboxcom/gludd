"""Agent file-overlap coordination API.

Endpoints (PSK auth applied by daemon middleware — not in _PUBLIC_PATHS):
  POST /api/coordination/claim           -> record that a worker is editing files
  POST /api/coordination/release         -> worker finished — drop its claims
  GET  /api/coordination/overlaps        -> overlaps + should_wait for a worker
  GET  /api/coordination/claims          -> all_claims + merge_plan (global view)

State is stored on ``app.state._file_claims`` (a FileClaimRegistry).
Claims are ephemeral (in-memory only); they are lost on daemon restart.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI
from pydantic import BaseModel, Field

from general_ludd.coordination.file_claims import FileClaimRegistry

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------

class ClaimRequest(BaseModel):
    worker_id: str = Field(min_length=1, max_length=256)
    files: list[str] = Field(default_factory=list)


class ReleaseRequest(BaseModel):
    worker_id: str = Field(min_length=1, max_length=256)


# ---------------------------------------------------------------------------
# Internal helper
# ---------------------------------------------------------------------------

def _get_registry(app: FastAPI) -> FileClaimRegistry:
    reg: FileClaimRegistry = app.state._file_claims
    return reg


# ---------------------------------------------------------------------------
# Facet helper (callable by facts.py after integration)
# ---------------------------------------------------------------------------

def _coordination_facet(app: FastAPI) -> dict[str, Any]:
    """Return a coordination snapshot suitable for /api/facts.

    Wired into routers/facts.py: imported as _coordination_facet and added to
    the facts dict under the "coordination" key.
    """
    try:
        reg = _get_registry(app)
        return {
            "claims": reg.all_claims(),
            "merge_plan": reg.merge_plan(),
        }
    except AttributeError:
        # Registry not yet initialised (degraded / test environment)
        return {"claims": {}, "merge_plan": {}}


# ---------------------------------------------------------------------------
# Router registration
# ---------------------------------------------------------------------------

def register(app: FastAPI, _daemon_state: dict[str, Any]) -> None:
    """Register coordination endpoints and initialise the FileClaimRegistry.

    Called once at daemon startup (or in tests via TestClient fixture).
    Stores the registry on ``app.state._file_claims`` so it is accessible
    from facet helpers and across request handlers.
    """
    registry = FileClaimRegistry()
    app.state._file_claims = registry

    @app.post("/api/coordination/claim", status_code=201)
    async def api_claim(req: ClaimRequest) -> dict[str, Any]:
        """Record that *worker_id* is editing *files*.

        Idempotent: calling again with the same worker_id updates its file set.
        """
        registry.claim(req.worker_id, req.files)
        logger.debug("coordination.claim: worker=%s files=%s", req.worker_id, req.files)
        return {"worker_id": req.worker_id, "files": list(req.files)}

    @app.post("/api/coordination/release")
    async def api_release(req: ReleaseRequest) -> dict[str, Any]:
        """Drop all file claims for *worker_id*.

        No-op (returns success) when the worker was not registered.
        """
        registry.release(req.worker_id)
        logger.debug("coordination.release: worker=%s", req.worker_id)
        return {"released": True, "worker_id": req.worker_id}

    @app.get("/api/coordination/overlaps")
    async def api_overlaps(worker_id: str) -> dict[str, Any]:
        """Return the conflict map and wait-list for *worker_id*.

        Response shape:
          {
            "worker_id": str,
            "overlaps": {file: [other_worker_ids]},
            "should_wait": [worker_ids]
          }
        """
        ov = registry.overlaps(worker_id)
        wait = registry.should_wait(worker_id)
        return {"worker_id": worker_id, "overlaps": ov, "should_wait": wait}

    @app.get("/api/coordination/claims")
    async def api_claims() -> dict[str, Any]:
        """Return the global claim map and merge plan.

        Response shape:
          {
            "claims": {file: [worker_ids]},
            "merge_plan": {file: "union"|"serialize"}
          }
        """
        return {
            "claims": registry.all_claims(),
            "merge_plan": registry.merge_plan(),
        }
