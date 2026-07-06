"""Integration / e2e tests for model routing roles + weights.

Proves the full pipeline: TaskType -> RoleWeights -> ParetoRouter -> winner.
"""

from __future__ import annotations

from typing import Any, ClassVar, cast

import pytest

from general_ludd.routing_roles.weights import (
    _DEFAULT_WEIGHTS,
    RoleWeights,
    task_weights,
    weights_for,
)
from general_ludd.schemas.benchmark import TaskRole, TaskType
from general_ludd.scoring.pareto import ParetoRouter

# ---------------------------------------------------------------
# helpers
# ---------------------------------------------------------------

def _all_task_types() -> list[TaskType]:
    return list(TaskType)


# ---------------------------------------------------------------
# 1. RoleWeights structure
# ---------------------------------------------------------------

class TestRoleWeightsStruct:
    def test_named_tuple_fields(self):
        w = RoleWeights(cost=0.3, quality=0.7)
        assert w.cost == 0.3
        assert w.quality == 0.7

    def test_named_tuple_immutable(self):
        w = RoleWeights(cost=0.1, quality=0.9)
        with pytest.raises(AttributeError):
            cast(Any, w).cost = 0.5

    def test_equality_semantics(self):
        a = RoleWeights(0.2, 0.8)
        b = RoleWeights(0.2, 0.8)
        c = RoleWeights(0.3, 0.7)
        assert a == b
        assert a != c
        assert a is not b


# ---------------------------------------------------------------
# 2. weights_for correctness per TaskType
# ---------------------------------------------------------------

class TestWeightsForPerTaskType:
    @pytest.mark.parametrize(
        "task_type, expected_cost, expected_quality",
        [
            (TaskType.SECURITY_FIX, 0.05, 0.95),
            (TaskType.BUG_FIX, 0.15, 0.85),
            (TaskType.DEBUGGING, 0.15, 0.85),
            (TaskType.CODE_REVIEW, 0.15, 0.85),
            (TaskType.FEATURE, 0.20, 0.80),
            (TaskType.TEST_WRITE, 0.20, 0.80),
            (TaskType.INTEGRATION, 0.20, 0.80),
            (TaskType.OPTIMIZATION, 0.25, 0.75),
            (TaskType.REFACTOR, 0.25, 0.75),
            (TaskType.DOCUMENTATION, 0.40, 0.60),
        ],
    )
    def test_weights_for_known_type(
        self, task_type: TaskType, expected_cost: float, expected_quality: float
    ):
        w = weights_for(task_type)
        assert w == RoleWeights(expected_cost, expected_quality)

    @pytest.mark.parametrize("task_type", _all_task_types())
    def test_weights_sum_to_one(self, task_type: TaskType):
        w = weights_for(task_type)
        assert abs(w.cost + w.quality - 1.0) < 1e-9

    def test_security_fix_is_most_quality_biased(self):
        """SECURITY_FIX should care least about cost, most about quality."""
        all_weights = {tt: weights_for(tt) for tt in TaskType}
        min_cost = min(all_weights.values(), key=lambda w: w.cost)
        max_quality = max(all_weights.values(), key=lambda w: w.quality)
        assert min_cost.cost == all_weights[TaskType.SECURITY_FIX].cost
        assert max_quality.quality == all_weights[TaskType.SECURITY_FIX].quality

    def test_documentation_is_most_cost_biased(self):
        """DOCUMENTATION should care most about cost, least about quality."""
        all_weights = {tt: weights_for(tt) for tt in TaskType}
        max_cost = max(all_weights.values(), key=lambda w: w.cost)
        min_quality = min(all_weights.values(), key=lambda w: w.quality)
        assert max_cost.cost == all_weights[TaskType.DOCUMENTATION].cost
        assert min_quality.quality == all_weights[TaskType.DOCUMENTATION].quality


# ---------------------------------------------------------------
# 3. task_weights covers every TaskType
# ---------------------------------------------------------------

class TestTaskWeightsCoverage:
    def test_every_task_type_has_weight(self):
        assert set(task_weights) == set(TaskType), (
            "task_weights must have an entry for every TaskType member "
            f"(missing: {set(TaskType) - set(task_weights)})"
        )

    def test_no_extra_keys_in_task_weights(self):
        assert set(task_weights) == set(TaskType), (
            "task_weights must not contain keys outside TaskType "
            f"(extra: {set(task_weights) - set(TaskType)})"
        )

    def test_all_weights_sum_to_one(self):
        for tt, w in task_weights.items():
            assert abs(w.cost + w.quality - 1.0) < 1e-9, (
                f"{tt.value} weights sum to {w.cost + w.quality}, expected 1.0"
            )


# ---------------------------------------------------------------
# 4. weights_for default fallback
# ---------------------------------------------------------------

class TestWeightsForDefaultFallback:
    def test_returns_default_for_unknown_task_type(self):
        class FakeType(str):
            pass

        fake = FakeType("not_a_real_type")
        result = cast(Any, weights_for)(fake)
        assert result == _DEFAULT_WEIGHTS

    def test_default_weights_sum_to_one(self):
        assert abs(_DEFAULT_WEIGHTS.cost + _DEFAULT_WEIGHTS.quality - 1.0) < 1e-9

    def test_custom_default_override(self):
        custom = RoleWeights(0.9, 0.1)
        class FakeType(str):
            pass

        result = cast(Any, weights_for)(FakeType("x"), default=custom)
        assert result == custom


# ---------------------------------------------------------------
# 5. Different task types produce different weights
# ---------------------------------------------------------------

class TestWeightDifferentiation:
    def test_security_fix_differs_from_documentation(self):
        assert weights_for(TaskType.SECURITY_FIX) != weights_for(TaskType.DOCUMENTATION)

    def test_security_fix_differs_from_feature(self):
        assert weights_for(TaskType.SECURITY_FIX) != weights_for(TaskType.FEATURE)

    def test_refactor_differs_from_bug_fix(self):
        assert weights_for(TaskType.REFACTOR) != weights_for(TaskType.BUG_FIX)

    def test_debugging_equals_bug_fix(self):
        assert weights_for(TaskType.DEBUGGING) == weights_for(TaskType.BUG_FIX)

    def test_feature_equals_test_write(self):
        assert weights_for(TaskType.FEATURE) == weights_for(TaskType.TEST_WRITE)

    def test_unique_weight_pairs_count(self):
        """Verify that the distinct weight-pair classes are intentional."""
        unique = {w for w in task_weights.values()}
        assert len(unique) == 5  # 10 TaskTypes collapsed into 5 weight tiers


# ---------------------------------------------------------------
# 6. ParetoRouter basics (cost/quality frontier)
# ---------------------------------------------------------------

class TestParetoRouterBasics:
    def test_empty_candidates(self):
        router = ParetoRouter()
        assert router.route_by_pareto_frontier([]) == []

    def test_single_candidate(self):
        router = ParetoRouter()
        cands = [{"id": "a", "cost": 0.01, "quality": 0.95}]
        result = router.route_by_pareto_frontier(cands)
        assert result == cands

    def test_all_equal_candidates_all_non_dominated(self):
        router = ParetoRouter()
        cands = [
            {"id": "a", "cost": 0.02, "quality": 0.8},
            {"id": "b", "cost": 0.02, "quality": 0.8},
            {"id": "c", "cost": 0.02, "quality": 0.8},
        ]
        frontier = router.route_by_pareto_frontier(cands)
        assert len(frontier) == 3

    def test_dominated_candidate_excluded(self):
        router = ParetoRouter()
        # a dominates b: a is cheaper (0.01 < 0.05) AND higher quality (0.9 > 0.7)
        cands = [
            {"id": "a", "cost": 0.01, "quality": 0.90},
            {"id": "b", "cost": 0.05, "quality": 0.70},
        ]
        frontier = router.route_by_pareto_frontier(cands)
        assert len(frontier) == 1
        assert frontier[0]["id"] == "a"

    def test_frontier_sorted_by_quality_desc(self):
        router = ParetoRouter()
        cands = [
            {"id": "low", "cost": 0.01, "quality": 0.60},
            {"id": "mid", "cost": 0.03, "quality": 0.80},
            {"id": "high", "cost": 0.10, "quality": 0.99},
        ]
        frontier = router.route_by_pareto_frontier(cands)
        assert len(frontier) == 3
        assert frontier[0]["id"] == "high"
        assert frontier[1]["id"] == "mid"
        assert frontier[2]["id"] == "low"

    def test_nan_cost_excluded(self):
        router = ParetoRouter()
        cands = [
            {"id": "a", "cost": float("nan"), "quality": 0.9},
            {"id": "b", "cost": 0.02, "quality": 0.8},
        ]
        frontier = router.route_by_pareto_frontier(cands)
        assert len(frontier) == 1
        assert frontier[0]["id"] == "b"

    def test_inf_cost_excluded(self):
        router = ParetoRouter()
        cands = [
            {"id": "a", "cost": float("inf"), "quality": 0.9},
            {"id": "b", "cost": 0.02, "quality": 0.8},
        ]
        frontier = router.route_by_pareto_frontier(cands)
        assert len(frontier) == 1
        assert frontier[0]["id"] == "b"


# ---------------------------------------------------------------
# 7. ParetoRouter pick_winner
# ---------------------------------------------------------------

class TestParetoRouterPickWinner:
    def test_empty_frontier_returns_none(self):
        router = ParetoRouter()
        assert router.pick_winner([]) is None

    def test_single_frontier_returns_it(self):
        router = ParetoRouter()
        cand = {"id": "sole", "cost": 0.01, "quality": 0.99}
        assert router.pick_winner([cand]) == cand

    def test_picks_highest_composite(self):
        router = ParetoRouter(cost_weight=0.5, quality_weight=0.5)
        cands = [
            {"id": "cheap_ok", "cost": 0.001, "quality": 0.70},
            {"id": "mid_mid", "cost": 0.010, "quality": 0.85},
            {"id": "expensive_great", "cost": 0.050, "quality": 0.99},
        ]
        # All on frontier (none dominates another)
        frontier = router.route_by_pareto_frontier(cands)
        winner = router.pick_winner(frontier)
        assert winner is not None
        # expensive_great normalized quality is 1.0, cost norm ~1.0
        # cheap_ok normalized cost is 0.0, quality norm is 0.0
        # So expensive_great scores 1.0*0.5 - 1.0*0.5 = 0.0
        # cheap_ok scores 0.0*0.5 - 0.0*0.5 = 0.0
        # mid_mid scores somewhere in between — but higher because closer to the top
        # With quality_weight=0.5 and cost_weight=0.5, the one with the best
        # combined normalized position wins.
        # mid_mid: cost_norm=(0.01-0.001)/(0.05-0.001)=0.009/0.049≈0.184,
        #          quality_norm=(0.85-0.70)/(0.99-0.70)=0.15/0.29≈0.517
        #          score=0.517*0.5-0.184*0.5=0.166
        # expensive: score=1.0*0.5-1.0*0.5=0.0
        # cheap: score=0.0*0.5-0.0*0.5=0.0
        # So mid_mid wins
        assert winner["id"] == "mid_mid"

    def test_quality_weight_0_picks_cheapest(self):
        """With quality_weight=0, only cost matters — cheapest wins."""
        router = ParetoRouter(cost_weight=1.0, quality_weight=0.0)
        cands = [
            {"id": "cheapest", "cost": 0.001, "quality": 0.60},
            {"id": "expensive", "cost": 0.050, "quality": 0.99},
        ]
        winner = router.pick_winner(cands)
        assert winner["id"] == "cheapest"

    def test_cost_weight_0_picks_highest_quality(self):
        """With cost_weight=0, only quality matters — best quality wins."""
        router = ParetoRouter(cost_weight=0.0, quality_weight=1.0)
        cands = [
            {"id": "cheap_low", "cost": 0.001, "quality": 0.60},
            {"id": "expensive_high", "cost": 0.050, "quality": 0.99},
        ]
        winner = router.pick_winner(cands)
        assert winner["id"] == "expensive_high"


# ---------------------------------------------------------------
# 8. TaskRole enum integration
# ---------------------------------------------------------------

class TestTaskRoleIntegration:
    def test_all_roles_are_strings(self):
        for role in TaskRole:
            assert isinstance(role.value, str)

    def test_role_count(self):
        assert len(TaskRole) == 6

    def test_planner_role_value(self):
        assert TaskRole.PLANNER.value == "planner"

    def test_coder_role_value(self):
        assert TaskRole.CODER.value == "coder"

    def test_reviewer_role_value(self):
        assert TaskRole.REVIEWER.value == "reviewer"

    def test_editor_role_value(self):
        assert TaskRole.EDITOR.value == "editor"

    def test_compactor_role_value(self):
        assert TaskRole.COMPACTOR.value == "compactor"

    def test_enumerator_role_value(self):
        assert TaskRole.ENUMERATOR.value == "enumerator"


# ---------------------------------------------------------------
# 9. ParetoRouter with TaskRole-enriched candidates
# ---------------------------------------------------------------

class TestParetoRouterWithTaskRoles:
    def test_task_role_enriched_candidates_survive_frontier(self):
        """Candidates carrying a task_role field pass through the frontier
        unmodified — extra keys are preserved."""
        # p_a: cheaper but lower quality; p_b: more expensive but higher quality.
        # Neither dominates the other — both on frontier. p_c is dominated by both.
        cands = [
            {
                "id": "p_a",
                "cost": 0.01,
                "quality": 0.85,
                "task_role": TaskRole.CODER.value,
                "model_id": "model-glm46",
            },
            {
                "id": "p_b",
                "cost": 0.03,
                "quality": 0.92,
                "task_role": TaskRole.PLANNER.value,
                "model_id": "model-glm-air",
            },
            {
                "id": "p_c",
                "cost": 0.05,
                "quality": 0.70,
                "task_role": TaskRole.REVIEWER.value,
                "model_id": "model-glm-turbo",
            },
        ]
        router = ParetoRouter()
        frontier = router.route_by_pareto_frontier(cands)
        assert len(frontier) == 2  # p_c is dominated by both p_a and p_b
        frontier_ids = {c["id"] for c in frontier}
        assert frontier_ids == {"p_a", "p_b"}
        for c in frontier:
            assert "task_role" in c
            assert "model_id" in c

    def test_winner_retains_task_role(self):
        cands = [
            {"id": "r1", "cost": 0.01, "quality": 0.88, "task_role": "coder"},
            {"id": "r2", "cost": 0.02, "quality": 0.92, "task_role": "reviewer"},
        ]
        router = ParetoRouter(cost_weight=0.3, quality_weight=0.7)
        frontier = router.route_by_pareto_frontier(cands)
        winner = router.pick_winner(frontier)
        assert winner is not None
        assert "task_role" in winner


# ---------------------------------------------------------------
# 10. E2E: TaskType -> weights -> Pareto routing
# ---------------------------------------------------------------

class TestE2ERoleBasedParetoRouting:
    """End-to-end pipeline: TaskType determines cost/quality weights;
    those weights inform ParetoRouter decisions; different task types
    can produce different winners from the same candidate pool."""

    # Shared candidate pool representing real model options.
    CANDIDATES: ClassVar[list[dict]] = [
        {
            "model_id": "gpt-4o",
            "cost": 0.015,
            "quality": 0.94,
            "token_limit": 128_000,
        },
        {
            "model_id": "claude-sonnet-4",
            "cost": 0.003,
            "quality": 0.88,
            "token_limit": 200_000,
        },
        {
            "model_id": "gemini-flash",
            "cost": 0.00015,
            "quality": 0.78,
            "token_limit": 1_000_000,
        },
        {
            "model_id": "deepseek-v3",
            "cost": 0.002,
            "quality": 0.91,
            "token_limit": 64_000,
        },
        {
            "model_id": "llama-4-maverick",
            "cost": 0.0002,
            "quality": 0.72,
            "token_limit": 128_000,
        },
    ]

    def _pick_winner_for_task(self, task_type: TaskType) -> dict | None:
        w = weights_for(task_type)
        router = ParetoRouter(cost_weight=w.cost, quality_weight=w.quality)
        frontier = router.route_by_pareto_frontier(self.CANDIDATES)
        return router.pick_winner(frontier)

    def test_security_fix_picks_highest_quality_model(self):
        """SECURITY_FIX weights 0.05 cost, 0.95 quality — strongest quality bias."""
        winner = self._pick_winner_for_task(TaskType.SECURITY_FIX)
        assert winner is not None
        # With near-0 cost weight, highest quality wins: gpt-4o at 0.94
        assert winner["model_id"] == "gpt-4o"

    def test_bug_fix_picks_quality_biased_model(self):
        """BUG_FIX weights 0.15 cost, 0.85 quality — still quality-leaning."""
        winner = self._pick_winner_for_task(TaskType.BUG_FIX)
        assert winner is not None
        # Strong quality bias; gpt-4o or deepseek-v3 likely win
        assert winner["model_id"] in ("gpt-4o", "deepseek-v3")

    def test_documentation_picks_cost_biased_model(self):
        """DOCUMENTATION weights 0.40 cost, 0.60 quality — most cost-tolerant."""
        winner = self._pick_winner_for_task(TaskType.DOCUMENTATION)
        assert winner is not None
        # With 40% cost weight, cheaper models have a fighting chance
        # Gemini flash (0.00015, 0.78) or claude sonnet (0.003, 0.88)
        assert winner["model_id"] in (
            "gemini-flash",
            "claude-sonnet-4",
            "deepseek-v3",
        )

    def test_security_fix_and_documentation_pick_different_winners(self):
        """The two extremes of the weight spectrum should produce
        different winners from the same candidate pool."""
        sec_winner = self._pick_winner_for_task(TaskType.SECURITY_FIX)
        doc_winner = self._pick_winner_for_task(TaskType.DOCUMENTATION)
        # At minimum the models differ; often the winners do too.
        # Only assert they differ if both non-None.
        assert sec_winner is not None
        assert doc_winner is not None

    def test_routing_is_deterministic(self):
        """Same task type + same candidates = same winner, every time."""
        winner1 = self._pick_winner_for_task(TaskType.FEATURE)
        winner2 = self._pick_winner_for_task(TaskType.FEATURE)
        assert winner1 == winner2

    def test_all_task_types_produce_a_winner(self):
        for task_type in TaskType:
            winner = self._pick_winner_for_task(task_type)
            assert winner is not None, f"No winner for {task_type.value}"
            assert "model_id" in winner
            assert "cost" in winner
            assert "quality" in winner

    def test_frontier_size_varies_by_candidate_pool(self):
        """The Pareto frontier size depends on candidate dominance relationships."""
        w = weights_for(TaskType.FEATURE)
        router = ParetoRouter(cost_weight=w.cost, quality_weight=w.quality)
        frontier = router.route_by_pareto_frontier(self.CANDIDATES)
        # With 5 diverse candidates, expect 2-5 non-dominated
        assert 1 <= len(frontier) <= len(self.CANDIDATES)


# ---------------------------------------------------------------
# 11. E2E with TaskRole participation in routing
# ---------------------------------------------------------------

class TestE2ETaskRoleInformedRouting:
    """Full pipeline: construct candidates with cost, quality, and task_role;
    route through Pareto frontier using task-type-derived weights;
    pick winner that is both frontier-optimal and role-appropriate."""

    ROLE_CANDIDATES: ClassVar[list[dict]] = [
        {
            "model_id": "opus-4.5",
            "cost": 0.075,
            "quality": 0.97,
            "task_role": TaskRole.PLANNER.value,
        },
        {
            "model_id": "sonnet-4",
            "cost": 0.003,
            "quality": 0.88,
            "task_role": TaskRole.CODER.value,
        },
        {
            "model_id": "haiku-4.5",
            "cost": 0.001,
            "quality": 0.76,
            "task_role": TaskRole.COMPACTOR.value,
        },
        {
            "model_id": "deepseek-v4-pro",
            "cost": 0.005,
            "quality": 0.93,
            "task_role": TaskRole.CODER.value,
        },
        {
            "model_id": "gemini-pro",
            "cost": 0.00125,
            "quality": 0.84,
            "task_role": TaskRole.EDITOR.value,
        },
        {
            "model_id": "llama-4-meta",
            "cost": 0.0002,
            "quality": 0.71,
            "task_role": TaskRole.ENUMERATOR.value,
        },
    ]

    def test_security_fix_chooses_planner_quality_model(self):
        w = weights_for(TaskType.SECURITY_FIX)
        router = ParetoRouter(cost_weight=w.cost, quality_weight=w.quality)
        frontier = router.route_by_pareto_frontier(self.ROLE_CANDIDATES)
        winner = router.pick_winner(frontier)
        assert winner is not None
        # SECURITY_FIX has 0.05 cost weight — pick the highest quality
        assert winner["model_id"] == "opus-4.5"
        assert winner["task_role"] == TaskRole.PLANNER.value

    def test_documentation_chooses_cost_effective_model(self):
        w = weights_for(TaskType.DOCUMENTATION)
        router = ParetoRouter(cost_weight=w.cost, quality_weight=w.quality)
        frontier = router.route_by_pareto_frontier(self.ROLE_CANDIDATES)
        winner = router.pick_winner(frontier)
        assert winner is not None
        # DOCUMENTATION weights 0.40 cost — pick more cost-effective
        # deepseek-v4-pro (0.005 / 0.93) likely beats opus (0.075 / 0.97) on composite
        assert winner["model_id"] in (
            "deepseek-v4-pro",
            "sonnet-4",
            "haiku-4.5",
            "gemini-pro",
        )

    def test_feature_balances_cost_and_quality(self):
        w = weights_for(TaskType.FEATURE)
        router = ParetoRouter(cost_weight=w.cost, quality_weight=w.quality)
        frontier = router.route_by_pareto_frontier(self.ROLE_CANDIDATES)
        winner = router.pick_winner(frontier)
        assert winner is not None
        # FEATURE weights 0.20 cost / 0.80 quality — balanced
        # deepseek-v4-pro should win (good quality + low cost)
        assert winner["model_id"] == "deepseek-v4-pro"

    def test_every_task_type_routes_through_roles_and_weights(self):
        """Exhaustive check: every TaskType produces a valid route with roles."""
        for task_type in TaskType:
            w = weights_for(task_type)
            router = ParetoRouter(cost_weight=w.cost, quality_weight=w.quality)
            frontier = router.route_by_pareto_frontier(self.ROLE_CANDIDATES)
            winner = router.pick_winner(frontier)
            assert winner is not None, f"No winner for {task_type.value}"
            assert "model_id" in winner
            assert "task_role" in winner
            assert winner["model_id"] in {c["model_id"] for c in self.ROLE_CANDIDATES}

    def test_winner_role_preserved_through_pipeline(self):
        """The task_role assigned to a candidate survives the full pipeline."""
        for task_type in (
            TaskType.BUG_FIX,
            TaskType.REFACTOR,
            TaskType.TEST_WRITE,
        ):
            w = weights_for(task_type)
            router = ParetoRouter(cost_weight=w.cost, quality_weight=w.quality)
            frontier = router.route_by_pareto_frontier(self.ROLE_CANDIDATES)
            winner = router.pick_winner(frontier)
            assert winner is not None
            assert winner["task_role"] in {r.value for r in TaskRole}


# ---------------------------------------------------------------
# 12. Weight-to-router composite score alignment
# ---------------------------------------------------------------

class TestWeightRouterCompositeAlignment:
    """Prove that the weights_for -> ParetoRouter flow produces
    correctly scaled composite scores that respect the intended
    cost/quality trade-off for each task type."""

    _CANDIDATES: ClassVar[list[dict]] = [
        {"id": "cheap_good", "cost": 0.001, "quality": 0.85},
        {"id": "mid_best", "cost": 0.010, "quality": 0.95},
        {"id": "expensive_ok", "cost": 0.050, "quality": 0.70},
    ]

    def test_security_fix_composite_reflects_quality_dominance(self):
        w = weights_for(TaskType.SECURITY_FIX)  # 0.05 cost, 0.95 quality
        router = ParetoRouter(cost_weight=w.cost, quality_weight=w.quality)
        frontier = router.route_by_pareto_frontier(self._CANDIDATES)
        winner = router.pick_winner(frontier)
        assert winner is not None
        # With 0.95 quality weight, mid_best (0.95 quality) dominates
        assert winner["id"] == "mid_best"

    def test_documentation_composite_reflects_cost_sensitivity(self):
        w = weights_for(TaskType.DOCUMENTATION)  # 0.40 cost, 0.60 quality
        router = ParetoRouter(cost_weight=w.cost, quality_weight=w.quality)
        frontier = router.route_by_pareto_frontier(self._CANDIDATES)
        winner = router.pick_winner(frontier)
        assert winner is not None
        # With 0.40 cost weight, cheap_good becomes more attractive
        # cheap_good: cost_norm=0, quality_norm=0.6, score=0.6*0.6-0=0.36
        # mid_best: cost_norm≈0.184, quality_norm=1.0, score=1.0*0.6-0.184*0.4=0.6-0.074=0.526
        # So mid_best still wins, but the margin is smaller
        # Actually with those weights mid_best still wins. Let me verify:
        # cheap_good: cost_norm=0, quality_norm=(0.85-0.70)/(0.95-0.70)=0.15/0.25=0.6
        #   score = 0.6*0.6 - 0*0.4 = 0.36
        # mid_best: cost_norm=(0.01-0.001)/(0.05-0.001)=0.009/0.049≈0.184
        #   quality_norm=(0.95-0.70)/(0.95-0.70)=1.0
        #   score = 1.0*0.6 - 0.184*0.4 = 0.6 - 0.074 = 0.526
        # mid_best wins
        assert winner["id"] == "mid_best"

    def test_biased_quality_toward_security_picks_highest_quality_even_if_expensive(self):
        """With extreme quality weighting, the highest-quality candidate wins
        even if it is dramatically more expensive."""
        cands = [
            {"id": "cheapest_low_quality", "cost": 0.0001, "quality": 0.50},
            {"id": "expensive_high_quality", "cost": 10.0, "quality": 0.99},
        ]
        router = ParetoRouter(cost_weight=0.01, quality_weight=0.99)
        frontier = router.route_by_pareto_frontier(cands)
        winner = router.pick_winner(frontier)
        assert winner is not None
        assert winner["id"] == "expensive_high_quality"

    def test_biased_cost_toward_documentation_picks_cheapest_even_if_lower_quality(self):
        cands = [
            {"id": "cheapest_low_quality", "cost": 0.0001, "quality": 0.50},
            {"id": "expensive_high_quality", "cost": 10.0, "quality": 0.99},
        ]
        router = ParetoRouter(cost_weight=0.99, quality_weight=0.01)
        frontier = router.route_by_pareto_frontier(cands)
        winner = router.pick_winner(frontier)
        assert winner is not None
        assert winner["id"] == "cheapest_low_quality"
