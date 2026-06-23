"""Deterministic verification: AdaptiveRouter task-type weighting changes model selection.

This proves the *routing_roles.weights_for(task_type)* RoleWeights (cost, quality)
actually alter which model the AdaptiveRouter picks, given an IDENTICAL candidate set.

Design
------
Two candidates are returned by a fake async benchmark_repo for EVERY task type:

  * "expensive_high_quality": composite=0.90, avg_cost=1.00  (best raw quality, costliest)
  * "cheap_good_enough":      composite=0.80, avg_cost=0.00  (slightly lower quality, free)

The router's internal rank key is::

    weights.quality * composite - weights.cost * (cost / max_cost)

With max_cost = 1.0 the normalized cost is 1.0 for the expensive model and 0.0 for the
cheap one, so the rank keys are:

  SECURITY_FIX  (cost=0.05, quality=0.95):
      expensive = 0.95*0.90 - 0.05*1.0 = 0.805
      cheap     = 0.95*0.80 - 0.05*0.0 = 0.760   -> EXPENSIVE wins (quality-sensitive)

  DOCUMENTATION (cost=0.40, quality=0.60):
      expensive = 0.60*0.90 - 0.40*1.0 = 0.140
      cheap     = 0.60*0.80 - 0.40*0.0 = 0.480   -> CHEAP wins (cost-sensitive)

Same candidates, different TaskType -> different selected model. That is the proof.

Run (make-only repo)::

    make test-specific TESTFILE='scripts/verify_routing_live.py::test_routing_varies_by_task'
"""

from __future__ import annotations

import pytest

from general_ludd.scoring.router import AdaptiveRouter
from general_ludd.schemas.benchmark import TaskType

EXPENSIVE = "expensive_high_quality"
CHEAP = "cheap_good_enough"


class _FakeBenchmarkRepo:
    """Returns the same two candidates for every task_type query."""

    async def get_aggregate_scores(self, task_type: str) -> list[dict]:
        return [
            {
                "prompt_profile_id": "pp-default",
                "model_profile_id": EXPENSIVE,
                "composite_score": 0.90,
                "avg_cost": 1.00,
                "sample_count": 10,
                "task_type": task_type,
            },
            {
                "prompt_profile_id": "pp-default",
                "model_profile_id": CHEAP,
                "composite_score": 0.80,
                "avg_cost": 0.00,
                "sample_count": 10,
                "task_type": task_type,
            },
        ]


async def _select(task_type: TaskType) -> str:
    # Fresh router per call so the in-memory decision cache can never leak
    # one task type's decision into another (defensive — cache key already
    # encodes task_type, but this keeps the proof airtight).
    router = AdaptiveRouter(benchmark_repo=_FakeBenchmarkRepo(), min_samples=1)
    decision = await router.route(task_type=task_type)
    return decision.selected_model_profile_id


@pytest.mark.asyncio
async def test_routing_varies_by_task() -> None:
    security_pick = await _select(TaskType.SECURITY_FIX)
    docs_pick = await _select(TaskType.DOCUMENTATION)

    print(f"\nSECURITY_FIX  selected -> {security_pick}")
    print(f"DOCUMENTATION selected -> {docs_pick}")

    # Quality-sensitive task tolerates the costlier, higher-quality model.
    assert security_pick == EXPENSIVE, (
        f"SECURITY_FIX should pick the high-quality model, got {security_pick!r}"
    )
    # Cost-sensitive task prefers the cheaper, good-enough model.
    assert docs_pick == CHEAP, (
        f"DOCUMENTATION should pick the cheap model, got {docs_pick!r}"
    )
    # The whole point: identical candidates -> DIFFERENT selection by task type.
    assert security_pick != docs_pick


if __name__ == "__main__":  # pragma: no cover
    import asyncio

    async def _main() -> None:
        print("SECURITY_FIX  ->", await _select(TaskType.SECURITY_FIX))
        print("DOCUMENTATION ->", await _select(TaskType.DOCUMENTATION))

    asyncio.run(_main())
