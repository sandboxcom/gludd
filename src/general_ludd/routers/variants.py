"""Admin endpoint for prompt variant A/B comparison reports (G6)."""

from __future__ import annotations

import logging
from typing import Any, cast

from fastapi import FastAPI

logger = logging.getLogger(__name__)


def register(app: FastAPI, _daemon_state: dict[str, Any]) -> None:
    @app.get(
        "/admin/prompts/variants/report",
        summary="Prompt variant comparison report",
        description="Aggregate A/B outcomes by template and expose which variant is winning.",
    )
    async def admin_variants_report() -> dict[str, Any]:
        event_loop = getattr(app.state, "event_loop", None)
        if event_loop is None:
            return {"templates": {}, "template_count": 0, "note": "EventLoop not running"}

        selector = getattr(event_loop, "_prompt_variant_selector", None)
        if selector is None:
            return {"templates": {}, "template_count": 0, "note": "PromptVariantSelector not wired"}

        metrics = getattr(selector, "variant_metrics", None)
        if metrics is None:
            return {"templates": {}, "template_count": 0, "note": "VariantMetrics not wired"}

        try:
            return cast("dict[str, Any]", metrics.generate_variant_report())
        except Exception:
            logger.warning("Failed to generate variant report", exc_info=True)
            return {"templates": {}, "template_count": 0, "error": "Report generation failed"}
