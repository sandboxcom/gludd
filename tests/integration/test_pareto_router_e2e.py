"""G8 cost/quality Pareto router — integration / e2e tests."""

from __future__ import annotations

from typing import Any

import pytest

from general_ludd.scoring.pareto import ParetoRouter

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _make_candidate(
    cost: float, quality: float, name: str = ""
) -> dict[str, Any]:
    return {"cost": cost, "quality": quality, "name": name}


def _names(frontier: list[dict[str, Any]]) -> list[str]:
    return [c["name"] for c in frontier]


# ---------------------------------------------------------------------------
# 1. construction
# ---------------------------------------------------------------------------

class TestParetoRouterConstruction:
    def test_default_weights(self) -> None:
        r = ParetoRouter()
        assert r._cost_weight == 0.5
        assert r._quality_weight == 0.5

    def test_custom_weights(self) -> None:
        r = ParetoRouter(cost_weight=0.2, quality_weight=0.8)
        assert r._cost_weight == 0.2
        assert r._quality_weight == 0.8

    def test_cost_dominant_weights(self) -> None:
        r = ParetoRouter(cost_weight=0.9, quality_weight=0.1)
        assert r._cost_weight == 0.9
        assert r._quality_weight == 0.1

    def test_quality_dominant_weights(self) -> None:
        r = ParetoRouter(cost_weight=0.1, quality_weight=0.9)
        assert r._quality_weight == 0.9

    def test_zero_weights(self) -> None:
        r = ParetoRouter(cost_weight=0.0, quality_weight=1.0)
        assert r._cost_weight == 0.0
        assert r._quality_weight == 1.0


# ---------------------------------------------------------------------------
# 2. clear domination — only non-dominated survive
# ---------------------------------------------------------------------------

class TestClearDomination:
    def test_single_dominator_excludes_others(self) -> None:
        router = ParetoRouter()
        candidates = [
            _make_candidate(0.1, 0.9, "best"),       # lower cost, higher quality -> dominates
            _make_candidate(0.5, 0.5, "mid"),
            _make_candidate(0.8, 0.3, "worst"),
        ]
        frontier = router.route_by_pareto_frontier(candidates)
        assert _names(frontier) == ["best"]

    def test_two_dominated_one_frontier(self) -> None:
        router = ParetoRouter()
        candidates = [
            _make_candidate(0.1, 0.95, "dominator"),
            _make_candidate(0.2, 0.80, "dominated"),  # worse cost AND quality
            _make_candidate(0.15, 0.85, "also_dominated"),
        ]
        frontier = router.route_by_pareto_frontier(candidates)
        assert _names(frontier) == ["dominator"]

    def test_multiple_dominators(self) -> None:
        router = ParetoRouter()
        candidates = [
            _make_candidate(0.0, 1.0, "perfect"),       # dominates all
            _make_candidate(0.2, 0.99, "great"),        # dominated by perfect
            _make_candidate(0.8, 0.5, "ok"),
            _make_candidate(0.9, 0.1, "bad"),
        ]
        frontier = router.route_by_pareto_frontier(candidates)
        assert _names(frontier) == ["perfect"]


# ---------------------------------------------------------------------------
# 3. no model dominates another — both on frontier
# ---------------------------------------------------------------------------

class TestNoDomination:
    def test_tradeoff_both_frontier(self) -> None:
        router = ParetoRouter()
        candidates = [
            _make_candidate(0.2, 0.6, "cheaper"),   # cheaper, lower quality
            _make_candidate(0.8, 0.9, "better"),    # more expensive, better quality
        ]
        frontier = router.route_by_pareto_frontier(candidates)
        names = _names(frontier)
        assert len(names) == 2
        assert "cheaper" in names
        assert "better" in names

    def test_three_way_tradeoff(self) -> None:
        router = ParetoRouter()
        candidates = [
            _make_candidate(0.1, 0.5, "lowest_cost"),
            _make_candidate(0.5, 0.7, "mid"),
            _make_candidate(0.9, 0.95, "highest_quality"),
        ]
        frontier = router.route_by_pareto_frontier(candidates)
        names = _names(frontier)
        assert len(names) == 3


# ---------------------------------------------------------------------------
# 4. 10+ candidates — correct frontier size
# ---------------------------------------------------------------------------

class TestLargeFrontier:
    @pytest.fixture()
    def _candidates(self) -> list[dict[str, Any]]:
        return [
            _make_candidate(0.1, 0.95, "c01"),
            _make_candidate(0.2, 0.90, "c02"),
            _make_candidate(0.3, 0.85, "c03"),
            _make_candidate(0.4, 0.80, "c04"),
            _make_candidate(0.5, 0.75, "c05"),
            _make_candidate(0.1, 0.80, "c06"),  # dominated by c01 (same cost, lower q)
            _make_candidate(0.2, 0.70, "c07"),  # dominated by c02
            _make_candidate(0.6, 0.70, "c08"),
            _make_candidate(0.7, 0.60, "c09"),
            _make_candidate(0.8, 0.50, "c10"),
            _make_candidate(0.3, 0.80, "c11"),  # dominated by c03
            _make_candidate(0.9, 0.40, "c12"),
        ]

    def test_frontier_size(self, _candidates: list[dict[str, Any]]) -> None:
        router = ParetoRouter()
        frontier = router.route_by_pareto_frontier(_candidates)
        # c01 (0.1, 0.95) has lowest cost AND highest quality — dominates all
        assert len(frontier) == 1

    def test_all_frontier_members_named(self, _candidates: list[dict[str, Any]]) -> None:
        router = ParetoRouter()
        frontier = router.route_by_pareto_frontier(_candidates)
        frontier_names = _names(frontier)
        # c01 (0.1, 0.95) dominates all — only c01 on frontier
        assert frontier_names == ["c01"]


# ---------------------------------------------------------------------------
# 5. frontier returned in quality-descending order
# ---------------------------------------------------------------------------

class TestFrontierOrdering:
    def test_quality_descending(self) -> None:
        router = ParetoRouter()
        candidates = [
            _make_candidate(0.1, 0.5, "a"),
            _make_candidate(0.8, 0.9, "b"),
            _make_candidate(0.4, 0.7, "c"),
            _make_candidate(0.2, 0.6, "d"),
        ]
        frontier = router.route_by_pareto_frontier(candidates)
        qualities = [float(c["quality"]) for c in frontier]
        assert qualities == sorted(qualities, reverse=True), f"expected desc, got {qualities}"

    def test_quality_descending_with_ties(self) -> None:
        router = ParetoRouter()
        candidates = [
            _make_candidate(0.2, 0.8, "a"),
            _make_candidate(0.4, 0.8, "b"),  # same quality, higher cost — dominated
            _make_candidate(0.1, 0.9, "c"),
        ]
        frontier = router.route_by_pareto_frontier(candidates)
        qualities = [float(c["quality"]) for c in frontier]
        assert qualities == sorted(qualities, reverse=True)

    def test_single_candidate_ordering(self) -> None:
        router = ParetoRouter()
        candidates = [_make_candidate(0.5, 0.5, "x")]
        frontier = router.route_by_pareto_frontier(candidates)
        assert len(frontier) == 1
        assert frontier[0]["quality"] == 0.5


# ---------------------------------------------------------------------------
# 6. edge cases: empty, single, all equal
# ---------------------------------------------------------------------------

class TestEdgeCases:
    def test_empty_list(self) -> None:
        router = ParetoRouter()
        assert router.route_by_pareto_frontier([]) == []

    def test_single_candidate(self) -> None:
        router = ParetoRouter()
        candidates = [_make_candidate(0.3, 0.7, "only")]
        frontier = router.route_by_pareto_frontier(candidates)
        assert _names(frontier) == ["only"]

    def test_all_equal(self) -> None:
        router = ParetoRouter()
        candidates = [
            _make_candidate(0.5, 0.5, "a"),
            _make_candidate(0.5, 0.5, "b"),
            _make_candidate(0.5, 0.5, "c"),
        ]
        frontier = router.route_by_pareto_frontier(candidates)
        assert len(frontier) == 3

    def test_all_equal_no_domination(self) -> None:
        router = ParetoRouter()
        candidates = [
            _make_candidate(0.4, 0.6, "a"),
            _make_candidate(0.4, 0.6, "b"),
            _make_candidate(0.4, 0.6, "c"),
        ]
        frontier = router.route_by_pareto_frontier(candidates)
        assert {c["name"] for c in frontier} == {"a", "b", "c"}


# ---------------------------------------------------------------------------
# 7 & 8. NaN / Inf and missing fields excluded
# ---------------------------------------------------------------------------

class TestNaNAndInfExclusion:
    def test_nan_cost_excluded(self) -> None:
        router = ParetoRouter()
        candidates = [
            _make_candidate(0.2, 0.8, "valid"),
            _make_candidate(float("nan"), 0.9, "nan_cost"),
            _make_candidate(0.5, 0.5, "also_valid"),
        ]
        frontier = router.route_by_pareto_frontier(candidates)
        names = _names(frontier)
        assert "nan_cost" not in names
        assert "valid" in names
        # also_valid (0.5, 0.5) dominated by valid (0.2, 0.8) — lower cost AND higher quality

    def test_nan_quality_excluded(self) -> None:
        router = ParetoRouter()
        candidates = [
            _make_candidate(0.2, 0.8, "valid"),
            _make_candidate(0.3, float("nan"), "nan_quality"),
        ]
        frontier = router.route_by_pareto_frontier(candidates)
        assert _names(frontier) == ["valid"]

    def test_inf_cost_excluded(self) -> None:
        router = ParetoRouter()
        candidates = [
            _make_candidate(0.2, 0.8, "valid"),
            _make_candidate(float("inf"), 0.99, "inf_cost"),
        ]
        frontier = router.route_by_pareto_frontier(candidates)
        assert "inf_cost" not in _names(frontier)

    def test_neg_inf_quality_excluded(self) -> None:
        router = ParetoRouter()
        candidates = [
            _make_candidate(0.2, 0.8, "valid"),
            _make_candidate(0.1, float("-inf"), "neg_inf_quality"),
        ]
        frontier = router.route_by_pareto_frontier(candidates)
        assert "neg_inf_quality" not in _names(frontier)

    def test_all_invalid_yields_empty(self) -> None:
        router = ParetoRouter()
        candidates = [
            _make_candidate(float("nan"), 0.5, "a"),
            _make_candidate(0.5, float("inf"), "b"),
        ]
        assert router.route_by_pareto_frontier(candidates) == []

    def test_missing_cost_field_excluded(self) -> None:
        router = ParetoRouter()
        candidates = [
            _make_candidate(0.2, 0.8, "valid"),
            {"quality": 0.9, "name": "no_cost"},  # .get("cost", float("nan")) → NaN
        ]
        frontier = router.route_by_pareto_frontier(candidates)
        assert _names(frontier) == ["valid"]

    def test_missing_quality_field_excluded(self) -> None:
        router = ParetoRouter()
        candidates = [
            _make_candidate(0.2, 0.8, "valid"),
            {"cost": 0.1, "name": "no_quality"},
        ]
        frontier = router.route_by_pareto_frontier(candidates)
        assert _names(frontier) == ["valid"]

    def test_both_fields_missing_excluded(self) -> None:
        router = ParetoRouter()
        candidates = [
            _make_candidate(0.2, 0.8, "valid"),
            {"name": "nothing"},
        ]
        frontier = router.route_by_pareto_frontier(candidates)
        assert _names(frontier) == ["valid"]

    def test_nan_and_missing_mixed_with_valids(self) -> None:
        router = ParetoRouter()
        candidates = [
            _make_candidate(0.1, 0.9, "v1"),
            {"cost": float("nan"), "quality": 0.5, "name": "nan_c"},
            {"cost": 0.5, "name": "no_q"},
            {"name": "no_both"},
            _make_candidate(0.3, 0.7, "v2"),
        ]
        frontier = router.route_by_pareto_frontier(candidates)
        names = _names(frontier)
        # v1 (0.1, 0.9) dominates v2 (0.3, 0.7) — lower cost AND higher quality
        assert names == ["v1"]
        assert "nan_c" not in names
        assert "no_q" not in names
        assert "no_both" not in names


# ---------------------------------------------------------------------------
# 9. pick_winner — highest composite score
# ---------------------------------------------------------------------------

class TestPickWinner:
    def test_highest_composite_wins(self) -> None:
        router = ParetoRouter(cost_weight=0.5, quality_weight=0.5)
        frontier = [
            _make_candidate(0.2, 0.8, "a"),
            _make_candidate(0.4, 0.9, "b"),
            _make_candidate(0.1, 0.6, "c"),
        ]
        winner = router.pick_winner(frontier)
        assert winner is not None
        # quality: [0.8, 0.9, 0.6] → range 0.3, min 0.6
        # cost:   [0.2, 0.4, 0.1] → range 0.3, min 0.1
        # a: q_norm=0.667, c_norm=0.333 → 0.333-0.167=0.167
        # b: q_norm=1.0,   c_norm=1.0   → 0.5-0.5=0.0
        # c: q_norm=0.0,   c_norm=0.0   → 0.0
        assert winner["name"] == "a"

    def test_pick_winner_quality_dominant(self) -> None:
        router = ParetoRouter(cost_weight=0.2, quality_weight=0.8)
        frontier = [
            _make_candidate(0.2, 0.6, "cheap_mid"),
            _make_candidate(0.8, 0.95, "expensive_high"),
        ]
        winner = router.pick_winner(frontier)
        assert winner is not None
        # expensive_high should win when quality is weighted heavily
        assert winner["name"] == "expensive_high"

    def test_pick_winner_cost_dominant(self) -> None:
        router = ParetoRouter(cost_weight=0.8, quality_weight=0.2)
        frontier = [
            _make_candidate(0.2, 0.6, "cheap_mid"),
            _make_candidate(0.8, 0.95, "expensive_high"),
        ]
        winner = router.pick_winner(frontier)
        assert winner is not None
        assert winner["name"] == "cheap_mid"


# ---------------------------------------------------------------------------
# 10. cost_weight dominant vs quality_weight dominant
# ---------------------------------------------------------------------------

class TestWeightDominance:
    def test_cost_weight_1_always_cheapest(self) -> None:
        router = ParetoRouter(cost_weight=1.0, quality_weight=0.0)
        frontier = [
            _make_candidate(0.1, 0.3, "cheapest_lowq"),
            _make_candidate(0.5, 0.99, "expensive_highq"),
        ]
        winner = router.pick_winner(frontier)
        assert winner is not None
        assert winner["name"] == "cheapest_lowq"

    def test_quality_weight_1_always_highest_quality(self) -> None:
        router = ParetoRouter(cost_weight=0.0, quality_weight=1.0)
        frontier = [
            _make_candidate(0.1, 0.3, "cheapest_lowq"),
            _make_candidate(0.5, 0.99, "expensive_highq"),
        ]
        winner = router.pick_winner(frontier)
        assert winner is not None
        assert winner["name"] == "expensive_highq"

    def test_equal_weights_equal_scores(self) -> None:
        router = ParetoRouter(cost_weight=0.5, quality_weight=0.5)
        frontier = [
            _make_candidate(0.0, 0.5, "a"),
            _make_candidate(1.0, 0.5, "b"),
        ]
        winner = router.pick_winner(frontier)
        assert winner is not None
        # All costs equal? No, a's cost=0, b's cost=1.0; both quality 0.5.
        # cost range = 1.0, quality range = 0 → both q_norm=0
        # a: c_norm=0 → 0, b: c_norm=1.0 → -0.5 → a wins
        assert winner["name"] == "a"


# ---------------------------------------------------------------------------
# 11. pick_winner empty frontier → None
# ---------------------------------------------------------------------------

class TestPickWinnerEdgeCases:
    def test_empty_frontier_none(self) -> None:
        router = ParetoRouter()
        assert router.pick_winner([]) is None

    def test_single_candidate_returns_it(self) -> None:
        router = ParetoRouter()
        candidate = _make_candidate(0.5, 0.5, "solo")
        winner = router.pick_winner([candidate])
        assert winner is candidate
        assert winner["name"] == "solo"

    def test_single_candidate_no_computation(self) -> None:
        router = ParetoRouter(cost_weight=0.0, quality_weight=0.0)
        candidate = _make_candidate(0.5, 0.5, "solo")
        winner = router.pick_winner([candidate])
        assert winner is candidate

    def test_frontier_with_all_same_composite(self) -> None:
        router = ParetoRouter()
        candidates = [
            _make_candidate(0.5, 0.5, "a"),
            _make_candidate(0.5, 0.5, "b"),
        ]
        winner = router.pick_winner(candidates)
        assert winner is not None
        assert winner["name"] in {"a", "b"}

    def test_two_candidate_frontier_deterministic(self) -> None:
        router = ParetoRouter(cost_weight=0.5, quality_weight=0.5)
        candidates = [
            _make_candidate(0.1, 0.8, "a"),
            _make_candidate(0.3, 0.9, "b"),
        ]
        winner = router.pick_winner(candidates)
        assert winner is not None
        # a: c from 0.1 to 0.3 range 0.2, q from 0.8 to 0.9 range 0.1
        # a: c_norm=(0.1-0.1)/0.2=0, q_norm=(0.8-0.8)/0.1=0 → 0
        # b: c_norm=1, q_norm=1 → 0.5-0.5=0
        # Should be deterministic: both 0.0, first wins due to > best_score (-inf)
        assert winner["name"] == "a"


# ---------------------------------------------------------------------------
# 12. pick_winner with single candidate → returns it
# ---------------------------------------------------------------------------

# (covered in TestPickWinnerEdgeCases above)


# ---------------------------------------------------------------------------
# 13. full pipeline: route_by_pareto_frontier → pick_winner
# ---------------------------------------------------------------------------

class TestFullPipeline:
    def test_winner_on_frontier(self) -> None:
        router = ParetoRouter(cost_weight=0.5, quality_weight=0.5)
        candidates = [
            _make_candidate(0.1, 0.9, "dominator"),
            _make_candidate(0.5, 0.5, "dominated"),
            _make_candidate(0.5, 0.8, "tradeoff"),
            _make_candidate(0.9, 0.95, "high_cost_high_q"),
            _make_candidate(float("nan"), 0.99, "invalid"),
        ]
        frontier = router.route_by_pareto_frontier(candidates)
        winner = router.pick_winner(frontier)
        assert winner is not None
        # winner MUST be on frontier
        assert winner in frontier
        # dominated MUST NOT be in frontier
        frontier_names = _names(frontier)
        assert "dominated" not in frontier_names

    def test_pipeline_deterministic(self) -> None:
        router = ParetoRouter()
        candidates = [
            _make_candidate(0.2, 0.9, "a"),
            _make_candidate(0.1, 0.85, "b"),
            _make_candidate(0.4, 0.8, "c"),
            _make_candidate(0.6, 0.7, "d"),
            _make_candidate(0.9, 0.4, "e"),
        ]
        r1 = router.route_by_pareto_frontier(candidates)
        w1 = router.pick_winner(r1)
        r2 = router.route_by_pareto_frontier(candidates)
        w2 = router.pick_winner(r2)
        assert [c["name"] for c in r1] == [c["name"] for c in r2]
        assert w1["name"] == w2["name"]

    def test_pipeline_empty_yields_none_winner(self) -> None:
        router = ParetoRouter()
        candidates: list[dict[str, Any]] = []
        frontier = router.route_by_pareto_frontier(candidates)
        winner = router.pick_winner(frontier)
        assert frontier == []
        assert winner is None

    def test_pipeline_all_invalid_yields_none_winner(self) -> None:
        router = ParetoRouter()
        candidates = [
            _make_candidate(float("nan"), 0.5, "a"),
            _make_candidate(0.5, float("inf"), "b"),
        ]
        frontier = router.route_by_pareto_frontier(candidates)
        assert frontier == []
        assert router.pick_winner(frontier) is None

    def test_pipeline_winner_not_dominated(self) -> None:
        router = ParetoRouter(cost_weight=0.5, quality_weight=0.5)
        candidates = [
            _make_candidate(0.2, 0.8, "a"),
            _make_candidate(0.1, 0.85, "b"),
            _make_candidate(0.3, 0.6, "c"),  # dominated by a (worse cost AND quality)
        ]
        frontier = router.route_by_pareto_frontier(candidates)
        winner = router.pick_winner(frontier)
        assert winner is not None
        assert "c" not in _names(frontier)
        assert winner["name"] in {"a", "b"}


# ---------------------------------------------------------------------------
# 14. realistic model candidates
# ---------------------------------------------------------------------------

class TestRealisticModels:
    @pytest.fixture()
    def _models(self) -> list[dict[str, Any]]:
        return [
            _make_candidate(0.05, 0.95, "opus"),
            _make_candidate(0.03, 0.90, "sonnet"),
            _make_candidate(0.01, 0.70, "haiku"),
            _make_candidate(0.10, 0.92, "gpt_4"),
            _make_candidate(0.08, 0.88, "gpt_4o"),
            _make_candidate(0.02, 0.65, "gpt_4o_mini"),
            _make_candidate(0.04, 0.85, "gemini_pro"),
            _make_candidate(0.03, 0.80, "gemini_flash"),
            _make_candidate(0.12, 0.93, "gpt_5"),
            _make_candidate(0.15, 0.97, "gpt_5_pro"),
        ]

    def test_realistic_frontier(self, _models: list[dict[str, Any]]) -> None:
        router = ParetoRouter()
        frontier = router.route_by_pareto_frontier(_models)
        frontier_names = _names(frontier)
        # haiku dominates gemini_flash (0.01,0.70 vs 0.03,0.80) — no. haiku is lower quality
        # Actually haiku(0.01,0.70) vs gemini_flash(0.03,0.80): gemini_flash has both HIGHER
        # cost AND higher quality — not comparable. Both could be on frontier.
        # opus(0.05,0.95) vs gpt_4(0.10,0.92): gpt_4 is more expensive AND lower quality
        # -> gpt_4 dominated by opus.
        assert "gpt_4" not in frontier_names

    def test_realistic_winner_exists(self, _models: list[dict[str, Any]]) -> None:
        router = ParetoRouter(cost_weight=0.2, quality_weight=0.8)
        frontier = router.route_by_pareto_frontier(_models)
        winner = router.pick_winner(frontier)
        assert winner is not None
        assert winner in frontier

    def test_all_models_have_names(self, _models: list[dict[str, Any]]) -> None:
        router = ParetoRouter()
        frontier = router.route_by_pareto_frontier(_models)
        for c in frontier:
            assert "name" in c
            assert isinstance(c["name"], str) and len(c["name"]) > 0

    def test_realistic_cost_quality_ranges(self) -> None:
        router = ParetoRouter()
        candidates = [
            _make_candidate(0.0, 1.0, "best"),
            _make_candidate(1.0, 0.0, "worst"),
            _make_candidate(0.5, 0.5, "mid"),
        ]
        frontier = router.route_by_pareto_frontier(candidates)
        # worst(1.0, 0.0) is dominated by best (0.0, 1.0) and mid(0.5, 0.5) too —
        # actually mid is more expensive (0.5>0.0) AND lower quality (0.5<1.0), so mid
        # is dominated by best. worst is ALSO dominated by best. Only best survives.
        # Actually: let's check pairwise:
        # best(0.0,1.0) vs mid(0.5,0.5): cost 0.0<=0.5, quality 1.0>=0.5, and 0.0<0.5
        #   → mid dominated by best
        # best(0.0,1.0) vs worst(1.0,0.0): cost 0.0<=1.0, quality 1.0>=0.0, and 0.0<1.0
        #   → worst dominated by best
        # result: frontier = [best]
        assert _names(frontier) == ["best"]


# ---------------------------------------------------------------------------
# 15. dominated models (higher cost AND lower quality) always excluded
# ---------------------------------------------------------------------------

class TestDominatedExclusion:
    def test_strictly_worse_both_axes_excluded(self) -> None:
        router = ParetoRouter()
        candidates = [
            _make_candidate(0.3, 0.8, "good"),
            _make_candidate(0.5, 0.4, "bad"),  # more expensive AND lower quality
        ]
        frontier = router.route_by_pareto_frontier(candidates)
        assert _names(frontier) == ["good"]

    def test_dominated_by_not_farthest(self) -> None:
        router = ParetoRouter()
        candidates = [
            _make_candidate(0.5, 0.6, "mid"),
            _make_candidate(0.3, 0.9, "best"),
            _make_candidate(0.4, 0.5, "worst"),  # dominated by both
        ]
        frontier = router.route_by_pareto_frontier(candidates)
        names = _names(frontier)
        # best (0.3, 0.9) dominates mid (0.5, 0.6) — lower cost AND higher quality
        assert names == ["best"]
        assert "worst" not in names

    def test_equal_cost_lower_quality_dominated(self) -> None:
        router = ParetoRouter()
        candidates = [
            _make_candidate(0.3, 0.8, "good"),
            _make_candidate(0.3, 0.5, "worse"),  # same cost, lower quality
        ]
        frontier = router.route_by_pareto_frontier(candidates)
        assert _names(frontier) == ["good"]

    def test_higher_cost_equal_quality_dominated(self) -> None:
        router = ParetoRouter()
        candidates = [
            _make_candidate(0.2, 0.7, "cheaper"),
            _make_candidate(0.5, 0.7, "expensive_same_q"),  # more expensive, same quality
        ]
        frontier = router.route_by_pareto_frontier(candidates)
        assert _names(frontier) == ["cheaper"]

    def test_higher_cost_lower_quality_dominated_by_middle(self) -> None:
        router = ParetoRouter()
        candidates = [
            _make_candidate(0.2, 0.6, "a"),
            _make_candidate(0.4, 0.8, "b"),
            _make_candidate(0.7, 0.3, "c"),  # more expensive AND lower quality than both
        ]
        frontier = router.route_by_pareto_frontier(candidates)
        assert "c" not in _names(frontier)

    def test_lower_cost_higher_quality_always_non_dominated(self) -> None:
        router = ParetoRouter()
        candidates = [
            _make_candidate(0.1, 0.99, "champ"),
            _make_candidate(0.5, 0.5, "mid"),
        ]
        frontier = router.route_by_pareto_frontier(candidates)
        assert "champ" in _names(frontier)


# ---------------------------------------------------------------------------
# 16. all frontier items are non-dominated (pairwise verification)
# ---------------------------------------------------------------------------

class TestPairwiseNonDomination:
    def test_no_frontier_item_dominates_another(self) -> None:
        router = ParetoRouter()
        candidates = [
            _make_candidate(0.2, 0.9, "a"),
            _make_candidate(0.1, 0.85, "b"),
            _make_candidate(0.3, 0.95, "c"),
            _make_candidate(0.5, 0.6, "d"),
            _make_candidate(0.7, 0.4, "e"),
            _make_candidate(0.4, 0.8, "f"),
            _make_candidate(0.25, 0.88, "g"),
        ]
        frontier = router.route_by_pareto_frontier(candidates)

        # Verify every FRONTIER pair: neither dominates the other
        for i, ci in enumerate(frontier):
            for j, cj in enumerate(frontier):
                if i >= j:
                    continue
                cost_i, qual_i = float(ci["cost"]), float(ci["quality"])
                cost_j, qual_j = float(cj["cost"]), float(cj["quality"])

                i_dominates_j = (
                    cost_i <= cost_j
                    and qual_i >= qual_j
                    and (cost_i < cost_j or qual_i > qual_j)
                )
                j_dominates_i = (
                    cost_j <= cost_i
                    and qual_j >= qual_i
                    and (cost_j < cost_i or qual_j > qual_i)
                )
                assert not i_dominates_j, (
                    f"frontier[{i}] dominates frontier[{j}]: "
                    f"({cost_i},{qual_i}) vs ({cost_j},{qual_j})"
                )
                assert not j_dominates_i, (
                    f"frontier[{j}] dominates frontier[{i}]: "
                    f"({cost_j},{qual_j}) vs ({cost_i},{qual_i})"
                )

    def test_pairwise_randomized(self) -> None:
        import random

        rng = random.Random(42)
        router = ParetoRouter()
        candidates = [
            _make_candidate(rng.uniform(0, 1), rng.uniform(0, 1), f"c{i}")
            for i in range(30)
        ]
        frontier = router.route_by_pareto_frontier(candidates)

        # Verify no frontier candidate is dominated by any candidate in FULL set
        for fc in frontier:
            fc_cost, fc_qual = float(fc["cost"]), float(fc["quality"])
            for c in candidates:
                c_cost, c_qual = float(c["cost"]), float(c["quality"])
                if c is fc:
                    continue
                dominated = (
                    c_cost <= fc_cost
                    and c_qual >= fc_qual
                    and (c_cost < fc_cost or c_qual > fc_qual)
                )
                assert not dominated, (
                    f"frontier {fc['name']}({fc_cost},{fc_qual}) is dominated by "
                    f"{c['name']}({c_cost},{c_qual})"
                )

    def test_pairwise_with_equal_candidates(self) -> None:
        router = ParetoRouter()
        candidates = [
            _make_candidate(0.5, 0.5, "a"),
            _make_candidate(0.5, 0.5, "b"),
            _make_candidate(0.5, 0.5, "c"),
        ]
        frontier = router.route_by_pareto_frontier(candidates)
        # All equal, all survive, no pair dominates another
        assert len(frontier) == 3
        names = _names(frontier)
        assert names == ["a", "b", "c"]  # quality-descending, all equal

    def test_known_benchmark_frontier(self) -> None:
        router = ParetoRouter()
        # Well-known Pareto set: (0.1,0.9), (0.2,0.95), (0.7,0.97), (1.0,0.99)
        candidates = [
            _make_candidate(0.1, 0.90, "a"),
            _make_candidate(0.2, 0.95, "b"),
            _make_candidate(0.7, 0.97, "c"),
            _make_candidate(1.0, 0.99, "d"),
            _make_candidate(0.3, 0.80, "dominated_by_b"),
            _make_candidate(0.5, 0.60, "dominated_by_both_b_and_c"),
        ]
        frontier = router.route_by_pareto_frontier(candidates)
        names = _names(frontier)
        assert "dominated_by_b" not in names
        assert "dominated_by_both_b_and_c" not in names
        assert names[:4] == ["d", "c", "b", "a"]  # quality-descending
        assert len(frontier) == 4


# ---------------------------------------------------------------------------
# integration: ParetoRouter used via AdaptiveRouter._apply_pareto_filter
# ---------------------------------------------------------------------------

class TestAdaptiveRouterIntegration:
    """Prove ParetoRouter integrates correctly with AdaptiveRouter's filter."""

    def test_apply_pareto_filter_calls_router(self) -> None:
        from general_ludd.scoring.router import AdaptiveRouter

        pareto = ParetoRouter(cost_weight=0.3, quality_weight=0.7)
        adaptive = AdaptiveRouter(pareto_router=pareto)

        from general_ludd.schemas.benchmark import RoutingCandidate, TaskType

        c1 = RoutingCandidate(
            prompt_profile_id="p1",
            model_profile_id="opus",
            composite_score=0.9,
            avg_cost_usd=0.05,
            sample_count=10,
            task_type=TaskType.BUG_FIX,
        )
        c2 = RoutingCandidate(
            prompt_profile_id="p2",
            model_profile_id="sonnet",
            composite_score=0.85,
            avg_cost_usd=0.03,
            sample_count=10,
            task_type=TaskType.BUG_FIX,
        )
        c3 = RoutingCandidate(
            prompt_profile_id="p3",
            model_profile_id="expensive_dumb",
            composite_score=0.6,
            avg_cost_usd=1.0,
            sample_count=10,
            task_type=TaskType.BUG_FIX,
        )

        weighted = [(c1, 0.9, None), (c2, 0.85, None), (c3, 0.6, None)]
        result = adaptive._apply_pareto_filter(weighted)
        model_ids = {c.model_profile_id for c, _, _ in result}
        assert "opus" in model_ids
        assert "sonnet" in model_ids
        assert "expensive_dumb" not in model_ids

    def test_apply_pareto_filter_single_candidate_noop(self) -> None:
        from general_ludd.schemas.benchmark import RoutingCandidate, TaskType
        from general_ludd.scoring.router import AdaptiveRouter

        pareto = ParetoRouter()
        adaptive = AdaptiveRouter(pareto_router=pareto)
        c1 = RoutingCandidate(
            prompt_profile_id="p1",
            model_profile_id="solo",
            composite_score=0.5,
            avg_cost_usd=0.5,
            sample_count=3,
            task_type=TaskType.BUG_FIX,
        )
        weighted = [(c1, 0.5, None)]
        result = adaptive._apply_pareto_filter(weighted)
        assert len(result) == 1
        assert result[0][0].model_profile_id == "solo"

    def test_apply_pareto_filter_no_router_passthrough(self) -> None:
        from general_ludd.schemas.benchmark import RoutingCandidate, TaskType
        from general_ludd.scoring.router import AdaptiveRouter

        adaptive = AdaptiveRouter(pareto_router=None)
        c1 = RoutingCandidate(
            prompt_profile_id="p1",
            model_profile_id="a",
            composite_score=0.9,
            avg_cost_usd=0.1,
            sample_count=5,
            task_type=TaskType.BUG_FIX,
        )
        c2 = RoutingCandidate(
            prompt_profile_id="p2",
            model_profile_id="b",
            composite_score=0.5,
            avg_cost_usd=1.0,
            sample_count=5,
            task_type=TaskType.BUG_FIX,
        )
        weighted = [(c1, 0.9, None), (c2, 0.5, None)]
        result = adaptive._apply_pareto_filter(weighted)
        assert len(result) == 2

    def test_apply_pareto_filter_full_pipeline(self) -> None:
        from general_ludd.schemas.benchmark import RoutingCandidate, TaskType
        from general_ludd.scoring.router import AdaptiveRouter

        pareto = ParetoRouter(cost_weight=0.3, quality_weight=0.7)
        adaptive = AdaptiveRouter(pareto_router=pareto)

        candidates = [
            RoutingCandidate(
                prompt_profile_id="p1", model_profile_id="opus",
                composite_score=0.95, avg_cost_usd=0.05,
                sample_count=10, task_type=TaskType.BUG_FIX,
            ),
            RoutingCandidate(
                prompt_profile_id="p2", model_profile_id="sonnet",
                composite_score=0.88, avg_cost_usd=0.03,
                sample_count=10, task_type=TaskType.BUG_FIX,
            ),
            RoutingCandidate(
                prompt_profile_id="p3", model_profile_id="haiku",
                composite_score=0.70, avg_cost_usd=0.01,
                sample_count=10, task_type=TaskType.BUG_FIX,
            ),
            RoutingCandidate(
                prompt_profile_id="p4", model_profile_id="gpt4",
                composite_score=0.90, avg_cost_usd=0.08,
                sample_count=10, task_type=TaskType.BUG_FIX,
            ),
            RoutingCandidate(
                prompt_profile_id="p5", model_profile_id="bad",
                composite_score=0.60, avg_cost_usd=0.50,
                sample_count=10, task_type=TaskType.BUG_FIX,
            ),
        ]
        weighted = [(c, float(c.composite_score), None) for c in candidates]

        filtered = adaptive._apply_pareto_filter(weighted)
        frontier_ids = {c.model_profile_id for c, _, _ in filtered}
        assert "bad" not in frontier_ids
        # Opus dominates gpt4 on both axes (0.05 < 0.08 AND 0.95 > 0.90)
        assert "gpt4" not in frontier_ids

        # Now pick_winner on the frontier
        pareto_input = [
            {"cost": c.avg_cost_usd, "quality": q, "name": c.model_profile_id}
            for c, q, _ in filtered
        ]
        winner = pareto.pick_winner(pareto_input)
        assert winner is not None
        assert winner["name"] in frontier_ids
