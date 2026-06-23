"""Unit tests for AdaptiveRouter + TaskEmbeddingStore integration (Tier 2 Step 5).

Step 5 of the Tier 2 RAG plan extends :class:`AdaptiveRouter` to consult a
:class:`TaskEmbeddingStore` when selecting a historical winner. With a store
attached the router queries ALL task types (not just the exact match) and
weights each candidate's quality by cosine similarity to the routing task.
Without a store the router preserves its exact-match behavior.

These tests mock both the benchmark repo and the embedding store so they run
fully offline (no DB, no embedder calls).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from general_ludd.schemas.benchmark import TaskType
from general_ludd.scoring.router import AdaptiveRouter

PATCH_WEIGHTS = "general_ludd.scoring.router.weights_for"


def _make_agg(
    model_id: str = "model-a",
    prompt_id: str | None = "prompt-1",
    composite: float = 0.85,
    avg_cost: float = 0.01,
    sample_count: int = 5,
    task_type: str = "bug_fix",
) -> dict:
    return {
        "model_profile_id": model_id,
        "prompt_profile_id": prompt_id,
        "composite_score": composite,
        "avg_cost": avg_cost,
        "sample_count": sample_count,
        "task_type": task_type,
    }


def _mock_store(
    sims: dict[str, float] | None = None,
    raise_keyerror: bool = False,
) -> MagicMock:
    """A stand-in for TaskEmbeddingStore — only similarity_to is exercised here."""
    store = MagicMock()
    if raise_keyerror:
        store.similarity_to = AsyncMock(side_effect=KeyError("bug_fix"))
    else:
        store.similarity_to = AsyncMock(return_value=sims or {})
    return store


@pytest.fixture(autouse=True)
def patch_weights():
    w = MagicMock()
    w.quality = 0.8
    w.cost = 0.2
    with patch(PATCH_WEIGHTS, return_value=w):
        yield


# ---------------------------------------------------------------------------
# Backwards compatibility: no embedding_store
# ---------------------------------------------------------------------------


async def test_route_without_store_queries_exact_task_type():
    """No embedding_store → router queries only the exact task type."""
    agg = _make_agg(task_type="bug_fix")
    mock_repo = MagicMock()
    mock_repo.get_aggregate_scores = AsyncMock(return_value=[agg])
    router = AdaptiveRouter(benchmark_repo=mock_repo)

    await router.route(TaskType.BUG_FIX)

    mock_repo.get_aggregate_scores.assert_called_with(task_type="bug_fix")


async def test_route_without_store_reason_unchanged():
    """No store → reason stays 'best_historical_score' (no similarity suffix)."""
    agg = _make_agg(task_type="bug_fix")
    mock_repo = MagicMock()
    mock_repo.get_aggregate_scores = AsyncMock(return_value=[agg])
    router = AdaptiveRouter(benchmark_repo=mock_repo)

    decision = await router.route(TaskType.BUG_FIX)

    assert decision.reason == "best_historical_score"


# ---------------------------------------------------------------------------
# Embedding store triggers cross-task-type query
# ---------------------------------------------------------------------------


async def test_route_with_store_queries_all_task_types():
    """With embedding_store, router queries task_type=None (all task types)."""
    agg = _make_agg(task_type="bug_fix")
    mock_repo = MagicMock()
    mock_repo.get_aggregate_scores = AsyncMock(return_value=[agg])
    store = _mock_store(sims={"debugging": 0.5})
    router = AdaptiveRouter(benchmark_repo=mock_repo, embedding_store=store)

    await router.route(TaskType.BUG_FIX)

    mock_repo.get_aggregate_scores.assert_called_with(task_type=None)


async def test_store_similarity_to_called_with_query_task():
    """The store's similarity_to is queried with the routing task type."""
    agg = _make_agg(task_type="bug_fix")
    mock_repo = MagicMock()
    mock_repo.get_aggregate_scores = AsyncMock(return_value=[agg])
    store = _mock_store(sims={})
    router = AdaptiveRouter(benchmark_repo=mock_repo, embedding_store=store)

    await router.route(TaskType.BUG_FIX)

    store.similarity_to.assert_awaited_once_with(TaskType.BUG_FIX)


# ---------------------------------------------------------------------------
# Cross-type borrowing (the main Tier 2 feature)
# ---------------------------------------------------------------------------


async def test_cross_type_borrowing_returns_lending_candidate():
    """No exact-match history, but a similar task type's data is used (not fallback)."""
    debugging_agg = _make_agg(
        model_id="model-debug",
        prompt_id="prompt-debug",
        composite=0.9,
        task_type="debugging",
    )
    mock_repo = MagicMock()
    mock_repo.get_aggregate_scores = AsyncMock(return_value=[debugging_agg])
    store = _mock_store(sims={"debugging": 0.9})
    router = AdaptiveRouter(benchmark_repo=mock_repo, embedding_store=store)

    decision = await router.route(TaskType.BUG_FIX)

    assert decision.fallback is False
    assert decision.selected_model_profile_id == "model-debug"
    assert decision.selected_prompt_profile_id == "prompt-debug"


async def test_exact_match_preferred_over_lending_neighbor():
    """When both exact-match and a neighbor exist with equal composite, exact wins."""
    bug_agg = _make_agg(
        model_id="m-bug", prompt_id="p-bug", composite=0.7, task_type="bug_fix"
    )
    doc_agg = _make_agg(
        model_id="m-doc", prompt_id="p-doc", composite=0.7, task_type="documentation"
    )
    mock_repo = MagicMock()
    mock_repo.get_aggregate_scores = AsyncMock(return_value=[doc_agg, bug_agg])
    store = _mock_store(sims={"documentation": 0.4})
    router = AdaptiveRouter(benchmark_repo=mock_repo, embedding_store=store)

    decision = await router.route(TaskType.BUG_FIX)

    assert decision.selected_model_profile_id == "m-bug"
    assert decision.selected_prompt_profile_id == "p-bug"


async def test_borrowed_decision_reason_reflects_similarity():
    """When the winner is from a different task type, reason reflects cross-type borrowing."""
    debugging_agg = _make_agg(
        model_id="model-debug", composite=0.95, task_type="debugging"
    )
    mock_repo = MagicMock()
    mock_repo.get_aggregate_scores = AsyncMock(return_value=[debugging_agg])
    store = _mock_store(sims={"debugging": 0.95})
    router = AdaptiveRouter(benchmark_repo=mock_repo, embedding_store=store)

    decision = await router.route(TaskType.BUG_FIX)

    assert decision.fallback is False
    assert decision.reason == "best_historical_score_similarity"


# ---------------------------------------------------------------------------
# Similarity weighting math
# ---------------------------------------------------------------------------


async def test_exact_match_preferred_on_equal_composite():
    """Equal composite: exact match (sim=1.0) outranks a similar neighbor."""
    bug_agg = _make_agg(model_id="m-bug", composite=0.8, task_type="bug_fix")
    debug_agg = _make_agg(model_id="m-debug", composite=0.8, task_type="debugging")
    mock_repo = MagicMock()
    mock_repo.get_aggregate_scores = AsyncMock(return_value=[bug_agg, debug_agg])
    store = _mock_store(sims={"debugging": 0.9})
    router = AdaptiveRouter(benchmark_repo=mock_repo, embedding_store=store)

    decision = await router.route(TaskType.BUG_FIX)

    assert decision.selected_model_profile_id == "m-bug"


async def test_similar_neighbor_outranks_dissimilar_neighbor():
    """A more-similar neighbor outranks a less-similar one with equal composite."""
    debug_agg = _make_agg(model_id="m-debug", composite=0.8, task_type="debugging")
    doc_agg = _make_agg(model_id="m-doc", composite=0.8, task_type="documentation")
    mock_repo = MagicMock()
    mock_repo.get_aggregate_scores = AsyncMock(return_value=[debug_agg, doc_agg])
    store = _mock_store(sims={"debugging": 0.9, "documentation": 0.1})
    router = AdaptiveRouter(benchmark_repo=mock_repo, embedding_store=store)

    decision = await router.route(TaskType.BUG_FIX)

    assert decision.selected_model_profile_id == "m-debug"


# ---------------------------------------------------------------------------
# Knobs: similarity_alpha and similarity_floor
# ---------------------------------------------------------------------------


async def test_alpha_zero_with_floor_one_treats_all_tasks_equally():
    """alpha=0 + floor=1: weight is 1 for everyone → highest raw composite wins."""
    debug_agg = _make_agg(model_id="m-debug", composite=0.6, task_type="debugging")
    doc_agg = _make_agg(model_id="m-doc", composite=0.9, task_type="documentation")
    mock_repo = MagicMock()
    mock_repo.get_aggregate_scores = AsyncMock(return_value=[debug_agg, doc_agg])
    # debugging is more similar, but alpha=0 means sim is ignored
    store = _mock_store(sims={"debugging": 0.95, "documentation": 0.05})
    router = AdaptiveRouter(
        benchmark_repo=mock_repo,
        embedding_store=store,
        similarity_alpha=0.0,
        similarity_floor=1.0,
    )

    decision = await router.route(TaskType.BUG_FIX)

    assert decision.selected_model_profile_id == "m-doc"


async def test_floor_zero_alpha_one_penalizes_dissimilar_high_composite():
    """alpha=1, floor=0: dissimilar candidate's quality contribution shrinks by sim."""
    # bug_fix composite is LOW but exact-match (sim=1.0); doc composite is HIGH but sim=0.05.
    # weighted_quality(bug) = 0.4 * 1.0 = 0.4
    # weighted_quality(doc) = 0.95 * 0.05 = 0.0475
    # → bug_fix wins despite lower raw composite.
    bug_agg = _make_agg(model_id="m-bug", composite=0.4, task_type="bug_fix")
    doc_agg = _make_agg(model_id="m-doc", composite=0.95, task_type="documentation")
    mock_repo = MagicMock()
    mock_repo.get_aggregate_scores = AsyncMock(return_value=[bug_agg, doc_agg])
    store = _mock_store(sims={"documentation": 0.05})
    router = AdaptiveRouter(
        benchmark_repo=mock_repo,
        embedding_store=store,
        similarity_alpha=1.0,
        similarity_floor=0.0,
    )

    decision = await router.route(TaskType.BUG_FIX)

    assert decision.selected_model_profile_id == "m-bug"


# ---------------------------------------------------------------------------
# Graceful fallback
# ---------------------------------------------------------------------------


async def test_keyerror_on_similarity_falls_back_to_exact_match():
    """If store.similarity_to raises KeyError (no embedding), router uses exact-match path."""
    agg = _make_agg(task_type="bug_fix")
    mock_repo = MagicMock()
    mock_repo.get_aggregate_scores = AsyncMock(return_value=[agg])
    store = _mock_store(raise_keyerror=True)
    router = AdaptiveRouter(benchmark_repo=mock_repo, embedding_store=store)

    decision = await router.route(TaskType.BUG_FIX)

    assert decision.fallback is False
    assert decision.selected_model_profile_id == "model-a"
    # The exact-match path was taken (task_type=<value>, not None)
    mock_repo.get_aggregate_scores.assert_called_with(task_type="bug_fix")


async def test_empty_aggregates_with_store_returns_fallback():
    """No historical data at all → still falls back, even with store attached."""
    mock_repo = MagicMock()
    mock_repo.get_aggregate_scores = AsyncMock(return_value=[])
    store = _mock_store(sims={"debugging": 0.9})
    router = AdaptiveRouter(benchmark_repo=mock_repo, embedding_store=store)

    decision = await router.route(TaskType.BUG_FIX)

    assert decision.fallback is True
    assert decision.reason == "insufficient_historical_data"


# ---------------------------------------------------------------------------
# Filters still apply in embedding mode
# ---------------------------------------------------------------------------


async def test_min_samples_filter_in_embedding_mode():
    """Candidates below min_samples are excluded even when borrowing cross-type."""
    thin_agg = _make_agg(
        model_id="m-thin", composite=0.95, sample_count=2, task_type="debugging"
    )
    mock_repo = MagicMock()
    mock_repo.get_aggregate_scores = AsyncMock(return_value=[thin_agg])
    store = _mock_store(sims={"debugging": 0.95})
    router = AdaptiveRouter(
        benchmark_repo=mock_repo, embedding_store=store, min_samples=3
    )

    decision = await router.route(TaskType.BUG_FIX)

    assert decision.fallback is True


async def test_health_tracker_filters_in_embedding_mode():
    """Unhealthy models are excluded even when borrowing cross-type."""
    agg = _make_agg(model_id="m-sick", composite=0.95, task_type="debugging")
    mock_repo = MagicMock()
    mock_repo.get_aggregate_scores = AsyncMock(return_value=[agg])
    health = MagicMock()
    health.is_healthy = MagicMock(return_value=False)
    store = _mock_store(sims={"debugging": 0.95})
    router = AdaptiveRouter(
        benchmark_repo=mock_repo, embedding_store=store, health_tracker=health
    )

    decision = await router.route(TaskType.BUG_FIX)

    assert decision.fallback is True
    health.is_healthy.assert_called_with("m-sick", admit_probe=False)


# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------


def test_router_defaults_have_no_store():
    """AdaptiveRouter() without embedding_store has it as None (backwards compat)."""
    router = AdaptiveRouter()
    assert router._embedding_store is None
    assert router._similarity_alpha == 1.0
    assert router._similarity_floor == 0.0
