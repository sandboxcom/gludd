"""Feature database API: list, get, and trigger verification.

Endpoints (PSK auth applied by daemon middleware — not in _PUBLIC_PATHS):
  GET  /api/features                      -> list all features (filter by status/category)
  GET  /api/features/{id}                 -> get a single feature by FEAT-... id
  POST /api/features/verify               -> trigger FeatureVerifier.verify_all + persist

PSK auth is applied by the daemon middleware (path is not public).

# Done: gludd_features Ansible module, feature_audit role, molecule scenario,
# scripts/dogfood.py self-check, and Makefile target have all shipped.
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any

from fastapi import FastAPI, HTTPException

from general_ludd.db.models import FeatureStatus
from general_ludd.db.repository import FeatureRepository

logger = logging.getLogger(__name__)


def _get_session_factory(app: FastAPI) -> Any:
    return getattr(app.state, "_session_factory", None)


def _feature_to_dict(feat: Any) -> dict[str, Any]:
    """Serialize a FeatureModel row to a dict, deserializing JSON fields."""
    def _load_json(val: str, default: Any) -> Any:
        try:
            return json.loads(val) if val else default
        except Exception:
            return val  # return raw if unparseable

    return {
        "id": feat.id,
        "project_id": feat.project_id,
        "name": feat.name,
        "description": feat.description,
        "category": feat.category,
        "status": feat.status,
        "acceptance_criteria": _load_json(feat.acceptance_criteria, []),
        "evidence": _load_json(feat.evidence, []),
        "verifier_kind": feat.verifier_kind,
        "requested_by": feat.requested_by,
        "requested_at": str(feat.requested_at) if feat.requested_at else None,
        "verified_at": str(feat.verified_at) if feat.verified_at else None,
        "last_verify_detail": _load_json(feat.last_verify_detail, {}),
    }


def register(app: FastAPI, _daemon_state: dict[str, Any]) -> None:

    @app.get("/api/features")
    async def list_features(
        status: str | None = None,
        category: str | None = None,
    ) -> dict[str, Any]:
        """List features, optionally filtered by status and/or category."""
        factory = _get_session_factory(app)
        if factory is None:
            return {"features": [], "total": 0, "filtered": True}

        async with factory() as session:
            repo = FeatureRepository(session)
            if status is not None:
                try:
                    status_enum = FeatureStatus(status)
                except ValueError:
                    raise HTTPException(
                        status_code=422,
                        detail=f"Invalid status: {status!r}. Valid values: {[s.value for s in FeatureStatus]}",
                    ) from None
                rows = await repo.list_by_status(status_enum)
            elif category is not None:
                rows = await repo.list_by_category(category)
            else:
                rows = await repo.list_all()

        features = [_feature_to_dict(r) for r in rows]
        return {
            "features": features,
            "total": len(features),
            "filtered": status is not None or category is not None,
        }

    @app.get("/api/features/{feature_id}")
    async def get_feature(feature_id: str) -> dict[str, Any]:
        """Get a single feature by its FEAT-... id."""
        factory = _get_session_factory(app)
        if factory is None:
            raise HTTPException(status_code=503, detail="Database unavailable")

        async with factory() as session:
            repo = FeatureRepository(session)
            feat = await repo.get_by_id(feature_id)

        if feat is None:
            raise HTTPException(status_code=404, detail=f"Feature {feature_id!r} not found")
        return _feature_to_dict(feat)

    @app.post("/api/features/verify")
    async def verify_features(project_id: str | None = None) -> dict[str, Any]:
        """Run FeatureVerifier.verify_all against all features and persist results.

        For each feature:
        - FeatureVerifier dispatches evidence refs (test: role: module: molecule: file:)
        - Status is updated in DB based on evidence results
        - EMPTY evidence NEVER reaches VERIFIED (fail-closed)
        """
        from general_ludd.quality.feature_verifier import FeatureVerifier

        factory = _get_session_factory(app)
        if factory is None:
            raise HTTPException(status_code=503, detail="Database unavailable")

        repo_root = str(getattr(app.state, "_repo_root", None) or os.getcwd())
        verifier = FeatureVerifier(repo_root=repo_root)

        async with factory() as session:
            repo = FeatureRepository(session)
            rows = await repo.list_all()

            if not rows:
                return {"summary": {"total": 0}, "results": []}

            # Build plain dicts for the verifier (include evidence as Python list)
            features_input = []
            for row in rows:
                try:
                    ev = json.loads(row.evidence) if row.evidence else []
                except Exception:
                    ev = []
                features_input.append({
                    "id": row.id,
                    "name": row.name,
                    "status": row.status,
                    "evidence": ev,
                })

            summary = verifier.verify_all(features_input)

            # Persist results
            from datetime import datetime
            for result in summary["results"]:
                feat_id = result.get("id")
                if feat_id is None:
                    continue
                try:
                    new_status = FeatureStatus(result["status"])
                    verified_at = None
                    if result.get("verified_at"):
                        try:
                            verified_at = datetime.fromisoformat(result["verified_at"])
                        except Exception:
                            verified_at = None
                    await repo.set_status(
                        feat_id,
                        new_status,
                        detail=result.get("evidence_results"),
                        verified_at=verified_at,
                    )
                except Exception as exc:
                    logger.warning("verify_features: could not persist %s: %s", feat_id, exc)

            await session.commit()

        return {"summary": {k: v for k, v in summary.items() if k != "results"}, "results": summary["results"]}
