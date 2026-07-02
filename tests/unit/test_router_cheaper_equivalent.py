"""Unit tests for AdaptiveRouter's 'prefer the cheapest quality-EQUIVALENT
candidate' tie-break (scoring/router.py).

Among candidates the ranker judges of comparable quality/value (their
cost-adjusted rank lands within a NARROW ``adequacy_margin`` of the top rank),
the lowest ``avg_cost_usd`` wins — WITHOUT ever trading away material quality.
When that tie-break actually displaces the top-ranked pick the decision reason
becomes ``"cheaper_equivalent"``; otherwise behaviour is unchanged.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from general_ludd.schemas.benchmark import RoutingCandidate, TaskType
from general_ludd.scoring.router import AdaptiveRouter

PATCH_WEIGHTS = "general_ludd.scoring.router.weights_for"


def _make_agg(
    model_id: str,
    composite: float,
    avg_cost: float,
    prompt_id: str | None = None,
    sample_count: int = 5,
    task_type: str = "bug_fix",
) -> dict:
    return {
        "model_profile_id": model_id,
        "prompt_profile_id": prompt_id if prompt_id is not None else f"pp-{model_id}",
        "composite_score": composite,
        "avg_cost": avg_cost,
        "sample_count": sample_count,
        "task_type": task_type,
    }


@pytest.fixture
def quality_only_weights():
    """Rank == quality (cost weight 0) so the band maths are exact and readable.

    This isolates the tie-break: without a cost term in the rank, two candidates
    of equal quality land on the SAME rank, so the only thing that can break the
    tie is the new cheapest-equivalent logic.
    """
    w = MagicMock()
    w.quality = 1.0
    w.cost = 0.0
    with patch(PATCH_WEIGHTS, return_value=w):
        yield


def _repo(*aggs: dict) -> MagicMock:
    repo = MagicMock()
    repo.get_aggregate_scores = AsyncMock(return_value=list(aggs))
    return repo


# ---------------------------------------------------------------------------
# 1. near-equal quality, different cost -> cheaper wins, reason cheaper_equivalent
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cheaper_equivalent_wins_when_quality_equal(quality_only_weights):
    # Expensive listed FIRST so it is the natural top-ranked winner (max() keeps
    # the first of equal ranks); the equally-good cheaper one must displace it.
    expensive = _make_agg("exp", composite=0.80, avg_cost=0.05)
    cheap = _make_agg("cheap", composite=0.80, avg_cost=0.01)
    router = AdaptiveRouter(benchmark_repo=_repo(expensive, cheap), min_samples=3)

    decision = await router.route(TaskType.BUG_FIX)

    assert decision.fallback is False
    assert decision.selected_model_profile_id == "cheap"
    assert decision.reason == "cheaper_equivalent"
    assert decision.estimated_cost_usd == pytest.approx(0.01)


@pytest.mark.asyncio
async def test_cheaper_equivalent_wins_within_narrow_band(quality_only_weights):
    # Expensive is SLIGHTLY better (0.01 quality gap, inside the 0.02 band) yet
    # top-ranked; the near-equal cheaper candidate still wins on cost.
    expensive = _make_agg("exp", composite=0.81, avg_cost=0.05)
    cheap = _make_agg("cheap", composite=0.80, avg_cost=0.01)
    router = AdaptiveRouter(benchmark_repo=_repo(expensive, cheap), min_samples=3)

    decision = await router.route(TaskType.BUG_FIX)

    assert decision.selected_model_profile_id == "cheap"
    assert decision.reason == "cheaper_equivalent"


# ---------------------------------------------------------------------------
# 2. materially better quality (gap > margin) beats a cheaper worse one
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_material_quality_beats_cheaper_worse(quality_only_weights):
    # 0.10 quality gap >> 0.02 margin: quality must win; tie-break must NOT fire.
    better = _make_agg("best", composite=0.90, avg_cost=0.05)
    cheap = _make_agg("cheap", composite=0.80, avg_cost=0.01)
    router = AdaptiveRouter(benchmark_repo=_repo(better, cheap), min_samples=3)

    decision = await router.route(TaskType.BUG_FIX)

    assert decision.selected_model_profile_id == "best"
    assert decision.reason == "best_historical_score"
    assert decision.reason != "cheaper_equivalent"


@pytest.mark.asyncio
async def test_quality_gap_just_outside_band_does_not_flip(quality_only_weights):
    # 0.03 gap sits just OUTSIDE the 0.02 band — the cheaper candidate is no
    # longer "equivalent", so quality wins and the reason is unchanged.
    better = _make_agg("best", composite=0.83, avg_cost=0.05)
    cheap = _make_agg("cheap", composite=0.80, avg_cost=0.01)
    router = AdaptiveRouter(benchmark_repo=_repo(better, cheap), min_samples=3)

    decision = await router.route(TaskType.BUG_FIX)

    assert decision.selected_model_profile_id == "best"
    assert decision.reason == "best_historical_score"


# ---------------------------------------------------------------------------
# 3. adequacy_margin == 0.0 reproduces the original winner (regression guard)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_margin_zero_disables_tiebreak(quality_only_weights):
    expensive = _make_agg("exp", composite=0.80, avg_cost=0.05)
    cheap = _make_agg("cheap", composite=0.80, avg_cost=0.01)
    router = AdaptiveRouter(
        benchmark_repo=_repo(expensive, cheap),
        min_samples=3,
        adequacy_margin=0.0,
    )

    decision = await router.route(TaskType.BUG_FIX)

    # Original behaviour: top-ranked (first of equal ranks) wins, no rename.
    assert decision.selected_model_profile_id == "exp"
    assert decision.reason == "best_historical_score"


# ---------------------------------------------------------------------------
# extra guards: single candidate + equal-cost band never emit cheaper_equivalent
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_single_candidate_unchanged(quality_only_weights):
    only = _make_agg("solo", composite=0.80, avg_cost=0.02)
    router = AdaptiveRouter(benchmark_repo=_repo(only), min_samples=3)

    decision = await router.route(TaskType.BUG_FIX)

    assert decision.selected_model_profile_id == "solo"
    assert decision.reason == "best_historical_score"


@pytest.mark.asyncio
async def test_equal_cost_band_does_not_flip(quality_only_weights):
    # Two equally-good, equally-priced candidates: no STRICTLY cheaper option
    # exists, so the top-ranked pick and its reason stay put.
    a = _make_agg("a", composite=0.80, avg_cost=0.02)
    b = _make_agg("b", composite=0.80, avg_cost=0.02)
    router = AdaptiveRouter(benchmark_repo=_repo(a, b), min_samples=3)

    decision = await router.route(TaskType.BUG_FIX)

    assert decision.selected_model_profile_id == "a"
    assert decision.reason == "best_historical_score"


# ---------------------------------------------------------------------------
# direct unit test of the helper
# ---------------------------------------------------------------------------


def _cand(model_id: str, cost: float) -> RoutingCandidate:
    return RoutingCandidate(
        prompt_profile_id=None,
        model_profile_id=model_id,
        composite_score=0.8,
        avg_cost_usd=cost,
        sample_count=5,
        task_type=TaskType.BUG_FIX,
    )


def test_helper_flips_to_cheaper_within_band():
    # ranked tuples are (rank, quality, candidate, reason). exp is top-ranked
    # but the near-equal-QUALITY (0.79 vs Q*=0.80) cheaper candidate wins.
    router = AdaptiveRouter(adequacy_margin=0.02)
    ranked = [
        (0.60, 0.80, _cand("exp", 0.05), None),
        (0.59, 0.79, _cand("cheap", 0.01), None),
    ]
    cand, reason = router._select_cheapest_equivalent(ranked)
    assert cand.model_profile_id == "cheap"
    assert reason == "cheaper_equivalent"


def test_helper_keeps_top_when_quality_gap_exceeds_band():
    # cheaper candidate is 0.30 lower on QUALITY — far outside the band — so it
    # is NOT equivalent; the top-ranked winner and its reason are preserved.
    router = AdaptiveRouter(adequacy_margin=0.02)
    ranked = [
        (0.60, 0.80, _cand("exp", 0.05), "best_historical_score_similarity"),
        (0.50, 0.50, _cand("cheap", 0.01), None),
    ]
    cand, reason = router._select_cheapest_equivalent(ranked)
    assert cand.model_profile_id == "exp"
    assert reason == "best_historical_score_similarity"


def test_helper_does_not_trade_material_quality_for_cost():
    # Regression guard for the rank-space double-count flaw: 'cheap' has a much
    # lower QUALITY (0.55 vs Q*=0.90) but a cost bonus lifts its RANK to within
    # 0.02 of the top rank. A quality-space band must still reject it.
    router = AdaptiveRouter(adequacy_margin=0.02)
    ranked = [
        (0.520, 0.90, _cand("exp", 0.05), None),
        (0.512, 0.55, _cand("cheap", 0.00), None),
    ]
    cand, reason = router._select_cheapest_equivalent(ranked)
    assert cand.model_profile_id == "exp"
    assert reason is None


def test_helper_ignores_non_finite_cheaper_cost():
    router = AdaptiveRouter(adequacy_margin=0.02)
    ranked = [
        (0.60, 0.80, _cand("exp", 0.05), None),
        (0.59, 0.80, _cand("nan", float("nan")), None),
    ]
    cand, reason = router._select_cheapest_equivalent(ranked)
    assert cand.model_profile_id == "exp"
    assert reason is None
