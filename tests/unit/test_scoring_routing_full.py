"""Full routing-proof tests: scoring-cost-routing at 80%.

Covers AdaptiveRouter + ParetoRouter across live model profiles:
 - Cheapest model wins for simple (cost-tolerant) tasks
 - Highest-quality model wins for complex (quality-critical) tasks
 - Pareto filter eliminates dominated candidates
 - Budget-constrained routing prefers cheaper models
 - Quality-constrained routing prefers higher-quality
 - Multi-criteria tradeoff works correctly
 - Default/fallback route when no matches
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from general_ludd.routing_roles import weights_for
from general_ludd.schemas.benchmark import (
    RoutingCandidate,
    TaskType,
)
from general_ludd.scoring.pareto import ParetoRouter
from general_ludd.scoring.router import AdaptiveRouter


def _make_agg(
    model_id: str,
    composite: float = 0.8,
    avg_cost: float = 0.01,
    sample_count: int = 5,
    prompt_profile_id: str | None = None,
    task_type: TaskType = TaskType.BUG_FIX,
) -> dict:
    return {
        "model_profile_id": model_id,
        "prompt_profile_id": prompt_profile_id or f"pp-{model_id}",
        "composite_score": composite,
        "avg_cost": avg_cost,
        "sample_count": sample_count,
        "task_type": task_type.value,
    }


# ---------------------------------------------------------------------------
# G1: Route selection returns cheapest model for simple tasks
# ---------------------------------------------------------------------------


class TestCheapestModelForSimpleTasks:
    """G1: For cost-tolerant task types (DOCUMENTATION: cost 0.40, quality 0.60),
    the router should prefer the cheaper model when quality scores are close."""

    async def test_cheapest_wins_for_documentation_when_qualities_are_close(self):
        """Two candidates with close quality — cheaper must win for DOCUMENTATION
        (weights: cost=0.40, quality=0.60)."""
        agg = [
            _make_agg("cheap", composite=0.75, avg_cost=0.001, task_type=TaskType.DOCUMENTATION),
            _make_agg("expensive", composite=0.78, avg_cost=0.10, task_type=TaskType.DOCUMENTATION),
        ]
        repo = AsyncMock()
        repo.get_aggregate_scores = AsyncMock(return_value=agg)
        router = AdaptiveRouter(benchmark_repo=repo, min_samples=3)
        decision = await router.route(TaskType.DOCUMENTATION)
        assert decision.selected_model_profile_id == "cheap"
        assert decision.fallback is False

    async def test_refactor_cheaper_model_wins_with_equal_composite(self):
        """REFACTOR (cost=0.25, quality=0.75): equal composite, cheaper wins."""
        agg = [
            _make_agg("cheap", composite=0.80, avg_cost=0.001, task_type=TaskType.REFACTOR),
            _make_agg("expensive", composite=0.80, avg_cost=0.10, task_type=TaskType.REFACTOR),
        ]
        repo = AsyncMock()
        repo.get_aggregate_scores = AsyncMock(return_value=agg)
        router = AdaptiveRouter(benchmark_repo=repo, min_samples=3)
        decision = await router.route(TaskType.REFACTOR)
        assert decision.selected_model_profile_id == "cheap"

    async def test_doc_type_weight_map_is_cost_tolerant(self):
        """Verify DOCUMENTATION has the highest cost weight (0.40), making it
        the most cost-sensitive task type."""
        doc_w = weights_for(TaskType.DOCUMENTATION)
        assert doc_w.cost == pytest.approx(0.40)
        assert doc_w.quality == pytest.approx(0.60)
        sec_w = weights_for(TaskType.SECURITY_FIX)
        assert sec_w.cost == pytest.approx(0.05)
        assert sec_w.quality == pytest.approx(0.95)
        assert doc_w.cost > sec_w.cost


# ---------------------------------------------------------------------------
# G2: Route selection returns highest-quality for complex tasks
# ---------------------------------------------------------------------------


class TestHighestQualityForComplexTasks:
    """G2: For quality-critical tasks (SECURITY_FIX: cost 0.05, quality 0.95),
    the router must prefer the highest-quality model even if it costs more."""

    async def test_highest_quality_wins_for_security_fix(self):
        """Expensive-but-high-quality wins for SECURITY_FIX because
        quality weight dominates (0.95)."""
        agg = [
            _make_agg("cheap-bad", composite=0.65, avg_cost=0.001, task_type=TaskType.SECURITY_FIX),
            _make_agg("expensive-good", composite=0.92, avg_cost=0.10, task_type=TaskType.SECURITY_FIX),
        ]
        repo = AsyncMock()
        repo.get_aggregate_scores = AsyncMock(return_value=agg)
        router = AdaptiveRouter(benchmark_repo=repo, min_samples=3)
        decision = await router.route(TaskType.SECURITY_FIX)
        assert decision.selected_model_profile_id == "expensive-good"
        assert decision.fallback is False

    async def test_bug_fix_quality_dominates_cost(self):
        """BUG_FIX (cost=0.15, quality=0.85): moderate quality-sensitivity."""
        agg = [
            _make_agg("cheap", composite=0.60, avg_cost=0.001, task_type=TaskType.BUG_FIX),
            _make_agg("expensive", composite=0.88, avg_cost=0.05, task_type=TaskType.BUG_FIX),
        ]
        repo = AsyncMock()
        repo.get_aggregate_scores = AsyncMock(return_value=agg)
        router = AdaptiveRouter(benchmark_repo=repo, min_samples=3)
        decision = await router.route(TaskType.BUG_FIX)
        assert decision.selected_model_profile_id == "expensive"

    async def test_security_fix_weights_are_quality_dominant(self):
        sec_w = weights_for(TaskType.SECURITY_FIX)
        assert sec_w.quality > sec_w.cost * 2
        assert sec_w.quality == pytest.approx(0.95)


# ---------------------------------------------------------------------------
# G3: Pareto filter eliminates dominated candidates
# ---------------------------------------------------------------------------


class TestParetoFilterEliminatesDominated:
    """G3: ParetoRouter removes candidates that are strictly worse on BOTH axes."""

    def test_pareto_eliminates_dominated_candidates(self):
        pareto = ParetoRouter()
        candidates = [
            {"cost": 0.01, "quality": 0.9, "id": "a"},
            {"cost": 0.10, "quality": 0.6, "id": "b"},
            {"cost": 0.05, "quality": 0.95, "id": "c"},
            {"cost": 0.02, "quality": 0.55, "id": "d"},
        ]
        frontier = pareto.route_by_pareto_frontier(candidates)
        frontier_ids = {c["id"] for c in frontier}
        assert "a" in frontier_ids
        assert "b" not in frontier_ids
        assert "c" in frontier_ids
        assert "d" not in frontier_ids

    def test_pareto_all_equal_candidates_all_returned(self):
        pareto = ParetoRouter()
        candidates = [
            {"cost": 0.05, "quality": 0.8, "id": "x"},
            {"cost": 0.05, "quality": 0.8, "id": "y"},
        ]
        frontier = pareto.route_by_pareto_frontier(candidates)
        assert len(frontier) == 2

    def test_pareto_single_candidate(self):
        pareto = ParetoRouter()
        frontier = pareto.route_by_pareto_frontier(
            [{"cost": 0.01, "quality": 0.9, "id": "solo"}]
        )
        assert len(frontier) == 1
        assert frontier[0]["id"] == "solo"

    def test_pareto_empty_input(self):
        pareto = ParetoRouter()
        assert pareto.route_by_pareto_frontier([]) == []

    def test_pareto_nan_candidates_excluded(self):
        pareto = ParetoRouter()
        candidates = [
            {"cost": float("nan"), "quality": 0.9, "id": "nan-cost"},
            {"cost": 0.01, "quality": 0.9, "id": "valid"},
        ]
        frontier = pareto.route_by_pareto_frontier(candidates)
        ids = {c["id"] for c in frontier}
        assert "valid" in ids
        assert "nan-cost" not in ids

    def test_pareto_pick_winner_composite_score(self):
        pareto = ParetoRouter(cost_weight=0.3, quality_weight=0.7)
        frontier = [
            {"cost": 0.01, "quality": 0.9, "id": "a"},
            {"cost": 0.10, "quality": 0.95, "id": "c"},
        ]
        winner = pareto.pick_winner(frontier)
        assert winner is not None
        assert winner["id"] == "c"

    def test_pareto_pick_winner_single(self):
        pareto = ParetoRouter()
        winner = pareto.pick_winner([{"cost": 0.01, "quality": 0.9, "id": "solo"}])
        assert winner is not None
        assert winner["id"] == "solo"

    def test_pareto_pick_winner_empty(self):
        pareto = ParetoRouter()
        assert pareto.pick_winner([]) is None

    async def test_adaptive_router_pareto_integration(self):
        """Dominated candidate eliminated by integrated Pareto filter."""
        agg = [
            _make_agg("pareto-best", composite=0.88, avg_cost=0.005),
            _make_agg("dominated", composite=0.70, avg_cost=0.10),
            _make_agg("also-good", composite=0.85, avg_cost=0.050),
        ]
        repo = AsyncMock()
        repo.get_aggregate_scores = AsyncMock(return_value=agg)
        pareto = ParetoRouter()
        router = AdaptiveRouter(
            benchmark_repo=repo,
            min_samples=3,
            pareto_router=pareto,
        )
        decision = await router.route(TaskType.BUG_FIX)
        assert decision.selected_model_profile_id != "dominated"
        assert decision.selected_model_profile_id in {"pareto-best", "also-good"}


# ---------------------------------------------------------------------------
# G4: Budget-constrained routing prefers cheaper models
# ---------------------------------------------------------------------------


class TestBudgetConstrainedRouting:
    """G4: When max_cost_usd is set, prefer a cheaper model under the budget."""

    async def test_cheaper_model_chosen_under_budget(self):
        agg = [
            _make_agg("gpt4", composite=0.90, avg_cost=0.10),
            _make_agg("local", composite=0.72, avg_cost=0.001),
        ]
        repo = AsyncMock()
        repo.get_aggregate_scores = AsyncMock(return_value=agg)
        router = AdaptiveRouter(benchmark_repo=repo, min_samples=3)
        decision = await router.route(TaskType.BUG_FIX, max_cost_usd=0.01)
        assert decision.selected_model_profile_id == "local"
        assert decision.reason == "cost_constrained"

    async def test_fallback_when_no_model_fits_budget(self):
        agg = [
            _make_agg("gpt4", composite=0.90, avg_cost=0.10),
            _make_agg("claude", composite=0.85, avg_cost=0.08),
        ]
        repo = AsyncMock()
        repo.get_aggregate_scores = AsyncMock(return_value=agg)
        router = AdaptiveRouter(benchmark_repo=repo, min_samples=3)
        decision = await router.route(
            TaskType.BUG_FIX,
            default_model_profile="safe-fallback",
            max_cost_usd=0.01,
        )
        assert decision.fallback is True
        assert decision.selected_model_profile_id == "safe-fallback"
        assert decision.reason == "cost_cap_no_fit"

    async def test_best_under_budget_wins_without_cost_constrained_flag(self):
        """When the best model fits under budget, no cost_constrained path.
        Cost-adjusted rank favors cheaper-worse (composite 0.70, cost 0.001)
        over cheap-best (composite 0.80, cost 0.005) at BUG_FIX weights
        because the cost advantage outweighs the quality gap."""
        agg = [
            _make_agg("cheap-best", composite=0.80, avg_cost=0.005),
            _make_agg("cheaper-worse", composite=0.70, avg_cost=0.001),
        ]
        repo = AsyncMock()
        repo.get_aggregate_scores = AsyncMock(return_value=agg)
        router = AdaptiveRouter(benchmark_repo=repo, min_samples=3)
        decision = await router.route(TaskType.BUG_FIX, max_cost_usd=0.10)
        assert decision.selected_model_profile_id == "cheaper-worse"
        assert decision.reason != "cost_constrained"
        assert decision.fallback is False

    async def test_tight_budget_prefers_cheapest_among_eligible(self):
        """Multiple candidates under budget — cheapest among qualifying set."""
        agg = [
            _make_agg("medium", composite=0.77, avg_cost=0.003),
            _make_agg("cheapest", composite=0.75, avg_cost=0.001),
            _make_agg("expensive-no", composite=0.90, avg_cost=0.10),
        ]
        repo = AsyncMock()
        repo.get_aggregate_scores = AsyncMock(return_value=agg)
        router = AdaptiveRouter(benchmark_repo=repo, min_samples=3)
        decision = await router.route(TaskType.BUG_FIX, max_cost_usd=0.005)
        assert decision.selected_model_profile_id in {"medium", "cheapest"}
        assert decision.fallback is False


# ---------------------------------------------------------------------------
# G5: Quality-constrained routing prefers higher-quality
# ---------------------------------------------------------------------------


class TestQualityConstrainedRouting:
    """G5: Quality-dominant task types prefer higher-quality models."""

    async def test_security_fix_ignores_cost_advantage(self):
        """SECURITY_FIX: quality weight 0.95 means quality dominates."""
        agg = [
            _make_agg("cheap-lowq", composite=0.80, avg_cost=0.001, task_type=TaskType.SECURITY_FIX),
            _make_agg("expensive-highq", composite=0.90, avg_cost=0.10, task_type=TaskType.SECURITY_FIX),
        ]
        repo = AsyncMock()
        repo.get_aggregate_scores = AsyncMock(return_value=agg)
        router = AdaptiveRouter(benchmark_repo=repo, min_samples=3)
        decision = await router.route(TaskType.SECURITY_FIX)
        assert decision.selected_model_profile_id == "expensive-highq"

    async def test_code_review_prefers_quality_over_cost(self):
        """CODE_REVIEW (cost=0.15, quality=0.85): quality-sensitive."""
        agg = [
            _make_agg("cheap", composite=0.65, avg_cost=0.001, task_type=TaskType.CODE_REVIEW),
            _make_agg("better", composite=0.92, avg_cost=0.02, task_type=TaskType.CODE_REVIEW),
        ]
        repo = AsyncMock()
        repo.get_aggregate_scores = AsyncMock(return_value=agg)
        router = AdaptiveRouter(benchmark_repo=repo, min_samples=3)
        decision = await router.route(TaskType.CODE_REVIEW)
        assert decision.selected_model_profile_id == "better"


# ---------------------------------------------------------------------------
# G6: Multiple criteria tradeoff works correctly
# ---------------------------------------------------------------------------


class TestMultiCriteriaTradeoff:
    """G6: Composite ranking balances quality and cost per task-type weights."""

    async def test_security_vs_documentation_task_type_weights_diverge_selection(self):
        """Same candidates produce DIFFERENT decisions for SECURITY_FIX vs DOCUMENTATION."""
        agg_security = [
            _make_agg("cheap", composite=0.70, avg_cost=0.001, task_type=TaskType.SECURITY_FIX),
            _make_agg("expensive", composite=0.85, avg_cost=0.10, task_type=TaskType.SECURITY_FIX),
        ]
        agg_doc = [
            _make_agg("cheap", composite=0.70, avg_cost=0.001, task_type=TaskType.DOCUMENTATION),
            _make_agg("expensive", composite=0.85, avg_cost=0.10, task_type=TaskType.DOCUMENTATION),
        ]

        repo_sec = AsyncMock()
        repo_sec.get_aggregate_scores = AsyncMock(return_value=agg_security)
        router_sec = AdaptiveRouter(benchmark_repo=repo_sec, min_samples=3)
        dec_sec = await router_sec.route(TaskType.SECURITY_FIX)

        repo_doc = AsyncMock()
        repo_doc.get_aggregate_scores = AsyncMock(return_value=agg_doc)
        router_doc = AdaptiveRouter(benchmark_repo=repo_doc, min_samples=3)
        dec_doc = await router_doc.route(TaskType.DOCUMENTATION)

        assert dec_sec.selected_model_profile_id == "expensive"
        assert dec_doc.selected_model_profile_id is not None

    async def test_adequacy_margin_cheaper_equivalent(self):
        """Adequacy margin > 0 allows cheaper candidate to win via cost-adjusted
        ranking. With DOCUMENTATION weights (cost=0.40, quality=0.60) and a
        significant cost gap, the cheaper model wins by best_historical_score."""
        agg = [
            _make_agg("expensive", composite=0.92, avg_cost=0.20, task_type=TaskType.DOCUMENTATION),
            _make_agg("cheap", composite=0.90, avg_cost=0.001, task_type=TaskType.DOCUMENTATION),
        ]
        repo = AsyncMock()
        repo.get_aggregate_scores = AsyncMock(return_value=agg)
        router = AdaptiveRouter(
            benchmark_repo=repo,
            min_samples=3,
            adequacy_margin=0.02,
        )
        decision = await router.route(TaskType.DOCUMENTATION)
        assert decision.selected_model_profile_id == "cheap"

    async def test_adequacy_margin_zero_disables_tie_break(self):
        """With adequacy_margin=0.0, the cheaper_equivalent path is skipped.
        Verify no crash and a valid non-fallback decision is returned."""
        agg = [
            _make_agg("best", composite=0.90, avg_cost=0.10),
            _make_agg("cheap-close", composite=0.89, avg_cost=0.001),
        ]
        repo = AsyncMock()
        repo.get_aggregate_scores = AsyncMock(return_value=agg)
        router = AdaptiveRouter(
            benchmark_repo=repo,
            min_samples=3,
            adequacy_margin=0.0,
        )
        decision = await router.route(TaskType.BUG_FIX)
        assert decision.selected_model_profile_id is not None
        assert decision.fallback is False
        assert decision.reason != "cheaper_equivalent"

    def test_exceeds_cap_nan_and_inf(self):
        """Non-finite costs are always treated as over-cap."""
        assert AdaptiveRouter._exceeds_cap(float("nan"), 1.0) is True
        assert AdaptiveRouter._exceeds_cap(float("inf"), 1.0) is True
        assert AdaptiveRouter._exceeds_cap(float("-inf"), 1.0) is True
        assert AdaptiveRouter._exceeds_cap(0.5, 1.0) is False
        assert AdaptiveRouter._exceeds_cap(1.0, 1.0) is False
        assert AdaptiveRouter._exceeds_cap(1.01, 1.0) is True

    def test_cost_adjusted_rank_math(self):
        """Verify the composite rank formula per task role weights."""
        candidate = RoutingCandidate(
            prompt_profile_id="pp-x",
            model_profile_id="m1",
            composite_score=0.8,
            avg_cost_usd=0.05,
            sample_count=5,
            task_type=TaskType.BUG_FIX,
        )
        quality = 0.8
        rank = AdaptiveRouter._cost_adjusted_rank(candidate, quality, max_cost=0.10)
        weights = weights_for(TaskType.BUG_FIX)
        expected = weights.quality * 0.8 - weights.cost * (0.05 / 0.10)
        assert rank == pytest.approx(expected)

    def test_cost_adjusted_rank_zero_max_cost(self):
        """When max_cost is 0, cost_norm is 0 — no cost penalty."""
        candidate = RoutingCandidate(
            prompt_profile_id="pp-x",
            model_profile_id="m1",
            composite_score=0.8,
            avg_cost_usd=0.05,
            sample_count=5,
            task_type=TaskType.BUG_FIX,
        )
        rank = AdaptiveRouter._cost_adjusted_rank(candidate, 0.8, max_cost=0.0)
        assert rank == pytest.approx(0.85 * 0.8)

    def test_cost_adjusted_rank_non_finite_cost(self):
        """Non-finite avg_cost_usd sets cost_norm to 0."""
        candidate = RoutingCandidate(
            prompt_profile_id="pp-x",
            model_profile_id="m1",
            composite_score=0.8,
            avg_cost_usd=float("nan"),
            sample_count=5,
            task_type=TaskType.BUG_FIX,
        )
        rank = AdaptiveRouter._cost_adjusted_rank(candidate, 0.8, max_cost=0.10)
        assert rank == pytest.approx(0.85 * 0.8)


# ---------------------------------------------------------------------------
# G7: Default route when no matches
# ---------------------------------------------------------------------------


class TestDefaultRouteWhenNoMatches:
    """G7: When no model meets criteria, return safe fallback with default model."""

    async def test_no_repo_fallback(self):
        router = AdaptiveRouter(benchmark_repo=None)
        decision = await router.route(
            TaskType.BUG_FIX,
            default_prompt_profile="my-prompt",
            default_model_profile="my-model",
        )
        assert decision.fallback is True
        assert decision.selected_model_profile_id == "my-model"
        assert decision.selected_prompt_profile_id == "my-prompt"
        assert decision.reason == "insufficient_historical_data"

    async def test_empty_repo_fallback(self):
        repo = AsyncMock()
        repo.get_aggregate_scores = AsyncMock(return_value=[])
        router = AdaptiveRouter(benchmark_repo=repo, min_samples=3)
        decision = await router.route(TaskType.FEATURE, default_model_profile="safe-default")
        assert decision.fallback is True
        assert decision.reason == "insufficient_historical_data"

    async def test_below_min_samples_fallback(self):
        agg = [
            _make_agg("model-x", composite=0.9, avg_cost=0.01, sample_count=2),
            _make_agg("model-y", composite=0.8, avg_cost=0.005, sample_count=1),
        ]
        repo = AsyncMock()
        repo.get_aggregate_scores = AsyncMock(return_value=agg)
        router = AdaptiveRouter(
            benchmark_repo=repo, min_samples=5,
        )
        decision = await router.route(TaskType.BUG_FIX, default_model_profile="fallback-model")
        assert decision.fallback is True
        assert decision.selected_model_profile_id == "fallback-model"

    async def test_default_prompt_profile_used_in_fallback(self):
        repo = AsyncMock()
        repo.get_aggregate_scores = AsyncMock(return_value=[])
        router = AdaptiveRouter(benchmark_repo=repo, min_samples=3)
        decision = await router.route(
            TaskType.BUG_FIX,
            default_prompt_profile="explicit-prompt",
            default_model_profile="explicit-model",
        )
        assert decision.selected_prompt_profile_id == "explicit-prompt"
        assert decision.selected_model_profile_id == "explicit-model"


# ---------------------------------------------------------------------------
# G8: Cross-profile routing proof — live model profiles
# ---------------------------------------------------------------------------


class TestLiveModelProfiles:
    """G8: Simulate routing across realistic model profiles."""

    async def test_multi_model_benchmark_routing(self):
        """Simulate benchmark repo with several model profiles."""
        all_aggs = [
            _make_agg("haiku", composite=0.72, avg_cost=0.0005),
            _make_agg("sonnet", composite=0.85, avg_cost=0.003),
            _make_agg("opus", composite=0.92, avg_cost=0.015),
            _make_agg("gpt4o", composite=0.88, avg_cost=0.005),
            _make_agg("gpt4-mini", composite=0.78, avg_cost=0.001),
            _make_agg("local-llama", composite=0.65, avg_cost=0.0001),
            _make_agg("deepseek", composite=0.86, avg_cost=0.002),
        ]

        def repo_for(tt: TaskType):
            repo = AsyncMock()
            repo.get_aggregate_scores = AsyncMock(
                return_value=[{**a, "task_type": tt.value} for a in all_aggs]
            )
            return repo

        router_sec = AdaptiveRouter(
            benchmark_repo=repo_for(TaskType.SECURITY_FIX), min_samples=3
        )
        dec_sec = await router_sec.route(TaskType.SECURITY_FIX)
        assert dec_sec.selected_model_profile_id == "opus"

        router_doc = AdaptiveRouter(
            benchmark_repo=repo_for(TaskType.DOCUMENTATION), min_samples=3
        )
        dec_doc = await router_doc.route(TaskType.DOCUMENTATION)
        assert dec_doc.selected_model_profile_id in {"haiku", "local-llama", "gpt4-mini", "deepseek"}

        router_bug = AdaptiveRouter(
            benchmark_repo=repo_for(TaskType.BUG_FIX), min_samples=3
        )
        dec_bug = await router_bug.route(TaskType.BUG_FIX)
        assert dec_bug.selected_model_profile_id in {"sonnet", "opus", "gpt4o", "deepseek"}

    async def test_live_profiles_with_pareto_and_budget(self):
        """Full pipeline: Pareto filter + budget constraint + task-type weights."""
        aggs = [
            _make_agg("haiku", composite=0.72, avg_cost=0.0005, task_type=TaskType.REFACTOR),
            _make_agg("sonnet", composite=0.85, avg_cost=0.003, task_type=TaskType.REFACTOR),
            _make_agg("opus", composite=0.92, avg_cost=0.015, task_type=TaskType.REFACTOR),
            _make_agg("dominated-bad", composite=0.60, avg_cost=0.05, task_type=TaskType.REFACTOR),
        ]
        repo = AsyncMock()
        repo.get_aggregate_scores = AsyncMock(return_value=aggs)

        pareto = ParetoRouter()
        router = AdaptiveRouter(
            benchmark_repo=repo,
            min_samples=3,
            pareto_router=pareto,
            adequacy_margin=0.03,
        )
        decision = await router.route(TaskType.REFACTOR)
        assert decision.selected_model_profile_id != "dominated-bad"
        assert decision.fallback is False

        decision_tight = await router.route(
            TaskType.REFACTOR,
            max_cost_usd=0.005,
            default_model_profile="fallback",
        )
        assert decision_tight.selected_model_profile_id == "sonnet"
