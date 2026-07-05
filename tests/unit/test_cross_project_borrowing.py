"""Project-hierarchy phase 3 (70%→85%): AdaptiveRouter cross-project knowledge borrowing.

Integration tests for the full borrow pipeline: relationship graph resolution,
weight scaling per relation type, edge-decay over distance, external penalty,
min-borrow-weight filtering, and the inherited_knowledge() read-only surface.

All borrowing is gated behind enable_cross_project_borrowing; with the flag OFF
(default) the router behaves EXACTLY as before phase 3. These tests use mocks
for the benchmark repository and a fake relationship-repo to prove the full
pipeline without a real database.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from general_ludd.schemas.benchmark import TaskType
from general_ludd.scoring.router import AdaptiveRouter

PATCH_WEIGHTS = "general_ludd.scoring.router.weights_for"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _agg(
    model_id: str,
    *,
    project_id: str | None = None,
    composite: float = 0.85,
    avg_cost: float = 0.01,
    sample_count: int = 5,
    task_type: str = "bug_fix",
    prompt_id: str | None = "prompt-1",
) -> dict[str, Any]:
    """A single aggregate-score row from a benchmark repo."""
    return {
        "model_profile_id": model_id,
        "prompt_profile_id": prompt_id,
        "composite_score": composite,
        "avg_cost": avg_cost,
        "sample_count": sample_count,
        "task_type": task_type,
        "project_id": project_id,
    }


class _Edge:
    """Duck-type for ProjectRelationshipModel as the router reads it."""

    def __init__(
        self,
        related_project_id: str,
        relation_type: str,
        controlled: bool = True,
    ) -> None:
        self.related_project_id = related_project_id
        self.relation_type = relation_type
        self.controlled_by_gludd = controlled


def _scores_by_project(table: dict[str | None, list[dict[str, Any]]]):
    """Build a get_aggregate_scores side_effect keyed on project_id kwarg.

    The router omits the project_id kwarg entirely when it is None (backward
    compat), so this resolves the project from ``kwargs.get('project_id')``.
    """

    async def _side_effect(*args: Any, **kwargs: Any) -> list[dict[str, Any]]:
        pid = kwargs.get("project_id")
        return list(table.get(pid, []))

    return _side_effect


def _rel_repo(edges_by_project: dict[str, list[_Edge]]):
    """A mock relationship repo whose ``list_for_project`` returns declared edges."""
    repo = MagicMock()

    async def _list_for_project(project_id: str, **kwargs: Any) -> list[_Edge]:
        return list(edges_by_project.get(project_id, []))

    repo.list_for_project = AsyncMock(side_effect=_list_for_project)
    return repo


def _empty_rel_repo():
    """A relationship repo with zero declared edges."""
    return _rel_repo({})


class FakeBenchRepo:
    """A benchmark repo that returns per-project aggregate scores."""

    def __init__(self, scores: dict[str | None, list[dict[str, Any]]]) -> None:
        self._scores = scores

    async def get_aggregate_scores(
        self, task_type: str | None = None, project_id: str | None = None
    ) -> list[dict[str, Any]]:
        return list(self._scores.get(project_id, []))


@pytest.fixture(autouse=True)
def _patch_weights() -> Any:
    w = MagicMock()
    w.quality = 0.8
    w.cost = 0.2
    with patch(PATCH_WEIGHTS, return_value=w):
        yield


# ---------------------------------------------------------------------------
# 1. inherited_knowledge() returns scored results from related projects
# ---------------------------------------------------------------------------


class TestInheritedKnowledge:
    @pytest.mark.asyncio
    async def test_returns_empty_when_borrowing_off(self) -> None:
        """Borrowing OFF → inherited_knowledge() returns {} even with a repo + project."""
        bench = FakeBenchRepo({"proj-parent": [_agg("p-m", project_id="proj-parent")]})
        rel_repo = _rel_repo({"proj-child": [_Edge("proj-parent", "parent")]})
        router = AdaptiveRouter(
            benchmark_repo=bench,
            project_id="proj-child",
            relationship_repo=rel_repo,
            enable_cross_project_borrowing=False,
        )
        result = await router.inherited_knowledge()
        assert result == {}

    @pytest.mark.asyncio
    async def test_returns_empty_without_project_id(self) -> None:
        """No project_id → {} even with repo + borrowing ON."""
        bench = FakeBenchRepo({"proj-parent": [_agg("p-m", project_id="proj-parent")]})
        rel_repo = _rel_repo({"proj-child": [_Edge("proj-parent", "parent")]})
        router = AdaptiveRouter(
            benchmark_repo=bench,
            project_id=None,
            relationship_repo=rel_repo,
            enable_cross_project_borrowing=True,
        )
        result = await router.inherited_knowledge()
        assert result == {}

    @pytest.mark.asyncio
    async def test_returns_empty_when_relationship_repo_is_none(self) -> None:
        """relationship_repo=None → {} (graceful degradation, requirement 7)."""
        bench = FakeBenchRepo({"proj-parent": [_agg("p-m", project_id="proj-parent")]})
        router = AdaptiveRouter(
            benchmark_repo=bench,
            project_id="proj-child",
            relationship_repo=None,
            enable_cross_project_borrowing=True,
        )
        result = await router.inherited_knowledge()
        assert result == {}

    @pytest.mark.asyncio
    async def test_returns_empty_when_no_neighbors(self) -> None:
        """Project has no declared edges → empty sources."""
        bench = FakeBenchRepo({})
        rel_repo = _rel_repo({"proj-child": []})
        router = AdaptiveRouter(
            benchmark_repo=bench,
            project_id="proj-child",
            relationship_repo=rel_repo,
            enable_cross_project_borrowing=True,
        )
        result = await router.inherited_knowledge()
        assert result == {}

    @pytest.mark.asyncio
    async def test_returns_sources_with_borrowed_candidates(self) -> None:
        """ON + project + declared parent → inherited_knowledge returns sources dict."""
        bench = FakeBenchRepo(
            {"proj-parent": [_agg("parent-best", project_id="proj-parent", composite=0.92)]}
        )
        rel_repo = _rel_repo(
            {"proj-child": [_Edge("proj-parent", "parent", controlled=True)]}
        )
        router = AdaptiveRouter(
            benchmark_repo=bench,
            project_id="proj-child",
            relationship_repo=rel_repo,
            enable_cross_project_borrowing=True,
        )
        result = await router.inherited_knowledge()
        assert result.get("enabled") is True
        sources = result["sources"]
        assert "proj-parent" in sources
        src = sources["proj-parent"]
        assert src["relation_type"] == "parent"
        assert src["edge_distance"] == 1
        assert src["controlled"] is True
        assert src["weight"] > 0.0
        cand_models = {c["model_profile_id"] for c in src["borrowed_candidates"]}
        assert "parent-best" in cand_models

    @pytest.mark.asyncio
    async def test_candidates_include_task_type_and_score(self) -> None:
        """Each borrowed candidate carries model_profile_id, task_type, composite_score,
        and sample_count."""
        bench = FakeBenchRepo(
            {
                "proj-parent": [
                    _agg("m1", project_id="proj-parent", composite=0.88, sample_count=10),
                ]
            }
        )
        rel_repo = _rel_repo(
            {"proj-child": [_Edge("proj-parent", "parent", controlled=True)]}
        )
        router = AdaptiveRouter(
            benchmark_repo=bench,
            project_id="proj-child",
            relationship_repo=rel_repo,
            enable_cross_project_borrowing=True,
        )
        result = await router.inherited_knowledge()
        cand = result["sources"]["proj-parent"]["borrowed_candidates"][0]
        assert cand["model_profile_id"] == "m1"
        assert cand["task_type"] == "bug_fix"
        assert cand["composite_score"] == pytest.approx(0.88)
        assert cand["sample_count"] == 10

    @pytest.mark.asyncio
    async def test_handles_empty_neighbor_history(self) -> None:
        """A neighbor with zero benchmarks yields borrowed_candidates=[]."""
        bench = FakeBenchRepo({"proj-parent": []})
        rel_repo = _rel_repo(
            {"proj-child": [_Edge("proj-parent", "parent", controlled=True)]}
        )
        router = AdaptiveRouter(
            benchmark_repo=bench,
            project_id="proj-child",
            relationship_repo=rel_repo,
            enable_cross_project_borrowing=True,
        )
        result = await router.inherited_knowledge()
        assert result["sources"]["proj-parent"]["borrowed_candidates"] == []

    @pytest.mark.asyncio
    async def test_multiple_neighbors_all_included(self) -> None:
        """BFS resolves multiple related projects into the sources dict."""
        bench = FakeBenchRepo(
            {
                "proj-parent": [_agg("pm", project_id="proj-parent")],
                "proj-sibling": [_agg("sm", project_id="proj-sibling")],
            }
        )
        rel_repo = _rel_repo(
            {
                "proj-child": [
                    _Edge("proj-parent", "parent"),
                    _Edge("proj-sibling", "sibling"),
                ],
            }
        )
        router = AdaptiveRouter(
            benchmark_repo=bench,
            project_id="proj-child",
            relationship_repo=rel_repo,
            enable_cross_project_borrowing=True,
        )
        result = await router.inherited_knowledge()
        sources = result["sources"]
        assert "proj-parent" in sources
        assert "proj-sibling" in sources
        assert sources["proj-parent"]["relation_type"] == "parent"
        assert sources["proj-sibling"]["relation_type"] == "sibling"

    @pytest.mark.asyncio
    async def test_repo_failure_is_graceful(self) -> None:
        """A benchmark-repo failure for one neighbor does not break the whole facet."""
        bench = MagicMock()
        bench.get_aggregate_scores = AsyncMock(side_effect=RuntimeError("boom"))
        rel_repo = _rel_repo(
            {"proj-child": [_Edge("proj-parent", "parent", controlled=True)]}
        )
        router = AdaptiveRouter(
            benchmark_repo=bench,
            project_id="proj-child",
            relationship_repo=rel_repo,
            enable_cross_project_borrowing=True,
        )
        result = await router.inherited_knowledge()
        # The neighbor is still listed but with zero candidates (repo failed).
        assert result["sources"]["proj-parent"]["borrowed_candidates"] == []

    @pytest.mark.asyncio
    async def test_below_min_weight_neighbor_excluded(self) -> None:
        """A neighbor whose composite weight falls below min_borrow_weight is excluded."""
        bench = FakeBenchRepo(
            {"proj-far": [_agg("fm", project_id="proj-far", composite=0.7)]}
        )
        rel_repo = _rel_repo(
            {
                "proj-child": [_Edge("proj-far", "external", controlled=False)],
                "proj-far": [_Edge("proj-far-far", "external", controlled=False)],
                "proj-far-far": [_Edge("proj-far-far-far", "external", controlled=False)],
            }
        )
        router = AdaptiveRouter(
            benchmark_repo=bench,
            project_id="proj-child",
            relationship_repo=rel_repo,
            enable_cross_project_borrowing=True,
            min_borrow_weight=0.05,
        )
        result = await router.inherited_knowledge()
        # At d=1: external base 0.4 * control 0.5 = 0.2 → included
        # At d=2: external base 0.4 * 0.5 * 0.5 = 0.1 → included
        # At d=3: external base 0.4 * 0.25 * 0.5 = 0.05 → NOT included (strictly below)
        # Only d=1 and d=2 survive. The inherited_knowledge filter uses < min_borrow_weight,
        # so weight exactly 0.05 passes but anything below fails.
        assert "proj-far" in result["sources"]
        # proj-far-far is at d=3 (external * 0.5^2 * 0.5 = 0.05) → included
        # proj-far-far-far is at d=4 → max_depth=3 so not in the map anyway


# ---------------------------------------------------------------------------
# 2. Relationship weight scaling: own=1.0, parent=0.8, sibling=0.7, child=0.6,
#    external=0.4
# ---------------------------------------------------------------------------


class TestRelationshipWeightScaling:
    def test_own_weight_is_one(self) -> None:
        r = AdaptiveRouter()
        assert r._project_rel_weight("own", 0, True) == pytest.approx(1.0)

    def test_parent_weight(self) -> None:
        r = AdaptiveRouter()
        assert r._project_rel_weight("parent", 1, True) == pytest.approx(0.8)

    def test_sibling_weight(self) -> None:
        r = AdaptiveRouter()
        assert r._project_rel_weight("sibling", 1, True) == pytest.approx(0.7)

    def test_child_weight(self) -> None:
        r = AdaptiveRouter()
        assert r._project_rel_weight("child", 1, True) == pytest.approx(0.6)

    def test_external_weight(self) -> None:
        r = AdaptiveRouter()
        assert r._project_rel_weight("external", 1, True) == pytest.approx(0.4)

    def test_unknown_relation_falls_back_to_external(self) -> None:
        r = AdaptiveRouter()
        assert r._project_rel_weight("bogus_relation", 1, True) == pytest.approx(0.4)

    def test_weights_are_monotonic_controlled(self) -> None:
        """own > parent > sibling > child > external for controlled neighbors at d=1."""
        r = AdaptiveRouter()
        own = r._project_rel_weight("own", 0, True)
        parent = r._project_rel_weight("parent", 1, True)
        sibling = r._project_rel_weight("sibling", 1, True)
        child = r._project_rel_weight("child", 1, True)
        external = r._project_rel_weight("external", 1, True)
        assert own > parent > sibling > child > external

    @pytest.mark.asyncio
    async def test_own_candidate_always_preferred_over_borrowed_at_equal_score(self) -> None:
        """An own pick with composite=0.8 beats a parent pick with composite=0.8
        because own weight=1.0 > parent weight=0.8."""
        table = {
            "proj-child": [_agg("own-model", project_id="proj-child", composite=0.8)],
            "proj-parent": [
                _agg("parent-model", project_id="proj-parent", composite=0.8)
            ],
        }
        repo = MagicMock()
        repo.get_aggregate_scores = AsyncMock(side_effect=_scores_by_project(table))
        rel_repo = _rel_repo({"proj-child": [_Edge("proj-parent", "parent")]})
        router = AdaptiveRouter(
            benchmark_repo=repo,
            project_id="proj-child",
            relationship_repo=rel_repo,
            enable_cross_project_borrowing=True,
            min_samples=3,
        )
        decision = await router.route(TaskType.BUG_FIX)
        assert decision.selected_model_profile_id == "own-model"

    @pytest.mark.asyncio
    async def test_strong_borrowed_beats_weak_own(self) -> None:
        """A borrowed candidate with much higher composite can beat a weak own pick
        even after discounting. Own: 0.5*1.0=0.5, Parent: 0.9*0.8=0.72.

        The own candidate has sample_count=2 (< min_samples=3) so it is filtered
        out, making the borrowing path activate and the parent pick win."""
        table = {
            "proj-child": [
                _agg("own-weak", project_id="proj-child", composite=0.5, sample_count=2)
            ],
            "proj-parent": [
                _agg("parent-strong", project_id="proj-parent", composite=0.9)
            ],
        }
        repo = MagicMock()
        repo.get_aggregate_scores = AsyncMock(side_effect=_scores_by_project(table))
        rel_repo = _rel_repo({"proj-child": [_Edge("proj-parent", "parent")]})
        router = AdaptiveRouter(
            benchmark_repo=repo,
            project_id="proj-child",
            relationship_repo=rel_repo,
            enable_cross_project_borrowing=True,
            min_samples=3,
        )
        decision = await router.route(TaskType.BUG_FIX)
        assert decision.selected_model_profile_id == "parent-strong"
        assert decision.reason == "inherited_parent_history"


# ---------------------------------------------------------------------------
# 3. Cross-project borrowing disabled when enable_cross_project_borrowing=False
# ---------------------------------------------------------------------------


class TestBorrowingDisabledByDefault:
    def test_default_construction_is_off(self) -> None:
        """Default AdaptiveRouter() has borrowing OFF."""
        router = AdaptiveRouter()
        assert router._enable_cross_project_borrowing is False

    @pytest.mark.asyncio
    async def test_borrowing_off_no_relationship_query(self) -> None:
        """With borrowing OFF the relationship repo is NEVER consulted even with
        a thin own history."""
        repo = MagicMock()
        repo.get_aggregate_scores = AsyncMock(return_value=[])
        rel_repo = _rel_repo({"proj-child": [_Edge("proj-parent", "parent")]})
        router = AdaptiveRouter(
            benchmark_repo=repo,
            project_id="proj-child",
            relationship_repo=rel_repo,
        )
        decision = await router.route(TaskType.BUG_FIX)
        assert decision.fallback is True
        rel_repo.list_for_project.assert_not_called()

    @pytest.mark.asyncio
    async def test_borrowing_off_inherited_knowledge_empty(self) -> None:
        """inherited_knowledge() returns {} when OFF."""
        bench = FakeBenchRepo({"proj-parent": [_agg("x", project_id="proj-parent")]})
        rel_repo = _rel_repo({"proj-child": [_Edge("proj-parent", "parent")]})
        router = AdaptiveRouter(
            benchmark_repo=bench,
            project_id="proj-child",
            relationship_repo=rel_repo,
            enable_cross_project_borrowing=False,
        )
        assert await router.inherited_knowledge() == {}

    @pytest.mark.asyncio
    async def test_build_relationship_map_empty_when_off(self) -> None:
        """_build_relationship_map returns {} when borrowing is OFF."""
        router = AdaptiveRouter(
            project_id="proj-a",
            relationship_repo=_rel_repo({"proj-a": [_Edge("proj-b", "parent")]}),
            enable_cross_project_borrowing=False,
        )
        result = await router._build_relationship_map()
        assert result == {}


# ---------------------------------------------------------------------------
# 4. Edge decay reduces weight over distance (parent: 0.8, grandparent: 0.4,
#    great-grandparent: 0.2)
# ---------------------------------------------------------------------------


class TestEdgeDecay:
    def test_parent_distance_one_gets_full_base(self) -> None:
        r = AdaptiveRouter(edge_decay=0.5)
        # d=1: base * decay^(1-1) = 0.8 * 1.0 = 0.8
        assert r._project_rel_weight("parent", 1, True) == pytest.approx(0.8)

    def test_grandparent_distance_two_decayed(self) -> None:
        r = AdaptiveRouter(edge_decay=0.5)
        # d=2: base * decay^(2-1) = 0.8 * 0.5 = 0.4
        assert r._project_rel_weight("parent", 2, True) == pytest.approx(0.4)

    def test_great_grandparent_distance_three_decayed(self) -> None:
        r = AdaptiveRouter(edge_decay=0.5)
        # d=3: base * decay^(3-1) = 0.8 * 0.25 = 0.2
        assert r._project_rel_weight("parent", 3, True) == pytest.approx(0.2)

    def test_distance_zero_is_weight_one(self) -> None:
        r = AdaptiveRouter(edge_decay=0.5)
        # d=0: max(0, -1) = 0 so decay^0 = 1.0 → own = 1.0
        assert r._project_rel_weight("own", 0, True) == pytest.approx(1.0)

    def test_monotonic_decay_with_distance(self) -> None:
        r = AdaptiveRouter(edge_decay=0.5)
        w1 = r._project_rel_weight("sibling", 1, True)
        w2 = r._project_rel_weight("sibling", 2, True)
        w3 = r._project_rel_weight("sibling", 3, True)
        assert w1 > w2 > w3

    def test_custom_edge_decay_factor(self) -> None:
        """With decay=0.7, grandparent = 0.8 * 0.7^(2-1) = 0.56."""
        r = AdaptiveRouter(edge_decay=0.7)
        assert r._project_rel_weight("parent", 2, True) == pytest.approx(0.8 * 0.7)

    @pytest.mark.asyncio
    async def test_nearer_neighbor_beats_distant_at_equal_score(self) -> None:
        """Parent (d=1, weight 0.8) beats grandparent (d=2, weight 0.4) when
        both have the same composite score."""
        table = {
            "proj-child": [],
            "proj-parent": [
                _agg("parent-m", project_id="proj-parent", composite=0.7)
            ],
            "proj-gp": [
                _agg("gp-m", project_id="proj-gp", composite=0.7)
            ],
        }
        repo = MagicMock()
        repo.get_aggregate_scores = AsyncMock(side_effect=_scores_by_project(table))
        rel_repo = _rel_repo(
            {
                "proj-child": [_Edge("proj-parent", "parent")],
                "proj-parent": [_Edge("proj-gp", "parent")],
            }
        )
        router = AdaptiveRouter(
            benchmark_repo=repo,
            project_id="proj-child",
            relationship_repo=rel_repo,
            enable_cross_project_borrowing=True,
        )
        decision = await router.route(TaskType.BUG_FIX)
        assert decision.selected_model_profile_id == "parent-m"

    @pytest.mark.asyncio
    async def test_distant_strong_candidate_can_win(self) -> None:
        """A grandparent candidate with much higher score can overcome the decay."""
        table = {
            "proj-child": [],
            "proj-parent": [
                _agg("parent-m", project_id="proj-parent", composite=0.55)
            ],
            "proj-gp": [
                _agg("gp-m", project_id="proj-gp", composite=0.99)
            ],
        }
        repo = MagicMock()
        repo.get_aggregate_scores = AsyncMock(side_effect=_scores_by_project(table))
        rel_repo = _rel_repo(
            {
                "proj-child": [_Edge("proj-parent", "parent")],
                "proj-parent": [_Edge("proj-gp", "parent")],
            }
        )
        router = AdaptiveRouter(
            benchmark_repo=repo,
            project_id="proj-child",
            relationship_repo=rel_repo,
            enable_cross_project_borrowing=True,
        )
        decision = await router.route(TaskType.BUG_FIX)
        # parent: 0.55 * 0.8 = 0.44; gp: 0.99 * 0.4 = 0.396 — parent wins
        assert decision.selected_model_profile_id == "parent-m"


# ---------------------------------------------------------------------------
# 5. External penalty caps borrowed candidate weight below own-project candidates
# ---------------------------------------------------------------------------


class TestExternalPenalty:
    def test_uncontrolled_external_penalty_applied(self) -> None:
        """controlled=False → weight is halved by external_penalty=0.5."""
        r = AdaptiveRouter(external_penalty=0.5)
        controlled = r._project_rel_weight("parent", 1, True)
        uncontrolled = r._project_rel_weight("parent", 1, False)
        assert controlled == pytest.approx(0.8)
        assert uncontrolled == pytest.approx(0.4)

    def test_custom_external_penalty(self) -> None:
        """external_penalty=0.25 → uncontrolled weight = base * 0.25."""
        r = AdaptiveRouter(external_penalty=0.25)
        assert r._project_rel_weight("sibling", 1, False) == pytest.approx(0.7 * 0.25)

    def test_external_penalty_one_is_no_penalty(self) -> None:
        """external_penalty=1.0 → uncontrolled == controlled."""
        r = AdaptiveRouter(external_penalty=1.0)
        assert r._project_rel_weight("parent", 1, False) == pytest.approx(
            r._project_rel_weight("parent", 1, True)
        )

    def test_external_penalty_stack_with_decay(self) -> None:
        """Uncontrolled grandparent: 0.8 * 0.5 * 0.5 = 0.2."""
        r = AdaptiveRouter(edge_decay=0.5, external_penalty=0.5)
        assert r._project_rel_weight("parent", 2, False) == pytest.approx(0.8 * 0.5 * 0.5)

    @pytest.mark.asyncio
    async def test_controlled_beats_uncontrolled_at_equal_distance_and_score(self) -> None:
        """Two siblings at d=1 with same score: the controlled one wins."""
        table = {
            "proj-child": [],
            "proj-sib-controlled": [
                _agg("sib-c", project_id="proj-sib-controlled", composite=0.7)
            ],
            "proj-sib-uncontrolled": [
                _agg("sib-u", project_id="proj-sib-uncontrolled", composite=0.7)
            ],
        }
        repo = MagicMock()
        repo.get_aggregate_scores = AsyncMock(side_effect=_scores_by_project(table))
        rel_repo = _rel_repo(
            {
                "proj-child": [
                    _Edge("proj-sib-controlled", "sibling", controlled=True),
                    _Edge("proj-sib-uncontrolled", "sibling", controlled=False),
                ],
            }
        )
        router = AdaptiveRouter(
            benchmark_repo=repo,
            project_id="proj-child",
            relationship_repo=rel_repo,
            enable_cross_project_borrowing=True,
        )
        decision = await router.route(TaskType.BUG_FIX)
        assert decision.selected_model_profile_id == "sib-c"


# ---------------------------------------------------------------------------
# 6. Min borrow weight filters out very low-weight candidates
# ---------------------------------------------------------------------------


class TestMinBorrowWeight:
    def test_composite_weight_below_min_is_zero(self) -> None:
        """The composite weight for a distant uncontrolled external edge falls below
        min_borrow_weight and collapses to 0.0."""
        r = AdaptiveRouter(
            project_id="proj-self",
            enable_cross_project_borrowing=True,
            edge_decay=0.5,
            external_penalty=0.5,
            min_borrow_weight=0.05,
        )
        # external base=0.4, d=3 decay=0.25, control=0.5 → rel=0.05
        # similarity=0.5 → composite = 0.5 * 0.05 = 0.025 < 0.05 → 0.0
        rel_map = {"proj-far": ("external", 3, False)}
        w = r._composite_similarity_weight(0.5, "proj-far", rel_map)
        assert w == 0.0

    def test_composite_weight_barely_above_min_passes(self) -> None:
        """When composite >= min_borrow_weight, the weight is returned as-is."""
        r = AdaptiveRouter(
            project_id="proj-self",
            enable_cross_project_borrowing=True,
            min_borrow_weight=0.05,
        )
        rel_map = {"proj-near": ("parent", 1, True)}
        # parent base=0.8, d=1 → rel=0.8, similarity=1.0 → composite=0.8 > 0.05
        w = r._composite_similarity_weight(1.0, "proj-near", rel_map)
        assert w == pytest.approx(0.8)

    def test_min_borrow_weight_zero_disables_filter(self) -> None:
        """min_borrow_weight=0.0 means even near-zero weights pass."""
        r = AdaptiveRouter(
            project_id="proj-self",
            enable_cross_project_borrowing=True,
            edge_decay=0.5,
            external_penalty=0.5,
            min_borrow_weight=0.0,
        )
        rel_map = {"proj-far": ("external", 4, False)}
        w = r._composite_similarity_weight(0.1, "proj-far", rel_map)
        # Should not be collapsed to zero.
        assert w > 0.0

    @pytest.mark.asyncio
    async def test_distant_edge_not_borrowed_in_route(self) -> None:
        """A very distant edge (d=3, uncontrolled, external) with low task similarity
        is excluded from route() because its weight hits 0.0."""
        table = {
            "proj-child": [],
            "proj-far": [_agg("far-m", project_id="proj-far", composite=0.99)],
            "proj-mid": [_agg("mid-m", project_id="proj-mid", composite=0.7)],
            "proj-outer": [],
        }
        repo = MagicMock()
        repo.get_aggregate_scores = AsyncMock(side_effect=_scores_by_project(table))
        rel_repo = _rel_repo(
            {
                "proj-child": [_Edge("proj-mid", "parent", controlled=True)],
                "proj-mid": [
                    _Edge("proj-far", "external", controlled=False),
                    _Edge("proj-outer", "external", controlled=False),
                ],
                "proj-far": [_Edge("proj-outer", "external", controlled=False)],
            }
        )
        router = AdaptiveRouter(
            benchmark_repo=repo,
            project_id="proj-child",
            relationship_repo=rel_repo,
            enable_cross_project_borrowing=True,
            min_borrow_weight=0.05,
        )
        decision = await router.route(TaskType.BUG_FIX)
        # mid-m at d=1 parent controlled = 0.8; proj-far at d=2 external uncontrolled = low
        assert decision.selected_model_profile_id == "mid-m"


# ---------------------------------------------------------------------------
# 7. relationship_repo=None produces empty borrowing without error
# ---------------------------------------------------------------------------


class TestNullRelationshipRepo:
    @pytest.mark.asyncio
    async def test_inherited_knowledge_empty_with_none_repo(self) -> None:
        bench = FakeBenchRepo({})
        router = AdaptiveRouter(
            benchmark_repo=bench,
            project_id="proj-child",
            relationship_repo=None,
            enable_cross_project_borrowing=True,
        )
        assert await router.inherited_knowledge() == {}

    @pytest.mark.asyncio
    async def test_route_does_not_borrow_with_none_repo(self) -> None:
        """route() falls back to own (global) history when relationship_repo is None."""
        repo = MagicMock()
        repo.get_aggregate_scores = AsyncMock(
            return_value=[_agg("own-model", project_id="proj-child")]
        )
        router = AdaptiveRouter(
            benchmark_repo=repo,
            project_id="proj-child",
            relationship_repo=None,
            enable_cross_project_borrowing=True,
        )
        decision = await router.route(TaskType.BUG_FIX)
        assert decision.fallback is False
        assert decision.selected_model_profile_id == "own-model"

    @pytest.mark.asyncio
    async def test_build_relationship_map_empty_with_none_repo(self) -> None:
        router = AdaptiveRouter(
            project_id="proj-a",
            relationship_repo=None,
            enable_cross_project_borrowing=True,
        )
        result = await router._build_relationship_map()
        assert result == {}

    @pytest.mark.asyncio
    async def test_borrowing_flag_on_but_no_repo_still_uses_own_history(self) -> None:
        """When enable_cross_project_borrowing=True but relationship_repo=None,
        the router gracefully degrades to own-history-only behaviour."""
        table = {
            "proj-child": [_agg("my-model", project_id="proj-child", composite=0.88)],
        }
        repo = MagicMock()
        repo.get_aggregate_scores = AsyncMock(side_effect=_scores_by_project(table))
        router = AdaptiveRouter(
            benchmark_repo=repo,
            project_id="proj-child",
            relationship_repo=None,
            enable_cross_project_borrowing=True,
        )
        decision = await router.route(TaskType.BUG_FIX)
        assert decision.selected_model_profile_id == "my-model"
        assert decision.reason == "best_historical_score"


# ---------------------------------------------------------------------------
# 8. Borrowing works when both enable flag and relationship_repo are set
# ---------------------------------------------------------------------------


class TestBorrowingEnabledWithRepo:
    @pytest.mark.asyncio
    async def test_thin_child_borrows_parent_best_model(self) -> None:
        """A child with no own history borrows the parent's proven model."""
        table = {
            "proj-child": [],
            "proj-parent": [_agg("parent-model", project_id="proj-parent")],
        }
        repo = MagicMock()
        repo.get_aggregate_scores = AsyncMock(side_effect=_scores_by_project(table))
        rel_repo = _rel_repo(
            {"proj-child": [_Edge("proj-parent", "parent", controlled=True)]}
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

    @pytest.mark.asyncio
    async def test_borrows_from_sibling(self) -> None:
        table = {
            "proj-child": [],
            "proj-sib": [_agg("sibling-model", project_id="proj-sib")],
        }
        repo = MagicMock()
        repo.get_aggregate_scores = AsyncMock(side_effect=_scores_by_project(table))
        rel_repo = _rel_repo(
            {"proj-child": [_Edge("proj-sib", "sibling", controlled=True)]}
        )
        router = AdaptiveRouter(
            benchmark_repo=repo,
            project_id="proj-child",
            relationship_repo=rel_repo,
            enable_cross_project_borrowing=True,
        )
        decision = await router.route(TaskType.BUG_FIX)
        assert decision.reason == "inherited_sibling_history"

    @pytest.mark.asyncio
    async def test_borrows_from_child(self) -> None:
        table = {
            "proj-parent": [],
            "proj-child": [_agg("child-model", project_id="proj-child")],
        }
        repo = MagicMock()
        repo.get_aggregate_scores = AsyncMock(side_effect=_scores_by_project(table))
        rel_repo = _rel_repo(
            {"proj-parent": [_Edge("proj-child", "child", controlled=True)]}
        )
        router = AdaptiveRouter(
            benchmark_repo=repo,
            project_id="proj-parent",
            relationship_repo=rel_repo,
            enable_cross_project_borrowing=True,
        )
        decision = await router.route(TaskType.BUG_FIX)
        assert decision.reason == "inherited_child_history"

    @pytest.mark.asyncio
    async def test_do_not_borrow_when_own_history_is_sufficient(self) -> None:
        """When own sample_count >= min_samples, no borrowing occurs even when
        the flag and repo are set."""
        table = {
            "proj-child": [_agg("own-model", project_id="proj-child", sample_count=5)],
            "proj-parent": [_agg("parent-model", project_id="proj-parent")],
        }
        repo = MagicMock()
        repo.get_aggregate_scores = AsyncMock(side_effect=_scores_by_project(table))
        rel_repo = _rel_repo(
            {"proj-child": [_Edge("proj-parent", "parent")]}
        )
        router = AdaptiveRouter(
            benchmark_repo=repo,
            project_id="proj-child",
            relationship_repo=rel_repo,
            enable_cross_project_borrowing=True,
            min_samples=3,
        )
        decision = await router.route(TaskType.BUG_FIX)
        assert decision.selected_model_profile_id == "own-model"
        assert "inherited" not in decision.reason

    @pytest.mark.asyncio
    async def test_bfs_resolves_deeper_neighbors(self) -> None:
        """A child's BFS resolves the parent AND grandparent through transitive edges."""
        table = {
            "proj-child": [],
            "proj-parent": [_agg("p-m", project_id="proj-parent", composite=0.7)],
            "proj-gp": [_agg("gp-m", project_id="proj-gp", composite=0.95)],
        }
        repo = MagicMock()
        repo.get_aggregate_scores = AsyncMock(side_effect=_scores_by_project(table))
        rel_repo = _rel_repo(
            {
                "proj-child": [_Edge("proj-parent", "parent")],
                "proj-parent": [_Edge("proj-gp", "parent")],
            }
        )
        router = AdaptiveRouter(
            benchmark_repo=repo,
            project_id="proj-child",
            relationship_repo=rel_repo,
            enable_cross_project_borrowing=True,
        )
        decision = await router.route(TaskType.BUG_FIX)
        # parent: 0.7 * 0.8 = 0.56; gp: 0.95 * 0.4 = 0.38 → parent wins
        assert decision.selected_model_profile_id == "p-m"

    @pytest.mark.asyncio
    async def test_cycle_safe_bfs(self) -> None:
        """A parent<->child mutual declaration does not loop."""
        rel_repo = _rel_repo(
            {
                "proj-a": [_Edge("proj-b", "parent")],
                "proj-b": [_Edge("proj-a", "child"), _Edge("proj-c", "parent")],
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
        assert "proj-a" not in rel_map

    @pytest.mark.asyncio
    async def test_borrow_reason_is_last_borrow_reason(self) -> None:
        """After route(), _last_borrow_reason is the correct inherited reason."""
        table = {
            "proj-child": [],
            "proj-parent": [_agg("p-m", project_id="proj-parent")],
        }
        repo = MagicMock()
        repo.get_aggregate_scores = AsyncMock(side_effect=_scores_by_project(table))
        rel_repo = _rel_repo(
            {"proj-child": [_Edge("proj-parent", "parent", controlled=True)]}
        )
        router = AdaptiveRouter(
            benchmark_repo=repo,
            project_id="proj-child",
            relationship_repo=rel_repo,
            enable_cross_project_borrowing=True,
        )
        decision = await router.route(TaskType.BUG_FIX)
        assert decision.reason == "inherited_parent_history"
        assert router._last_borrow_reason == "inherited_parent_history"

    @pytest.mark.asyncio
    async def test_repo_list_for_project_failure_is_graceful(self) -> None:
        """When list_for_project raises for a neighbor, BFS skips further expansion
        from that node but keeps already-resolved edges (they were added before the
        expansion attempt)."""
        repo = MagicMock()
        repo.get_aggregate_scores = AsyncMock(return_value=[])

        rel_repo = MagicMock()
        # First call (own project "proj-child") returns proj-parent edge → proj-parent
        # is added to the map at d=1. Second call (proj-parent's edges) fails.
        rel_repo.list_for_project = AsyncMock(
            side_effect=[
                [_Edge("proj-parent", "parent")],
                RuntimeError("db error"),
            ]
        )

        router = AdaptiveRouter(
            benchmark_repo=repo,
            project_id="proj-child",
            relationship_repo=rel_repo,
            enable_cross_project_borrowing=True,
        )
        rel_map = await router._build_relationship_map()
        # proj-parent was added to map when processing proj-child's edges, before
        # the BFS tried to expand from it. The failure prevents further expansion
        # but does not remove the already-recorded edge.
        assert "proj-parent" in rel_map
        assert rel_map["proj-parent"] == ("parent", 1, True)
        # The call count is 2: root + failed neighbor.
        assert rel_repo.list_for_project.call_count == 2


# ---------------------------------------------------------------------------
# Boundary conditions
# ---------------------------------------------------------------------------


class TestBoundaryConditions:
    @pytest.mark.asyncio
    async def test_zero_neighbors_no_borrowing(self) -> None:
        """A project with zero declared edges routes with its own history only."""
        table = {
            "proj-child": [_agg("own", project_id="proj-child")],
        }
        repo = MagicMock()
        repo.get_aggregate_scores = AsyncMock(side_effect=_scores_by_project(table))
        router = AdaptiveRouter(
            benchmark_repo=repo,
            project_id="proj-child",
            relationship_repo=_empty_rel_repo(),
            enable_cross_project_borrowing=True,
        )
        decision = await router.route(TaskType.BUG_FIX)
        assert decision.selected_model_profile_id == "own"

    @pytest.mark.asyncio
    async def test_all_neighbors_have_insufficient_samples(self) -> None:
        """Neighbor candidates below min_samples are filtered out, resulting in
        no borrowable candidates."""
        table = {
            "proj-child": [],
            "proj-parent": [
                _agg("p-m", project_id="proj-parent", sample_count=1),
            ],
            "proj-sib": [
                _agg("s-m", project_id="proj-sib", sample_count=2),
            ],
        }
        repo = MagicMock()
        repo.get_aggregate_scores = AsyncMock(side_effect=_scores_by_project(table))
        rel_repo = _rel_repo(
            {
                "proj-child": [
                    _Edge("proj-parent", "parent"),
                    _Edge("proj-sib", "sibling"),
                ],
            }
        )
        router = AdaptiveRouter(
            benchmark_repo=repo,
            project_id="proj-child",
            relationship_repo=rel_repo,
            enable_cross_project_borrowing=True,
            min_samples=3,
        )
        decision = await router.route(TaskType.BUG_FIX)
        assert decision.fallback is True
        assert decision.reason == "insufficient_historical_data"

    @pytest.mark.asyncio
    async def test_max_depth_truncates_far_neighbors(self) -> None:
        """Neighbors beyond max_depth=3 are excluded from the relationship map."""
        rel_repo = _rel_repo(
            {
                "proj-a": [_Edge("proj-b", "parent")],
                "proj-b": [_Edge("proj-c", "parent")],
                "proj-c": [_Edge("proj-d", "parent")],
                "proj-d": [_Edge("proj-e", "external")],
            }
        )
        r = AdaptiveRouter(
            project_id="proj-a",
            relationship_repo=rel_repo,
            enable_cross_project_borrowing=True,
        )
        rel_map = await r._build_relationship_map()
        # max_depth=3: proj-b(d=1), proj-c(d=2), proj-d(d=3) → proj-e(d=4) excluded
        assert "proj-b" in rel_map
        assert "proj-c" in rel_map
        assert "proj-d" in rel_map
        assert "proj-e" not in rel_map


# ---------------------------------------------------------------------------
# Composite-similarity weight helper
# ---------------------------------------------------------------------------


class TestCompositeSimilarityWeight:
    def test_off_returns_task_weight_alone(self) -> None:
        """borrowing OFF → composite weight = task-similarity weight only."""
        r = AdaptiveRouter(enable_cross_project_borrowing=False, project_id="proj-self")
        rel_map = {"proj-parent": ("parent", 1, True)}
        assert r._composite_similarity_weight(1.0, "proj-parent", rel_map) == pytest.approx(1.0)
        assert r._composite_similarity_weight(0.5, "proj-parent", rel_map) == pytest.approx(0.5)

    def test_own_project_returns_task_weight_alone(self) -> None:
        """candidate_project_id == self._project_id → no project-axis discount."""
        r = AdaptiveRouter(enable_cross_project_borrowing=True, project_id="proj-self")
        rel_map = {"proj-self": ("own", 0, True)}
        assert r._composite_similarity_weight(1.0, "proj-self", rel_map) == pytest.approx(1.0)

    def test_none_relationship_map_returns_task_weight_alone(self) -> None:
        """When relationship_map is None, the project axis is not applied."""
        r = AdaptiveRouter(enable_cross_project_borrowing=True, project_id="proj-self")
        assert r._composite_similarity_weight(1.0, "proj-parent", None) == pytest.approx(1.0)

    def test_not_in_relationship_map_returns_task_weight_alone(self) -> None:
        """Candidate not in the map → no borrowing, own weight."""
        r = AdaptiveRouter(enable_cross_project_borrowing=True, project_id="proj-self")
        rel_map = {"proj-sibling": ("sibling", 1, True)}
        assert r._composite_similarity_weight(1.0, "proj-unknown", rel_map) == pytest.approx(1.0)

    def test_in_relationship_map_multiplied(self) -> None:
        """In the map → task_weight * rel_weight."""
        r = AdaptiveRouter(
            enable_cross_project_borrowing=True,
            project_id="proj-self",
            edge_decay=0.5,
        )
        rel_map = {"proj-parent": ("parent", 1, True)}
        # task=1.0 * rel(parent,d=1)=0.8 → 0.8
        assert r._composite_similarity_weight(1.0, "proj-parent", rel_map) == pytest.approx(0.8)

    def test_similarity_floor_included(self) -> None:
        """similarity_floor + similarity_alpha * similarity is used for task_w."""
        r = AdaptiveRouter(
            enable_cross_project_borrowing=True,
            project_id="proj-self",
            similarity_floor=0.1,
            similarity_alpha=0.9,
        )
        rel_map = {"proj-parent": ("parent", 1, True)}
        # task_w = 0.1 + 0.9 * 0.5 = 0.55; rel_w = 0.8 → composite = 0.55 * 0.8 = 0.44
        w = r._composite_similarity_weight(0.5, "proj-parent", rel_map)
        assert w == pytest.approx(0.55 * 0.8)
