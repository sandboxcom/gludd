"""HTTP router: adversarial code detection endpoints.

PSK-gated (admin-only). Surfaces:

  - POST /admin/security/scan-text  — scan arbitrary text for adversarial patterns
  - POST /admin/security/scan-file  — scan a file path
  - GET  /admin/security/adversarial/report — get recent adversarial findings

All endpoints require the daemon PSK (admin middleware enforces it on every
``/admin/*`` path).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, Field

from general_ludd.security.adversarial_detector import (
    AdversarialCodeDetector,
    AdversarialFinding,
    AdversarialScanResult,
)

logger = logging.getLogger(__name__)

_MAX_RECENT = 100


def _get_detector(app: FastAPI) -> AdversarialCodeDetector:
    from general_ludd.security.adversarial_detector import (
        default_adversarial_detector,
    )

    det: AdversarialCodeDetector | None = getattr(
        app.state, "_adversarial_detector", None
    )
    if det is not None:
        return det
    det = default_adversarial_detector()
    app.state._adversarial_detector = det
    return det


class ScanTextRequest(BaseModel):
    text: str = Field(..., min_length=1, description="Text content to scan")
    file_path: str | None = Field(default=None, description="Optional file path for context")


class ScanTextResponse(BaseModel):
    findings: list[dict[str, object]]
    high_confidence: bool
    scanned_files: int
    lines_scanned: int
    critical_count: int
    blocked: bool
    summary: str


class ScanFileRequest(BaseModel):
    file_path: str = Field(..., min_length=1, description="Path to file to scan")


class ScanFileResponse(BaseModel):
    parsed: bool
    error: str | None = None
    findings: list[dict[str, object]]
    high_confidence: bool
    critical_count: int
    blocked: bool
    summary: str


def _finding_to_dict(f: AdversarialFinding) -> dict[str, object]:
    return {
        "pattern_id": f.pattern_id,
        "category": f.category.value if hasattr(f.category, "value") else str(f.category),
        "severity": f.severity.value if hasattr(f.severity, "value") else str(f.severity),
        "description": f.description,
        "match_text": f.match_text,
        "file_path": f.file_path,
        "line_number": f.line_number,
        "confidence": f.confidence,
        "remediation": f.remediation,
    }


def _result_to_response(result: AdversarialScanResult) -> dict[str, object]:
    findings = [_finding_to_dict(f) for f in result.findings]
    return {
        "findings": findings,
        "high_confidence": result.high_confidence,
        "scanned_files": result.scanned_files,
        "lines_scanned": result.lines_scanned,
        "critical_count": result.critical_count,
        "blocked": result.blocked,
        "summary": (
            f"{len(findings)} finding(s), "
            f"{result.critical_count} critical, "
            f"{'BLOCKED' if result.blocked else 'PASS'}"
        ),
    }


def register(app: FastAPI, daemon_state: dict[str, object]) -> None:
    @app.post("/admin/security/scan-text", response_model=ScanTextResponse)
    async def scan_text(body: ScanTextRequest) -> dict[str, object]:
        """Scan arbitrary text for adversarial code patterns."""
        detector = _get_detector(app)
        result = detector.scan_text(body.text, file_path=body.file_path)
        return _result_to_response(result)

    @app.post("/admin/security/scan-file", response_model=ScanFileResponse)
    async def scan_file(body: ScanFileRequest) -> dict[str, object]:
        """Scan a file on disk for adversarial code patterns."""
        detector = _get_detector(app)
        try:
            result = detector.scan_file(body.file_path)
        except (PermissionError, ValueError) as exc:
            # scan_file jails the path (security.sanitize.confine_path_multi);
            # an out-of-root path is a client error, not a server fault.
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        payload = _result_to_response(result)
        # ScanFileResponse requires ``parsed`` (absent from the shared
        # _result_to_response payload, which also serves ScanTextResponse);
        # without it FastAPI response validation 500s on EVERY successful
        # scan — latent since the endpoint was added, surfaced by the first
        # HTTP-level test of this route.
        payload["parsed"] = True
        return payload

    @app.get("/admin/security/adversarial/report", response_model=None)
    async def get_adversarial_report(
        limit: int = Query(default=50, ge=1, le=_MAX_RECENT),
        category: str | None = Query(default=None),
    ) -> dict[str, object]:
        """Return recent adversarial findings from the detector cache."""
        detector = _get_detector(app)
        categories = detector.get_all_categories()
        patterns_by_cat: dict[str, list[dict[str, object]]] = {}
        for cat in categories:
            cat_str = cat.value if hasattr(cat, "value") else str(cat)
            patterns = detector.get_patterns_by_category(cat)
            if category is not None and cat_str != category:
                continue
            patterns_by_cat[cat_str] = [
                {
                    "id": p.id,
                    "description": p.description,
                    "severity": p.severity.value if hasattr(p.severity, "value") else str(p.severity),
                }
                for p in patterns[:limit]
            ]
        return {
            "generated_at": datetime.now(UTC).isoformat(),
            "categories": list(patterns_by_cat.keys()),
            "patterns_by_category": patterns_by_cat,
            "total_patterns": sum(len(v) for v in patterns_by_cat.values()),
        }
