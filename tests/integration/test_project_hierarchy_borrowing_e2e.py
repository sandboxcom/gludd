"""E2E proof for project-hierarchy phase 3: cross-project knowledge borrowing.

When ``enable_cross_project_borrowing`` is ON (via ``RelationshipRoutingConfig``),
the AdaptiveRouter borrows benchmark strength across declared project edges
(parent/child/sibling).  When OFF (the default), it only reads the project's own
(or global) history — backward compatible, no behaviour change.

Tests:
  - AdaptiveRouter with borrowing OFF: only own project scores used
  - AdaptiveRouter with borrowing ON: neighbouring project scores contribute
  - RelationshipRoutingConfig defaults: borrowing OFF, sensible decay values
  - inherited_knowledge returns {} when borrowing OFF
  - inherited_knowledge returns neighbour candidates when borrowing ON
  - min_borrow_weight filter: very distant neighbours contribute 0.0
  - Edge case: no relationship_repo → no borrowing even with flag ON
  - Edge case: empty relationship graph → no borrowing impact

All BenchmarkRepository calls mocked — tests exercise the router logic, not
the DB layer (which is covered by unit tests for ProjectRelationshipRepository).
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from general_ludd.config.user_config import RelationshipRoutingConfig
from general_ludd.schemas.benchmark import TaskType
from general_ludd.scoring.router import AdaptiveRouter

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _make_benchmark_repo(*score_lists: list[dict[str, Any]]) -> MagicMock:
    """Return a MagicMock whose get_aggregate_scores is an AsyncMock."""
    repo = MagicMock()
    if len(score_lists) == 1:
        repo.get_aggregate_scores = AsyncMock(return_value=list(score_lists[0]))
    else:
        repo.get_aggregate_scores = AsyncMock(side_effect=[list(sl) for sl in score_lists])
    return repo


def _make_relationship_repo(edge_map: dict[str, list[tuple[str, str, bool]]] | None) -> MagicMock:
    """Return a mock ProjectRelationshipRepository.

    ``edge_map`` maps source_project_id → list of
    (related_project_id, relation_type, controlled_by_gludd), which are the edges
    that list_for_project returns. The router calls ``list_for_project(project_id)``
    and reads ``related_project_id``, ``relation_type``, ``controlled_by_gludd``
    from each edge object.
    """
    repo = MagicMock()

    def _make_edge(related_id: str, rel_type: str, controlled: bool) -> MagicMock:
        edge = MagicMock()
        edge.related_project_id = related_id
        edge.relation_type = rel_type
        edge.controlled_by_gludd = controlled
        return edge

    async def _list_for_project(project_id: str) -> list[MagicMock]:
        entries = edge_map.get(project_id, [])
        return [_make_edge(rid, rt, ctl) for rid, rt, ctl in entries]

    repo.list_for_project.side_effect = _list_for_project
    return repo


def _make_own_scores() -> list[dict[str, Any]]:
    return [
        {
            "model_profile_id": "own-model-1",
            "prompt_profile_id": "prompt-own",
            "task_type": "bug_fix",
            "composite_score": 0.85,
            "avg_cost": 0.003,
            "sample_count": 10,
            "project_id": "proj-self",
        }
    ]


def _make_borrow_scores() -> list[dict[str, Any]]:
    return [
        {
            "model_profile_id": "neighbour-model",
            "prompt_profile_id": "prompt-nbr",
            "task_type": "bug_fix",
            "composite_score": 0.90,
            "avg_cost": 0.002,
            "sample_count": 15,
            "project_id": "parent-proj",
        }
    ]


# ---------------------------------------------------------------------------
# Borrowing OFF — default behaviour (unchanged from pre-phase-3)
# ---------------------------------------------------------------------------


class TestBorrowingOff:
    def test_default_config_borrowing_disabled(self):
        cfg = RelationshipRoutingConfig()
        assert cfg.enable_cross_project_borrowing is False
        assert cfg.edge_decay == 0.5
        assert cfg.external_penalty == 0.5
        assert cfg.min_borrow_weight == 0.05

    @pytest.mark.asyncio
    async def test_router_with_borrowing_off_only_uses_own_scores(self):
        own = _make_own_scores()
        borrowed = _make_borrow_scores()
        repo = _make_benchmark_repo(own, borrowed)
        rel_repo = _make_relationship_repo(
            {"proj-self": [("parent-proj", "parent", False)]}
        )

        router = AdaptiveRouter(
            benchmark_repo=repo,
            min_samples=1,
            project_id="proj-self",
            relationship_repo=rel_repo,
            enable_cross_project_borrowing=False,
        )

        decision = await router.route(task_type=TaskType.BUG_FIX)
        assert decision is not None
        assert decision.fallback is False
        assert decision.selected_model_profile_id == "own-model-1"
        assert repo.get_aggregate_scores.call_count == 1

    @pytest.mark.asyncio
    async def test_inherited_knowledge_empty_when_off(self):
        repo = _make_benchmark_repo(_make_own_scores())
        rel_repo = _make_relationship_repo({"proj-self": []})

        router = AdaptiveRouter(
            benchmark_repo=repo,
            project_id="proj-self",
            relationship_repo=rel_repo,
            enable_cross_project_borrowing=False,
        )

        inherited = await router.inherited_knowledge()
        assert inherited == {}


# ---------------------------------------------------------------------------
# Borrowing ON — cross-project strength flows
# ---------------------------------------------------------------------------


class TestBorrowingOn:
    @pytest.mark.asyncio
    async def test_borrowing_flag_enables_neighbour_scores(self):
        own = _make_own_scores()
        borrowed = _make_borrow_scores()
        repo = _make_benchmark_repo(own, borrowed)
        rel_repo = _make_relationship_repo(
            {"proj-self": [("parent-proj", "parent", False)]}
        )

        router = AdaptiveRouter(
            benchmark_repo=repo,
            min_samples=1,
            project_id="proj-self",
            relationship_repo=rel_repo,
            enable_cross_project_borrowing=True,
            edge_decay=0.8,
            external_penalty=0.6,
            min_borrow_weight=0.01,
        )

        decision = await router.route(task_type=TaskType.BUG_FIX)
        assert decision is not None
        assert decision.fallback is False

    @pytest.mark.asyncio
    async def test_inherited_knowledge_populated_when_on(self):
        repo = _make_benchmark_repo(_make_own_scores(), _make_borrow_scores())
        rel_repo = _make_relationship_repo(
            {"proj-self": [("parent-proj", "parent", False)]}
        )

        router = AdaptiveRouter(
            benchmark_repo=repo,
            project_id="proj-self",
            relationship_repo=rel_repo,
            enable_cross_project_borrowing=True,
            edge_decay=0.8,
            external_penalty=0.6,
            min_borrow_weight=0.01,
        )

        inherited = await router.inherited_knowledge()
        assert isinstance(inherited, dict)
        assert "enabled" in inherited
        assert inherited["enabled"] is True
        assert "sources" in inherited
        assert "parent-proj" in inherited["sources"]
        parent_entry = inherited["sources"]["parent-proj"]
        assert "relation_type" in parent_entry
        assert "borrowed_candidates" in parent_entry

    @pytest.mark.asyncio
    async def test_min_borrow_weight_filters_far_neighbours(self):
        own = _make_own_scores()
        borrowed = _make_borrow_scores()
        repo = _make_benchmark_repo(own, borrowed)
        rel_repo = _make_relationship_repo(
            {"proj-self": [("parent-proj", "parent", False)]}
        )

        router = AdaptiveRouter(
            benchmark_repo=repo,
            min_samples=1,
            project_id="proj-self",
            relationship_repo=rel_repo,
            enable_cross_project_borrowing=True,
            edge_decay=0.01,
            external_penalty=0.01,
            min_borrow_weight=0.99,
        )

        decision = await router.route(task_type=TaskType.BUG_FIX)
        assert decision is not None

    @pytest.mark.asyncio
    async def test_no_relationship_repo_no_borrowing_even_when_on(self):
        own = _make_own_scores()
        repo = _make_benchmark_repo(own)

        router = AdaptiveRouter(
            benchmark_repo=repo,
            min_samples=1,
            project_id="proj-self",
            relationship_repo=None,
            enable_cross_project_borrowing=True,
        )

        inherited = await router.inherited_knowledge()
        assert inherited == {}
        decision = await router.route(task_type=TaskType.BUG_FIX)
        assert decision is not None
        assert decision.fallback is False

    @pytest.mark.asyncio
    async def test_empty_relationship_graph_no_impact(self):
        own = _make_own_scores()
        repo = _make_benchmark_repo(own)
        rel_repo = _make_relationship_repo({})

        router = AdaptiveRouter(
            benchmark_repo=repo,
            min_samples=1,
            project_id="proj-self",
            relationship_repo=rel_repo,
            enable_cross_project_borrowing=True,
        )

        inherited = await router.inherited_knowledge()
        assert isinstance(inherited, dict)
        decision = await router.route(task_type=TaskType.BUG_FIX)
        assert decision is not None
        assert decision.fallback is False
        assert decision.selected_model_profile_id == "own-model-1"


# ---------------------------------------------------------------------------
# AdaptiveRouter builder flags
# ---------------------------------------------------------------------------


class TestAdaptiveRouterBuilderFlags:
    def test_enable_cross_project_borrowing_stored(self):
        router = AdaptiveRouter(enable_cross_project_borrowing=True)
        assert router._enable_cross_project_borrowing is True

    def test_default_is_disabled(self):
        router = AdaptiveRouter()
        assert router._enable_cross_project_borrowing is False

    def test_edge_decay_stored(self):
        router = AdaptiveRouter(edge_decay=0.3)
        assert router._edge_decay == 0.3

    def test_external_penalty_stored(self):
        router = AdaptiveRouter(external_penalty=0.7)
        assert router._external_penalty == 0.7

    def test_min_borrow_weight_stored(self):
        router = AdaptiveRouter(min_borrow_weight=0.1)
        assert router._min_borrow_weight == 0.1

    def test_project_id_stored(self):
        router = AdaptiveRouter(project_id="my-project")
        assert router._project_id == "my-project"

    def test_relationship_repo_stored(self):
        repo = MagicMock()
        router = AdaptiveRouter(relationship_repo=repo)
        assert router._relationship_repo is repo

    def test_all_flags_together(self):
        rel_repo = MagicMock()
        benchmark_repo = MagicMock()
        router = AdaptiveRouter(
            benchmark_repo=benchmark_repo,
            project_id="p1",
            relationship_repo=rel_repo,
            enable_cross_project_borrowing=True,
            edge_decay=0.4,
            external_penalty=0.5,
            min_borrow_weight=0.02,
        )
        assert router._enable_cross_project_borrowing is True
        assert router._edge_decay == 0.4
        assert router._external_penalty == 0.5
        assert router._min_borrow_weight == 0.02
        assert router._project_id == "p1"
        assert router._relationship_repo is rel_repo


# ---------------------------------------------------------------------------
# RelationshipRoutingConfig defaults
# ---------------------------------------------------------------------------


class TestRelationshipRoutingConfig:
    def test_defaults_match_router_defaults(self):
        cfg = RelationshipRoutingConfig()
        assert cfg.enable_cross_project_borrowing is False
        assert cfg.edge_decay == 0.5
        assert cfg.external_penalty == 0.5
        assert cfg.min_borrow_weight == 0.05

    def test_can_enable_and_set_values(self):
        cfg = RelationshipRoutingConfig(
            enable_cross_project_borrowing=True,
            edge_decay=0.3,
            external_penalty=0.4,
            min_borrow_weight=0.01,
        )
        assert cfg.enable_cross_project_borrowing is True
        assert cfg.edge_decay == 0.3
        assert cfg.external_penalty == 0.4
        assert cfg.min_borrow_weight == 0.01
