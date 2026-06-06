"""Model comparison — ranks model profiles by historical benchmark performance."""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


class ModelComparison:
    """Compares model profiles by their historical benchmark scores."""

    def __init__(self, benchmark_repo: Any | None = None) -> None:
        self._repo = benchmark_repo

    async def compare_models(
        self,
        task_type: str | None = None,
        sort_by: str = "composite",
        min_samples: int = 2,
    ) -> dict[str, Any]:
        """Compare all model+prompt combinations for a given task type.

        Returns rankings sorted by the specified sort key.
        """
        if self._repo is None:
            return {"rankings": [], "summary": "No benchmark repository available"}

        try:
            raw = await self._repo.get_aggregate_scores(task_type=task_type)
        except Exception as exc:
            logger.warning("Failed to get benchmark scores: %s", exc)
            return {"rankings": [], "summary": f"Error fetching data: {exc}"}

        if not raw:
            return {"rankings": [], "summary": "No benchmark data available"}

        qualified = [r for r in raw if r.get("sample_count", 0) >= min_samples]
        if sort_by == "cost":
            qualified.sort(key=lambda r: float(r.get("avg_cost", 0)))
        else:
            qualified.sort(key=lambda r: float(r.get("composite_score", 0)), reverse=True)

        rankings = [
            {
                "model_profile_id": r.get("model_profile_id", "unknown"),
                "prompt_profile_id": r.get("prompt_profile_id"),
                "task_type": r.get("task_type", task_type),
                "sample_count": r.get("sample_count", 0),
                "composite_score": round(float(r.get("composite_score", 0)), 4),
                "avg_cost": round(float(r.get("avg_cost", 0)), 6),
                "avg_completion": round(float(r.get("avg_completion", 0)), 4),
                "avg_code_quality": round(float(r.get("avg_code_quality", 0)), 4),
                "avg_instruction": round(float(r.get("avg_instruction", 0)), 4),
                "avg_token_efficiency": round(float(r.get("avg_token_efficiency", 0)), 4),
            }
            for r in qualified
        ]

        best = rankings[0] if rankings else None
        summary_parts = []
        if best:
            summary_parts.append(
                f"Best: {best['model_profile_id']} "
                f"(score={best['composite_score']}, "
                f"samples={best['sample_count']})"
            )
        else:
            summary_parts.append(f"No models with >= {min_samples} samples")

        return {
            "rankings": rankings,
            "summary": ". ".join(summary_parts),
            "total_combinations": len(raw),
            "qualified_combinations": len(qualified),
            "sort_by": sort_by,
        }
