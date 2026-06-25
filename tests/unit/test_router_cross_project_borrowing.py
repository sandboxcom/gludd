"""Project-hierarchy phase 3: AdaptiveRouter cross-project knowledge borrowing.

Borrowing is OFF by default; with the flag OFF the router behaves EXACTLY as
before phase 3 (see test_adaptive_router.py / test_scoring.py for the unchanged
behaviour — those suites are the binding regression contract). These tests cover
the ON path: a project with thin own history borrows a related project's proven
pick, weighted DOWN by the project-relationship axis with edge-distance decay,
and always strictly below an equally-scored own pick.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from general_ludd.schemas.benchmark import TaskType
from general_ludd.scoring.router import AdaptiveRouter

# asyncio mode is AUTO in pyproject.toml, so async tests run without an explicit
# mark; sync helper-formula tests run normally. No module-level asyncio mark
# (it would wrongly flag the sync tests).

PATCH_WEIGHTS = "general_ludd.scoring.router.weights_for"


def _agg(
    model_id: str,
    *,
    project_id: str | None = None,
    composite: float = 0.85,
    avg_cost: float = 0.01,
    sample_count: int = 5,
    task_type: str = "bug_fix",
    prompt_id: str | None = "prompt-1",
) -> dict:
    return {
        "model_profile_id": model_id,
        "prompt_profile_id": prompt_id,
        "composite_score": composite,
        "avg_cost": avg_cost,
        "sample_count": sample_count,
        "task_type": task_type,
        "project_id": project_id,
    }


def _edge(related_project_id: str, relation_type: str, controlled: bool = True):
    """A minimal stand-in for ProjectRelationshipModel as the router reads it."""
    return SimpleNamespace(
        related_project_id=related_project_id,
        relation_type=relation_type,
        controlled_by_gludd=controlled,
    )


def _scores_by_project(table: dict[str | None, list[dict]]):
    """Build a get_aggregate_scores side_effect keyed on the project_id kwarg.

    The router omits the project_id kwarg entirely when it is None (backward
    compat), so this resolves the project from kwargs.get('project_id').
    """

    async def _side_effect(*args, **kwargs):
        pid = kwargs.get("project_id")
        return list(table.get(pid, []))

    return _side_effect


def _rel_repo(edges_by_project: dict[str, list]):
    repo = MagicMock()

    async def _list_for_project(project_id, *a, **k):
        return list(edges_by_project.get(project_id, []))

    repo.list_for_project = AsyncMock(side_effect=_list_for_project)
    return repo


@pytest.fixture(autouse=True)
def patch_weights():
    w = MagicMock()
    w.quality = 0.8
    w.cost = 0.2
    with patch(PATCH_WEIGHTS, return_value=w):
        yield


# ---------------------------------------------------------------------------
# OFF by default — no borrowing, router unchanged
# ---------------------------------------------------------------------------


async def test_borrowing_off_by_default_no_relationship_query():
    """Default construction: enable_cross_project_borrowing is False and the
    relationship repo is NEVER consulted even with thin own history."""
    repo = MagicMock()
    repo.get_aggregate_scores = AsyncMock(return_value=[])
    rel_repo = _rel_repo({})
    router = AdaptiveRouter(
        benchmark_repo=repo,
        project_id="proj-child",
        relationship_repo=rel_repo,
        # flag intentionally omitted → default OFF
    )
    assert router._enable_cross_project_borrowing is False
    decision = await router.route(TaskType.BUG_FIX)
    assert decision.fallback is True
    rel_repo.list_for_project.assert_not_called()


async def test_borrowing_off_does_not_pass_project_id_kwarg():
    """With no project set, the repo is called with the EXACT pre-phase-3
    signature get_aggregate_scores(task_type=...) — no project_id kwarg —
    so existing assert_called_with(task_type=...) tests keep passing."""
    repo = MagicMock()
    repo.get_aggregate_scores = AsyncMock(return_value=[_agg("model-a")])
    router = AdaptiveRouter(benchmark_repo=repo)  # project_id None, flag OFF
    await router.route(TaskType.BUG_FIX)
    repo.get_aggregate_scores.assert_called_with(task_type="bug_fix")


# ---------------------------------------------------------------------------
# HEADLINE: thin own history borrows the parent's best model
# ---------------------------------------------------------------------------


async def test_thin_child_borrows_parent_best_model():
    """A child with NO own history borrows the parent's proven model and reports
    reason='inherited_parent_history'."""
    table = {
        "proj-child": [],  # thin/empty own history
        "proj-parent": [_agg("parent-model", project_id="proj-parent")],
    }
    repo = MagicMock()
    repo.get_aggregate_scores = AsyncMock(side_effect=_scores_by_project(table))
    rel_repo = _rel_repo(
        {"proj-child": [_edge("proj-parent", "parent", controlled=True)]}
    )
    router = AdaptiveRouter(
        benchmark_repo=repo,
        project_id="proj-child",
        relationship_repo=rel_repo,
        enable_cross_project_borrowing=True,
    )
    decision = await router.route(TaskType.BUG_FIX)
    assert decision.fallback is False
    assert decision.selected_model_profile_id == "parent-model"
    assert decision.reason == "inherited_parent_history"


# ---------------------------------------------------------------------------
# Own history always outranks borrowed at equal score
# ---------------------------------------------------------------------------


async def test_own_history_outranks_borrowed_at_equal_score():
    """An own pick (weight 1.0) wins over a parent pick (weight 0.8) when both
    have the same composite score — borrowed is strictly ≤ own."""
    table = {
        "proj-child": [_agg("own-model", project_id="proj-child", composite=0.8)],
        "proj-parent": [_agg("parent-model", project_id="proj-parent", composite=0.8)],
    }
    repo = MagicMock()
    repo.get_aggregate_scores = AsyncMock(side_effect=_scores_by_project(table))
    rel_repo = _rel_repo(
        {"proj-child": [_edge("proj-parent", "parent")]}
    )
    router = AdaptiveRouter(
        benchmark_repo=repo,
        project_id="proj-child",
        relationship_repo=rel_repo,
        enable_cross_project_borrowing=True,
        min_samples=3,
    )
    # Own history here is 5 samples (>= min_samples) so the borrow path is not
    # even entered; the own pick must win regardless.
    decision = await router.route(TaskType.BUG_FIX)
    assert decision.selected_model_profile_id == "own-model"
    assert decision.reason == "best_historical_score"


# ---------------------------------------------------------------------------
# Edge-decay monotonicity: parent (0.8) > grandparent (0.4)
# ---------------------------------------------------------------------------


async def test_edge_decay_parent_outweighs_grandparent():
    """At equal borrowed composite, the nearer parent (d=1, weight 0.8) beats the
    grandparent (d=2, weight 0.8*0.5=0.4)."""
    table = {
        "proj-child": [],
        "proj-parent": [_agg("parent-model", project_id="proj-parent", composite=0.7)],
        "proj-gp": [_agg("gp-model", project_id="proj-gp", composite=0.7)],
    }
    repo = MagicMock()
    repo.get_aggregate_scores = AsyncMock(side_effect=_scores_by_project(table))
    rel_repo = _rel_repo(
        {
            "proj-child": [_edge("proj-parent", "parent")],
            "proj-parent": [_edge("proj-gp", "parent")],
        }
    )
    router = AdaptiveRouter(
        benchmark_repo=repo,
        project_id="proj-child",
        relationship_repo=rel_repo,
        enable_cross_project_borrowing=True,
    )
    decision = await router.route(TaskType.BUG_FIX)
    assert decision.selected_model_profile_id == "parent-model"


def test_project_rel_weight_decay_is_monotone():
    """Direct check of the decay formula: parent(0.8) > grandparent(0.4) >
    great-grandparent(0.2); controlled ≥ uncontrolled at equal distance."""
    r = AdaptiveRouter(edge_decay=0.5, external_penalty=0.5)
    parent = r._project_rel_weight("parent", 1, True)
    grandparent = r._project_rel_weight("parent", 2, True)
    great = r._project_rel_weight("parent", 3, True)
    assert parent == pytest.approx(0.8)
    assert grandparent == pytest.approx(0.4)
    assert great == pytest.approx(0.2)
    assert parent > grandparent > great
    # control factor: uncontrolled parent is halved
    uncontrolled = r._project_rel_weight("parent", 1, False)
    assert uncontrolled == pytest.approx(0.4)
    assert parent > uncontrolled


# ---------------------------------------------------------------------------
# Below-min_borrow_weight dropped
# ---------------------------------------------------------------------------


def test_below_min_borrow_weight_dropped_to_zero():
    """A composite multiplier under min_borrow_weight collapses to 0.0 so the
    candidate is dropped (a distant uncontrolled external edge)."""
    r = AdaptiveRouter(
        project_id="proj-self",
        enable_cross_project_borrowing=True,
        edge_decay=0.5,
        external_penalty=0.5,
        min_borrow_weight=0.05,
    )
    rel_map = {"proj-far": ("external", 3, False)}
    # external base 0.4 * decay 0.5^2 (=0.25) * control 0.5 = 0.05; with a low
    # task similarity the product drops below the 0.05 floor → 0.0.
    w = r._composite_similarity_weight(0.5, "proj-far", rel_map)
    assert w == 0.0


def test_composite_weight_off_returns_task_weight_alone():
    """Backward compat: borrowing OFF → _composite_similarity_weight returns the
    plain task-similarity weight, ignoring the project axis entirely."""
    r = AdaptiveRouter(enable_cross_project_borrowing=False, project_id="proj-self")
    rel_map = {"proj-parent": ("parent", 1, True)}
    assert r._composite_similarity_weight(1.0, "proj-parent", rel_map) == pytest.approx(1.0)


def test_composite_weight_own_project_returns_task_weight_alone():
    """Own-project candidate → weight 1.0 (no project-axis discount) even with
    borrowing ON."""
    r = AdaptiveRouter(enable_cross_project_borrowing=True, project_id="proj-self")
    rel_map = {"proj-self": ("own", 0, True)}
    assert r._composite_similarity_weight(1.0, "proj-self", rel_map) == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# BFS relationship map
# ---------------------------------------------------------------------------


async def test_build_relationship_map_bfs_and_cycle_safe():
    """The BFS walks resolved edges, records edge distance, and is cycle-safe
    (a parent<->child mutual declaration does not loop)."""
    rel_repo = _rel_repo(
        {
            "proj-a": [_edge("proj-b", "parent")],
            "proj-b": [_edge("proj-a", "child"), _edge("proj-c", "parent")],
        }
    )
    r = AdaptiveRouter(
        project_id="proj-a",
        relationship_repo=rel_repo,
        enable_cross_project_borrowing=True,
    )
    rel_map = await r._build_relationship_map()
    assert rel_map["proj-b"] == ("parent", 1, True)
    assert rel_map["proj-c"] == ("parent", 2, True)
    # proj-a (self) is never re-added despite the back-edge from proj-b.
    assert "proj-a" not in rel_map


async def test_build_relationship_map_empty_when_off():
    """Borrowing OFF → empty map, repo never queried."""
    rel_repo = _rel_repo({"proj-a": [_edge("proj-b", "parent")]})
    r = AdaptiveRouter(
        project_id="proj-a",
        relationship_repo=rel_repo,
        enable_cross_project_borrowing=False,
    )
    assert await r._build_relationship_map() == {}
    rel_repo.list_for_project.assert_not_called()
